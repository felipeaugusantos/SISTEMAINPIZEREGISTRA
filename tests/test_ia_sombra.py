import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.ia_sombra import (
    _analisar_explicacao,
    _analisar_qualificacao,
    _analisar_resposta,
    buscar_leads_similares,
    enfileirar_qualificacao_ia_se_ativa,
    gerar_explicacao_risco,
    gerar_explicacoes_risco_pendentes,
    gerar_qualificacao_lead,
    gerar_sugestao_lead,
    gerar_sugestoes_ia_pendentes,
    indexar_embeddings_leads_pendentes,
    indexar_lead_para_rag,
    montar_contexto_avaliacao_risco,
    montar_contexto_captacao_lead,
    montar_contexto_lead,
    montar_resumo_lead_resultado,
)
from app.models import (
    AvaliacaoRiscoMarca,
    ContatoLead,
    EmbeddingLead,
    FeatureFlag,
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


async def _embedding_fake(_texto: str) -> list[float]:
    return [0.1, 0.2, 0.3]


async def _embedding_com_falha(_texto: str) -> list[float]:
    raise TimeoutError("modelo de embeddings não respondeu a tempo")


def _flag_rag_ativa() -> FeatureFlag:
    return FeatureFlag(
        id=1,
        codigo="rag-local-ia-sombra",
        nome="RAG local na IA em sombra",
        descricao="Precedentes reais na sugestão de próxima ação.",
        modulos_envolvidos=["leads", "ia"],
        dependencias=[],
        estado_padrao="ligado",
        ativo=True,
        data_expiracao=None,
    )


def test_gerar_sugestao_lead_usa_chamada_injetada_e_nao_comita() -> None:
    lead = _lead()
    session = FakeSession(
        [FakeResult(itens=[]), FakeResult(itens=[]), FakeResult(scalar=None), FakeResult(scalar=None)]
    )  # último item: flag do RAG não cadastrada -- casos semelhantes nem chega a consultar

    sugestao = asyncio.run(
        gerar_sugestao_lead(session, lead, chamar_ia=_chamada_fake, gerar_embedding=_embedding_fake)
    )

    assert sugestao.resumo == "Cliente ainda não retornou contato inicial."
    assert sugestao.sugestao_proxima_acao == "Ligar para o cliente."
    assert sugestao.erro is None
    assert sugestao in session.adicionados
    assert session.commits == 0  # persistência é responsabilidade do chamador (o job de manutenção)


def test_gerar_sugestao_lead_erro_na_chamada_vira_campo_erro_sem_propagar() -> None:
    lead = _lead()
    session = FakeSession(
        [FakeResult(itens=[]), FakeResult(itens=[]), FakeResult(scalar=None), FakeResult(scalar=None)]
    )  # último item: flag do RAG não cadastrada

    sugestao = asyncio.run(
        gerar_sugestao_lead(session, lead, chamar_ia=_chamada_com_falha, gerar_embedding=_embedding_fake)
    )

    assert sugestao.erro is not None
    assert "TimeoutError" in sugestao.erro
    assert sugestao.resumo == ""


def test_gerar_sugestao_lead_inclui_casos_semelhantes_no_prompt_quando_ha_precedente() -> None:
    lead = _lead()
    precedente = EmbeddingLead(
        organizacao_id=1,
        lead_id=99,
        resumo_indexado="Lead parecido que fechou depois de 2 ligações.",
        resultado="ganho",
        modelo="nomic-embed-text",
        embedding=[0.1, 0.2, 0.3],
    )
    session = FakeSession(
        [
            FakeResult(itens=[]),  # contatos
            FakeResult(itens=[]),  # respostas
            FakeResult(scalar=None),  # pesquisa
            FakeResult(scalar=_flag_rag_ativa()),  # flag do RAG ativa
            FakeResult(scalar=None),  # sem override por organização -- usa estado_padrao
            FakeResult(itens=[precedente]),  # casos semelhantes
        ]
    )
    prompts_recebidos = []

    async def _chamada_captura_prompt(prompt: str) -> str:
        prompts_recebidos.append(prompt)
        return await _chamada_fake(prompt)

    asyncio.run(
        gerar_sugestao_lead(session, lead, chamar_ia=_chamada_captura_prompt, gerar_embedding=_embedding_fake)
    )

    assert len(prompts_recebidos) == 1
    assert "Casos semelhantes" in prompts_recebidos[0]
    assert "Lead parecido que fechou depois de 2 ligações." in prompts_recebidos[0]


def test_gerar_sugestao_lead_degrada_sem_erro_quando_embedding_falha() -> None:
    lead = _lead()
    session = FakeSession(
        [
            FakeResult(itens=[]),  # contatos
            FakeResult(itens=[]),  # respostas
            FakeResult(scalar=None),  # pesquisa
            FakeResult(scalar=_flag_rag_ativa()),  # flag do RAG ativa -- chega a tentar o embedding
            FakeResult(scalar=None),  # sem override por organização
        ]
    )

    sugestao = asyncio.run(
        gerar_sugestao_lead(session, lead, chamar_ia=_chamada_fake, gerar_embedding=_embedding_com_falha)
    )

    assert sugestao.erro is None
    assert sugestao.resumo == "Cliente ainda não retornou contato inicial."


def test_gerar_sugestao_lead_flag_do_rag_desligada_ignora_precedente_existente() -> None:
    """Rollout gradual (Fase 4): mesmo com uma flag cadastrada, se o estado
    padrão for "desligado" (e a organização não tiver override), o RAG
    fica fora -- nunca chega a chamar gerar_embedding nem
    buscar_leads_similares."""
    lead = _lead()
    flag_desligada = FeatureFlag(
        id=1,
        codigo="rag-local-ia-sombra",
        nome="RAG local na IA em sombra",
        descricao="Precedentes reais na sugestão de próxima ação.",
        modulos_envolvidos=["leads"],
        dependencias=[],
        estado_padrao="desligado",
        ativo=True,
        data_expiracao=None,
    )
    chamadas_embedding = []

    async def _embedding_que_nao_deveria_ser_chamado(texto: str) -> list[float]:
        chamadas_embedding.append(texto)
        return [0.1]

    session = FakeSession(
        [
            FakeResult(itens=[]),  # contatos
            FakeResult(itens=[]),  # respostas
            FakeResult(scalar=None),  # pesquisa
            FakeResult(scalar=flag_desligada),  # flag existe, mas estado_padrao="desligado"
            FakeResult(scalar=None),  # sem override -- cai no padrão desligado
        ]
    )

    sugestao = asyncio.run(
        gerar_sugestao_lead(session, lead, chamar_ia=_chamada_fake, gerar_embedding=_embedding_que_nao_deveria_ser_chamado)
    )

    assert chamadas_embedding == []
    assert sugestao.erro is None


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
                FakeResult(scalar=None),  # flag do RAG não cadastrada -- casos semelhantes nem consulta
            ]
        )
        resultado = asyncio.run(
            gerar_sugestoes_ia_pendentes(session, chamar_ia=_chamada_fake, gerar_embedding=_embedding_fake)
        )
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


