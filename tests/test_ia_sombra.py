import asyncio
from datetime import UTC, datetime, timedelta

from app.ia_sombra import (
    _analisar_explicacao,
    _analisar_qualificacao,
    _analisar_resposta,
    enfileirar_qualificacao_ia_se_ativa,
    gerar_explicacao_risco,
    gerar_explicacoes_risco_pendentes,
    gerar_qualificacao_lead,
    gerar_sugestao_lead,
    gerar_sugestoes_ia_pendentes,
    montar_contexto_avaliacao_risco,
    montar_contexto_captacao_lead,
    montar_contexto_lead,
)
from app.models import (
    AvaliacaoRiscoMarca,
    ContatoLead,
    ExplicacaoAnaliseMarca,
    Lead,
    PoliticaCRM,
    QualificacaoIALead,
    RespostaEmailLead,
    StatusLead,
    SugestaoIALead,
)
from app.settings import get_settings
from tests.conftest import FakeResult, FakeSession

# --- "IA em sombra" (fora do roteiro da auditoria completa do CRM): resumo
# de histórico + sugestão de próxima ação por lead, gerados em segundo
# plano por um modelo local (Ollama). Nunca escreve mensagem para o
# cliente, nunca envia nada sozinha -- toda sugestão exige revisão humana
# explícita. Desligada por padrão em dois níveis: settings.ia_sombra_enabled
# (kill-switch global) e PoliticaCRM.ia_sombra_ativa (opt-in por
# organização). ---


def _lead(**kwargs: object) -> Lead:
    base = dict(
        id=5,
        organizacao_id=1,
        nome="Cliente Teste",
        empresa="Empresa X",
        origem="site",
        fase="qualificado",
        status=StatusLead.NOVO,
        criado_em=datetime(2026, 8, 1, tzinfo=UTC),
        atualizado_em=datetime(2026, 9, 1, tzinfo=UTC),
    )
    base.update(kwargs)
    return Lead(**base)


def test_analisar_resposta_extrai_resumo_e_sugestao_bem_formados() -> None:
    resumo, acao = _analisar_resposta("RESUMO: Cliente ainda não retornou.\nPROXIMA_ACAO: Ligar amanhã.")
    assert resumo == "Cliente ainda não retornou."
    assert acao == "Ligar amanhã."


def test_analisar_resposta_sem_marcadores_cai_tudo_no_resumo() -> None:
    resumo, acao = _analisar_resposta("O modelo respondeu qualquer coisa fora do formato pedido.")
    assert resumo == "O modelo respondeu qualquer coisa fora do formato pedido."
    assert acao == ""


def test_montar_contexto_lead_inclui_dados_basicos_e_historico() -> None:
    lead = _lead()
    contato = ContatoLead(canal="telefone", resultado="sem retorno", observacao="tentou 2x", criado_em=datetime.now(UTC))
    resposta = RespostaEmailLead(corpo="Obrigado, vou pensar.", recebido_em=datetime.now(UTC))

    contexto = montar_contexto_lead(lead, [contato], [resposta], None)

    assert "Cliente Teste" in contexto
    assert "Empresa X" in contexto
    assert "telefone" in contexto
    assert "Obrigado, vou pensar." in contexto


async def _chamada_fake(_prompt: str) -> str:
    return "RESUMO: Cliente ainda não retornou contato inicial.\nPROXIMA_ACAO: Ligar para o cliente."


async def _chamada_com_falha(_prompt: str) -> str:
    raise TimeoutError("modelo não respondeu a tempo")


def test_gerar_sugestao_lead_usa_chamada_injetada_e_nao_comita() -> None:
    lead = _lead()
    session = FakeSession([FakeResult(itens=[]), FakeResult(itens=[]), FakeResult(scalar=None)])

    sugestao = asyncio.run(gerar_sugestao_lead(session, lead, chamar_ia=_chamada_fake))

    assert sugestao.resumo == "Cliente ainda não retornou contato inicial."
    assert sugestao.sugestao_proxima_acao == "Ligar para o cliente."
    assert sugestao.erro is None
    assert sugestao in session.adicionados
    assert session.commits == 0  # persistência é responsabilidade do chamador (o job de manutenção)


def test_gerar_sugestao_lead_erro_na_chamada_vira_campo_erro_sem_propagar() -> None:
    lead = _lead()
    session = FakeSession([FakeResult(itens=[]), FakeResult(itens=[]), FakeResult(scalar=None)])

    sugestao = asyncio.run(gerar_sugestao_lead(session, lead, chamar_ia=_chamada_com_falha))

    assert sugestao.erro is not None
    assert "TimeoutError" in sugestao.erro
    assert sugestao.resumo == ""


