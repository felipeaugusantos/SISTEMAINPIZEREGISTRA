import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.observabilidade import (
    _emails_rejeitados_24h,
    _erros_por_versao,
    _latencia_por_endpoint,
    _recursos_host,
    desligar_flag_imediatamente,
    painel_tecnico,
    religar_flag,
)
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import EventoAuditoria, EventoOperacional, FeatureFlag, VersaoSistema
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste

# --- Achado FASE6-4 da auditoria (04/09/2026): dead-letter queue visível e
# tratável no painel admin (antes só existia a contagem em status_fila). ---


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


def _override_session(session: FakeSession):
    async def _gen():
        yield session

    return _gen


def _sessao_tech(*resultados: FakeResult) -> FakeSession:
    session = FakeSession(list(resultados))
    usuario = usuario_teste(perfil="administrador")
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return session


async def _falhas_falsas(*, limite: int = 50, deslocamento: int = 0) -> list[dict]:
    return [{"id": "job-1", "tipo": "prospeccao.enriquecer_prospect", "tentativas": 3, "_bruto": "..."}]


def test_listar_fila_falhas_remove_campo_interno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.observabilidade.listar_falhas", _falhas_falsas)
    _sessao_tech()

    resposta = TestClient(app).get("/v1/admin/fila/falhas")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["itens"][0]["id"] == "job-1"
    assert "_bruto" not in corpo["itens"][0]


