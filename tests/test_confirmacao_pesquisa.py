"""E-mail de confirmação da pesquisa pública e link estável do relatório.

Cobre: conteúdo e identidade do e-mail, envio só após processamento,
não reenvio em duplicidade, falha de SMTP sem derrubar a requisição,
autorização/expiração/revogação do link estável e proteção dos dados
(token opaco, só hash na base, sem dados pessoais na URL).
"""

import asyncio
import smtplib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.database import get_session
from app.emailing import (
    _mensagem_confirmacao_pesquisa,
    enviar_confirmacao_pesquisa,
)
from app.main import app
from app.models import PesquisaMarca
from app.relatorio_link import (
    definir_link_relatorio,
    revogar_link_relatorio,
    validar_token_link,
)
from app.security import exigir_token_relatorio_publico
from app.settings import Settings
from tests.conftest import FakeResult, FakeSession


def configuracao_email() -> Settings:
    return Settings(
        app_env="test",
        app_public_url="https://app.zeregistra.test/",
        email_enabled=True,
        confirmacao_pesquisa_email_enabled=True,
        email_from_address="nao-responda@zeregistra.test",
        email_from_name="Zé Registra",
        smtp_host="mailpit",
        smtp_port=1025,
        smtp_starttls=False,
    )


# --- Conteúdo do e-mail -------------------------------------------------


def test_mensagem_confirmacao_tem_marca_link_validade_e_ressalva() -> None:
    mensagem = _mensagem_confirmacao_pesquisa(
        "cliente@example.test",
        "Ana",
        "PAOCOMLATE",
        "https://app.zeregistra.test/relatorios/abc#token=rlk_secreto",
        168,
        configuracao_email(),
    )

    assert mensagem["To"] == "cliente@example.test"
    assert mensagem["From"] == "Zé Registra <nao-responda@zeregistra.test>"
    assert "PAOCOMLATE" in mensagem["Subject"]
    assert "Zé Registra" in mensagem["Subject"]
    texto = mensagem.get_body(preferencelist=("plain",)).get_content()
    pagina = mensagem.get_body(preferencelist=("html",)).get_content()
    for conteudo in (texto, pagina):
        normalizado = " ".join(conteudo.split())
        assert "PAOCOMLATE" in normalizado
        assert "recebida" in normalizado and "consulta" in normalizado
        assert "https://app.zeregistra.test/relatorios/abc#token=rlk_secreto" in normalizado
        assert "7 dias" in normalizado
        assert "indicativa" in normalizado
        assert "não representa garantia de registro" in normalizado
    assert "ZÉ REGISTRA" in pagina


def test_mensagem_confirmacao_escapa_html_e_sanitiza_cabecalho() -> None:
    mensagem = _mensagem_confirmacao_pesquisa(
        "cliente@example.test",
        "Ana <Admin>",
        "MARCA\nX <b>",
        "https://app.zeregistra.test/relatorios/abc#token=rlk_secreto",
        168,
        configuracao_email(),
    )

    assert "\n" not in mensagem["Subject"]
    pagina = mensagem.get_body(preferencelist=("html",)).get_content()
    assert "Ana &lt;Admin&gt;" in pagina
    assert "MARCA\nX &lt;b&gt;" in pagina


def test_mensagem_confirmacao_usa_nome_do_escritorio_quando_presente() -> None:
    mensagem = _mensagem_confirmacao_pesquisa(
        "cliente@example.test",
        "Ana",
        "MARCA",
        "https://app.zeregistra.test/relatorios/abc#token=rlk_secreto",
        168,
        configuracao_email(),
        organizacao_nome="Escritório Parceiro",
    )

    assert "Escritório Parceiro" in mensagem["Subject"]
    assert "ESCRITÓRIO PARCEIRO" in mensagem.get_body(preferencelist=("html",)).get_content()


# --- Envio ----------------------------------------------------------------


