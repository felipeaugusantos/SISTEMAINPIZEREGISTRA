from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import (
    AvaliacaoRiscoMarca,
    EventoAuditoria,
    ExecucaoAgenteRegistrabilidade,
    Lead,
    PesquisaMarca,
    PrevisaoRegistrabilidade,
    RotuloHistoricoMarca,
    StatusLead,
    VersaoRelatorioMarca,
)
from app.trademarks.analysis_workflow import (
    AcaoWorkflowAnalise,
    EstadoAnalise,
    proximo_estado_analise,
)
from tests.conftest import FakeResult, FakeSession, auth_override, sessao_override, usuario_teste


@pytest.fixture(autouse=True)
def _cleanup() -> None:
    yield
    app.dependency_overrides.clear()


def test_api_da_central_exige_autenticacao() -> None:
    assert TestClient(app).get("/v1/admin/analises/pesquisa-1").status_code == 401


@pytest.mark.parametrize(
    ("origem", "acao", "destino"),
    [
        ("DRAFT", "SUBMIT_REVIEW", "PENDING_REVIEW"),
        ("PENDING_REVIEW", "START_REVIEW", "IN_REVIEW"),
        ("IN_REVIEW", "REQUEST_CHANGES", "CHANGES_REQUESTED"),
        ("CHANGES_REQUESTED", "START_REVIEW", "IN_REVIEW"),
        ("IN_REVIEW", "VALIDATE", "VALIDATED"),
        ("VALIDATED", "REOPEN", "IN_REVIEW"),
    ],
)
def test_transicoes_formais_do_workflow(origem: str, acao: str, destino: str) -> None:
    assert proximo_estado_analise(origem, acao).value == destino


def test_workflow_rejeita_pulo_da_fila_para_validacao() -> None:
    with pytest.raises(ValueError, match="Transição não permitida"):
        proximo_estado_analise(
            EstadoAnalise.PENDING_REVIEW,
            AcaoWorkflowAnalise.VALIDATE,
        )


def test_api_da_central_consolida_pesquisa_e_status_do_relatorio() -> None:
    agora = datetime.now(UTC)
    lead = Lead(
        id=1,
        organizacao_id=1,
        nome="Cliente Teste",
        email="cliente@empresa.com",
        telefone="11999998888",
        empresa="Empresa",
        marca="ACME",
        origem="relatorio",
        status=StatusLead.NOVO,
    )
    lead.responsavel = None
    pesquisa = PesquisaMarca(
        id="pesquisa-1",
        organizacao_id=1,
        lead_id=1,
        marca="ACME",
        atividade="Tecnologia",
        tipo_pesquisa="completa",
        relatorio_completo_gerado_em=None,
        criado_em=agora,
    )
    versao = VersaoRelatorioMarca(
        pesquisa_id=pesquisa.id,
        numero_versao=2,
        schema_versao="relatorio-marca-4.2",
        conteudo_hash="hash",
        payload={
            "ultima_rpi": 2900,
            "total": 3,
            "limite_exibido": 3,
            "matriz_afinidade_status": "validada",
            "qualidade_base": {"status": "adequada", "avisos": []},
            "classes_atividade": [],
            "itens": [],
        },
        gerado_em=agora,
    )
    evento_workflow = EventoAuditoria(
        id=9,
        organizacao_id=1,
        actor_id=1,
        ator="revisor@teste.local",
        acao="workflow_transition",
        recurso="pesquisa:pesquisa-1",
        resource_type="analysis_workflow",
        resource_id="pesquisa-1",
        sucesso=True,
        status_http=200,
        detalhes={"action": "START_REVIEW"},
        before_state={"state": "PENDING_REVIEW"},
        after_state={"state": "IN_REVIEW"},
        criado_em=agora,
    )
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(itens=[(pesquisa, lead)]),
        FakeResult(scalar=versao),
        FakeResult(scalar=None),
        FakeResult(itens=[]),
        FakeResult(scalar=None),
        FakeResult(itens=[evento_workflow]),
    )
    app.dependency_overrides[obter_usuario_atual] = auth_override()

    response = TestClient(app).get("/v1/admin/analises/pesquisa-1")

    assert response.status_code == 200
    data = response.json()
    assert data["pesquisa"]["marca"] == "ACME"
    assert data["validacao"]["ultima_rpi"] == 2900
    assert data["validacao"]["total_ocorrencias"] == 3
    assert data["validacao"]["matriz_registrabilidade"]["versao"].startswith("manual-inpi")
    assert len(data["validacao"]["matriz_registrabilidade"]["regras"]) == 12
    assert data["risco"] is None
    assert data["relatorio_completo"]["gerado"] is False
    assert data["permissoes"]["relatorio_gerar"] is True
    assert data["workflow"]["state"] == "PENDING_REVIEW"
    assert data["workflow"]["review_required"] is True
    assert data["workflow"]["history"][0]["after"] == {"state": "IN_REVIEW"}


