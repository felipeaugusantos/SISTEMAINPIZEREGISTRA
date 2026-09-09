from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import UsuarioAutenticado, hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import (
    BloqueioRetencao,
    EventoAuditoria,
    Lead,
    Organizacao,
    PoliticaRetencao,
    SimulacaoRetencao,
    StatusLead,
)
from app.retencao import motivos_bloqueio_lead, simular_retencao_leads
from tests.conftest import FakeResult, FakeSession, auth_override


@pytest.fixture(autouse=True)
def _limpar_overrides():
    yield
    app.dependency_overrides.clear()


def _usuario(organizacao_id: int = 1) -> UsuarioAutenticado:
    return UsuarioAutenticado(
        id=7,
        nome="Responsável",
        usuario="responsavel",
        email="responsavel@teste.local",
        perfil="administrador",
        permissoes=frozenset(),
        alterar_senha=False,
        sessao_id=1,
        csrf_hash=hash_token("csrf-teste"),
        organizacao_id=organizacao_id,
    )


def _request(session: FakeSession, method: str, path: str, json: dict | None = None, org_id: int = 1):
    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[obter_usuario_atual] = auth_override(_usuario(org_id))
    return TestClient(app).request(method, path, headers={"X-CSRF-Token": "csrf-teste"}, json=json)


@pytest.mark.asyncio
async def test_simulacao_retorna_impacto_sem_escrever_ou_descartar() -> None:
    antigo = datetime.now(UTC) - timedelta(days=900)
    session = FakeSession(
        [
            FakeResult(itens=[(3, antigo)]),
            FakeResult(itens=[(StatusLead.DESCARTADO, 2), (StatusLead.NOVO, 1)]),
            FakeResult(scalar=1),
            FakeResult(scalar=1),
            FakeResult(scalar=1),
            FakeResult(scalar=1),
            FakeResult(scalar=1),
            FakeResult(scalar=1),
            FakeResult(itens=[11, 12, 13]),
        ]
    )
    org = Organizacao(id=1, retencao_dados_dias=730)

    resultado = await simular_retencao_leads(session, org, prazo_dias=365)

    assert resultado["modo"] == "simulacao"
    assert resultado["executou_descarte"] is False
    assert resultado["total_afetado"] == 3
    assert resultado["registro_mais_antigo"] == antigo
    assert resultado["categorias"] == {"lead": 3}
    assert resultado["por_status"] == {"descartado": 2, "novo": 1}
    assert resultado["bloqueios"]["processo_ativo"] == 1
    assert resultado["elegiveis_revisao_humana"] == 1
    assert session.adicionados == []
    assert session.deletados == []
    assert session.commits == 0
    assert "leads.anonimizado_em IS NULL" in str(session.executados[0])
    assert "leads.organizacao_id" in str(session.executados[0])


@pytest.mark.asyncio
async def test_simulacao_conta_mais_de_200_e_pagina_apenas_a_amostra() -> None:
    ids = list(range(1, 201))
    session = FakeSession(
        [
            FakeResult(itens=[(350, datetime.now(UTC) - timedelta(days=1000))]),
            FakeResult(itens=[(StatusLead.DESCARTADO, 350)]),
            *[FakeResult(scalar=0) for _ in range(5)],
            FakeResult(scalar=350),
            FakeResult(itens=ids),
        ]
    )
    org = Organizacao(id=1, retencao_dados_dias=730)

    resultado = await simular_retencao_leads(session, org, prazo_dias=365)

    assert resultado["total_afetado"] == 350
    assert len(resultado["amostra_ids"]) == 200
    assert resultado["amostra_limitada"] is True
    assert resultado["paginacao"] == {"limite": 200, "deslocamento": 0, "proximo_deslocamento": 200}
    consulta_amostra = str(session.executados[-1])
    assert "LIMIT" in consulta_amostra


def test_simulacao_rejeita_limite_maior_que_200() -> None:
    session = FakeSession(objetos_get=[Organizacao(id=1, retencao_dados_dias=730)])

    resposta = _request(
        session,
        "POST",
        "/v1/admin/confiabilidade/retencao/simular",
        {"prazo_dias": 365, "limite": 201, "deslocamento": 0},
    )

    assert resposta.status_code == 422
    assert session.executados == []


