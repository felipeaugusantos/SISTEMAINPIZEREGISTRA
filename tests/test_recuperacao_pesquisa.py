"""Revisão de segurança (12/11/2026) — recuperação de acesso a pesquisas
anteriores com comprovação de posse do e-mail.

Contexto: o formulário público não comprova a posse do e-mail informado, e
o reenvio do par marca+e-mail devolvia um token utilizável da pesquisa
original (qualquer pessoa que soubesse o par abria o relatório dela).

A correção:
1. O reenvio não devolve mais token (ver tests/test_pesquisas_reemissao_publica.py).
2. POST /v1/pesquisas-marca/recuperar-acesso inicia a prova de posse:
   resposta SEMPRE uniforme (sem oráculo de enumeração), código de 6
   dígitos por e-mail (nunca o link do resultado), só o hash na base,
   expiração curta, limite de tentativas e uso único. Desabilitado
   (recuperacao_pesquisa_email_enabled=false), falha fechado: mesma
   resposta, nenhum envio, nenhum código gravado.
3. POST /v1/pesquisas-marca/recuperar-acesso/confirmar troca o código pelo
   token curto da tela; qualquer falha devolve o mesmo 400 genérico.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token
from app.database import get_session
from app.main import app
from app.models import Lead, PesquisaMarca, StatusLead
from app.public_report_tokens import TokenRelatorioInvalido, validar_token_relatorio
from app.tenancy import _organizacao_padrao, resolver_organizacao_publica
from tests.conftest import FakeResult, FakeSession

_SETTINGS = SimpleNamespace(
    email_enabled=True,
    recuperacao_pesquisa_email_enabled=True,
    recuperacao_codigo_minutos=15,
    recuperacao_codigo_max_tentativas=5,
)


def _session_override(session: FakeSession):
    async def override():
        yield session

    return override


def _preparar(monkeypatch, *, lead, original, enviados: list[dict] | None = None) -> None:
    async def lead_encontrado(*_args, **_kwargs):
        return lead

    async def original_encontrado(*_args, **_kwargs):
        return original

    async def envio_falso(**kwargs):
        if enviados is not None:
            enviados.append(kwargs)
        return True

    monkeypatch.setattr("app.api.pesquisas.buscar_lead_ativo_por_email", lead_encontrado)
    monkeypatch.setattr("app.api.pesquisas.detectar_pesquisa_duplicada", original_encontrado)
    monkeypatch.setattr("app.api.pesquisas.enviar_codigo_recuperacao_pesquisa", envio_falso)
    monkeypatch.setattr("app.api.pesquisas.get_settings", lambda: _SETTINGS)
    app.dependency_overrides[resolver_organizacao_publica] = _organizacao_padrao


def _limpar() -> None:
    app.dependency_overrides.clear()


def _lead() -> Lead:
    return Lead(
        id=7,
        organizacao_id=1,
        nome="Nome Original",
        email="contato@empresa.test",
        telefone="11111111111",
        marca="Marca Original",
        status=StatusLead.NOVO,
    )


def _pesquisa(**kwargs: object) -> PesquisaMarca:
    base: dict[str, object] = {
        "id": "pesquisa-original",
        "organizacao_id": 1,
        "lead_id": 7,
        "marca": "Marca Teste",
        "tipo_pesquisa": "completa",
    }
    base.update(kwargs)
    return PesquisaMarca(**base)


def _oid_do_token(token: str) -> int:
    payload_codificado = token.split(".", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(payload_codificado + "=" * (-len(payload_codificado) % 4)))
    return payload["oid"]


def test_recuperar_par_inexistente_responde_uniforme_e_nao_envia(monkeypatch) -> None:
    enviados: list[dict] = []
    _preparar(monkeypatch, lead=None, original=None, enviados=enviados)
    app.dependency_overrides[get_session] = _session_override(FakeSession())
    try:
        resposta = TestClient(app).post(
            "/v1/pesquisas-marca/recuperar-acesso",
            json={"email_corporativo": "ninguem@empresa.test", "marca": "Marca Teste"},
        )
    finally:
        _limpar()

    assert resposta.status_code == 200, resposta.text
    assert "código de acesso" in resposta.json()["detalhe"]
    assert enviados == []


def test_recuperar_par_existente_envia_codigo_e_responde_igual(monkeypatch) -> None:
    enviados: list[dict] = []
    pesquisa = _pesquisa()
    _preparar(monkeypatch, lead=_lead(), original="pesquisa-original", enviados=enviados)
    session = FakeSession(resultados=[FakeResult(scalar=pesquisa)])
    app.dependency_overrides[get_session] = _session_override(session)
    try:
        cliente = TestClient(app)
        resposta = cliente.post(
            "/v1/pesquisas-marca/recuperar-acesso",
            json={"email_corporativo": "contato@empresa.test", "marca": "Marca Teste"},
        )
        # Resposta idêntica à do par inexistente: nada distingue os dois casos.
        _preparar(monkeypatch, lead=None, original=None, enviados=[])
        resposta_inexistente = cliente.post(
            "/v1/pesquisas-marca/recuperar-acesso",
            json={"email_corporativo": "ninguem@empresa.test", "marca": "Marca Teste"},
        )
    finally:
        _limpar()

    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == resposta_inexistente.json()
    assert len(enviados) == 1
    chamada = enviados[0]
    assert chamada["destinatario"] == "contato@empresa.test"
    assert chamada["marca"] == "Marca Teste"
    codigo = chamada["codigo"]
    assert len(codigo) == 6 and codigo.isdigit()
    # Só o hash fica na base; o código em claro nunca é gravado.
    assert pesquisa.recuperacao_codigo_hash == hash_token(codigo)
    assert pesquisa.recuperacao_codigo_hash != codigo
    assert pesquisa.recuperacao_codigo_tentativas == 0
    assert pesquisa.recuperacao_codigo_expira_em is not None
    assert pesquisa.recuperacao_codigo_enviado_em is not None


def test_recuperar_desabilitado_falha_fechado(monkeypatch) -> None:
    enviados: list[dict] = []
    _preparar(monkeypatch, lead=_lead(), original="pesquisa-original", enviados=enviados)
    monkeypatch.setattr(
        "app.api.pesquisas.get_settings",
        lambda: SimpleNamespace(**{**_SETTINGS.__dict__, "recuperacao_pesquisa_email_enabled": False}),
    )
    app.dependency_overrides[get_session] = _session_override(FakeSession())
    try:
        resposta = TestClient(app).post(
            "/v1/pesquisas-marca/recuperar-acesso",
            json={"email_corporativo": "contato@empresa.test", "marca": "Marca Teste"},
        )
    finally:
        _limpar()

    assert resposta.status_code == 200, resposta.text
    assert "código de acesso" in resposta.json()["detalhe"]
    assert enviados == []


def test_confirmar_codigo_correto_devolve_token_e_esgota_o_codigo(monkeypatch) -> None:
    pesquisa = _pesquisa(
        recuperacao_codigo_hash=hash_token("123456"),
        recuperacao_codigo_expira_em=datetime.now(UTC) + timedelta(minutes=10),
        recuperacao_codigo_tentativas=0,
    )
    _preparar(monkeypatch, lead=_lead(), original="pesquisa-original")
    session = FakeSession(resultados=[FakeResult(scalar=pesquisa), FakeResult(scalar=pesquisa)])
    app.dependency_overrides[get_session] = _session_override(session)
    try:
        cliente = TestClient(app)
        resposta = cliente.post(
            "/v1/pesquisas-marca/recuperar-acesso/confirmar",
            json={"email_corporativo": "contato@empresa.test", "marca": "Marca Teste", "codigo": "123456"},
        )
        # Uso único: a segunda tentativa com o mesmo código falha.
        repetida = cliente.post(
            "/v1/pesquisas-marca/recuperar-acesso/confirmar",
            json={"email_corporativo": "contato@empresa.test", "marca": "Marca Teste", "codigo": "123456"},
        )
    finally:
        _limpar()

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["id"] == "pesquisa-original"
    assert corpo["relatorio_token"]
    validar_token_relatorio(corpo["relatorio_token"], "pesquisa-original", _oid_do_token(corpo["relatorio_token"]))
    assert pesquisa.recuperacao_codigo_hash is None
    assert pesquisa.recuperacao_codigo_expira_em is None
    assert repetida.status_code == 400
    assert repetida.json()["detail"] == "Código inválido, expirado ou já utilizado"
    # Acesso a resultados de terceiros: o token emitido abre SOMENTE a
    # pesquisa recuperada, nunca a de outro par marca+e-mail.
    with pytest.raises(TokenRelatorioInvalido):
        validar_token_relatorio(corpo["relatorio_token"], "pesquisa-de-terceiro", _oid_do_token(corpo["relatorio_token"]))


def test_confirmar_codigo_de_outro_par_nao_autoriza(monkeypatch) -> None:
    # Acesso a resultados de terceiros: um código válido enviado ao par A
    # não autoriza o par B, mesmo sabendo o par marca+e-mail de B.
    pesquisa_b = _pesquisa(
        id="pesquisa-de-terceiro",
        marca="Marca de Terceiro",
        recuperacao_codigo_hash=hash_token("999999"),
        recuperacao_codigo_expira_em=datetime.now(UTC) + timedelta(minutes=10),
        recuperacao_codigo_tentativas=0,
    )
    _preparar(monkeypatch, lead=_lead(), original="pesquisa-original")
    session = FakeSession(resultados=[FakeResult(scalar=pesquisa_b)])
    app.dependency_overrides[get_session] = _session_override(session)
    try:
        cruzada = TestClient(app).post(
            "/v1/pesquisas-marca/recuperar-acesso/confirmar",
            json={"email_corporativo": "contato@empresa.test", "marca": "Marca de Terceiro", "codigo": "123456"},
        )
    finally:
        _limpar()

    assert cruzada.status_code == 400
    assert cruzada.json()["detail"] == "Código inválido, expirado ou já utilizado"
    assert pesquisa_b.recuperacao_codigo_tentativas == 1


def test_confirmar_codigo_errado_incrementa_tentativas_mesmo_erro(monkeypatch) -> None:
    pesquisa = _pesquisa(
        recuperacao_codigo_hash=hash_token("123456"),
        recuperacao_codigo_expira_em=datetime.now(UTC) + timedelta(minutes=10),
        recuperacao_codigo_tentativas=0,
    )
    _preparar(monkeypatch, lead=_lead(), original="pesquisa-original")
    session = FakeSession(resultados=[FakeResult(scalar=pesquisa), FakeResult(scalar=None)])
    app.dependency_overrides[get_session] = _session_override(session)
    try:
        cliente = TestClient(app)
        errada = cliente.post(
            "/v1/pesquisas-marca/recuperar-acesso/confirmar",
            json={"email_corporativo": "contato@empresa.test", "marca": "Marca Teste", "codigo": "654321"},
        )
        inexistente = cliente.post(
            "/v1/pesquisas-marca/recuperar-acesso/confirmar",
            json={"email_corporativo": "ninguem@empresa.test", "marca": "Marca Teste", "codigo": "654321"},
        )
    finally:
        _limpar()

    assert errada.status_code == 400
    assert errada.json() == inexistente.json()
    assert pesquisa.recuperacao_codigo_tentativas == 1


def test_confirmar_expirado_e_bloqueado_por_tentativas(monkeypatch) -> None:
    expirada = _pesquisa(
        recuperacao_codigo_hash=hash_token("123456"),
        recuperacao_codigo_expira_em=datetime.now(UTC) - timedelta(minutes=1),
        recuperacao_codigo_tentativas=0,
    )
    bloqueada = _pesquisa(
        recuperacao_codigo_hash=hash_token("123456"),
        recuperacao_codigo_expira_em=datetime.now(UTC) + timedelta(minutes=10),
        recuperacao_codigo_tentativas=5,
    )
    _preparar(monkeypatch, lead=_lead(), original="pesquisa-original")
    session = FakeSession(resultados=[FakeResult(scalar=expirada), FakeResult(scalar=bloqueada)])
    app.dependency_overrides[get_session] = _session_override(session)
    try:
        cliente = TestClient(app)
        resp_expirada = cliente.post(
            "/v1/pesquisas-marca/recuperar-acesso/confirmar",
            json={"email_corporativo": "contato@empresa.test", "marca": "Marca Teste", "codigo": "123456"},
        )
        # Mesmo o código certo não passa depois do teto de tentativas.
        resp_bloqueada = cliente.post(
            "/v1/pesquisas-marca/recuperar-acesso/confirmar",
            json={"email_corporativo": "contato@empresa.test", "marca": "Marca Teste", "codigo": "123456"},
        )
    finally:
        _limpar()

    assert resp_expirada.status_code == 400
    assert resp_bloqueada.status_code == 400
    assert resp_expirada.json() == resp_bloqueada.json()


def test_email_de_recuperacao_leva_so_o_codigo_nunca_o_link() -> None:
    from app.emailing import _mensagem_codigo_recuperacao
    from tests.test_confirmacao_pesquisa import configuracao_email

    mensagem = _mensagem_codigo_recuperacao(
        "contato@empresa.test", "Marca Teste", "123456", 15, configuracao_email(),
    )
    texto = mensagem.get_body(preferencelist=("plain",)).get_content()
    pagina = mensagem.get_body(preferencelist=("html",)).get_content()
    for conteudo in (texto, pagina):
        assert "123456" in conteudo
        # Nunca o link do resultado: só o código, digitado no site pelo dono da caixa.
        assert "/relatorios/" not in conteudo
        assert "#token=" not in conteudo
    assert "http" not in texto
    assert "Marca Teste" in mensagem["Subject"]