def test_central_executa_e_persiste_agente_com_snapshot_atual() -> None:
    agora = datetime.now(UTC)
    pesquisa = PesquisaMarca(
        id="pesquisa-agente",
        organizacao_id=1,
        marca="ACME",
        atividade="Tecnologia",
        tipo_pesquisa="completa",
        criado_em=agora,
    )
    versao = VersaoRelatorioMarca(
        pesquisa_id=pesquisa.id,
        numero_versao=1,
        schema_versao="relatorio-marca-4.2",
        conteudo_hash="hash-agente",
        payload={
            "ultima_rpi": 2900,
            "total": 0,
            "limite_exibido": 0,
            "matriz_afinidade_status": "validada",
            "qualidade_base": {"status": "adequada", "avisos": []},
            "classes_atividade": [],
            "itens": [],
        },
        gerado_em=agora,
    )
    sessao = FakeSession(
        [
            FakeResult(scalar=pesquisa),
            FakeResult(scalar=versao),
            FakeResult(scalar=None),
            FakeResult(itens=[]),
            FakeResult(scalar=None),
        ]
    )

    async def override_session():
        yield sessao

    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/analises/pesquisa-agente/executar-agente",
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["decisao"] == "dados_insuficientes"
    assert "modelo estatístico indisponível" in resposta.json()["motivos"]
    assert any(isinstance(item, ExecucaoAgenteRegistrabilidade) for item in sessao.adicionados)
    assert sessao.commits == 1


def test_leitura_supervisionada_e_persistida() -> None:
    previsao = PrevisaoRegistrabilidade(
        id=17,
        pesquisa_id="pesquisa-1",
        modelo_id=2,
        modo="sombra",
        probabilidade_deferimento=0.98,
        nivel="favoravel",
        confianca=0.78,
        cobertura_entrada=0.51,
        atributos={},
        fatores_principais=[],
    )
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=previsao),
        FakeResult(scalar=1),
    )
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).patch(
        "/v1/admin/aprendizado/previsoes/17",
        headers={"X-CSRF-Token": "csrf-teste"},
        json={
            "nivel_humano": "favoravel",
            "avaliador": "Felipe Augusto dos Santos",
            "observacoes": "Grande chance de registro",
        },
    )

    assert resposta.status_code == 200
    assert previsao.nivel_humano == "favoravel"
    assert previsao.avaliador == "Felipe Augusto dos Santos"
    assert previsao.observacoes_humanas == "Grande chance de registro"
    assert previsao.avaliado_em is not None


def test_revisao_de_rotulo_define_elegibilidade_e_registra_auditoria() -> None:
    rotulo = RotuloHistoricoMarca(
        id=33,
        processo_id=10,
        rotulo="indeferida",
        alvo_deferimento=False,
        fundamento="indeferimento_nao_especificado",
        origem="rpi_automatica",
        confianca=0.65,
        data_referencia=date(2025, 1, 10),
        tipo_decisao="merito",
        elegivel_treinamento=True,
        classificador_versao="rotulo-marcario-1.1",
        evidencias_classificacao=[],
        status_revisao="pendente",
    )

    class RotuloSession(FakeSession):
        async def get(self, *_args, **_kwargs):
            return rotulo

    sessao = RotuloSession()

    async def override_session():
        yield sessao

    usuario = usuario_teste()
    object.__setattr__(usuario, "superadmin", True)
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).patch(
        "/v1/admin/aprendizado/rotulos/33",
        headers={"X-CSRF-Token": "csrf-teste"},
        json={
            "status_revisao": "rejeitada",
            "rotulo": "indeferida",
            "fundamento": "indeferimento_nao_especificado",
            "observacoes": "Despacho sem fundamento suficiente para o treinamento.",
        },
    )

    assert resposta.status_code == 200, resposta.text
    assert rotulo.elegivel_treinamento is False
    assert rotulo.motivo_inelegibilidade == "rejeitado_por_especialista"
    assert rotulo.revisor == "admin@teste.local"
    assert any(isinstance(item, EventoAuditoria) for item in sessao.adicionados)


def test_dados_complementares_sao_persistidos_para_recalcular_matriz() -> None:
    pesquisa = PesquisaMarca(
        id="pesquisa-complementar",
        organizacao_id=1,
        marca="ACME",
        atividade="Tecnologia",
        tipo_pesquisa="completa",
    )
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=pesquisa))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).patch(
        "/v1/admin/analises/pesquisa-complementar/dados-complementares",
        headers={"X-CSRF-Token": "csrf-teste"},
        json={
            "forma_apresentacao": "nominativa",
            "significado": "Expressão de fantasia",
            "atividade_compativel": True,
            "usa_simbolo_oficial": False,
            "conteudo_potencialmente_ofensivo": False,
            "termo_generico_descritivo": False,
            "deposito_realizado": False,
        },
    )

    assert resposta.status_code == 200
    assert pesquisa.dados_complementares_registrabilidade["forma_apresentacao"] == "nominativa"
    assert pesquisa.dados_complementares_registrabilidade["atividade_compativel"] is True
    assert pesquisa.dados_complementares_registrabilidade["preenchido_por"] == usuario.email