def test_simulacao_valida_fica_registrada_sem_descartar(monkeypatch: pytest.MonkeyPatch) -> None:
    async def simular(*_args, **_kwargs):
        return {
            "modo": "simulacao",
            "executou_descarte": False,
            "categoria": "lead",
            "prazo_dias": 365,
            "data_corte": datetime.now(UTC) - timedelta(days=365),
            "total_afetado": 2,
            "registro_mais_antigo": datetime.now(UTC) - timedelta(days=800),
            "categorias": {"lead": 2},
            "por_status": {"descartado": 2},
            "bloqueios": {},
            "elegiveis_revisao_humana": 2,
            "amostra_ids": [1, 2],
            "amostra_limitada": False,
            "paginacao": {"limite": 200, "deslocamento": 0, "proximo_deslocamento": None},
        }

    monkeypatch.setattr("app.api.confiabilidade.simular_retencao_leads", simular)
    session = FakeSession(objetos_get=[Organizacao(id=1, retencao_dados_dias=730)])

    resposta = _request(
        session,
        "POST",
        "/v1/admin/confiabilidade/retencao/simular",
        {"prazo_dias": 365},
    )

    assert resposta.status_code == 200
    assert resposta.json()["simulacao_id"] == 1
    simulacao = next(item for item in session.adicionados if isinstance(item, SimulacaoRetencao))
    assert simulacao.resultado["executou_descarte"] is False
    assert simulacao.organizacao_id == 1 and simulacao.criado_por_id == 7
    assert session.deletados == []
    assert session.commits == 1


@pytest.mark.asyncio
async def test_bloqueios_reais_cobrem_atendimento_processo_contrato_documento_e_legal_hold() -> None:
    lead = Lead(
        id=8,
        organizacao_id=3,
        nome="Lead",
        email="lead@teste.local",
        telefone="1",
        marca="Marca",
        status=StatusLead.NOVO,
    )
    session = FakeSession([FakeResult(scalar=1) for _ in range(4)])

    motivos = await motivos_bloqueio_lead(session, lead)

    assert motivos == [
        "atendimento_ativo",
        "processo_ativo",
        "contrato_ativo",
        "documento_obrigacao_juridica",
        "legal_hold",
    ]
    consultas = "\n".join(str(item) for item in session.executados)
    assert "processos_monitorados.status" in consultas
    assert "contratacoes_servicos.status" in consultas
    assert "documentos_lead.obrigatorio" in consultas
    assert "bloqueios_retencao.ativo" in consultas


@pytest.mark.parametrize(
    "payload,mensagem",
    [
        ({"retencao_dados_dias": 365}, "justificativa"),
        (
            {
                "retencao_dados_dias": 365,
                "retencao_justificativa": "Redução fundamentada para dados comerciais encerrados.",
                "retencao_finalidade": "Encerrar a guarda de contatos comerciais inativos.",
                "retencao_base_legal": "Política interna validada pelo encarregado de dados.",
                "retencao_vigencia_em": datetime.now(UTC).isoformat(),
            },
            "confirmação explícita",
        ),
    ],
)
def test_reducao_exige_governanca_completa(payload: dict, mensagem: str) -> None:
    org = Organizacao(id=1, retencao_dados_dias=730, branding={})
    session = FakeSession([FakeResult(scalar=None)], objetos_get=[org])

    resposta = _request(session, "PATCH", "/v1/admin/confiabilidade/configuracao", payload)

    assert resposta.status_code == 422
    assert mensagem in resposta.json()["detail"]
    assert org.retencao_dados_dias == 730
    assert session.commits == 0


