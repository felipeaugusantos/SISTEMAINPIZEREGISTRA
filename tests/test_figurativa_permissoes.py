import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from starlette.requests import Request

from app.api.figurativa import (
    BenchmarkEntrada,
    ValidacaoHumanaEntrada,
    anterioridades_figurativas,
    benchmark_figurativo,
    validar_resultado_figurativo,
)
from app.api.visual import validar_imagem
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import EventoAuditoria
from app.permissions import permissoes_do_perfil
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste

# --- Achado médio da Fase 11 (auditoria da busca figurativa, 22/09/2026):
# POST /v1/admin/figurativa/benchmark (decide se um modelo pode ser
# publicado) e POST /v1/admin/figurativa/validacoes-humanas (registra uma
# decisão legal/técnica sobre anterioridade figurativa) exigiam só
# leads.view, a mesma permissão de qualquer perfil comercial -- alinhado
# aos módulos irmãos (Validação/Risco), que exigem validation.review/
# risk.review para escrever. ---


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/figurativa/benchmark",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


async def _sessao() -> FakeSession:
    yield FakeSession()


def test_comercial_nao_pode_rodar_benchmark_de_modelo() -> None:
    usuario = usuario_teste("comercial", set(permissoes_do_perfil("comercial")))
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/figurativa/benchmark",
            json={"casos": [{"relevantes": ["900000001"], "retornados": ["900000001"]}]},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 403


def test_comercial_nao_pode_registrar_validacao_humana() -> None:
    usuario = usuario_teste("comercial", set(permissoes_do_perfil("comercial")))
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/figurativa/validacoes-humanas",
            json={"processo": "900000001", "decisao": "confirmado", "observacao": "Conferido manualmente."},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 403


def test_administrador_ainda_pode_rodar_benchmark_e_validar() -> None:
    """A correção acima não pode travar quem legitimamente tem acesso --
    administrador continua com todas as permissões."""
    usuario = usuario_teste("administrador")
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/figurativa/benchmark",
            json={"casos": [{"relevantes": ["900000001"], "retornados": ["900000001"]}]},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200


# --- Achado baixo da Fase 11: caso de benchmark malformado estourava 500
# cru (TypeError não tratado) em vez de uma mensagem clara. ---


def test_benchmark_com_relevantes_nao_hasheavel_devolve_422() -> None:
    dados = BenchmarkEntrada(casos=[{"relevantes": [{"id": 1}], "retornados": ["900000001"]}])
    usuario = usuario_teste()
    session = FakeSession()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(benchmark_figurativo(dados, _request(), session, usuario))
    assert exc_info.value.status_code == 422


# --- Achado baixo da Fase 14.1 (auditoria fina da busca figurativa,
# 23/09/2026): a rota mais usada no dia a dia (busca real por Viena) e o
# upload de imagem não deixavam nenhum rastro de quem pesquisou/validou o
# quê -- só as rotas administrativas raramente usadas (benchmark,
# validações humanas) registravam EventoAuditoria. ---


def test_anterioridades_figurativas_registra_evento_auditoria() -> None:
    usuario = usuario_teste()
    linha = ("900000001", "Marca Exemplo", "figurativa", None, ["27.5.1"], 1)
    # Achado da Fase 14.4 (auditoria fina, 23/09/2026): "total" agora é a
    # contagem real (contar_anterioridades_viena), sem o LIMIT aplicado --
    # aqui simula 3 anterioridades na base, mas só 1 retornada (limite).
    session = FakeSession([FakeResult(itens=[linha]), FakeResult(scalar=3)])

    resultado = asyncio.run(anterioridades_figurativas(_request(), session, usuario, codigos="27.5.1"))

    assert resultado["total"] == 3
    assert resultado["retornados"] == 1
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "buscar"
    assert evento.recurso == "busca_figurativa"
    assert evento.detalhes == {"codigos": ["27.5.1"], "apresentacao": None, "total": 3, "retornados": 1}
    assert session.commits == 1