def test_confirmacao_nao_envia_quando_email_desabilitado(monkeypatch: pytest.MonkeyPatch) -> None:
    enviados: list[str] = []
    settings = configuracao_email().model_copy(update={"email_enabled": False})
    monkeypatch.setattr("app.emailing.get_settings", lambda: settings)
    monkeypatch.setattr("app.emailing._enviar_smtp", lambda msg, _s: enviados.append(msg["To"]))

    enviado = asyncio.run(
        enviar_confirmacao_pesquisa(
            destinatario="cliente@example.test", nome="Ana", marca="MARCA", link_relatorio="https://x#token=rlk_a"
        )
    )

    assert enviado is False
    assert enviados == []


def test_confirmacao_envia_e_devolve_true(monkeypatch: pytest.MonkeyPatch) -> None:
    enviados: list[str] = []

    async def _uso(_provedor: object) -> int:
        return 0

    monkeypatch.setattr("app.emailing.get_settings", configuracao_email)
    monkeypatch.setattr("app.emailing._PROVEDORES_ESGOTADOS_ATE", {})
    monkeypatch.setattr("app.emailing._uso_24h", _uso)
    monkeypatch.setattr("app.emailing._enviar_smtp", lambda msg, _s: enviados.append(msg["To"]))

    enviado = asyncio.run(
        enviar_confirmacao_pesquisa(
            destinatario="cliente@example.test", nome="Ana", marca="MARCA", link_relatorio="https://x#token=rlk_a"
        )
    )

    assert enviado is True
    assert enviados == ["cliente@example.test"]


def test_confirmacao_falha_smtp_nao_levanta_e_registra_rejeicao(monkeypatch: pytest.MonkeyPatch) -> None:
    rejeicoes: list[str] = []

    async def _uso(_provedor: object) -> int:
        return 0

    def _smtp_falho(_msg, _s) -> None:
        raise smtplib.SMTPRecipientsRefused({"cliente@example.test": (550, b"mailbox unavailable")})

    async def _registrar(operacao: str, _exc: Exception) -> None:
        rejeicoes.append(operacao)

    monkeypatch.setattr("app.emailing.get_settings", configuracao_email)
    monkeypatch.setattr("app.emailing._PROVEDORES_ESGOTADOS_ATE", {})
    monkeypatch.setattr("app.emailing._uso_24h", _uso)
    monkeypatch.setattr("app.emailing._enviar_smtp", _smtp_falho)
    monkeypatch.setattr("app.emailing._registrar_email_rejeitado", _registrar)

    enviado = asyncio.run(
        enviar_confirmacao_pesquisa(
            destinatario="cliente@example.test", nome="Ana", marca="MARCA", link_relatorio="https://x#token=rlk_a"
        )
    )

    assert enviado is False
    assert rejeicoes == ["confirmacao_pesquisa"]


def test_confirmacao_erro_ao_montar_mensagem_nao_levanta_e_registra_rejeicao(monkeypatch: pytest.MonkeyPatch) -> None:
    rejeicoes: list[str] = []

    def _montagem_falha(*_args, **_kwargs):
        raise ValueError("falha ao montar a mensagem")

    async def _registrar(operacao: str, _exc: Exception) -> None:
        rejeicoes.append(operacao)

    monkeypatch.setattr("app.emailing.get_settings", configuracao_email)
    monkeypatch.setattr("app.emailing._mensagem_confirmacao_pesquisa", _montagem_falha)
    monkeypatch.setattr("app.emailing._registrar_email_rejeitado", _registrar)

    enviado = asyncio.run(
        enviar_confirmacao_pesquisa(
            destinatario="cliente@example.test", nome="Ana", marca="MARCA", link_relatorio="https://x#token=rlk_a"
        )
    )

    assert enviado is False
    assert rejeicoes == ["confirmacao_pesquisa"]


# --- Link estável: autorização, expiração e revogação --------------------


def _pesquisa_com_link() -> tuple[PesquisaMarca, str]:
    pesquisa = PesquisaMarca(
        id="pesq-1",
        organizacao_id=1,
        marca="MARCA",
        tipo_pesquisa="completa",
    )
    token = definir_link_relatorio(pesquisa, ttl_horas=168)
    return pesquisa, token


