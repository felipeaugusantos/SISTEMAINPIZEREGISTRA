"""Fase 4: CRUD e ações de feature flags -- ver docstring de
app.feature_flags para as regras de produto (migrations incondicionais,
compatibilidade com a flag desligada, validação obrigatória no backend).

Cadastro e ações restritos a superadmin (mesmo nível de app.api.
versoes_sistema, Fase 1) -- é a equipe de Tech quem decide qual
organização testa o quê, não autoatendimento pela própria organização.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.saas import SuperAdminDep
from app.api.versoes_sistema import MODULOS_RELEASE, _texto_seguro
from app.auditing import criar_evento_auditoria
from app.auth import UsuarioAtualDep, UsuarioAutenticado
from app.database import get_session
from app.feature_flags import (
    ESTADOS_ORGANIZACAO_VALIDOS,
    ESTAGIOS_ROLLOUT,
    flag_ativa,
    interromper_rollout,
    obter_flag,
    obter_override,
    retomar_rollout,
)
from app.models import FeatureFlag, FeatureFlagEvento, FeatureFlagOrganizacao, Organizacao, ProblemaVersaoSistema

router = APIRouter(prefix="/v1/admin/feature-flags", tags=["feature flags"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_CODIGO_MIN, _CODIGO_MAX = 3, 80


class FeatureFlagInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str = Field(min_length=_CODIGO_MIN, max_length=_CODIGO_MAX, pattern=r"^[a-z0-9][a-z0-9-]*$")
    nome: str = Field(min_length=3, max_length=180)
    descricao: str = Field(min_length=20, max_length=3000)
    modulos_envolvidos: list[str] = Field(min_length=1, max_length=30)
    dependencias: list[str] = Field(default_factory=list, max_length=30)
    estado_padrao: Literal["desligado", "somente_administradores", "ligado"] = "desligado"
    data_expiracao: datetime | None = None
    # Fase 5: dial fino de audiencia, so relevante quando estado_padrao ==
    # "ligado" (ver app.feature_flags._grupo_correspondente). Toda flag
    # nova comeca em "liberacao_geral" (comportamento de sempre) -- avancar
    # pra um rollout gradual e uma acao explicita depois, via
    # PATCH /{codigo}/rollout.
    limite_taxa_erro: float | None = Field(default=None, gt=0, le=1)
    limite_eventos_minimo: int = Field(default=20, ge=1, le=100_000)
    # Regra do usuário: correção de segurança nunca é feature flag --
    # exige confirmação explícita na criação, mesmo padrão de
    # confirmar_publicacao em app.api.versoes_sistema.
    confirmar_nao_e_correcao_seguranca: bool

    @field_validator("descricao")
    @classmethod
    def validar_descricao(cls, valor: str) -> str:
        return _texto_seguro(valor)

    @field_validator("modulos_envolvidos")
    @classmethod
    def validar_modulos(cls, valores: list[str]) -> list[str]:
        invalidos = sorted(set(valores) - MODULOS_RELEASE)
        if invalidos:
            raise ValueError(f"Módulo(s) desconhecido(s): {', '.join(invalidos)}")
        return sorted(set(valores))


class AdiarInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dias: int = Field(ge=1, le=365)


class ExcluirFlagInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmar_exclusao: bool


class RolloutInput(BaseModel):
    """Fase 5: avança (ou recua) o estágio de liberação gradual e/ou o
    percentual usado no estágio "percentual_limitado". Ação isolada do
    resto da flag -- mesmo espírito das ações por organização (Ativar/
    Testar administradores/Adiar/Desativar), só que na dimensão do
    estágio global em vez de uma organização específica."""

    model_config = ConfigDict(extra="forbid")

    estagio_rollout: Literal[
        "ambiente_interno", "administradores", "organizacoes_piloto", "percentual_limitado", "liberacao_geral"
    ]
    percentual_rollout: int = Field(default=100, ge=0, le=100)
    limite_taxa_erro: float | None = Field(default=None, gt=0, le=1)
    limite_eventos_minimo: int = Field(default=20, ge=1, le=100_000)


class InterromperRolloutInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(min_length=10, max_length=2000)

    @field_validator("motivo")
    @classmethod
    def validar_motivo(cls, valor: str) -> str:
        return _texto_seguro(valor)


def flag_json(item: FeatureFlag) -> dict:
    return {
        "id": item.id,
        "codigo": item.codigo,
        "nome": item.nome,
        "descricao": item.descricao,
        "modulos_envolvidos": item.modulos_envolvidos,
        "dependencias": item.dependencias,
        "estado_padrao": item.estado_padrao,
        "ativo": item.ativo,
        "responsavel_id": item.responsavel_id,
        "data_ativacao": item.data_ativacao,
        "data_expiracao": item.data_expiracao,
        "estagio_rollout": item.estagio_rollout,
        "percentual_rollout": item.percentual_rollout,
        "limite_taxa_erro": item.limite_taxa_erro,
        "limite_eventos_minimo": item.limite_eventos_minimo,
        "pausado_em": item.pausado_em,
        "pausado_motivo": item.pausado_motivo,
        "pausado_por": item.pausado_por,
        "criado_em": item.criado_em,
        "atualizado_em": item.atualizado_em,
    }


async def _obter_flag_ou_404(session: AsyncSession, codigo: str) -> FeatureFlag:
    flag = await obter_flag(session, codigo)
    if flag is None:
        raise HTTPException(404, "Feature flag não encontrada")
    return flag


async def _obter_organizacao_ou_404(session: AsyncSession, organizacao_id: int) -> Organizacao:
    organizacao = await session.get(Organizacao, organizacao_id)
    if organizacao is None:
        raise HTTPException(404, "Organização não encontrada")
    return organizacao


async def _dependencias_ativas(session: AsyncSession, codigos: list[str]) -> list[str]:
    """Códigos em `codigos` que não existem como flag cadastrada -- checado
    na criação para não deixar uma dependência apontando para nada."""
    if not codigos:
        return []
    existentes = set(
        (await session.execute(select(FeatureFlag.codigo).where(FeatureFlag.codigo.in_(codigos)))).scalars()
    )
    return sorted(set(codigos) - existentes)


@router.get("")
async def listar_flags(session: SessionDep, _: SuperAdminDep) -> dict:
    itens = (await session.execute(select(FeatureFlag).order_by(FeatureFlag.criado_em.desc()))).scalars().all()
    return {"itens": [flag_json(item) for item in itens]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def criar_flag(dados: FeatureFlagInput, session: SessionDep, usuario: SuperAdminDep) -> dict:
    if not dados.confirmar_nao_e_correcao_seguranca:
        raise HTTPException(422, "Confirme que esta flag não é uma correção de segurança")
    if await obter_flag(session, dados.codigo) is not None:
        raise HTTPException(409, "Já existe uma feature flag com este código")
    faltando = await _dependencias_ativas(session, dados.dependencias)
    if faltando:
        raise HTTPException(422, f"Dependência(s) inexistente(s): {', '.join(faltando)}")
    item = FeatureFlag(
        codigo=dados.codigo,
        nome=dados.nome,
        descricao=dados.descricao,
        modulos_envolvidos=dados.modulos_envolvidos,
        dependencias=sorted(set(dados.dependencias)),
        estado_padrao=dados.estado_padrao,
        responsavel_id=usuario.id,
        data_expiracao=dados.data_expiracao,
        limite_taxa_erro=dados.limite_taxa_erro,
        limite_eventos_minimo=dados.limite_eventos_minimo,
    )
    session.add(item)
    await session.flush()
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="FLAG_CRIAR",
            recurso=f"feature_flag:{item.id}",
            sucesso=True,
            status_http=201,
            detalhes={"codigo": item.codigo, "estado_padrao": item.estado_padrao},
        )
    )
    await session.commit()
    return flag_json(item)


@router.get("/{codigo}")
async def consultar_flag(codigo: str, session: SessionDep, _: SuperAdminDep) -> dict:
    flag = await _obter_flag_ou_404(session, codigo)
    overrides = (
        (
            await session.execute(
                select(FeatureFlagOrganizacao, Organizacao.nome)
                .join(Organizacao, Organizacao.id == FeatureFlagOrganizacao.organizacao_id)
                .where(FeatureFlagOrganizacao.feature_flag_id == flag.id)
                .order_by(Organizacao.nome)
            )
        )
        .all()
    )
    return {
        **flag_json(flag),
        "organizacoes": [
            {
                "organizacao_id": override.organizacao_id,
                "organizacao_nome": nome,
                "estado": override.estado,
                "adiado_ate": override.adiado_ate,
                "responsavel_id": override.responsavel_id,
                "atualizado_em": override.atualizado_em,
            }
            for override, nome in overrides
        ],
    }


async def _aplicar_estado_organizacao(
    codigo: str,
    organizacao_id: int,
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    *,
    estado: str,
    adiado_ate: datetime | None = None,
    acao: str,
) -> dict:
    if estado not in ESTADOS_ORGANIZACAO_VALIDOS:
        raise HTTPException(422, "Estado inválido")
    flag = await _obter_flag_ou_404(session, codigo)
    organizacao = await _obter_organizacao_ou_404(session, organizacao_id)
    override = await obter_override(session, flag.id, organizacao.id)
    estado_anterior = override.estado if override else flag.estado_padrao
    if override is None:
        override = FeatureFlagOrganizacao(feature_flag_id=flag.id, organizacao_id=organizacao.id, estado=estado)
        session.add(override)
    else:
        override.estado = estado
    override.adiado_ate = adiado_ate
    override.responsavel_id = usuario.id
    if flag.data_ativacao is None and estado == "ativo":
        flag.data_ativacao = datetime.now(UTC)
    session.add(
        criar_evento_auditoria(
            organizacao_id=organizacao.id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao=acao,
            recurso=f"feature_flag:{flag.id}",
            sucesso=True,
            status_http=200,
            detalhes={"codigo": flag.codigo, "estado_anterior": estado_anterior, "estado_novo": estado},
        )
    )
    await session.commit()
    return {"codigo": flag.codigo, "organizacao_id": organizacao.id, "estado": estado, "adiado_ate": adiado_ate}


@router.post("/{codigo}/organizacoes/{organizacao_id}/ativar")
async def ativar_para_organizacao(
    codigo: str, organizacao_id: int, session: SessionDep, usuario: SuperAdminDep
) -> dict:
    return await _aplicar_estado_organizacao(
        codigo, organizacao_id, session, usuario, estado="ativo", acao="FLAG_ATIVAR"
    )


@router.post("/{codigo}/organizacoes/{organizacao_id}/testar-administradores")
async def restringir_a_administradores(
    codigo: str, organizacao_id: int, session: SessionDep, usuario: SuperAdminDep
) -> dict:
    return await _aplicar_estado_organizacao(
        codigo,
        organizacao_id,
        session,
        usuario,
        estado="somente_administradores",
        acao="FLAG_SOMENTE_ADMIN",
    )


@router.post("/{codigo}/organizacoes/{organizacao_id}/adiar")
async def adiar_ativacao(
    codigo: str, organizacao_id: int, dados: AdiarInput, session: SessionDep, usuario: SuperAdminDep
) -> dict:
    adiado_ate = datetime.now(UTC) + timedelta(days=dados.dias)
    return await _aplicar_estado_organizacao(
        codigo, organizacao_id, session, usuario, estado="adiado", adiado_ate=adiado_ate, acao="FLAG_ADIAR"
    )


@router.post("/{codigo}/organizacoes/{organizacao_id}/desativar")
async def desativar_para_organizacao(
    codigo: str, organizacao_id: int, session: SessionDep, usuario: SuperAdminDep
) -> dict:
    return await _aplicar_estado_organizacao(
        codigo, organizacao_id, session, usuario, estado="desativado", acao="FLAG_DESATIVAR"
    )


@router.patch("/{codigo}/rollout")
async def avancar_rollout(codigo: str, dados: RolloutInput, session: SessionDep, usuario: SuperAdminDep) -> dict:
    """Fase 5: avança (ou recua) o estágio de liberação gradual. Não mexe
    em estado_padrao/ativo -- o estágio só passa a valer quando
    estado_padrao == "ligado" (ver app.feature_flags). Estourar o limite
    de erro configurado aqui interrompe automaticamente (ver
    app.feature_flags.avaliar_circuito_flags, rodado periodicamente pelo
    worker)."""
    flag = await _obter_flag_ou_404(session, codigo)
    anterior = {
        "estagio_rollout": flag.estagio_rollout,
        "percentual_rollout": flag.percentual_rollout,
        "limite_taxa_erro": flag.limite_taxa_erro,
    }
    flag.estagio_rollout = dados.estagio_rollout
    flag.percentual_rollout = dados.percentual_rollout
    flag.limite_taxa_erro = dados.limite_taxa_erro
    flag.limite_eventos_minimo = dados.limite_eventos_minimo
    if flag.data_ativacao is None and dados.estagio_rollout != "ambiente_interno":
        flag.data_ativacao = datetime.now(UTC)
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="FLAG_ROLLOUT",
            recurso=f"feature_flag:{flag.id}",
            sucesso=True,
            status_http=200,
            detalhes={"codigo": flag.codigo, "estagio_anterior": anterior, "estagio_novo": dados.model_dump()},
        )
    )
    await session.commit()
    return flag_json(flag)


@router.post("/{codigo}/interromper")
async def interromper_rollout_manual(
    codigo: str, dados: InterromperRolloutInput, session: SessionDep, usuario: SuperAdminDep
) -> dict:
    """Interrupção manual (mesma ação que o circuito automático toma
    sozinho): recua um estágio, ou desativa a flag se já estiver no
    estágio mínimo -- critério de aceite da Fase 5, sem derrubar quem já
    está usando."""
    flag = await _obter_flag_ou_404(session, codigo)
    resumo = await interromper_rollout(session, flag, motivo=dados.motivo, por=usuario.email)
    await session.commit()
    return {**flag_json(flag), "resultado": resumo}


@router.post("/{codigo}/retomar")
async def retomar_rollout_manual(codigo: str, session: SessionDep, usuario: SuperAdminDep) -> dict:
    """Limpa a marca de interrupção. Não readianta o estágio sozinho --
    quem retoma decide explicitamente pra onde avançar de novo (PATCH
    /{codigo}/rollout)."""
    flag = await _obter_flag_ou_404(session, codigo)
    if flag.pausado_em is None:
        raise HTTPException(409, "Esta flag não está interrompida")
    retomar_rollout(flag)
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="FLAG_RETOMAR",
            recurso=f"feature_flag:{flag.id}",
            sucesso=True,
            status_http=200,
            detalhes={"codigo": flag.codigo},
        )
    )
    await session.commit()
    return flag_json(flag)


@router.get("/{codigo}/monitoramento")
async def monitoramento_rollout(codigo: str, session: SessionDep, _: SuperAdminDep, horas: int = 24) -> dict:
    """Fase 5: erros, tempo de resposta e uso por grupo de implantação,
    mais reclamações relatadas (Central de Atualizações, app.api.
    atualizacoes) para os módulos desta flag desde que ela começou a ser
    ativada -- proxy razoável já que reclamação não é uma ação atômica
    ligada a uma flag específica, e sim um relato livre do operador."""
    horas = max(1, min(horas, 24 * 30))
    flag = await _obter_flag_ou_404(session, codigo)
    desde = datetime.now(UTC) - timedelta(hours=horas)
    linhas = (
        await session.execute(
            select(
                FeatureFlagEvento.grupo,
                FeatureFlagEvento.tipo,
                func.count().label("total"),
                func.avg(FeatureFlagEvento.duracao_ms).label("duracao_media_ms"),
            )
            .where(FeatureFlagEvento.feature_flag_id == flag.id, FeatureFlagEvento.criado_em >= desde)
            .group_by(FeatureFlagEvento.grupo, FeatureFlagEvento.tipo)
        )
    ).all()
    por_grupo: dict[str, dict] = {
        grupo: {"grupo": grupo, "uso": 0, "erros": 0, "falhas_integracao": 0, "duracao_media_ms": None}
        for grupo in ESTAGIOS_ROLLOUT
    }
    for grupo, tipo, total, duracao_media_ms in linhas:
        alvo = por_grupo.setdefault(
            grupo, {"grupo": grupo, "uso": 0, "erros": 0, "falhas_integracao": 0, "duracao_media_ms": None}
        )
        if tipo == "uso":
            alvo["uso"] = total
            alvo["duracao_media_ms"] = round(duracao_media_ms) if duracao_media_ms is not None else None
        elif tipo == "erro":
            alvo["erros"] = total
        elif tipo == "falha_integracao":
            alvo["falhas_integracao"] = total
    for grupo, dados_grupo in por_grupo.items():
        total_eventos = dados_grupo["uso"] + dados_grupo["erros"] + dados_grupo["falhas_integracao"]
        dados_grupo["taxa_erro"] = (
            round((dados_grupo["erros"] + dados_grupo["falhas_integracao"]) / total_eventos, 4)
            if total_eventos
            else None
        )
    reclamacoes = 0
    if flag.modulos_envolvidos:
        janela_reclamacao = flag.data_ativacao or desde
        reclamacoes = (
            await session.execute(
                select(func.count())
                .select_from(ProblemaVersaoSistema)
                .where(
                    ProblemaVersaoSistema.modulo.in_(flag.modulos_envolvidos),
                    ProblemaVersaoSistema.criado_em >= janela_reclamacao,
                )
            )
        ).scalar_one()
    return {
        "codigo": flag.codigo,
        "horas": horas,
        "estagio_rollout": flag.estagio_rollout,
        "pausado_em": flag.pausado_em,
        "pausado_motivo": flag.pausado_motivo,
        "pausado_por": flag.pausado_por,
        "grupos": [por_grupo[grupo] for grupo in ESTAGIOS_ROLLOUT],
        "reclamacoes": reclamacoes or 0,
    }


@router.delete("/{codigo}")
async def excluir_flag(codigo: str, dados: ExcluirFlagInput, session: SessionDep, usuario: SuperAdminDep) -> dict:
    """Exclusão definitiva (cascata sobre FeatureFlagOrganizacao) -- exige
    confirmação explícita, mesmo padrão de confirmar_publicacao/
    confirmar_arquivamento em app.api.versoes_sistema. Uso esperado:
    limpeza de flags de teste, não desligar uma flag em produção (para
    isso, use o kill-switch `ativo` ou desative por organização)."""
    if not dados.confirmar_exclusao:
        raise HTTPException(422, "Confirme expressamente a exclusão")
    flag = await _obter_flag_ou_404(session, codigo)
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="FLAG_EXCLUIR",
            recurso=f"feature_flag:{flag.id}",
            sucesso=True,
            status_http=200,
            detalhes={"codigo": flag.codigo},
        )
    )
    await session.delete(flag)
    await session.commit()
    return {"excluido": True, "codigo": codigo}


@router.get("/{codigo}/verificar")
async def verificar_para_mim(codigo: str, session: SessionDep, usuario: UsuarioAtualDep) -> dict:
    """Aberto a qualquer usuário autenticado (não só Tech) -- é o que o
    frontend chama para decidir mostrar ou não algo na interface. Nunca é a
    proteção de verdade: o endpoint por trás continua exigindo
    Depends(exigir_feature_ativa(codigo)) independentemente disto."""
    return {"codigo": codigo, "ativa": await flag_ativa(session, codigo, usuario)}