# --- RAG local (pgvector): embeddings de leads com resultado conhecido
# (ganho/perdido), usados como precedente na sugestão de próxima ação de
# outros leads (ver gerar_sugestao_lead acima). Só indexa leads com
# resultado -- sem resultado ainda não há o que aprender. ---


def test_montar_resumo_lead_resultado_inclui_contexto_e_resultado_final() -> None:
    lead = _lead(resultado="ganho")
    resumo = montar_resumo_lead_resultado(lead, [], [], None)
    assert "Cliente Teste" in resumo
    assert "Resultado final: ganho" in resumo


def test_indexar_lead_para_rag_sem_resultado_nao_indexa() -> None:
    lead = _lead(resultado=None)
    session = FakeSession([])

    registro = asyncio.run(indexar_lead_para_rag(session, lead, gerar_embedding=_embedding_fake))

    assert registro is None
    assert session.adicionados == []
    assert session.executados == []


def test_indexar_lead_para_rag_cria_registro_novo() -> None:
    lead = _lead(resultado="ganho")
    session = FakeSession(
        [
            FakeResult(itens=[]),  # contatos
            FakeResult(itens=[]),  # respostas
            FakeResult(scalar=None),  # pesquisa
            FakeResult(scalar=None),  # embedding existente (nenhum ainda)
        ]
    )

    registro = asyncio.run(indexar_lead_para_rag(session, lead, gerar_embedding=_embedding_fake))

    assert isinstance(registro, EmbeddingLead)
    assert registro.lead_id == lead.id
    assert registro.resultado == "ganho"
    assert registro.embedding == [0.1, 0.2, 0.3]
    assert registro in session.adicionados


def test_indexar_lead_para_rag_atualiza_registro_existente_sem_duplicar() -> None:
    lead = _lead(resultado="perdido")
    existente = EmbeddingLead(
        organizacao_id=1,
        lead_id=lead.id,
        resumo_indexado="resumo antigo",
        resultado="ganho",
        modelo="nomic-embed-text",
        embedding=[0.9, 0.9, 0.9],
    )
    session = FakeSession(
        [
            FakeResult(itens=[]),  # contatos
            FakeResult(itens=[]),  # respostas
            FakeResult(scalar=None),  # pesquisa
            FakeResult(scalar=existente),  # embedding já existia
        ]
    )

    registro = asyncio.run(indexar_lead_para_rag(session, lead, gerar_embedding=_embedding_fake))

    assert registro is existente
    assert registro.resultado == "perdido"
    assert registro.embedding == [0.1, 0.2, 0.3]
    assert session.adicionados == []  # upsert -- não cria um segundo registro


