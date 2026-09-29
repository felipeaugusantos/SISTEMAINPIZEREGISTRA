from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.painel import (
    _CODIGOS_ALERTA_COMERCIAL,
    indicadores_fluxo,
    listar_alertas_rotinas,
    listar_notificacoes,
    marcar_notificacao_lida,
    marcar_notificacao_nao_lida,
    marcar_todas_notificacoes,
    painel_executivo,
)
from app.permissions import permissoes_do_perfil
from tests.conftest import FakeResult, FakeSession, usuario_teste

TODAS = set(permissoes_do_perfil("ceo"))


@pytest.mark.asyncio
async def test_indicadores_fluxo_mede_conversao_gargalos_e_tempos() -> None:
    inicio = datetime(2026, 8, 1, tzinfo=UTC)
    propostas = [
        SimpleNamespace(
            aceito_em=inicio,
            pagamento_confirmado_em=inicio.replace(day=2),
            juridico_recebido_em=inicio.replace(day=3),
            protocolo_em=inicio.replace(day=5),
            honorarios=1000,
            taxa_gru=200,
        ),
        SimpleNamespace(
            aceito_em=inicio,
            pagamento_confirmado_em=inicio.replace(day=2),
            juridico_recebido_em=None,
            protocolo_em=None,
            honorarios=800,
            taxa_gru=200,
        ),
        SimpleNamespace(
            aceito_em=inicio,
            pagamento_confirmado_em=None,
            juridico_recebido_em=None,
            protocolo_em=None,
            honorarios=500,
            taxa_gru=None,
        ),
    ]
    session = FakeSession([FakeResult(itens=propostas)])
    resultado = await indicadores_fluxo(session, usuario_teste(perfil="ceo", permissoes=TODAS), dias=90)

    assert [item["total"] for item in resultado["etapas"]] == [3, 2, 1, 1]
    assert resultado["valor_contratado"] == 2700.0
    assert resultado["taxas"] == {
        "aceite_pagamento": 0.6667,
        "pagamento_juridico": 0.5,
        "juridico_protocolo": 1.0,
        "aceite_protocolo": 0.3333,
    }
    assert resultado["gargalos"] == {
        "aguardando_pagamento": 1,
        "aguardando_juridico": 1,
        "aguardando_protocolo": 0,
    }
    assert resultado["tempos_medios_horas"]["aceite_pagamento"] == 24.0
    assert resultado["tempos_medios_horas"]["pagamento_juridico"] == 24.0
    assert resultado["tempos_medios_horas"]["juridico_protocolo"] == 48.0


@pytest.mark.asyncio
async def test_indicadores_fluxo_nao_expoe_financeiro_ou_juridico_sem_permissao() -> None:
    proposta = SimpleNamespace(
        aceito_em=datetime(2026, 8, 1, tzinfo=UTC),
        pagamento_confirmado_em=datetime(2026, 8, 2, tzinfo=UTC),
        juridico_recebido_em=datetime(2026, 8, 3, tzinfo=UTC),
        protocolo_em=None,
        honorarios=1000,
        taxa_gru=200,
    )
    session = FakeSession([FakeResult(itens=[proposta])])
    resultado = await indicadores_fluxo(
        session,
        usuario_teste(perfil="operador", permissoes={"dashboard.view"}),
        dias=30,
    )

    assert resultado["etapas"] == [{"id": "aceite", "label": "Propostas aceitas", "total": 1}]
    assert "valor_contratado" not in resultado
    assert resultado["taxas"] == {}
    assert resultado["tempos_medios_horas"] == {}
    assert resultado["gargalos"] == {}


# --- Achado P1 da auditoria de Leads (03/09/2026): só NOVA_PESQUISA chegava a
# quem tinha leads.view sem production.manage -- os alertas de automação de
# Leads/CRM (cadência, reengajamento, retenção) ficavam invisíveis. ---


def test_codigos_alerta_comercial_inclui_automacoes_de_leads_crm() -> None:
    esperados = {
        "NOVA_PESQUISA",
        "RETENCAO_PENDENTE",
        "REENGAJAMENTO_CRM_EXECUTADO",
        "CADENCIA_EMAILS_PROCESSADOS",
        "CADENCIA_PAUSADA_POR_RESPOSTA",
    }
    assert esperados <= _CODIGOS_ALERTA_COMERCIAL