def _objetos_workflow(
    estado: EstadoAnalise,
) -> tuple[PesquisaMarca, VersaoRelatorioMarca, AvaliacaoRiscoMarca]:
    agora = datetime.now(UTC)
    pesquisa = PesquisaMarca(
        id="pesquisa-workflow",
        organizacao_id=1,
        marca="ACME",
        tipo_pesquisa="completa",
        analysis_state=estado.value,
    )
    versao = VersaoRelatorioMarca(
        pesquisa_id=pesquisa.id,
        numero_versao=3,
        schema_versao="relatorio-marca-4.3",
        conteudo_hash="workflow-hash",
        payload={},
    )
    avaliacao = AvaliacaoRiscoMarca(
        pesquisa_id=pesquisa.id,
        versao_motor="deterministico-2.0",
        modo="sombra",
        pontuacao=42,
        nivel="moderado",
        principais_conflitos=[],
        regras_aplicadas={},
        nivel_humano="moderado",
        avaliador="Especialista",
        observacoes_humanas="Evidências conferidas no processo.",
        avaliado_em=agora,
    )
    return pesquisa, versao, avaliacao


def test_endpoint_registra_tentativa_de_pular_etapa_no_historico() -> None:
    pesquisa, versao, avaliacao = _objetos_workflow(EstadoAnalise.PENDING_REVIEW)
    sessao = FakeSession([FakeResult(scalar=pesquisa), FakeResult(scalar=versao), FakeResult(scalar=avaliacao)])

    async def override_session():
        yield sessao

    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).patch(
        "/v1/admin/analises/pesquisa-workflow/workflow",
        headers={"X-CSRF-Token": "csrf-teste"},
        json={"action": "VALIDATE", "notes": "Tentativa de validação direta."},
    )

    assert resposta.status_code == 409
    evento = next(item for item in sessao.adicionados if isinstance(item, EventoAuditoria))
    assert evento.sucesso is False
    assert evento.before_state == {"state": "PENDING_REVIEW"}
    assert evento.detalhes["action"] == "VALIDATE"
    assert sessao.commits == 1


def test_validacao_final_exige_permissao_de_risco() -> None:
    pesquisa, versao, avaliacao = _objetos_workflow(EstadoAnalise.IN_REVIEW)
    sessao = FakeSession([FakeResult(scalar=pesquisa), FakeResult(scalar=versao), FakeResult(scalar=avaliacao)])

    async def override_session():
        yield sessao

    usuario = usuario_teste("operador", {"validation.review"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).patch(
        "/v1/admin/analises/pesquisa-workflow/workflow",
        headers={"X-CSRF-Token": "csrf-teste"},
        json={"action": "VALIDATE", "notes": "Versão revisada integralmente."},
    )

    assert resposta.status_code == 403
    assert pesquisa.analysis_state == EstadoAnalise.IN_REVIEW.value
    evento = next(item for item in sessao.adicionados if isinstance(item, EventoAuditoria))
    assert evento.status_http == 403


def test_alteracao_do_workflow_exige_permissao_de_revisao_tecnica() -> None:
    usuario = usuario_teste("operador", {"leads.view"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).patch(
        "/v1/admin/analises/pesquisa-workflow/workflow",
        headers={"X-CSRF-Token": "csrf-teste"},
        json={"action": "START_REVIEW", "notes": "Início da revisão."},
    )

    assert resposta.status_code == 403


def test_validacao_vincula_responsavel_data_notas_e_versao() -> None:
    pesquisa, versao, avaliacao = _objetos_workflow(EstadoAnalise.IN_REVIEW)
    sessao = FakeSession([FakeResult(scalar=pesquisa), FakeResult(scalar=versao), FakeResult(scalar=avaliacao)])

    async def override_session():
        yield sessao

    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).patch(
        "/v1/admin/analises/pesquisa-workflow/workflow",
        headers={"X-CSRF-Token": "csrf-teste"},
        json={"action": "VALIDATE", "notes": "Versão e evidências revisadas."},
    )

    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["state"] == "VALIDATED"
    assert pesquisa.validated_by == usuario.email
    assert pesquisa.validated_at is not None
    assert versao.validated_by == usuario.email
    assert versao.validated_at == pesquisa.validated_at
    assert versao.validation_notes == "Versão e evidências revisadas."
    evento = next(item for item in sessao.adicionados if isinstance(item, EventoAuditoria))
    assert evento.before_state == {"state": "IN_REVIEW"}
    assert evento.after_state == {"state": "VALIDATED"}