def test_gerar_sugestoes_ia_pendentes_desligado_por_padrao_devolve_zero() -> None:
    settings = get_settings()
    assert settings.ia_sombra_enabled is False  # comportamento padrão, sem precisar mexer na flag

    session = FakeSession([])
    resultado = asyncio.run(gerar_sugestoes_ia_pendentes(session))

    assert resultado == 0
    assert session.executados == []  # nem chega a consultar o banco


def test_gerar_sugestoes_ia_pendentes_sem_organizacao_ativa_devolve_zero() -> None:
    import app.ia_sombra as modulo

    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True
    original_horario = modulo.dentro_do_horario_comercial
    modulo.dentro_do_horario_comercial = lambda *_a, **_k: False
    try:
        session = FakeSession([FakeResult(itens=[])])
        resultado = asyncio.run(gerar_sugestoes_ia_pendentes(session))
    finally:
        settings.ia_sombra_enabled = original
        modulo.dentro_do_horario_comercial = original_horario

    assert resultado == 0


def test_gerar_sugestoes_ia_pendentes_pula_lead_sem_atividade_nova_e_gera_para_lead_com_atividade_nova() -> None:
    import app.ia_sombra as modulo

    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True
    # Job só roda fora do horário comercial (item pedido pelo usuário após
    # avaliar os riscos de deixar a IA em sombra ativa) -- força "fora do
    # horário" pra não depender da hora em que a suíte é executada.
    original_horario = modulo.dentro_do_horario_comercial
    modulo.dentro_do_horario_comercial = lambda *_a, **_k: False

    ultima_sugestao_em = datetime(2026, 9, 1, tzinfo=UTC)
    lead_sem_novidade = _lead(id=1, atualizado_em=ultima_sugestao_em - timedelta(days=5))
    lead_com_novidade = _lead(id=2, atualizado_em=ultima_sugestao_em + timedelta(days=1))

    try:
        session = FakeSession(
            [
                FakeResult(itens=[1]),  # organizacoes_ativas
                FakeResult(itens=[(1, ultima_sugestao_em), (2, ultima_sugestao_em)]),  # ultima_sugestao_por_lead
                FakeResult(itens=[]),  # ultimo_contato_por_lead
                FakeResult(itens=[]),  # ultima_resposta_por_lead
                FakeResult(itens=[lead_sem_novidade, lead_com_novidade]),  # leads_abertos
                FakeResult(itens=[]),  # gerar_sugestao_lead(lead_com_novidade): contatos
                FakeResult(itens=[]),  # respostas
                FakeResult(scalar=None),  # pesquisa
            ]
        )
        resultado = asyncio.run(gerar_sugestoes_ia_pendentes(session, chamar_ia=_chamada_fake))
    finally:
        settings.ia_sombra_enabled = original
        modulo.dentro_do_horario_comercial = original_horario

    assert resultado == 1
    sugestoes_criadas = [item for item in session.adicionados if isinstance(item, SugestaoIALead)]
    assert len(sugestoes_criadas) == 1
    assert sugestoes_criadas[0].lead_id == 2


# --- Frente A: explicação em linguagem simples do risco já calculado
# (análise de marca). NUNCA recalcula nem substitui o resultado técnico. ---


def _avaliacao(**kwargs: object) -> AvaliacaoRiscoMarca:
    base = dict(
        id=1,
        pesquisa_id="pesquisa-1",
        versao_motor="1.0",
        pontuacao=72,
        nivel="alto",
        principais_conflitos=[{"titulo": "MARCA CONCORRENTE", "pontuacao": 72, "nivel": "alto"}],
        regras_aplicadas={},
        calculado_em=datetime(2026, 9, 7, tzinfo=UTC),
    )
    base.update(kwargs)
    return AvaliacaoRiscoMarca(**base)


def test_analisar_explicacao_extrai_texto_apos_marcador() -> None:
    assert _analisar_explicacao("EXPLICACAO: o risco é alto por causa de X.") == "o risco é alto por causa de X."


def test_analisar_explicacao_sem_marcador_usa_texto_inteiro() -> None:
    assert _analisar_explicacao("resposta fora do formato") == "resposta fora do formato"


def test_montar_contexto_avaliacao_risco_inclui_pontuacao_nivel_e_conflitos() -> None:
    contexto = montar_contexto_avaliacao_risco(_avaliacao())
    assert "72/100" in contexto
    assert "alto" in contexto
    assert "MARCA CONCORRENTE" in contexto