@pytest.mark.asyncio
async def test_painel_executivo_operador_ve_apenas_comercial() -> None:
    session = FakeSession([FakeResult(itens=[(5, 2, 19)])])
    usuario = usuario_teste(perfil="operador", permissoes={"dashboard.view"})
    painel = await painel_executivo(session, usuario)
    assert set(painel) == {"comercial"}
    assert painel["comercial"]["leads_novos"] == 2


@pytest.mark.asyncio
async def test_painel_executivo_ceo_ve_todos_os_blocos() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[(5, 2, 19)]),  # comercial
            FakeResult(itens=[(750, 700, 1450, 2)]),  # financeiro
            FakeResult(itens=[(7, 0, 0, 14)]),  # juridico
            FakeResult(itens=[(16, 2)]),  # risco
            FakeResult(scalar=None),  # aprendizado (sem modelo ativo)
        ]
    )
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    painel = await painel_executivo(session, usuario)
    assert set(painel) == {"comercial", "financeiro", "juridico", "risco", "aprendizado"}
    assert painel["juridico"]["aguardando_confirmacao"] == 14
    assert painel["financeiro"]["vencido"] == 1450.0
    assert painel["aprendizado"]["modelo_ativo"] is False


@pytest.mark.asyncio
async def test_notificacoes_unifica_e_ordena_por_data() -> None:
    juridica = SimpleNamespace(
        id=1,
        tipo="vencido",
        titulo="Prazo vencido",
        mensagem="Venceu ontem",
        criado_em=datetime(2026, 8, 10, tzinfo=UTC),
        lida_em=None,
    )
    alerta = SimpleNamespace(
        id=2,
        severidade="aviso",
        codigo="RETENCAO_PENDENTE",
        mensagem="10 leads excedem a retenção",
        criado_em=datetime(2026, 8, 11, tzinfo=UTC),
        resolvido_em=None,
    )
    session = FakeSession([FakeResult(itens=[juridica]), FakeResult(itens=[alerta]), FakeResult(itens=[])])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resposta = await listar_notificacoes(session, usuario)
    assert resposta["total"] == 2
    # Mais recente primeiro: o alerta (11/08) antes do prazo jurídico (10/08).
    assert resposta["itens"][0]["fonte"] == "sistema"
    assert resposta["itens"][0]["url"] == "/admin/confiabilidade"
    assert resposta["itens"][1]["severidade"] == "aviso"
    assert resposta["itens"][1]["url"] == "/admin/operacao-juridica"


@pytest.mark.asyncio
async def test_sino_mostra_apenas_alertas_operacionais_acionaveis() -> None:
    agora = datetime.now(UTC)
    aviso = SimpleNamespace(
        id=10,
        organizacao_id=1,
        severidade="aviso",
        codigo="BACKUP_AUSENTE",
        mensagem="Backup atrasado",
        criado_em=agora,
        resolvido_em=None,
    )
    informativo = SimpleNamespace(
        id=11,
        organizacao_id=1,
        severidade="info",
        codigo="RENOVACOES_GERADAS",
        mensagem="Renovações geradas com sucesso",
        criado_em=agora,
        resolvido_em=None,
    )
    resolvido = SimpleNamespace(
        id=12,
        organizacao_id=1,
        severidade="critico",
        codigo="FILA_INDISPONIVEL",
        mensagem="Fila indisponível",
        criado_em=agora,
        resolvido_em=agora,
    )
    session = FakeSession([FakeResult(itens=[aviso, informativo, resolvido])])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)

    resposta = await listar_alertas_rotinas(session, usuario)

    assert resposta["total"] == 1
    assert resposta["itens"][0]["id"] == 10
    assert resposta["itens"][0]["fonte"] == "sistema"
    assert resposta["itens"][0]["url"] == "/admin/producao"


@pytest.mark.asyncio
async def test_notificacoes_operador_sem_permissao_nao_ve_nada() -> None:
    session = FakeSession([])
    usuario = usuario_teste(perfil="operador", permissoes={"dashboard.view"})
    resposta = await listar_notificacoes(session, usuario)
    assert resposta == {"total": 0, "total_itens": 0, "itens": []}