def test_reprocessar_fila_falha_inexistente_retorna_404(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _nao_encontrado(job_id: str) -> bool:
        return False

    monkeypatch.setattr("app.api.observabilidade.reprocessar_falha", _nao_encontrado)
    _sessao_tech()

    resposta = TestClient(app).post(
        "/v1/admin/fila/falhas/job-1/reprocessar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 404


def test_reprocessar_fila_falha_sucesso_audita_e_comita(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _encontrado(job_id: str) -> bool:
        return True

    monkeypatch.setattr("app.api.observabilidade.reprocessar_falha", _encontrado)
    session = _sessao_tech()

    resposta = TestClient(app).post(
        "/v1/admin/fila/falhas/job-1/reprocessar", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 200
    assert resposta.json() == {"reprocessado": True}
    assert session.commits == 1


def test_descartar_fila_falha_sucesso(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _encontrado(job_id: str) -> bool:
        return True

    monkeypatch.setattr("app.api.observabilidade.descartar_falha", _encontrado)
    session = _sessao_tech()

    resposta = TestClient(app).delete(
        "/v1/admin/fila/falhas/job-1", headers={"X-CSRF-Token": "csrf-teste"}
    )

    assert resposta.status_code == 200
    assert resposta.json() == {"descartado": True}
    assert session.commits == 1


# --- Achado de uma auditoria sistemática (08/09/2026, mesmo padrão do
# achado de EnvioCadenciaEmail): EventoOperacional so era exposto de forma
# agregada (contagens/medias) -- nenhum endpoint listava os eventos
# individuais para investigar um erro especifico. ---


def test_listar_eventos_operacionais_devolve_itens_formatados() -> None:
    evento = EventoOperacional(
        id=1,
        componente="api",
        operacao="POST /v1/admin/carteira",
        request_id="req-123",
        sucesso=False,
        duracao_ms=42,
        status_http=500,
        codigo_erro="ValueError",
        detalhes={"mensagem": "algo deu errado"},
        criado_em=datetime(2026, 9, 8, tzinfo=UTC),
    )
    _sessao_tech(FakeResult(scalar=1), FakeResult(itens=[evento]))

    resposta = TestClient(app).get("/v1/admin/observabilidade/eventos")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["itens"][0]["codigo_erro"] == "ValueError"
    assert corpo["itens"][0]["detalhes"] == {"mensagem": "algo deu errado"}


def test_listar_eventos_operacionais_exige_acesso_tech() -> None:
    session = FakeSession([])
    usuario = usuario_teste(perfil="operador")
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).get("/v1/admin/observabilidade/eventos")

    assert resposta.status_code == 403


# --- Fase 7: painel técnico de observabilidade e rollback. Critério de
# aceite: a equipe identifica rapidamente uma regressão e consegue limitar
# seu impacto. ---


def _versao_publicada(**kwargs: object) -> VersaoSistema:
    base = dict(
        id=1,
        versao="1.0.90",
        titulo="Fase 5",
        tipo_atualizacao="funcionalidade",
        modulos_afetados=["producao"],
        implantada_em=datetime.now(UTC) - timedelta(hours=2),
        commit_sha="a" * 40,
        migration_revision="p42d8f6r286",
        evidencias_testes=[{"resultado": "aprovado"}],
        status="publicada",
        publicado_por="tech@zeregistra.com",
    )
    base.update(kwargs)
    return VersaoSistema(**base)


def test_erros_por_versao_usa_janela_entre_implantacoes() -> None:
    v2 = _versao_publicada(id=2, versao="1.0.91", implantada_em=datetime.now(UTC) - timedelta(hours=1))
    v1 = _versao_publicada(id=1, versao="1.0.90", implantada_em=datetime.now(UTC) - timedelta(hours=5))
    session = FakeSession(
        [
            FakeResult(itens=[v2, v1]),
            FakeResult(itens=[(100, 5)]),  # janela da v2 (mais recente): 100 requisicoes, 5 erros
            FakeResult(itens=[(40, 0)]),  # janela da v1: 40 requisicoes, 0 erros
        ]
    )

    resultado = asyncio.run(_erros_por_versao(session))

    assert resultado[0]["versao"] == "1.0.91"
    assert resultado[0]["requisicoes"] == 100
    assert resultado[0]["erros"] == 5
    assert resultado[0]["taxa_erro"] == 0.05
    assert resultado[1]["versao"] == "1.0.90"
    assert resultado[1]["taxa_erro"] == 0.0  # 40 requisicoes, 0 erros


def test_erros_por_versao_sem_requisicoes_nao_divide_por_zero() -> None:
    v1 = _versao_publicada(id=1, versao="1.0.90")
    session = FakeSession([FakeResult(itens=[v1]), FakeResult(itens=[(0, 0)])])

    resultado = asyncio.run(_erros_por_versao(session))

    assert resultado[0]["requisicoes"] == 0
    assert resultado[0]["taxa_erro"] is None


def test_latencia_por_endpoint_devolve_campos_formatados() -> None:
    session = FakeSession(
        [
            FakeResult(
                itens=[
                    ("api", "GET /v1/admin/relatorios/pesado", 20, 850.333, 4200.0, 5100, 1),
                ]
            ),
        ]
    )

    resultado = asyncio.run(_latencia_por_endpoint(session))

    assert resultado[0]["componente"] == "api"
    assert resultado[0]["operacao"] == "GET /v1/admin/relatorios/pesado"
    assert resultado[0]["requisicoes"] == 20
    assert resultado[0]["duracao_media_ms"] == 850.3
    assert resultado[0]["duracao_p95_ms"] == 4200.0
    assert resultado[0]["duracao_max_ms"] == 5100
    assert resultado[0]["erros"] == 1


def test_latencia_por_endpoint_sem_dados_devolve_lista_vazia() -> None:
    session = FakeSession([FakeResult(itens=[])])

    resultado = asyncio.run(_latencia_por_endpoint(session))

    assert resultado == []


def test_recursos_host_devolve_disco_sempre_e_nao_quebra_fora_do_linux() -> None:
    resultado = _recursos_host()

    assert resultado["disco"]["total_bytes"] > 0
    assert resultado["disco"]["usado_bytes"] >= 0
    assert 0 <= resultado["disco"]["percentual_uso"] <= 100
    assert resultado["cpu"]["nucleos"] >= 1
    # cpu.carga_* e memoria.* só existem em Linux (leitura de /proc) -- em
    # qualquer outro SO (ex.: rodando os testes localmente no Windows) o
    # painel deve mostrar "—" em vez de quebrar.
    assert set(resultado["cpu"]) == {"carga_1min", "carga_5min", "carga_15min", "nucleos"}
    assert set(resultado["memoria"]) == {"total_bytes", "disponivel_bytes", "percentual_uso"}


def test_recursos_host_nao_quebra_quando_so_nao_disponibiliza_getloadavg(monkeypatch) -> None:
    monkeypatch.delattr("app.api.observabilidade.os.getloadavg", raising=False)

    resultado = _recursos_host()

    assert resultado["cpu"]["carga_1min"] is None
    assert resultado["cpu"]["carga_5min"] is None
    assert resultado["cpu"]["carga_15min"] is None


def test_emails_rejeitados_24h_agrupa_por_operacao() -> None:
    ultima_em = datetime.now(UTC) - timedelta(hours=1)
    session = FakeSession([FakeResult(itens=[("recuperacao_senha", 3, ultima_em)])])

    resultado = asyncio.run(_emails_rejeitados_24h(session))

    assert resultado[0]["operacao"] == "recuperacao_senha"
    assert resultado[0]["quantidade"] == 3
    assert resultado[0]["ultima_em"] == ultima_em


def test_emails_rejeitados_24h_sem_dados_devolve_lista_vazia() -> None:
    session = FakeSession([FakeResult(itens=[])])

    resultado = asyncio.run(_emails_rejeitados_24h(session))

    assert resultado == []


def test_desligar_flag_imediatamente_corta_para_todas_as_organizacoes() -> None:
    flag = FeatureFlag(id=1, codigo="nova-busca", nome="Nova busca", ativo=True)
    session = FakeSession([FakeResult(scalar=flag)])

    resposta = asyncio.run(desligar_flag_imediatamente("nova-busca", session, usuario_teste(perfil="tech")))

    assert resposta == {"codigo": "nova-busca", "ativo": False, "ja_estava_desligada": False}
    assert flag.ativo is False
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "FLAG_DESLIGAR"
    assert session.commits == 1


def test_desligar_flag_ja_desligada_nao_audita_de_novo() -> None:
    flag = FeatureFlag(id=1, codigo="nova-busca", nome="Nova busca", ativo=False)
    session = FakeSession([FakeResult(scalar=flag)])

    resposta = asyncio.run(desligar_flag_imediatamente("nova-busca", session, usuario_teste(perfil="tech")))

    assert resposta == {"codigo": "nova-busca", "ativo": False, "ja_estava_desligada": True}
    assert session.commits == 0
    assert session.adicionados == []


def test_desligar_flag_inexistente_devolve_404() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(desligar_flag_imediatamente("nao-existe", session, usuario_teste(perfil="tech")))

    assert erro.value.status_code == 404


def test_religar_flag_reativa_e_audita() -> None:
    flag = FeatureFlag(id=1, codigo="nova-busca", nome="Nova busca", ativo=False)
    session = FakeSession([FakeResult(scalar=flag)])

    resposta = asyncio.run(religar_flag("nova-busca", session, usuario_teste(perfil="tech")))

    assert resposta == {"codigo": "nova-busca", "ativo": True, "ja_estava_ligada": False}
    assert flag.ativo is True
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "FLAG_RELIGAR"
    assert session.commits == 1


def test_religar_flag_ja_ligada_nao_audita_de_novo() -> None:
    flag = FeatureFlag(id=1, codigo="nova-busca", nome="Nova busca", ativo=True)
    session = FakeSession([FakeResult(scalar=flag)])

    resposta = asyncio.run(religar_flag("nova-busca", session, usuario_teste(perfil="tech")))

    assert resposta == {"codigo": "nova-busca", "ativo": True, "ja_estava_ligada": True}
    assert session.commits == 0


def test_desligar_flag_exige_acesso_tech() -> None:
    session = FakeSession([])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(desligar_flag_imediatamente("nova-busca", session, usuario_teste(perfil="operador")))

    assert erro.value.status_code == 403
    assert session.executados == []


def test_painel_tecnico_com_dados_vazios_nao_quebra() -> None:
    session = FakeSession(
        [
            FakeResult(scalar=None),  # heartbeat do worker
            FakeResult(itens=[]),  # feature flags ativas
            FakeResult(itens=[]),  # organizacoes afetadas
            FakeResult(scalar=None),  # ultimo deploy
            FakeResult(scalar=None),  # migration atual
            FakeResult(itens=[]),  # versoes publicadas (erros_por_versao)
            FakeResult(itens=[]),  # latencia_por_endpoint
            FakeResult(itens=[]),  # emails_rejeitados
        ],
        objetos_get=[None],  # RpiSyncEstado
    )

    resposta = asyncio.run(painel_tecnico(session, usuario_teste(perfil="tech")))

    assert resposta["processos"]["worker"]["status"] == "indisponivel"
    assert resposta["processos"]["rpi_sync"]["status"] == "indisponivel"
    assert resposta["feature_flags_ativas"] == []
    assert resposta["organizacoes_afetadas"] == []
    assert resposta["erros_por_versao"] == []
    assert resposta["latencia_por_endpoint"] == []
    assert resposta["ultimo_deploy"] is None
    assert resposta["recursos_host"]["disco"]["total_bytes"] > 0
    assert resposta["emails_rejeitados"] == []