def test_buscar_leads_similares_devolve_itens_da_consulta() -> None:
    precedente = EmbeddingLead(
        organizacao_id=1, lead_id=2, resumo_indexado="parecido", resultado="ganho", modelo="m", embedding=[0.1]
    )
    session = FakeSession([FakeResult(itens=[precedente])])

    resultado = asyncio.run(
        buscar_leads_similares(session, 1, [0.1, 0.2, 0.3], excluir_lead_id=5)
    )

    assert resultado == [precedente]


def test_indexar_embeddings_leads_pendentes_desligado_por_padrao_devolve_zero() -> None:
    session = FakeSession([])
    resultado = asyncio.run(indexar_embeddings_leads_pendentes(session))
    assert resultado == 0
    assert session.executados == []


def test_indexar_embeddings_leads_pendentes_sem_organizacao_ativa_devolve_zero() -> None:
    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True
    try:
        session = FakeSession([FakeResult(itens=[])])
        resultado = asyncio.run(indexar_embeddings_leads_pendentes(session))
    finally:
        settings.ia_sombra_enabled = original

    assert resultado == 0


def test_indexar_embeddings_leads_pendentes_indexa_leads_com_resultado_conhecido() -> None:
    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True

    lead_ganho = _lead(id=1, resultado="ganho")
    lead_perdido = _lead(id=2, resultado="perdido")

    try:
        session = FakeSession(
            [
                FakeResult(itens=[1]),  # organizacoes_ativas
                FakeResult(itens=[lead_ganho, lead_perdido]),  # pendentes
                FakeResult(itens=[]),  # lead_ganho: contatos
                FakeResult(itens=[]),  # respostas
                FakeResult(scalar=None),  # pesquisa
                FakeResult(scalar=None),  # embedding existente
                FakeResult(itens=[]),  # lead_perdido: contatos
                FakeResult(itens=[]),  # respostas
                FakeResult(scalar=None),  # pesquisa
                FakeResult(scalar=None),  # embedding existente
            ]
        )
        resultado = asyncio.run(
            indexar_embeddings_leads_pendentes(session, gerar_embedding=_embedding_fake)
        )
    finally:
        settings.ia_sombra_enabled = original

    assert resultado == 2
    registros_criados = [item for item in session.adicionados if isinstance(item, EmbeddingLead)]
    assert {item.lead_id for item in registros_criados} == {1, 2}


# --- Provider Gemini (decisão do usuário, 11/09/2026): geração via API do
# Google como alternativa ao Ollama local, escolhida por
# settings.ia_sombra_provider. ---


def test_chamar_gemini_sem_chave_configurada_levanta_erro() -> None:
    import app.ia_sombra as modulo

    settings = get_settings()
    original = settings.gemini_api_key
    settings.gemini_api_key = ""
    try:
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            asyncio.run(modulo.chamar_gemini("prompt qualquer"))
    finally:
        settings.gemini_api_key = original


def test_chamar_ia_configurada_usa_gemini_quando_provider_e_gemini() -> None:
    import app.ia_sombra as modulo

    settings = get_settings()
    original_provider = settings.ia_sombra_provider
    settings.ia_sombra_provider = "gemini"
    chamadas = []

    async def _gemini_fake(prompt: str, *, http_client: object | None = None) -> str:
        chamadas.append(prompt)
        return "RESUMO: ok\nPROXIMA_ACAO: ok"

    original_gemini = modulo.chamar_gemini
    modulo.chamar_gemini = _gemini_fake
    try:
        resultado = asyncio.run(modulo.chamar_ia_configurada("prompt teste"))
    finally:
        settings.ia_sombra_provider = original_provider
        modulo.chamar_gemini = original_gemini

    assert resultado == "RESUMO: ok\nPROXIMA_ACAO: ok"
    assert chamadas == ["prompt teste"]


def test_chamar_ia_configurada_usa_ollama_por_padrao() -> None:
    import app.ia_sombra as modulo

    settings = get_settings()
    original_provider = settings.ia_sombra_provider
    settings.ia_sombra_provider = "ollama"
    chamadas = []

    async def _ollama_fake(prompt: str, *, http_client: object | None = None) -> str:
        chamadas.append(prompt)
        return "ok"

    original_ollama = modulo.chamar_ollama
    modulo.chamar_ollama = _ollama_fake
    try:
        resultado = asyncio.run(modulo.chamar_ia_configurada("prompt teste"))
    finally:
        settings.ia_sombra_provider = original_provider
        modulo.chamar_ollama = original_ollama

    assert resultado == "ok"
    assert chamadas == ["prompt teste"]
