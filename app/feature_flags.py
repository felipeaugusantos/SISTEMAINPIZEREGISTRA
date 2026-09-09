"""Fase 4: feature flags com ativação controlada por organização.

Regras de produto (decisão do usuário):
- Só para funcionalidades novas e compatíveis com a flag desligada -- uma
  correção de segurança NUNCA é feature flag, sempre deploy normal e
  incondicional (ver confirmar_nao_e_correcao_seguranca em
  app.api.feature_flags).
- Migrations associadas a uma flag nunca podem depender do estado dela --
  o schema muda incondicionalmente, a flag só controla comportamento em
  runtime.
- API e banco continuam funcionando com a flag desligada (o código sempre
  precisa de um caminho "desligado" que funciona).
- O backend valida a flag em cada chamada (exigir_feature_ativa abaixo) --
  ocultar só a interface nunca é suficiente. Uma tela pode esconder um
  botão como conveniência, mas o endpoint por trás dele tem que recusar a
  chamada de qualquer forma se a flag estiver desligada para aquela
  organização.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAtualDep, UsuarioAutenticado
from app.database import get_session
from app.models import FeatureFlag, FeatureFlagOrganizacao

SessionDep = Annotated[AsyncSession, Depends(get_session)]

ESTADOS_PADRAO_VALIDOS = frozenset({"desligado", "somente_administradores", "ligado"})
ESTADOS_ORGANIZACAO_VALIDOS = frozenset({"ativo", "somente_administradores", "adiado", "desativado"})


def _usuario_e_administrador(usuario: UsuarioAutenticado) -> bool:
    return usuario.superadmin or usuario.perfil == "administrador"


async def obter_flag(session: AsyncSession, codigo: str) -> FeatureFlag | None:
    return (await session.execute(select(FeatureFlag).where(FeatureFlag.codigo == codigo))).scalar_one_or_none()


async def obter_override(session: AsyncSession, flag_id: int, organizacao_id: int) -> FeatureFlagOrganizacao | None:
    return (
        await session.execute(
            select(FeatureFlagOrganizacao).where(
                FeatureFlagOrganizacao.feature_flag_id == flag_id,
                FeatureFlagOrganizacao.organizacao_id == organizacao_id,
            )
        )
    ).scalar_one_or_none()


def _avaliar_estado(estado: str, *, usuario_e_administrador: bool) -> bool:
    if estado in {"ativo", "ligado"}:
        return True
    if estado == "somente_administradores":
        return usuario_e_administrador
    return False  # "adiado", "desativado", "desligado" e qualquer valor desconhecido


async def flag_ativa(session: AsyncSession, codigo: str, usuario: UsuarioAutenticado) -> bool:
    """Fecha em falso sempre que houver dúvida (flag inexistente, desligada
    globalmente, expirada, ou organização sem override caindo no padrão
    "desligado") -- API e banco continuam compatíveis com a flag
    desligada, então nunca há problema em recusar por segurança."""
    flag = await obter_flag(session, codigo)
    if flag is None or not flag.ativo:
        return False
    if flag.data_expiracao is not None and flag.data_expiracao <= datetime.now(UTC):
        return False
    administrador = _usuario_e_administrador(usuario)
    override = await obter_override(session, flag.id, usuario.organizacao_id)
    if override is not None:
        if override.estado == "adiado" and (override.adiado_ate is None or override.adiado_ate > datetime.now(UTC)):
            return False
        if override.estado != "adiado":
            return _avaliar_estado(override.estado, usuario_e_administrador=administrador)
        # adiado_ate já passou -- cai no padrão da flag, mesmo caminho de
        # quem nunca teve override.
    return _avaliar_estado(flag.estado_padrao, usuario_e_administrador=administrador)


def exigir_feature_ativa(codigo: str) -> Callable[..., Awaitable[UsuarioAutenticado]]:
    """Dependency FastAPI para gatear um endpoint por trás de uma feature
    flag -- uso: `Depends(exigir_feature_ativa("codigo-da-flag"))` na
    assinatura do endpoint. Esta é a validação real; qualquer botão/tela
    escondida no frontend é só conveniência, nunca a proteção em si."""

    async def dependencia(session: SessionDep, usuario: UsuarioAtualDep) -> UsuarioAutenticado:
        if not await flag_ativa(session, codigo, usuario):
            raise HTTPException(status_code=403, detail="Funcionalidade não disponível para esta organização")
        return usuario

    return dependencia