@pytest.mark.asyncio
async def test_marcar_juridica_como_lida_registra_autor_e_data() -> None:
    item = SimpleNamespace(status="nova", lida_em=None, lida_por=None)
    session = FakeSession([FakeResult(scalar=item)])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resultado = await marcar_notificacao_lida("juridico", 7, session, usuario)
    assert resultado == {"lida": True}
    assert item.status == "lida"
    assert item.lida_em is not None
    assert item.lida_por == usuario.ator


@pytest.mark.asyncio
async def test_marcar_alerta_sistema_como_resolvido() -> None:
    item = SimpleNamespace(organizacao_id=1, resolvido_em=None)
    session = FakeSession([FakeResult(scalar=item)])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    await marcar_notificacao_lida("sistema", 3, session, usuario)
    assert item.resolvido_em is not None


@pytest.mark.asyncio
async def test_marcar_sem_permissao_retorna_404() -> None:
    session = FakeSession([])
    usuario = usuario_teste(perfil="operador", permissoes={"dashboard.view"})
    with pytest.raises(HTTPException) as erro:
        await marcar_notificacao_lida("sistema", 3, session, usuario)
    assert erro.value.status_code == 404


@pytest.mark.asyncio
async def test_marcar_juridica_como_nao_lida_reverte_estado() -> None:
    item = SimpleNamespace(status="lida", lida_em=datetime(2026, 8, 10, tzinfo=UTC), lida_por="admin@teste.local")
    session = FakeSession([FakeResult(scalar=item)])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resultado = await marcar_notificacao_nao_lida("juridico", 7, session, usuario)
    assert resultado == {"lida": False}
    assert item.status == "nova"
    assert item.lida_em is None
    assert item.lida_por is None


@pytest.mark.asyncio
async def test_marcar_alerta_sistema_como_nao_resolvido() -> None:
    item = SimpleNamespace(organizacao_id=1, resolvido_em=datetime(2026, 8, 10, tzinfo=UTC))
    session = FakeSession([FakeResult(scalar=item)])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    await marcar_notificacao_nao_lida("sistema", 3, session, usuario)
    assert item.resolvido_em is None


@pytest.mark.asyncio
async def test_marcar_todas_como_lidas_afeta_juridico_e_sistema() -> None:
    juridica = SimpleNamespace(status="nova", lida_em=None, lida_por=None)
    alerta = SimpleNamespace(resolvido_em=None)
    session = FakeSession(
        [FakeResult(itens=[juridica]), FakeResult(itens=[alerta]), FakeResult(itens=[]), FakeResult(itens=[])]
    )
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resultado = await marcar_todas_notificacoes(session, usuario, lida=True)
    assert resultado == {"afetadas": 2, "lida": True}
    assert juridica.status == "lida"
    assert juridica.lida_em is not None
    assert alerta.resolvido_em is not None


@pytest.mark.asyncio
async def test_marcar_todas_como_nao_lidas_reverte_tudo() -> None:
    juridica = SimpleNamespace(status="lida", lida_em=datetime(2026, 8, 10, tzinfo=UTC), lida_por="x")
    alerta = SimpleNamespace(resolvido_em=datetime(2026, 8, 10, tzinfo=UTC))
    session = FakeSession([FakeResult(itens=[juridica]), FakeResult(itens=[alerta])])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resultado = await marcar_todas_notificacoes(session, usuario, lida=False)
    assert resultado == {"afetadas": 2, "lida": False}
    assert juridica.status == "nova"
    assert juridica.lida_em is None
    assert alerta.resolvido_em is None


@pytest.mark.asyncio
async def test_notificacoes_incluem_id_e_fonte() -> None:
    juridica = SimpleNamespace(
        id=42,
        tipo="vencido",
        titulo="Prazo",
        mensagem="x",
        criado_em=datetime(2026, 8, 10, tzinfo=UTC),
        lida_em=None,
    )
    session = FakeSession([FakeResult(itens=[juridica]), FakeResult(itens=[]), FakeResult(itens=[])])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resposta = await listar_notificacoes(session, usuario)
    assert resposta["itens"][0]["id"] == 42
    assert resposta["itens"][0]["fonte"] == "juridico"