def test_link_estavel_valido() -> None:
    pesquisa, token = _pesquisa_com_link()
    session = FakeSession([FakeResult(scalar=pesquisa)])

    assert asyncio.run(validar_token_link(session, "pesq-1", 1, token)) is True


def test_link_estavel_guarda_apenas_hash() -> None:
    pesquisa, token = _pesquisa_com_link()

    assert pesquisa.relatorio_link_token_hash
    assert pesquisa.relatorio_link_token_hash != token
    assert token not in (pesquisa.relatorio_link_token_hash or "")


def test_link_estavel_errado_ou_de_outra_pesquisa_e_rejeitado() -> None:
    pesquisa, _token = _pesquisa_com_link()
    session = FakeSession([FakeResult(scalar=pesquisa)])

    assert asyncio.run(validar_token_link(session, "pesq-1", 1, "rlk_outro-token-qualquer")) is False


def test_link_estavel_de_outro_tenant_e_rejeitado() -> None:
    _pesquisa, token = _pesquisa_com_link()
    # O banco filtra por (id, organizacao_id): outro tenant não encontra a pesquisa.
    session = FakeSession([FakeResult(scalar=None)])

    assert asyncio.run(validar_token_link(session, "pesq-1", 2, token)) is False


def test_link_estavel_expirado_e_rejeitado() -> None:
    pesquisa, token = _pesquisa_com_link()
    pesquisa.relatorio_link_expira_em = datetime.now(UTC) - timedelta(minutes=1)
    session = FakeSession([FakeResult(scalar=pesquisa)])

    assert asyncio.run(validar_token_link(session, "pesq-1", 1, token)) is False


def test_link_estavel_revogado_e_rejeitado() -> None:
    pesquisa, token = _pesquisa_com_link()
    revogar_link_relatorio(pesquisa)
    assert pesquisa.relatorio_link_token_hash is None
    session = FakeSession([FakeResult(scalar=pesquisa)])

    assert asyncio.run(validar_token_link(session, "pesq-1", 1, token)) is False


def test_token_hmac_curto_nao_passa_pela_validacao_do_link() -> None:
    pesquisa, _token = _pesquisa_com_link()
    session = FakeSession([FakeResult(scalar=pesquisa)])

    # O token HMAC de 15 minutos não tem o prefixo do link estável.
    assert asyncio.run(validar_token_link(session, "pesq-1", 1, "payload.assinatura")) is False


# --- Dependência de acesso ao relatório público ---------------------------


def _request_com_token(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/v1/pesquisas-marca/pesq-1/relatorio",
            "query_string": b"",
            "headers": [(b"x-report-token", token.encode("ascii"))],
            "server": ("app.zeregistra.test", 443),
            "client": ("127.0.0.1", 1234),
        }
    )


def test_acesso_relatorio_aceita_link_estavel() -> None:
    pesquisa, token = _pesquisa_com_link()
    session = FakeSession([FakeResult(scalar=pesquisa)])
    organizacao = SimpleNamespace(id=1)

    asyncio.run(exigir_token_relatorio_publico("pesq-1", _request_com_token(token), organizacao, session))


def test_acesso_relatorio_recusa_link_revogado() -> None:
    pesquisa, token = _pesquisa_com_link()
    revogar_link_relatorio(pesquisa)
    session = FakeSession([FakeResult(scalar=pesquisa)])
    organizacao = SimpleNamespace(id=1)

    with pytest.raises(HTTPException) as erro:
        asyncio.run(exigir_token_relatorio_publico("pesq-1", _request_com_token(token), organizacao, session))
    assert erro.value.status_code == 401


def test_acesso_relatorio_recusa_token_inexistente() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    organizacao = SimpleNamespace(id=1)

    with pytest.raises(HTTPException) as erro:
        asyncio.run(
            exigir_token_relatorio_publico("pesq-1", _request_com_token("rlk_inexistente"), organizacao, session)
        )
    assert erro.value.status_code == 401


# --- Fluxo público: envio uma única vez, nunca em duplicidade --------------


