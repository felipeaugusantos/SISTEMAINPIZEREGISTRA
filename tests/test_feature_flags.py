import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.api.feature_flags import (
    AdiarInput,
    ExcluirFlagInput,
    FeatureFlagInput,
    adiar_ativacao,
    ativar_para_organizacao,
    criar_flag,
    desativar_para_organizacao,
    excluir_flag,
    restringir_a_administradores,
    verificar_para_mim,
)
from app.feature_flags import exigir_feature_ativa, flag_ativa, flag_ativa_para_organizacao
from app.models import EventoAuditoria, FeatureFlag, FeatureFlagOrganizacao, Organizacao
from tests.conftest import FakeResult, FakeSession, usuario_teste

# --- Fase 4: feature flags com ativação controlada por organização. O
# backend valida a flag em cada chamada (exigir_feature_ativa) -- ocultar
# só a interface nunca é suficiente. Migrations associadas nunca dependem
# do estado da flag; correção de segurança nunca é feature flag. ---


def _flag(**kwargs: object) -> FeatureFlag:
    base = dict(
        id=1,
        codigo="nova-busca",
        nome="Nova busca",
        descricao="Motor de busca reformulado.",
        modulos_envolvidos=["consulta"],
        dependencias=[],
        estado_padrao="desligado",
        ativo=True,
        data_expiracao=None,
    )
    base.update(kwargs)
    return FeatureFlag(**base)


# --- app.feature_flags.flag_ativa: avaliação da flag -----------------------


def test_flag_ativa_flag_inexistente_devolve_falso() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    resultado = asyncio.run(flag_ativa(session, "nao-existe", usuario_teste()))
    assert resultado is False


def test_flag_ativa_kill_switch_global_desligado_devolve_falso() -> None:
    session = FakeSession([FakeResult(scalar=_flag(ativo=False, estado_padrao="ligado"))])
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste()))
    assert resultado is False


def test_flag_ativa_expirada_devolve_falso() -> None:
    session = FakeSession(
        [FakeResult(scalar=_flag(estado_padrao="ligado", data_expiracao=datetime.now(UTC) - timedelta(days=1)))]
    )
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste()))
    assert resultado is False


def test_flag_ativa_sem_override_usa_estado_padrao_desligado() -> None:
    session = FakeSession([FakeResult(scalar=_flag(estado_padrao="desligado")), FakeResult(scalar=None)])
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste()))
    assert resultado is False


def test_flag_ativa_sem_override_usa_estado_padrao_ligado() -> None:
    session = FakeSession([FakeResult(scalar=_flag(estado_padrao="ligado")), FakeResult(scalar=None)])
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste()))
    assert resultado is True


def test_flag_ativa_padrao_somente_administradores_bloqueia_operador() -> None:
    session = FakeSession(
        [FakeResult(scalar=_flag(estado_padrao="somente_administradores")), FakeResult(scalar=None)]
    )
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste(perfil="operador")))
    assert resultado is False


def test_flag_ativa_padrao_somente_administradores_libera_administrador() -> None:
    session = FakeSession(
        [FakeResult(scalar=_flag(estado_padrao="somente_administradores")), FakeResult(scalar=None)]
    )
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste(perfil="administrador")))
    assert resultado is True


def test_flag_ativa_override_ativo_ignora_padrao_desligado() -> None:
    override = FeatureFlagOrganizacao(feature_flag_id=1, organizacao_id=1, estado="ativo")
    session = FakeSession([FakeResult(scalar=_flag(estado_padrao="desligado")), FakeResult(scalar=override)])
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste()))
    assert resultado is True


def test_flag_ativa_override_desativado_ignora_padrao_ligado() -> None:
    override = FeatureFlagOrganizacao(feature_flag_id=1, organizacao_id=1, estado="desativado")
    session = FakeSession([FakeResult(scalar=_flag(estado_padrao="ligado")), FakeResult(scalar=override)])
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste()))
    assert resultado is False


def test_flag_ativa_override_somente_administradores_bloqueia_operador() -> None:
    override = FeatureFlagOrganizacao(feature_flag_id=1, organizacao_id=1, estado="somente_administradores")
    session = FakeSession([FakeResult(scalar=_flag(estado_padrao="ligado")), FakeResult(scalar=override)])
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste(perfil="operador")))
    assert resultado is False


