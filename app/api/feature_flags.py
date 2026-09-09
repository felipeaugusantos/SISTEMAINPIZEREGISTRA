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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.saas import SuperAdminDep
from app.api.versoes_sistema import MODULOS_RELEASE, _texto_seguro
from app.auditing import criar_evento_auditoria
from app.auth import UsuarioAtualDep, UsuarioAutenticado
from app.database import get_session
from app.feature_flags import ESTADOS_ORGANIZACAO_VALIDOS, flag_ativa, obter_flag, obter_override
from app.models import FeatureFlag, FeatureFlagOrganizacao, Organizacao

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
    )
    session.add(item)
    await session.flush()
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="CRIAR_FEATURE_FLAG",
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
        codigo, organizacao_id, session, usuario, estado="ativo", acao="ATIVAR_FEATURE_FLAG"
    )


@router.post("/{codigo}/organizacoes/{organizacao_id}/testar-administradores")
async def testar_somente_administradores(
    codigo: str, organizacao_id: int, session: SessionDep, usuario: SuperAdminDep
) -> dict:
    return await _aplicar_estado_organizacao(
        codigo,
        organizacao_id,
        session,
        usuario,
        estado="somente_administradores",
        acao="TESTAR_FEATURE_FLAG_ADMINISTRADORES",
    )


@router.post("/{codigo}/organizacoes/{organizacao_id}/adiar")
async def adiar_ativacao(
    codigo: str, organizacao_id: int, dados: AdiarInput, session: SessionDep, usuario: SuperAdminDep
) -> dict:
    adiado_ate = datetime.now(UTC) + timedelta(days=dados.dias)
    return await _aplicar_estado_organizacao(
        codigo, organizacao_id, session, usuario, estado="adiado", adiado_ate=adiado_ate, acao="ADIAR_FEATURE_FLAG"
    )


@router.post("/{codigo}/organizacoes/{organizacao_id}/desativar")
async def desativar_para_organizacao(
    codigo: str, organizacao_id: int, session: SessionDep, usuario: SuperAdminDep
) -> dict:
    return await _aplicar_estado_organizacao(
        codigo, organizacao_id, session, usuario, estado="desativado", acao="DESATIVAR_FEATURE_FLAG"
    )


@router.get("/{codigo}/verificar")
async def verificar_para_mim(codigo: str, session: SessionDep, usuario: UsuarioAtualDep) -> dict:
    """Aberto a qualquer usuário autenticado (não só Tech) -- é o que o
    frontend chama para decidir mostrar ou não algo na interface. Nunca é a
    proteção de verdade: o endpoint por trás continua exigindo
    Depends(exigir_feature_ativa(codigo)) independentemente disto."""
    return {"codigo": codigo, "ativa": await flag_ativa(session, codigo, usuario)}