async def _chamada_explicacao_fake(_prompt: str) -> str:
    return "EXPLICACAO: O risco foi classificado como alto porque há um conflito direto com MARCA CONCORRENTE."


def test_gerar_explicacao_risco_usa_chamada_injetada_e_nao_comita() -> None:
    avaliacao = _avaliacao()
    session = FakeSession([])

    explicacao = asyncio.run(gerar_explicacao_risco(session, avaliacao, 1, chamar_ia=_chamada_explicacao_fake))

    assert "MARCA CONCORRENTE" in explicacao.explicacao
    assert explicacao.erro is None
    assert explicacao.organizacao_id == 1
    assert explicacao.avaliacao_risco_id == avaliacao.id
    assert explicacao in session.adicionados
    assert session.commits == 0


def test_gerar_explicacao_risco_erro_na_chamada_vira_campo_erro() -> None:
    avaliacao = _avaliacao()
    session = FakeSession([])

    explicacao = asyncio.run(gerar_explicacao_risco(session, avaliacao, 1, chamar_ia=_chamada_com_falha))

    assert explicacao.erro is not None
    assert "TimeoutError" in explicacao.erro


def test_gerar_explicacoes_risco_pendentes_desligado_devolve_zero() -> None:
    session = FakeSession([])
    resultado = asyncio.run(gerar_explicacoes_risco_pendentes(session))
    assert resultado == 0
    assert session.executados == []


def test_gerar_explicacoes_risco_pendentes_pula_avaliacao_sem_mudanca() -> None:
    import app.ia_sombra as modulo

    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True
    original_horario = modulo.dentro_do_horario_comercial
    modulo.dentro_do_horario_comercial = lambda *_a, **_k: False

    calculado_em = datetime(2026, 9, 1, tzinfo=UTC)
    avaliacao_sem_mudanca = _avaliacao(id=1, calculado_em=calculado_em)
    avaliacao_recalculada = _avaliacao(id=2, calculado_em=calculado_em + timedelta(hours=1))

    try:
        session = FakeSession(
            [
                FakeResult(itens=[1]),  # organizacoes_ativas
                FakeResult(itens=[(1, calculado_em), (2, calculado_em)]),  # ultima_explicacao_por_avaliacao
                FakeResult(itens=[(avaliacao_sem_mudanca, 1), (avaliacao_recalculada, 1)]),  # avaliacoes
            ]
        )
        resultado = asyncio.run(gerar_explicacoes_risco_pendentes(session, chamar_ia=_chamada_explicacao_fake))
    finally:
        settings.ia_sombra_enabled = original
        modulo.dentro_do_horario_comercial = original_horario

    assert resultado == 1


# --- Job periódico só roda fora do horário comercial (item pedido pelo
# usuário após avaliar os riscos de deixar a IA em sombra ativa 24h --
# cada chamada ao modelo local consome CPU cheia por vários segundos, e
# até 20 chamadas por execução competiriam com o tráfego real durante o
# horário de maior uso). ---


def test_gerar_sugestoes_ia_pendentes_dentro_do_horario_comercial_devolve_zero() -> None:
    import app.ia_sombra as modulo

    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True
    original_horario = modulo.dentro_do_horario_comercial
    modulo.dentro_do_horario_comercial = lambda *_a, **_k: True

    try:
        session = FakeSession([])
        resultado = asyncio.run(gerar_sugestoes_ia_pendentes(session))
    finally:
        settings.ia_sombra_enabled = original
        modulo.dentro_do_horario_comercial = original_horario

    assert resultado == 0
    assert session.executados == []  # nem chega a consultar organizações ativas


def test_gerar_explicacoes_risco_pendentes_dentro_do_horario_comercial_devolve_zero() -> None:
    import app.ia_sombra as modulo

    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True
    original_horario = modulo.dentro_do_horario_comercial
    modulo.dentro_do_horario_comercial = lambda *_a, **_k: True

    try:
        session = FakeSession([])
        resultado = asyncio.run(gerar_explicacoes_risco_pendentes(session))
    finally:
        settings.ia_sombra_enabled = original
        modulo.dentro_do_horario_comercial = original_horario

    assert resultado == 0
    assert session.executados == []
    explicacoes_criadas = [item for item in session.adicionados if isinstance(item, ExplicacaoAnaliseMarca)]
    assert len(explicacoes_criadas) == 1
    assert explicacoes_criadas[0].avaliacao_risco_id == 2