def test_flag_ativa_override_adiado_com_data_futura_devolve_falso() -> None:
    override = FeatureFlagOrganizacao(
        feature_flag_id=1, organizacao_id=1, estado="adiado", adiado_ate=datetime.now(UTC) + timedelta(days=7)
    )
    session = FakeSession([FakeResult(scalar=_flag(estado_padrao="ligado")), FakeResult(scalar=override)])
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste()))
    assert resultado is False


def test_flag_ativa_override_adiado_com_data_passada_cai_no_padrao() -> None:
    override = FeatureFlagOrganizacao(
        feature_flag_id=1, organizacao_id=1, estado="adiado", adiado_ate=datetime.now(UTC) - timedelta(days=1)
    )
    session = FakeSession([FakeResult(scalar=_flag(estado_padrao="ligado")), FakeResult(scalar=override)])
    resultado = asyncio.run(flag_ativa(session, "nova-busca", usuario_teste()))
    assert resultado is True


# --- flag_ativa_para_organizacao: núcleo usado por código de fundo sem
# usuário logado (ex.: app.ia_sombra, RAG local por trás de uma flag) ------


def test_flag_ativa_para_organizacao_sem_usuario_respeita_somente_administradores() -> None:
    session = FakeSession(
        [FakeResult(scalar=_flag(estado_padrao="somente_administradores")), FakeResult(scalar=None)]
    )
    resultado = asyncio.run(flag_ativa_para_organizacao(session, "nova-busca", 1))
    assert resultado is False  # administrador=False é o padrão -- job de fundo não é "um administrador logado"


def test_flag_ativa_para_organizacao_administrador_true_libera_somente_administradores() -> None:
    session = FakeSession(
        [FakeResult(scalar=_flag(estado_padrao="somente_administradores")), FakeResult(scalar=None)]
    )
    resultado = asyncio.run(flag_ativa_para_organizacao(session, "nova-busca", 1, administrador=True))
    assert resultado is True


# --- exigir_feature_ativa: dependency FastAPI, a validação real ------------


def test_exigir_feature_ativa_bloqueia_quando_flag_desligada() -> None:
    dependencia = exigir_feature_ativa("nova-busca")
    session = FakeSession([FakeResult(scalar=_flag(estado_padrao="desligado")), FakeResult(scalar=None)])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(dependencia(session, usuario_teste()))

    assert erro.value.status_code == 403


def test_exigir_feature_ativa_libera_quando_flag_ligada() -> None:
    dependencia = exigir_feature_ativa("nova-busca")
    session = FakeSession([FakeResult(scalar=_flag(estado_padrao="ligado")), FakeResult(scalar=None)])

    usuario = asyncio.run(dependencia(session, usuario_teste()))

    assert usuario.id == 1


# --- API: criação e ações por organização -----------------------------------


def _dados_flag(**kwargs: object) -> FeatureFlagInput:
    base = dict(
        codigo="nova-busca",
        nome="Nova busca",
        descricao="Motor de busca reformulado, ainda em avaliação com clientes selecionados.",
        modulos_envolvidos=["consulta"],
        dependencias=[],
        estado_padrao="desligado",
        confirmar_nao_e_correcao_seguranca=True,
    )
    base.update(kwargs)
    return FeatureFlagInput(**base)


def test_criar_flag_exige_confirmacao_de_nao_ser_correcao_seguranca() -> None:
    session = FakeSession([])
    dados = _dados_flag(confirmar_nao_e_correcao_seguranca=False)

    with pytest.raises(HTTPException) as erro:
        asyncio.run(criar_flag(dados, session, usuario_teste()))

    assert erro.value.status_code == 422


def test_criar_flag_rejeita_codigo_duplicado() -> None:
    session = FakeSession([FakeResult(scalar=_flag())])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(criar_flag(_dados_flag(), session, usuario_teste()))

    assert erro.value.status_code == 409


def test_criar_flag_rejeita_dependencia_inexistente() -> None:
    session = FakeSession([FakeResult(scalar=None), FakeResult(itens=[])])
    dados = _dados_flag(dependencias=["flag-fantasma"])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(criar_flag(dados, session, usuario_teste()))

    assert erro.value.status_code == 422


