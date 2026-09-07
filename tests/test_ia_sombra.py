import asyncio
from datetime import UTC, datetime, timedelta

from app.ia_sombra import (
    _analisar_resposta,
    gerar_sugestao_lead,
    gerar_sugestoes_ia_pendentes,
    montar_contexto_lead,
)
from app.models import ContatoLead, Lead, RespostaEmailLead, StatusLead, SugestaoIALead
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
    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True
    try:
        session = FakeSession([FakeResult(itens=[])])
        resultado = asyncio.run(gerar_sugestoes_ia_pendentes(session))
    finally:
        settings.ia_sombra_enabled = original

    assert resultado == 0


def test_gerar_sugestoes_ia_pendentes_pula_lead_sem_atividade_nova_e_gera_para_lead_com_atividade_nova() -> None:
    settings = get_settings()
    original = settings.ia_sombra_enabled
    settings.ia_sombra_enabled = True

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

    assert resultado == 1
    sugestoes_criadas = [item for item in session.adicionados if isinstance(item, SugestaoIALead)]
    assert len(sugestoes_criadas) == 1
    assert sugestoes_criadas[0].lead_id == 2