# --- Achado do usuário (21/09/2026): mensagem do cliente pelo portal não
# gerava nenhum aviso na central de notificações, só o selo "!" na lista
# de Leads. Nova fonte "mensagem_portal", derivada de
# MensagemClientePortal.lida_em (sem tabela nova). ---


@pytest.mark.asyncio
async def test_notificacoes_inclui_lead_com_mensagem_pendente() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[]),  # juridico
            FakeResult(itens=[]),  # sistema
            FakeResult(itens=[(9, "Gustavo Moraes", "Tactical Cloud", 2, datetime(2026, 9, 21, tzinfo=UTC))]),
        ]
    )
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)

    resposta = await listar_notificacoes(session, usuario)

    assert resposta["total"] == 1
    item = resposta["itens"][0]
    assert item["fonte"] == "mensagem_portal"
    assert item["id"] == 9
    assert item["lida"] is False
    assert "2 mensagens" in item["mensagem"]
    assert item["url"] == "/admin/pesquisas?lead_id=9"


@pytest.mark.asyncio
async def test_marcar_mensagem_portal_como_lida_marca_todas_as_mensagens_do_lead() -> None:
    mensagem1 = SimpleNamespace(lida_em=None)
    mensagem2 = SimpleNamespace(lida_em=None)
    session = FakeSession(
        [
            FakeResult(scalar=9),  # lead existe e é visível
            FakeResult(itens=[mensagem1, mensagem2]),
        ]
    )
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)

    resultado = await marcar_notificacao_lida("mensagem_portal", 9, session, usuario)

    assert resultado == {"lida": True}
    assert mensagem1.lida_em is not None
    assert mensagem2.lida_em is not None


@pytest.mark.asyncio
async def test_marcar_mensagem_portal_lida_nega_quando_lead_nao_e_visivel() -> None:
    # Achado: operador só pode marcar como lida mensagens de leads onde é
    # responsável -- mesma regra de portal_cliente.listar_mensagens_portal_admin.
    session = FakeSession([FakeResult(scalar=None)])
    usuario = usuario_teste(perfil="operador", permissoes={"leads.manage", "leads.view", "dashboard.view"})

    with pytest.raises(HTTPException) as erro:
        await marcar_notificacao_lida("mensagem_portal", 9, session, usuario)

    assert erro.value.status_code == 404


@pytest.mark.asyncio
async def test_marcar_mensagem_portal_lida_exige_leads_manage() -> None:
    # Achado do Codex review (PR #91): leads.view é só consulta no
    # catálogo de permissões -- marcar_mensagens_portal_lidas (endpoint
    # equivalente) já exige leads.manage, então essa mutação também
    # precisa, senão um perfil só-leitura apagaria o sinal de pendência.
    session = FakeSession([])
    usuario = usuario_teste(perfil="operador", permissoes={"leads.view", "dashboard.view"})

    with pytest.raises(HTTPException) as erro:
        await marcar_notificacao_lida("mensagem_portal", 9, session, usuario)

    assert erro.value.status_code == 404


@pytest.mark.asyncio
async def test_marcar_todas_notificacoes_marca_mensagens_pendentes_quando_lida() -> None:
    mensagem = SimpleNamespace(lida_em=None)
    session = FakeSession(
        [
            FakeResult(itens=[]),  # juridico
            FakeResult(itens=[]),  # sistema
            FakeResult(itens=[]),  # alertas de plataforma (leitura por usuário)
            FakeResult(itens=[mensagem]),
        ]
    )
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)

    resultado = await marcar_todas_notificacoes(session, usuario, lida=True)

    assert resultado == {"afetadas": 1, "lida": True}
    assert mensagem.lida_em is not None


@pytest.mark.asyncio
async def test_marcar_todas_notificacoes_leads_view_nao_mexe_em_mensagens() -> None:
    # Mesmo achado do Codex review (PR #91) que exigiu leads.manage em
    # marcar_notificacao_lida -- vale também pro branch em lote.
    session = FakeSession([FakeResult(itens=[])])  # sistema (só leads.view, sem juridico)
    usuario = usuario_teste(perfil="operador", permissoes={"leads.view", "dashboard.view"})

    resultado = await marcar_todas_notificacoes(session, usuario, lida=True)

    assert resultado == {"afetadas": 0, "lida": True}