class _ArquivoFalso:
    def __init__(self, conteudo: bytes, *, size: int | None = None) -> None:
        self.filename = "logo.png"
        self.content_type = "image/png"
        self.size = len(conteudo) if size is None else size
        self._conteudo = conteudo

    async def read(self) -> bytes:
        return self._conteudo


def _imagem_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), color=(10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_validar_imagem_registra_evento_auditoria() -> None:
    usuario = usuario_teste()
    session = FakeSession()
    arquivo = _ArquivoFalso(_imagem_png())

    resultado = asyncio.run(validar_imagem(arquivo, _request(), session, usuario))

    assert resultado["assinatura_visual"]
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "validar_imagem"
    assert evento.recurso == "busca_figurativa"
    assert evento.detalhes["arquivo"] == "logo.png"
    # Achado P2 do Codex no PR #126: nome/tipo/tamanho não identificam a
    # imagem de fato -- o rastro precisa de um identificador estável do
    # conteúdo (hash), não só dos metadados informados pelo cliente.
    assert evento.resource_id == evento.detalhes["hash_conteudo"]
    assert evento.detalhes["assinatura_visual"] == resultado["assinatura_visual"]
    assert session.commits == 1


def test_validar_imagem_nao_devolve_score_decorativo_e_expoe_ocr_real() -> None:
    # Achado da Fase 14.2 (auditoria fina da busca figurativa, 23/09/2026):
    # o "score visual" antigo sempre passava similaridade=1.0 fixa (fator
    # de maior peso), sem nenhuma comparação real com acervo -- qualquer
    # upload válido saía com nota alta que não significava nada. E a
    # resposta de OCR era descartada e trocada por um texto estático de
    # "pendente" mesmo quando o OCR real já tinha rodado.
    usuario = usuario_teste()
    session = FakeSession()
    arquivo = _ArquivoFalso(_imagem_png())

    resultado = asyncio.run(validar_imagem(arquivo, _request(), session, usuario))

    assert "score_combinado" not in resultado
    assert resultado["score_visual"]["disponivel"] is False
    assert resultado["ocr"].get("motivo") != "OCR será executado na etapa de processamento textual."


def test_validar_imagem_invalida_nao_registra_evento_auditoria() -> None:
    usuario = usuario_teste()
    session = FakeSession()
    arquivo = _ArquivoFalso(b"nao e uma imagem")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(validar_imagem(arquivo, _request(), session, usuario))

    assert exc_info.value.status_code == 422
    assert session.adicionados == []
    assert session.commits == 0


def test_validar_imagem_rejeita_pelo_tamanho_declarado_sem_ler_o_arquivo() -> None:
    # Achado da Fase 14.3 (auditoria fina da busca figurativa, 23/09/2026):
    # o arquivo inteiro era lido em memória antes de checar o tamanho --
    # agora arquivo.size (já conhecido pelo Starlette durante o parse do
    # multipart) é checado primeiro, sem precisar ler o conteúdo inteiro.
    usuario = usuario_teste()
    session = FakeSession()
    arquivo = _ArquivoFalso(b"conteudo pequeno", size=20 * 1024 * 1024)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(validar_imagem(arquivo, _request(), session, usuario, limite_mb=5))

    assert exc_info.value.status_code == 413


def test_validar_imagem_tem_rate_limit_por_operador() -> None:
    # Achado da Fase 14.3: upload + processamento de imagem (PIL) é a
    # operação mais cara em CPU/IO do módulo, mas não tinha nenhum
    # limitador de taxa -- diferente de todo outro endpoint de upload do
    # sistema.
    usuario = usuario_teste()
    for _ in range(20):
        session = FakeSession()
        asyncio.run(validar_imagem(_ArquivoFalso(_imagem_png()), _request(), session, usuario))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(validar_imagem(_ArquivoFalso(_imagem_png()), _request(), FakeSession(), usuario))
    assert exc_info.value.status_code == 429


def test_validar_imagem_registra_log_do_erro_real(monkeypatch: pytest.MonkeyPatch) -> None:
    # Achado da Fase 14.3: "except Exception" cru mascarava qualquer erro
    # interno (não só imagem inválida) como "arquivo inválido" 422, sem
    # nenhum log -- passa a registrar o erro real pra observabilidade.
    import app.api.visual as visual_module

    chamadas: list[str] = []
    monkeypatch.setattr(visual_module.logger, "exception", lambda msg: chamadas.append(msg))
    usuario = usuario_teste()
    session = FakeSession()
    arquivo = _ArquivoFalso(b"nao e uma imagem")

    with pytest.raises(HTTPException):
        asyncio.run(validar_imagem(arquivo, _request(), session, usuario))

    assert chamadas


def test_validar_resultado_figurativo_rejeita_processo_inexistente() -> None:
    # Achado da Fase 14.5 (auditoria fina da busca figurativa, 23/09/2026):
    # aceitava qualquer string de 1-40 caracteres como "processo" sem checar
    # se corresponde a um Processo real -- uma decisão jurídica podia ficar
    # associada a um número de processo inexistente ou digitado errado.
    usuario = usuario_teste()
    session = FakeSession([FakeResult(scalar=None)])
    dados = ValidacaoHumanaEntrada(processo="900000001", decisao="confirmado", observacao="Conferido manualmente.")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(validar_resultado_figurativo(dados, _request(), session, usuario))

    assert exc_info.value.status_code == 404
    assert session.adicionados == []
    assert session.commits == 0


def test_validar_resultado_figurativo_aceita_processo_existente() -> None:
    usuario = usuario_teste()
    session = FakeSession([FakeResult(scalar=42)])
    dados = ValidacaoHumanaEntrada(processo="900000001", decisao="confirmado", observacao="Conferido manualmente.")

    resultado = asyncio.run(validar_resultado_figurativo(dados, _request(), session, usuario))

    assert resultado["registrado"] is True
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.resource_id == "900000001"
    assert session.commits == 1


def test_validar_resultado_figurativo_normaliza_o_numero_antes_de_checar() -> None:
    # Achado P2 do Codex no PR #130: comparar direto com Processo.numero
    # rejeitava números válidos só por diferença de formatação (espaços,
    # pontuação) -- normaliza igual à consulta pública de processos.
    usuario = usuario_teste()
    session = FakeSession([FakeResult(scalar=42)])
    dados = ValidacaoHumanaEntrada(
        processo=" 900.000.001 ", decisao="confirmado", observacao="Conferido manualmente."
    )

    resultado = asyncio.run(validar_resultado_figurativo(dados, _request(), session, usuario))

    assert resultado["registrado"] is True


# --- Achado da Fase 14.5 (auditoria fina da busca figurativa, 23/09/2026):
# zero teste de integração HTTP nas duas rotas centrais de uso diário
# (busca por Viena e upload de imagem) -- só havia teste unitário das
# funções de domínio, chamadas diretamente sem passar pela camada HTTP. ---


def test_get_anterioridades_via_http_exige_autenticacao() -> None:
    resposta = TestClient(app).get("/v1/admin/figurativa/anterioridades", params={"codigos": "27.5.1"})
    assert resposta.status_code == 401


def test_post_validar_imagem_via_http_exige_autenticacao() -> None:
    resposta = TestClient(app).post(
        "/v1/admin/figurativa/validar-imagem", files={"arquivo": ("logo.png", _imagem_png(), "image/png")}
    )
    assert resposta.status_code == 401


async def _sessao_com_um_resultado() -> FakeSession:
    linha = ("900000001", "Marca Exemplo", "figurativa", None, ["27.5.1"], 1)
    yield FakeSession([FakeResult(itens=[linha]), FakeResult(scalar=1)])


def test_get_anterioridades_via_http_com_usuario_autenticado() -> None:
    usuario = usuario_teste()
    app.dependency_overrides[get_session] = _sessao_com_um_resultado
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get("/v1/admin/figurativa/anterioridades", params={"codigos": "27.5.1"})
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    assert resposta.json()["total"] == 1
