import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.api.feature_flags import (
    InterromperRolloutInput,
    RolloutInput,
    avancar_rollout,
    interromper_rollout_manual,
    monitoramento_rollout,
    retomar_rollout_manual,
)
from app.feature_flags import (
    ESTAGIOS_ROLLOUT,
    _bucket_percentual,
    _grupo_correspondente,
    _reverter_estagio_ou_desativar,
    avaliar_circuito_flags,
    flag_ativa_para_organizacao,
    interromper_rollout,
    retomar_rollout,
)
from app.models import EventoAuditoria, FeatureFlag, FeatureFlagOrganizacao, Organizacao
from tests.conftest import FakeResult, FakeSession, usuario_teste

# --- Fase 5: liberação gradual por grupo de implantação. Estágios
# cumulativos (ambiente_interno < administradores < organizacoes_piloto <
# percentual_limitado < liberacao_geral), só entram em jogo quando
# estado_padrao == "ligado" -- ver docstring de app.feature_flags. ---


def _flag(**kwargs: object) -> FeatureFlag:
    base = dict(
        id=1,
        codigo="nova-busca",
        nome="Nova busca",
        descricao="Motor de busca reformulado.",
        modulos_envolvidos=["consulta"],
        dependencias=[],
        estado_padrao="ligado",
        ativo=True,
        data_expiracao=None,
        estagio_rollout="ambiente_interno",
        percentual_rollout=0,
        limite_taxa_erro=None,
        limite_eventos_minimo=20,
        pausado_em=None,
        pausado_motivo=None,
        pausado_por=None,
    )
    base.update(kwargs)
    return FeatureFlag(**base)


# --- _bucket_percentual: hash estável, nunca "pisca" -----------------------


def test_bucket_percentual_e_deterministico() -> None:
    a = _bucket_percentual("minha-flag", 42)
    b = _bucket_percentual("minha-flag", 42)
    assert a == b
    assert 0 <= a < 100


def test_bucket_percentual_muda_por_flag() -> None:
    """Duas flags nao devem sempre liberar exatamente as mesmas
    organizacoes primeiro -- o codigo da flag entra no hash."""
    buckets_a = {org_id: _bucket_percentual("flag-a", org_id) for org_id in range(1, 30)}
    buckets_b = {org_id: _bucket_percentual("flag-b", org_id) for org_id in range(1, 30)}
    assert buckets_a != buckets_b


# --- _grupo_correspondente: qual estagio libera para a organizacao --------


def test_grupo_correspondente_liberacao_geral_e_o_padrao_sem_consultar_organizacao() -> None:
    """estagio_rollout None (flag criada antes da Fase 5, nunca migrada em
    memoria) cai no padrao liberacao_geral -- preserva "ligado = todo
    mundo" de sempre, sem precisar de FakeResult pra Organizacao."""
    flag = _flag(estagio_rollout=None)
    session = FakeSession()
    grupo = asyncio.run(_grupo_correspondente(session, flag, 99, administrador=False))
    assert grupo == "liberacao_geral"


def test_grupo_correspondente_ambiente_interno_libera_organizacao_marcada() -> None:
    flag = _flag(estagio_rollout="ambiente_interno")
    organizacao = Organizacao(id=1, nome="Interna", slug="interna", plano_id=1, ambiente_interno=True)
    session = FakeSession(objetos_get=[organizacao])
    grupo = asyncio.run(_grupo_correspondente(session, flag, 1, administrador=False))
    assert grupo == "ambiente_interno"


def test_grupo_correspondente_ambiente_interno_bloqueia_organizacao_comum() -> None:
    flag = _flag(estagio_rollout="ambiente_interno")
    organizacao = Organizacao(id=2, nome="Cliente", slug="cliente", plano_id=1, ambiente_interno=False)
    session = FakeSession(objetos_get=[organizacao])
    grupo = asyncio.run(_grupo_correspondente(session, flag, 2, administrador=False))
    assert grupo is None


def test_grupo_correspondente_administradores_libera_admin_em_qualquer_organizacao() -> None:
    flag = _flag(estagio_rollout="administradores")
    grupo = asyncio.run(_grupo_correspondente(FakeSession(), flag, 7, administrador=True))
    assert grupo == "administradores"


def test_grupo_correspondente_administradores_ainda_verifica_ambiente_interno_pra_operador() -> None:
    flag = _flag(estagio_rollout="administradores")
    organizacao = Organizacao(id=3, nome="Interna", slug="interna", plano_id=1, ambiente_interno=True)
    session = FakeSession(objetos_get=[organizacao])
    grupo = asyncio.run(_grupo_correspondente(session, flag, 3, administrador=False))
    assert grupo == "ambiente_interno"