def test_criar_flag_sucesso_audita_e_comita() -> None:
    session = FakeSession([FakeResult(scalar=None), FakeResult(itens=[])])

    resposta = asyncio.run(criar_flag(_dados_flag(), session, usuario_teste()))

    assert resposta["codigo"] == "nova-busca"
    item = next(obj for obj in session.adicionados if isinstance(obj, FeatureFlag))
    assert item.estado_padrao == "desligado"
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "FLAG_CRIAR"
    assert session.commits == 1


def test_ativar_para_organizacao_cria_override_e_registra_data_ativacao() -> None:
    flag = _flag()
    organizacao = Organizacao(id=5, nome="Cliente Teste", slug="cliente-teste", plano_id=1)
    session = FakeSession([FakeResult(scalar=flag), FakeResult(scalar=None)], objetos_get=[organizacao])

    resposta = asyncio.run(ativar_para_organizacao("nova-busca", 5, session, usuario_teste()))

    assert resposta["estado"] == "ativo"
    override = next(obj for obj in session.adicionados if isinstance(obj, FeatureFlagOrganizacao))
    assert override.organizacao_id == 5
    assert override.estado == "ativo"
    assert flag.data_ativacao is not None
    assert session.commits == 1


def test_desativar_para_organizacao_nao_afeta_outras(monkeypatch: pytest.MonkeyPatch) -> None:
    """Critério de aceite: uma organização pode ser desativada sem mexer em
    outra -- o override é sempre filtrado por (flag, organizacao_id)."""
    flag = _flag(estado_padrao="ligado")
    organizacao = Organizacao(id=7, nome="Outra Org", slug="outra-org", plano_id=1)
    session = FakeSession([FakeResult(scalar=flag), FakeResult(scalar=None)], objetos_get=[organizacao])

    resposta = asyncio.run(desativar_para_organizacao("nova-busca", 7, session, usuario_teste()))

    assert resposta["estado"] == "desativado"
    override = next(obj for obj in session.adicionados if isinstance(obj, FeatureFlagOrganizacao))
    assert override.organizacao_id == 7


def test_adiar_ativacao_calcula_data_futura() -> None:
    flag = _flag()
    organizacao = Organizacao(id=5, nome="Cliente Teste", slug="cliente-teste", plano_id=1)
    session = FakeSession([FakeResult(scalar=flag), FakeResult(scalar=None)], objetos_get=[organizacao])

    antes = datetime.now(UTC)
    resposta = asyncio.run(adiar_ativacao("nova-busca", 5, AdiarInput(dias=7), session, usuario_teste()))

    assert resposta["estado"] == "adiado"
    assert resposta["adiado_ate"] > antes + timedelta(days=6)


def test_restringir_a_administradores_define_estado_correto() -> None:
    flag = _flag()
    organizacao = Organizacao(id=5, nome="Cliente Teste", slug="cliente-teste", plano_id=1)
    session = FakeSession([FakeResult(scalar=flag), FakeResult(scalar=None)], objetos_get=[organizacao])

    resposta = asyncio.run(restringir_a_administradores("nova-busca", 5, session, usuario_teste()))

    assert resposta["estado"] == "somente_administradores"


def test_verificar_para_mim_reflete_flag_ativa() -> None:
    session = FakeSession([FakeResult(scalar=_flag(estado_padrao="ligado")), FakeResult(scalar=None)])

    resposta = asyncio.run(verificar_para_mim("nova-busca", session, usuario_teste()))

    assert resposta == {"codigo": "nova-busca", "ativa": True}


# --- Exclusão --------------------------------------------------------------


def test_excluir_flag_exige_confirmacao() -> None:
    session = FakeSession([])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(excluir_flag("kanban-v2", ExcluirFlagInput(confirmar_exclusao=False), session, usuario_teste()))

    assert erro.value.status_code == 422
    assert session.deletados == []


def test_excluir_flag_sucesso_audita_deleta_e_comita() -> None:
    flag = _flag(codigo="kanban-v2")
    session = FakeSession([FakeResult(scalar=flag)])

    resposta = asyncio.run(
        excluir_flag("kanban-v2", ExcluirFlagInput(confirmar_exclusao=True), session, usuario_teste())
    )

    assert resposta == {"excluido": True, "codigo": "kanban-v2"}
    assert flag in session.deletados
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "FLAG_EXCLUIR"
    assert session.commits == 1