# --- Frente B: qualificação da IA na captação de leads --------------------


def _lead_captacao(**kwargs: object) -> Lead:
    base = dict(
        id=10,
        organizacao_id=1,
        nome="Cliente Novo",
        empresa="Empresa Nova",
        marca="MARCA NOVA",
        origem="site",
        utm_source="google",
        utm_medium="cpc",
        utm_campaign="marcas-2026",
    )
    base.update(kwargs)
    return Lead(**base)


def test_montar_contexto_captacao_lead_inclui_dados_da_chegada() -> None:
    contexto = montar_contexto_captacao_lead(_lead_captacao())
    assert "Cliente Novo" in contexto
    assert "Empresa Nova" in contexto
    assert "MARCA NOVA" in contexto
    assert "google" in contexto


def test_analisar_qualificacao_bem_formada() -> None:
    prioridade, observacao = _analisar_qualificacao("PRIORIDADE: alta\nOBSERVACAO: empresa grande, marca conhecida.")
    assert prioridade == "alta"
    assert observacao == "empresa grande, marca conhecida."


def test_analisar_qualificacao_prioridade_fora_do_vocabulario_cai_em_media() -> None:
    prioridade, _ = _analisar_qualificacao("PRIORIDADE: urgentissimo\nOBSERVACAO: teste.")
    assert prioridade == "media"


def test_analisar_qualificacao_sem_marcadores_cai_em_media_com_texto_bruto() -> None:
    prioridade, observacao = _analisar_qualificacao("resposta livre do modelo")
    assert prioridade == "media"
    assert observacao == "resposta livre do modelo"


async def _chamada_qualificacao_fake(_prompt: str) -> str:
    return "PRIORIDADE: alta\nOBSERVACAO: marca conhecida, boa chance de fechar."


def test_gerar_qualificacao_lead_usa_chamada_injetada_e_nao_comita() -> None:
    lead = _lead_captacao()
    session = FakeSession([])

    qualificacao = asyncio.run(gerar_qualificacao_lead(session, lead, chamar_ia=_chamada_qualificacao_fake))

    assert isinstance(qualificacao, QualificacaoIALead)
    assert qualificacao.prioridade == "alta"
    assert "boa chance" in qualificacao.observacao
    assert qualificacao.erro is None
    assert qualificacao in session.adicionados
    assert session.commits == 0


def test_gerar_qualificacao_lead_erro_na_chamada_vira_campo_erro() -> None:
    lead = _lead_captacao()
    session = FakeSession([])

    qualificacao = asyncio.run(gerar_qualificacao_lead(session, lead, chamar_ia=_chamada_com_falha))

    assert qualificacao.erro is not None
    assert qualificacao.prioridade == "media"


def test_enfileirar_qualificacao_ia_nao_enfileira_com_flag_global_desligada() -> None:
    lead = _lead_captacao()
    session = FakeSession([])

    asyncio.run(enfileirar_qualificacao_ia_se_ativa(session, lead))

    assert session.executados == []  # nem chega a consultar a política da organização


def test_enfileirar_qualificacao_ia_nao_enfileira_com_organizacao_sem_opt_in() -> None:
    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True
    lead = _lead_captacao()

    try:
        session = FakeSession([FakeResult(scalar=PoliticaCRM(organizacao_id=1, ia_sombra_ativa=False))])
        asyncio.run(enfileirar_qualificacao_ia_se_ativa(session, lead))
    finally:
        settings.ia_sombra_enabled = original


def test_enfileirar_qualificacao_ia_enfileira_quando_tudo_ativo() -> None:
    import app.ia_sombra as modulo

    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True
    lead = _lead_captacao()
    chamadas = []

    async def _enfileirar_fake(tipo: str, payload: dict, *, idempotency_key: str | None = None) -> dict:
        chamadas.append((tipo, payload, idempotency_key))
        return {}

    original_enfileirar = modulo.enfileirar
    modulo.enfileirar = _enfileirar_fake
    try:
        session = FakeSession([FakeResult(scalar=PoliticaCRM(organizacao_id=1, ia_sombra_ativa=True))])
        asyncio.run(enfileirar_qualificacao_ia_se_ativa(session, lead))
    finally:
        settings.ia_sombra_enabled = original
        modulo.enfileirar = original_enfileirar

    assert chamadas == [("leads.qualificar_ia", {"lead_id": 10, "organizacao_id": 1}, "10:qualificacao_ia")]