def test_grupo_correspondente_percentual_limitado_usa_bucket_estavel() -> None:
    flag = _flag(estagio_rollout="percentual_limitado", percentual_rollout=100)
    grupo = asyncio.run(_grupo_correspondente(FakeSession(), flag, 55, administrador=False))
    assert grupo == "percentual_limitado"  # percentual 100 == todo mundo dentro do bucket


def test_grupo_correspondente_percentual_limitado_zero_bloqueia_ate_administrador_comum() -> None:
    flag = _flag(estagio_rollout="percentual_limitado", percentual_rollout=0)
    organizacao = Organizacao(id=4, nome="Cliente", slug="cliente-4", plano_id=1, ambiente_interno=False)
    session = FakeSession(objetos_get=[organizacao])
    grupo = asyncio.run(_grupo_correspondente(session, flag, 4, administrador=False))
    assert grupo is None


def test_grupo_correspondente_liberacao_geral_nao_consulta_organizacao() -> None:
    flag = _flag(estagio_rollout="liberacao_geral")
    session = FakeSession()  # sem objetos_get -- quebraria se session.get fosse chamado
    grupo = asyncio.run(_grupo_correspondente(session, flag, 123, administrador=False))
    assert grupo == "liberacao_geral"


# --- flag_ativa_para_organizacao: integração ponta a ponta com o estágio --


def test_flag_ativa_para_organizacao_respeita_estagio_ambiente_interno() -> None:
    flag = _flag(estado_padrao="ligado", estagio_rollout="ambiente_interno")
    organizacao = Organizacao(id=9, nome="Cliente", slug="cliente-9", plano_id=1, ambiente_interno=False)
    session = FakeSession([FakeResult(scalar=flag), FakeResult(scalar=None)], objetos_get=[organizacao])
    resultado = asyncio.run(flag_ativa_para_organizacao(session, "nova-busca", 9))
    assert resultado is False


def test_flag_ativa_para_organizacao_libera_organizacao_interna() -> None:
    flag = _flag(estado_padrao="ligado", estagio_rollout="ambiente_interno")
    organizacao = Organizacao(id=1, nome="Interna", slug="interna", plano_id=1, ambiente_interno=True)
    session = FakeSession([FakeResult(scalar=flag), FakeResult(scalar=None)], objetos_get=[organizacao])
    resultado = asyncio.run(flag_ativa_para_organizacao(session, "nova-busca", 1))
    assert resultado is True


def test_flag_ativa_para_organizacao_override_ainda_funciona_em_qualquer_estagio() -> None:
    """Override por organizacao (Fase 4, "organizacoes_piloto" na pratica)
    continua valendo independente do estagio -- e o mecanismo de piloto."""
    flag = _flag(estado_padrao="ligado", estagio_rollout="ambiente_interno")
    override = FeatureFlagOrganizacao(feature_flag_id=1, organizacao_id=5, estado="ativo")
    session = FakeSession([FakeResult(scalar=flag), FakeResult(scalar=override)])
    resultado = asyncio.run(flag_ativa_para_organizacao(session, "nova-busca", 5))
    assert resultado is True


# --- Circuito de interrupção automática -------------------------------------


def test_reverter_estagio_recua_um_nivel() -> None:
    flag = _flag(estagio_rollout="percentual_limitado", ativo=True)
    resumo = _reverter_estagio_ou_desativar(flag)
    assert flag.estagio_rollout == "organizacoes_piloto"
    assert flag.ativo is True
    assert "recuado" in resumo


def test_reverter_estagio_desativa_quando_ja_esta_no_minimo() -> None:
    flag = _flag(estagio_rollout="ambiente_interno", ativo=True)
    resumo = _reverter_estagio_ou_desativar(flag)
    assert flag.ativo is False
    assert "desativada" in resumo


def test_interromper_rollout_registra_motivo_e_auditoria() -> None:
    flag = _flag(estagio_rollout="percentual_limitado")
    session = FakeSession()
    resumo = asyncio.run(interromper_rollout(session, flag, motivo="Erro alto detectado", por="tech@zeregistra.com"))
    assert flag.pausado_em is not None
    assert flag.pausado_motivo == "Erro alto detectado"
    assert flag.pausado_por == "tech@zeregistra.com"
    assert "organizacoes_piloto" in resumo
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "FLAG_INTERROMPER"


def test_retomar_rollout_limpa_pausa() -> None:
    flag = _flag(pausado_em=datetime.now(UTC), pausado_motivo="x", pausado_por="sistema")
    retomar_rollout(flag)
    assert flag.pausado_em is None
    assert flag.pausado_motivo is None
    assert flag.pausado_por is None