def test_reducao_confirmada_cria_versao_e_auditoria() -> None:
    org = Organizacao(id=1, retencao_dados_dias=730, branding={})
    simulacao = SimulacaoRetencao(
        id=10,
        organizacao_id=1,
        categoria="lead",
        prazo_dias=365,
        resultado={},
        criado_por_id=7,
        criado_por="responsavel@teste.local",
        criado_em=datetime.now(UTC),
    )
    session = FakeSession([FakeResult(scalar=None), FakeResult(scalar=simulacao)], objetos_get=[org])
    payload = {
        "retencao_dados_dias": 365,
        "retencao_justificativa": "Redução fundamentada para dados comerciais encerrados.",
        "retencao_finalidade": "Encerrar a guarda de contatos comerciais inativos.",
        "retencao_base_legal": "Política interna validada pelo encarregado de dados.",
        "retencao_vigencia_em": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "confirmar_reducao_retencao": True,
        "retencao_simulacao_id": 10,
    }

    resposta = _request(session, "PATCH", "/v1/admin/confiabilidade/configuracao", payload)

    assert resposta.status_code == 200, resposta.text
    politica = next(item for item in session.adicionados if isinstance(item, PoliticaRetencao))
    auditoria = next(item for item in session.adicionados if isinstance(item, EventoAuditoria))
    assert politica.criado_por == "responsavel@teste.local"
    assert politica.reducao is True and politica.reducao_confirmada is True
    assert politica.marco_inicial == "lead.criado_em"
    assert politica.finalidade and politica.base_legal and politica.excecoes
    assert politica.ativo is True
    assert auditoria.acao == "ALTERAR_POLITICA_RETENCAO"
    assert org.retencao_dados_dias == 365
    assert simulacao.usada_em is not None


def test_politica_futura_nao_altera_fallback_antes_da_vigencia() -> None:
    org = Organizacao(id=1, retencao_dados_dias=730, branding={})
    simulacao = SimulacaoRetencao(
        id=11,
        organizacao_id=1,
        categoria="lead",
        prazo_dias=900,
        resultado={},
        criado_por_id=7,
        criado_por="responsavel@teste.local",
        criado_em=datetime.now(UTC),
    )
    session = FakeSession([FakeResult(scalar=None), FakeResult(scalar=simulacao)], objetos_get=[org])
    payload = {
        "retencao_dados_dias": 900,
        "retencao_justificativa": "Ampliação programada conforme obrigação contratual vigente.",
        "retencao_finalidade": "Preservar dados necessários à execução contratual em andamento.",
        "retencao_base_legal": "Execução de contrato e cumprimento de obrigação legal aplicável.",
        "retencao_vigencia_em": (datetime.now(UTC) + timedelta(days=10)).isoformat(),
        "retencao_simulacao_id": 11,
    }

    resposta = _request(session, "PATCH", "/v1/admin/confiabilidade/configuracao", payload)

    assert resposta.status_code == 200
    assert org.retencao_dados_dias == 730
    assert any(isinstance(item, PoliticaRetencao) and item.prazo_dias == 900 for item in session.adicionados)


def test_alteracao_rejeita_simulacao_ausente() -> None:
    org = Organizacao(id=1, retencao_dados_dias=730, branding={})
    session = FakeSession([FakeResult(scalar=None)], objetos_get=[org])
    payload = {
        "retencao_dados_dias": 900,
        "retencao_justificativa": "Ampliação programada conforme obrigação contratual vigente.",
        "retencao_finalidade": "Preservar dados necessários à execução contratual em andamento.",
        "retencao_base_legal": "Execução de contrato e cumprimento de obrigação legal aplicável.",
        "retencao_vigencia_em": datetime.now(UTC).isoformat(),
    }

    resposta = _request(session, "PATCH", "/v1/admin/confiabilidade/configuracao", payload)

    assert resposta.status_code == 422
    assert "simulação" in resposta.json()["detail"].lower()
    assert session.commits == 0


def test_legal_hold_isolado_por_organizacao_e_auditado() -> None:
    lead = Lead(id=44, organizacao_id=1, nome="Lead", email="lead@test.local", telefone="1", marca="Marca")
    session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=None)])

    resposta = _request(
        session,
        "POST",
        "/v1/admin/confiabilidade/retencao/leads/44/legal-hold",
        {"motivo": "Processo administrativo em andamento e sujeito a preservação."},
    )

    assert resposta.status_code == 201
    hold = next(item for item in session.adicionados if isinstance(item, BloqueioRetencao))
    assert hold.organizacao_id == 1 and hold.recurso_id == 44 and hold.ativo is True
    assert any(isinstance(item, EventoAuditoria) and item.acao == "ATIVAR_LEGAL_HOLD" for item in session.adicionados)
    consulta = str(session.executados[0])
    assert "leads.organizacao_id" in consulta


def test_legal_hold_nao_encontra_lead_de_outro_tenant() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    resposta = _request(
        session,
        "POST",
        "/v1/admin/confiabilidade/retencao/leads/44/legal-hold",
        {"motivo": "Tentativa controlada de acessar recurso pertencente a outro tenant."},
        org_id=2,
    )

    assert resposta.status_code == 404
    assert session.adicionados == []