def _monkeypatch_fluxo(monkeypatch: pytest.MonkeyPatch, capturados: list[dict]) -> None:
    async def nenhum(*_args, **_kwargs):
        return None

    async def fase_alterada(*_args, **_kwargs):
        return True

    async def confirmacao(**kwargs):
        capturados.append(kwargs)
        return True

    monkeypatch.setattr("app.api.pesquisas.get_settings", configuracao_email)
    monkeypatch.setattr("app.api.pesquisas.validar_limite_pesquisas", nenhum)
    monkeypatch.setattr("app.api.pesquisas.obter_ou_criar_empresa", nenhum)
    monkeypatch.setattr("app.api.pesquisas.buscar_lead_ativo_por_email", nenhum)
    monkeypatch.setattr("app.api.pesquisas.avancar_fase_lead", fase_alterada)
    monkeypatch.setattr("app.api.pesquisas.enviar_alerta_nova_pesquisa", nenhum)
    monkeypatch.setattr("app.api.pesquisas.enviar_confirmacao_pesquisa", confirmacao)


_PAYLOAD = {
    "nome": "Contato Teste",
    "empresa": "Empresa Teste",
    "email_corporativo": "contato@empresa.test",
    "telefone": "16999999999",
    "marca": "Marca Teste",
    "atividade": "Serviços de tecnologia",
    "aceite_privacidade": True,
    "aceite_marketing": False,
    "website": "",
}