def test_avaliar_circuito_flags_interrompe_quando_taxa_de_erro_estoura() -> None:
    flag = _flag(estagio_rollout="percentual_limitado", limite_taxa_erro=0.1, limite_eventos_minimo=10, ativo=True)
    # 10 eventos, 3 problematicos (erro+falha_integracao) = 30% > limite de 10%
    contagem = FakeResult(itens=[("uso", 7), ("erro", 2), ("falha_integracao", 1)])
    session = FakeSession([FakeResult(itens=[flag]), contagem])

    interrompidas = asyncio.run(avaliar_circuito_flags(session))

    assert interrompidas == ["nova-busca"]
    assert flag.estagio_rollout == "organizacoes_piloto"  # recuou um estagio
    assert flag.pausado_em is not None
    assert session.commits == 1


def test_avaliar_circuito_flags_nao_interrompe_com_amostra_pequena() -> None:
    flag = _flag(estagio_rollout="percentual_limitado", limite_taxa_erro=0.1, limite_eventos_minimo=50, ativo=True)
    contagem = FakeResult(itens=[("uso", 2), ("erro", 3)])  # so 5 eventos, abaixo do minimo de 50
    session = FakeSession([FakeResult(itens=[flag]), contagem])

    interrompidas = asyncio.run(avaliar_circuito_flags(session))

    assert interrompidas == []
    assert flag.estagio_rollout == "percentual_limitado"
    assert session.commits == 0


def test_avaliar_circuito_flags_ignora_flag_ja_pausada() -> None:
    flag = _flag(
        estagio_rollout="percentual_limitado",
        limite_taxa_erro=0.1,
        limite_eventos_minimo=1,
        ativo=True,
        pausado_em=datetime.now(UTC),
    )
    session = FakeSession([FakeResult(itens=[flag])])  # nao deveria nem chegar a consultar eventos

    interrompidas = asyncio.run(avaliar_circuito_flags(session))

    assert interrompidas == []
    assert len(session.executados) == 1  # so a query de flags, nao a de contagem


# --- API: PATCH /rollout, /interromper, /retomar, GET /monitoramento -------


def test_avancar_rollout_muda_estagio_e_audita() -> None:
    flag = _flag(estagio_rollout="ambiente_interno")
    session = FakeSession([FakeResult(scalar=flag)])
    dados = RolloutInput(estagio_rollout="administradores", percentual_rollout=100)

    resposta = asyncio.run(avancar_rollout("nova-busca", dados, session, usuario_teste()))

    assert resposta["estagio_rollout"] == "administradores"
    assert flag.data_ativacao is not None
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "FLAG_ROLLOUT"
    assert session.commits == 1


def test_interromper_rollout_manual_recua_estagio() -> None:
    flag = _flag(estagio_rollout="organizacoes_piloto")
    session = FakeSession([FakeResult(scalar=flag)])
    dados = InterromperRolloutInput(motivo="Clientes reportando erro 500 recorrente")

    resposta = asyncio.run(interromper_rollout_manual("nova-busca", dados, session, usuario_teste()))

    assert resposta["estagio_rollout"] == "administradores"
    assert resposta["pausado_por"] == usuario_teste().email
    assert session.commits == 1


def test_retomar_rollout_manual_falha_se_nao_estiver_pausada() -> None:
    flag = _flag(pausado_em=None)
    session = FakeSession([FakeResult(scalar=flag)])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(retomar_rollout_manual("nova-busca", session, usuario_teste()))

    assert erro.value.status_code == 409


def test_retomar_rollout_manual_sucesso() -> None:
    flag = _flag(pausado_em=datetime.now(UTC), pausado_motivo="x", pausado_por="sistema")
    session = FakeSession([FakeResult(scalar=flag)])

    resposta = asyncio.run(retomar_rollout_manual("nova-busca", session, usuario_teste()))

    assert resposta["pausado_em"] is None
    assert session.commits == 1


def test_monitoramento_rollout_agrega_por_grupo() -> None:
    flag = _flag(estagio_rollout="percentual_limitado", modulos_envolvidos=["consulta"])
    linhas = FakeResult(
        itens=[
            ("ambiente_interno", "uso", 10, 120.0),
            ("ambiente_interno", "erro", 1, None),
            ("percentual_limitado", "uso", 40, 80.0),
        ]
    )
    reclamacoes = FakeResult(scalar=2)
    session = FakeSession([FakeResult(scalar=flag), linhas, reclamacoes])

    resposta = asyncio.run(monitoramento_rollout("nova-busca", session, usuario_teste(), horas=24))

    grupo_interno = next(g for g in resposta["grupos"] if g["grupo"] == "ambiente_interno")
    assert grupo_interno["uso"] == 10
    assert grupo_interno["erros"] == 1
    assert grupo_interno["taxa_erro"] == round(1 / 11, 4)
    grupo_percentual = next(g for g in resposta["grupos"] if g["grupo"] == "percentual_limitado")
    assert grupo_percentual["duracao_media_ms"] == 80
    assert resposta["reclamacoes"] == 2
    assert len(resposta["grupos"]) == len(ESTAGIOS_ROLLOUT)