@pytest.mark.asyncio
async def test_marcar_todas_notificacoes_nao_lidas_nao_mexe_em_mensagens() -> None:
    # "Marcar todas como não lidas" não se aplica a mensagens -- reverter
    # exigiria escolher qual mensagem específica desmarcar.
    session = FakeSession([FakeResult(itens=[]), FakeResult(itens=[])])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)

    resultado = await marcar_todas_notificacoes(session, usuario, lida=False)

    assert resultado == {"afetadas": 0, "lida": False}

# --- Achado do usuário (29/09/2026): alerta de plataforma (sem organização,
# ex.: "Atualizacao pendente 157") aparecia no sino, mas "marcar como lida"
# procurava só alertas da própria organização e devolvia 404. A leitura
# desses alertas é por usuário (detalhes["lido_por"]), sem resolver o alerta
# para as demais organizações nem fazer a rotina recriá-lo. ---


def _alerta_plataforma(**kwargs: object) -> SimpleNamespace:
    base: dict = {
        "id": 157,
        "organizacao_id": None,
        "severidade": "aviso",
        "codigo": "ATUALIZACAO_PENDENTE_157",
        "mensagem": "Atualização pendente de confirmação para 5 usuário(s).",
        "criado_em": datetime.now(UTC),
        "resolvido_em": None,
        "detalhes": {"versao_id": 157, "pendentes": 5},
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_marcar_alerta_de_plataforma_como_lido_vale_so_para_o_usuario() -> None:
    alerta = _alerta_plataforma()
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)

    resultado = await marcar_notificacao_lida("sistema", 157, FakeSession([FakeResult(scalar=alerta)]), usuario)

    assert resultado == {"lida": True}
    # Não resolve para todo mundo -- a rotina reabriria (e reenviaria e-mail).
    assert alerta.resolvido_em is None
    assert alerta.detalhes["lido_por"] == [usuario.id]
    assert alerta.detalhes["pendentes"] == 5


@pytest.mark.asyncio
async def test_alerta_de_plataforma_lido_some_do_sino_do_usuario() -> None:
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    lido = _alerta_plataforma(detalhes={"lido_por": [usuario.id]})
    outro_usuario = _alerta_plataforma(id=158, detalhes={"lido_por": [usuario.id + 1]})

    resposta = await listar_alertas_rotinas(FakeSession([FakeResult(itens=[lido, outro_usuario])]), usuario)

    assert [item["id"] for item in resposta["itens"]] == [158]


@pytest.mark.asyncio
async def test_alerta_de_plataforma_pode_voltar_a_nao_lido() -> None:
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    alerta = _alerta_plataforma(detalhes={"lido_por": [usuario.id, 99]})

    await marcar_notificacao_nao_lida("sistema", 157, FakeSession([FakeResult(scalar=alerta)]), usuario)

    assert alerta.detalhes["lido_por"] == [99]
    assert alerta.resolvido_em is None


@pytest.mark.asyncio
async def test_marcar_todas_inclui_alertas_de_plataforma_por_usuario() -> None:
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    alerta = _alerta_plataforma()
    session = FakeSession(
        [FakeResult(itens=[]), FakeResult(itens=[]), FakeResult(itens=[alerta]), FakeResult(itens=[])]
    )

    resultado = await marcar_todas_notificacoes(session, usuario, lida=True)

    assert resultado == {"afetadas": 1, "lida": True}
    assert alerta.detalhes["lido_por"] == [usuario.id]
    assert alerta.resolvido_em is None

@pytest.mark.asyncio
async def test_alertas_de_plataforma_lidos_nao_consomem_o_limite_do_sino() -> None:
    # Revisão do Codex no PR #152: com LIMIT no SQL, alertas já lidos pelo
    # usuário ocupavam as vagas e escondiam um alerta não lido mais antigo.
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    lidos = [_alerta_plataforma(id=200 + i, detalhes={"lido_por": [usuario.id]}) for i in range(3)]
    nao_lido = _alerta_plataforma(id=300, organizacao_id=1, detalhes={})

    resposta = await listar_alertas_rotinas(FakeSession([FakeResult(itens=[*lidos, nao_lido])]), usuario, limite=2)

    assert [item["id"] for item in resposta["itens"]] == [300]