def test_fluxo_publico_envia_confirmacao_uma_vez_com_link_estavel(monkeypatch: pytest.MonkeyPatch) -> None:
    capturados: list[dict] = []
    _monkeypatch_fluxo(monkeypatch, capturados)

    async def nenhum(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.api.pesquisas.detectar_pesquisa_duplicada", nenhum)

    session = PesquisaSession()
    app.dependency_overrides[get_session] = _override(session)
    try:
        resposta = TestClient(app).post("/v1/pesquisas-marca", json=_PAYLOAD)
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 201, resposta.text
    assert len(capturados) == 1
    chamada = capturados[0]
    assert chamada["destinatario"] == "contato@empresa.test"
    assert chamada["marca"] == "Marca Teste"
    assert "/relatorios/" in chamada["link_relatorio"]
    assert "#token=rlk_" in chamada["link_relatorio"]
    # Nenhum dado pessoal na URL: só o id da pesquisa e o token opaco.
    assert "contato" not in chamada["link_relatorio"].split("/relatorios/")[1]
    assert "@" not in chamada["link_relatorio"]

    pesquisa = next(obj for obj in session.adicionados if isinstance(obj, PesquisaMarca))
    assert pesquisa.relatorio_link_token_hash
    assert pesquisa.relatorio_link_expira_em is not None
    assert pesquisa.confirmacao_email_enviado_em is not None


def test_fluxo_publico_duplicado_nao_envia_confirmacao(monkeypatch: pytest.MonkeyPatch) -> None:
    capturados: list[dict] = []
    _monkeypatch_fluxo(monkeypatch, capturados)

    async def duplicada(*_args, **_kwargs):
        return "pesquisa-original"

    monkeypatch.setattr("app.api.pesquisas.detectar_pesquisa_duplicada", duplicada)

    session = FakeSession()
    app.dependency_overrides[get_session] = _override(session)
    try:
        resposta = TestClient(app).post("/v1/pesquisas-marca", json=_PAYLOAD)
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 201, resposta.text
    assert resposta.json()["duplicada"] is True
    assert capturados == []


def _override(session: FakeSession):
    async def override():
        yield session

    return override


class PesquisaSession(FakeSession):
    async def refresh(self, obj) -> None:
        await super().refresh(obj)
        if isinstance(obj, PesquisaMarca):
            obj.id = "pesquisa-fluxo"
            obj.criado_em = datetime.now(UTC)


def test_confirmacao_feature_flag_desligada_nao_envia(monkeypatch):
    settings = configuracao_email().model_copy(update={"confirmacao_pesquisa_email_enabled": False})
    monkeypatch.setattr("app.emailing.get_settings", lambda: settings)
    def proibido(*_args):
        pytest.fail("SMTP não deve ser chamado")
    monkeypatch.setattr("app.emailing._enviar_smtp", proibido)
    assert asyncio.run(enviar_confirmacao_pesquisa(
        destinatario="cliente@example.test", nome="Ana", marca="Marca", link_relatorio="https://x"
    )) is False


@pytest.mark.parametrize("endereco", ["a,b@example.com", "a;z@example.com", "x@-dominio.com", "a\nBcc:x@example.com"])
def test_confirmacao_endereco_invalido_nao_envia(monkeypatch, endereco):
    monkeypatch.setattr("app.emailing.get_settings", configuracao_email)
    def proibido(*_args):
        pytest.fail("SMTP não deve ser chamado")
    monkeypatch.setattr("app.emailing._enviar_smtp", proibido)
    assert asyncio.run(enviar_confirmacao_pesquisa(
        destinatario=endereco, nome="Ana", marca="Marca", link_relatorio="https://x"
    )) is False


def test_confirmacao_limite_por_endereco_independe_da_marca(monkeypatch):
    enviados = []
    monkeypatch.setattr("app.emailing.get_settings", configuracao_email)
    async def smtp(msg, *_args):
        enviados.append(msg["To"])
        return "principal"
    monkeypatch.setattr("app.emailing._enviar_smtp_contabilizado", smtp)
    resultados = [asyncio.run(enviar_confirmacao_pesquisa(
        destinatario="cliente@example.test", nome="Ana", marca=f"Marca {i}", link_relatorio="https://x"
    )) for i in range(4)]
    assert resultados == [True, True, True, False]
    assert len(enviados) == 3


def test_confirmacao_timeout_nao_tenta_novamente(monkeypatch):
    chamadas = []
    monkeypatch.setattr("app.emailing.get_settings", configuracao_email)
    async def smtp(*_args):
        chamadas.append(1)
        raise TimeoutError("resultado SMTP ambíguo")
    monkeypatch.setattr("app.emailing._enviar_smtp_contabilizado", smtp)
    assert asyncio.run(enviar_confirmacao_pesquisa(
        destinatario="cliente@example.test", nome="Ana", marca="Marca", link_relatorio="https://x"
    )) is False
    assert chamadas == [1]


def test_fluxo_falha_commit_token_preserva_resposta(monkeypatch):
    capturados = []
    _monkeypatch_fluxo(monkeypatch, capturados)
    async def nenhum(*_args, **_kwargs):
        return None
    monkeypatch.setattr("app.api.pesquisas.detectar_pesquisa_duplicada", nenhum)
    class FalhaTokenSession(PesquisaSession):
        chamadas = 0
        rollback_feito = False
        async def commit(self):
            self.chamadas += 1
            if self.chamadas == 2:
                raise RuntimeError("banco indisponível ao gravar token")
            await super().commit()
        async def rollback(self):
            self.rollback_feito = True
    session = FalhaTokenSession()
    app.dependency_overrides[get_session] = _override(session)
    try:
        resposta = TestClient(app).post("/v1/pesquisas-marca", json=_PAYLOAD)
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 201
    assert resposta.json()["id"] == "pesquisa-fluxo"
    assert session.rollback_feito
    assert capturados == []


def test_fluxo_confirmacao_excecao_preserva_pesquisa(monkeypatch):
    _monkeypatch_fluxo(monkeypatch, [])
    async def nenhum(*_args, **_kwargs):
        return None
    async def falha(**_kwargs):
        raise TimeoutError("SMTP offline")
    monkeypatch.setattr("app.api.pesquisas.detectar_pesquisa_duplicada", nenhum)
    monkeypatch.setattr("app.api.pesquisas.enviar_confirmacao_pesquisa", falha)
    class RollbackSession(PesquisaSession):
        async def rollback(self):
            pass
    app.dependency_overrides[get_session] = _override(RollbackSession())
    try:
        resposta = TestClient(app).post("/v1/pesquisas-marca", json=_PAYLOAD)
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 201
    assert resposta.json()["relatorio_token"]
