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

import hashlib
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auditing import criar_evento_auditoria
from app.auth import UsuarioAtualDep, UsuarioAutenticado
from app.database import get_session, session_factory
from app.models import FeatureFlag, FeatureFlagEvento, FeatureFlagOrganizacao, Organizacao

SessionDep = Annotated[AsyncSession, Depends(get_session)]

ESTADOS_PADRAO_VALIDOS = frozenset({"desligado", "somente_administradores", "ligado"})
ESTADOS_ORGANIZACAO_VALIDOS = frozenset({"ativo", "somente_administradores", "adiado", "desativado"})

# Fase 5: estagios de liberacao gradual, cumulativos -- cada um inclui as
# audiencias dos anteriores. So entram em jogo quando estado_padrao ==
# "ligado" (ver _grupo_correspondente); estado_padrao "desligado" e
# "somente_administradores" continuam funcionando exatamente como na Fase 4.
ESTAGIOS_ROLLOUT = (
    "ambiente_interno",
    "administradores",
    "organizacoes_piloto",
    "percentual_limitado",
    "liberacao_geral",
)
TIPOS_EVENTO_FLAG = frozenset({"uso", "erro", "falha_integracao"})


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


def _bucket_percentual(codigo: str, organizacao_id: int) -> int:
    """Posição estável (0-99) da organização para esta flag -- muda de flag
    para flag (mistura o código), mas nunca "pisca" pra mesma flag: usar
    hash() nativo do Python seria instável entre processos (salgado por
    padrão), por isso sha256."""
    digest = hashlib.sha256(f"{codigo}:{organizacao_id}".encode("utf-8")).hexdigest()
    return int(digest, 16) % 100


async def _grupo_correspondente(
    session: AsyncSession, flag: FeatureFlag, organizacao_id: int, *, administrador: bool
) -> str | None:
    """Qual grupo do rollout gradual (Fase 5) libera a flag para esta
    organização, ou None se nenhum libera ainda. Só chamada quando
    estado_padrao == "ligado" -- checa do estagio mais amplo pro mais
    estreito pra evitar a consulta a Organizacao quando ja da pra decidir
    sem ela (preserva o numero de consultas de quem nao usa rollout
    gradual, com estagio_rollout no padrao "liberacao_geral")."""
    indice = ESTAGIOS_ROLLOUT.index(flag.estagio_rollout or "liberacao_geral")
    if indice >= 4:
        return "liberacao_geral"
    if indice >= 3 and _bucket_percentual(flag.codigo, organizacao_id) < flag.percentual_rollout:
        return "percentual_limitado"
    if indice >= 1 and administrador:
        return "administradores"
    organizacao = await session.get(Organizacao, organizacao_id)
    if organizacao is not None and organizacao.ambiente_interno:
        return "ambiente_interno"
    return None


async def registrar_evento_flag(
    codigo: str,
    organizacao_id: int | None,
    grupo: str,
    tipo: str,
    *,
    duracao_ms: int | None = None,
    detalhes: dict | None = None,
) -> None:
    """Telemetria de uso/erro por grupo (Fase 5) -- roda numa sessão própria
    e nunca propaga exceção: telemetria não pode derrubar a operação
    principal (mesmo padrão de app.observability._registrar). `tipo="uso"`
    é chamado automaticamente por flag_ativa_para_organizacao sempre que a
    flag libera algo; `tipo in {"erro", "falha_integracao"}` é opcional,
    reportado pelo próprio código por trás da flag quando quiser ser
    monitorado (ver app.ia_sombra para um exemplo)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if tipo not in TIPOS_EVENTO_FLAG:
        return
    try:
        async with session_factory() as session:
            flag = await obter_flag(session, codigo)
            if flag is None:
                return
            session.add(
                FeatureFlagEvento(
                    feature_flag_id=flag.id,
                    organizacao_id=organizacao_id,
                    grupo=grupo,
                    tipo=tipo,
                    duracao_ms=duracao_ms,
                    detalhes=detalhes or {},
                )
            )
            await session.commit()
    except Exception:
        return


async def flag_ativa_para_organizacao(
    session: AsyncSession, codigo: str, organizacao_id: int, *, administrador: bool = False
) -> bool:
    """Núcleo da avaliação, sem depender de uma requisição HTTP autenticada
    -- usado tanto por `flag_ativa` (usuário logado) quanto por código de
    fundo (jobs do worker, ex. app.ia_sombra) que só tem organizacao_id.
    Fecha em falso sempre que houver dúvida (flag inexistente, desligada
    globalmente, expirada, ou organização sem override caindo no padrão
    "desligado") -- API e banco continuam compatíveis com a flag
    desligada, então nunca há problema em recusar por segurança.
    `administrador=False` (padrão) é a escolha certa pra código de fundo:
    um job não é "um administrador logado", então uma flag em
    "somente_administradores" fica desligada para ele, mesmo que a
    organização tenha um administrador de verdade.

    Sempre que libera a flag, registra um evento de "uso" (Fase 5) com o
    grupo que liberou -- é o que alimenta o monitoramento por grupo e o
    circuito de interrupção automática."""
    flag = await obter_flag(session, codigo)
    if flag is None or not flag.ativo:
        return False
    if flag.data_expiracao is not None and flag.data_expiracao <= datetime.now(UTC):
        return False
    override = await obter_override(session, flag.id, organizacao_id)
    if override is not None:
        if override.estado == "adiado" and (override.adiado_ate is None or override.adiado_ate > datetime.now(UTC)):
            return False
        if override.estado != "adiado":
            resultado = _avaliar_estado(override.estado, usuario_e_administrador=administrador)
            if resultado:
                await registrar_evento_flag(codigo, organizacao_id, "organizacoes_piloto", "uso")
            return resultado
        # adiado_ate já passou -- cai no padrão da flag, mesmo caminho de
        # quem nunca teve override.
    if flag.estado_padrao == "somente_administradores":
        if administrador:
            await registrar_evento_flag(codigo, organizacao_id, "administradores", "uso")
            return True
        return False
    if flag.estado_padrao != "ligado":
        return False
    grupo = await _grupo_correspondente(session, flag, organizacao_id, administrador=administrador)
    if grupo is None:
        return False
    await registrar_evento_flag(codigo, organizacao_id, grupo, "uso")
    return True


async def flag_ativa(session: AsyncSession, codigo: str, usuario: UsuarioAutenticado) -> bool:
    """Avaliação para um usuário autenticado de verdade (endpoint HTTP) --
    ver flag_ativa_para_organizacao para a versão usada por código de
    fundo sem usuário logado."""
    return await flag_ativa_para_organizacao(
        session, codigo, usuario.organizacao_id, administrador=_usuario_e_administrador(usuario)
    )


async def registrar_resultado_flag(
    codigo: str,
    organizacao_id: int,
    tipo: str,
    *,
    administrador: bool = False,
    duracao_ms: int | None = None,
    detalhes: dict | None = None,
) -> None:
    """Para o código por trás de uma flag reportar erro/falha de integração
    (Fase 5) -- opcional, chamado pelo próprio código que decidiu usar a
    flag depois de tentar a operação de verdade (ver app.ia_sombra para um
    exemplo). `tipo="uso"` não precisa disso: já é registrado
    automaticamente por flag_ativa_para_organizacao no momento da
    liberação."""
    if tipo not in {"erro", "falha_integracao"}:
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    grupo = "liberacao_geral"
    try:
        async with session_factory() as session:
            flag = await obter_flag(session, codigo)
            if flag is None:
                return
            grupo = await _grupo_correspondente(session, flag, organizacao_id, administrador=administrador) or grupo
    except Exception:
        return
    await registrar_evento_flag(codigo, organizacao_id, grupo, tipo, duracao_ms=duracao_ms, detalhes=detalhes)


JANELA_CIRCUITO_MINUTOS = 60


def _reverter_estagio_ou_desativar(flag: FeatureFlag) -> str:
    """Reduz a exposição da flag sem derrubar quem já está usando --
    critério de aceite da Fase 5 ("uma funcionalidade problemática pode ter
    sua expansão interrompida sem retirar o sistema do ar"). Recua um
    estágio; se já estiver no mínimo (ambiente_interno), não há pra onde
    recuar, então desliga a flag inteira (kill-switch já existente)."""
    indice = ESTAGIOS_ROLLOUT.index(flag.estagio_rollout or "liberacao_geral")
    if indice == 0:
        flag.ativo = False
        return "flag desativada (já estava no estágio mínimo, ambiente interno, e continuou com erro)"
    estagio_anterior = flag.estagio_rollout
    flag.estagio_rollout = ESTAGIOS_ROLLOUT[indice - 1]
    return f"estágio recuado de '{estagio_anterior}' para '{flag.estagio_rollout}'"


async def interromper_rollout(session: AsyncSession, flag: FeatureFlag, *, motivo: str, por: str) -> str:
    """Ação compartilhada pela interrupção manual (endpoint, superadmin) e
    automática (avaliar_circuito_flags, worker) -- ambas reduzem a
    exposição (ver _reverter_estagio_ou_desativar) e deixam rastro
    auditável. Não commita: quem chama decide quando."""
    resumo = _reverter_estagio_ou_desativar(flag)
    flag.pausado_em = datetime.now(UTC)
    flag.pausado_motivo = motivo
    flag.pausado_por = por
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            ator=por,
            acao="FLAG_INTERROMPER",
            recurso=f"feature_flag:{flag.id}",
            sucesso=True,
            status_http=200,
            detalhes={"codigo": flag.codigo, "motivo": motivo, "resultado": resumo},
        )
    )
    return resumo


def retomar_rollout(flag: FeatureFlag) -> None:
    """Limpa a marca de interrupção -- não reativa estágio nem `ativo`
    sozinho: quem retoma decide explicitamente pra onde avançar de novo
    (mesmo formulário de edição/ações já usado pra tudo mais na flag)."""
    flag.pausado_em = None
    flag.pausado_motivo = None
    flag.pausado_por = None


async def avaliar_circuito_flags(session: AsyncSession) -> list[str]:
    """Roda periodicamente (app.worker, tarefa "feature_flags.
    avaliar_circuito") -- para cada flag ativa com limite_taxa_erro
    configurado e ainda não interrompida, calcula a taxa de erro dos
    últimos JANELA_CIRCUITO_MINUTOS minutos (erro + falha_integracao sobre
    o total de eventos) e interrompe automaticamente o rollout se
    ultrapassar o limite -- com amostra mínima de limite_eventos_minimo,
    pra não reagir a ruído estatístico de poucos eventos. Devolve os
    códigos interrompidos nesta execução (usado só em logs/testes)."""
    desde = datetime.now(UTC) - timedelta(minutes=JANELA_CIRCUITO_MINUTOS)
    flags = (
        await session.execute(
            select(FeatureFlag).where(FeatureFlag.ativo.is_(True), FeatureFlag.limite_taxa_erro.is_not(None))
        )
    ).scalars().all()
    interrompidas: list[str] = []
    for flag in flags:
        if flag.pausado_em is not None:
            continue  # já interrompida (manual ou automática) -- espera alguém retomar
        contagem = (
            await session.execute(
                select(FeatureFlagEvento.tipo, func.count())
                .where(FeatureFlagEvento.feature_flag_id == flag.id, FeatureFlagEvento.criado_em >= desde)
                .group_by(FeatureFlagEvento.tipo)
            )
        ).all()
        por_tipo = dict(contagem)
        total = sum(por_tipo.values())
        if total < flag.limite_eventos_minimo:
            continue
        problematicos = por_tipo.get("erro", 0) + por_tipo.get("falha_integracao", 0)
        taxa = problematicos / total
        if taxa <= flag.limite_taxa_erro:
            continue
        motivo = (
            f"Taxa de erro {taxa:.1%} acima do limite {flag.limite_taxa_erro:.1%} "
            f"(janela de {JANELA_CIRCUITO_MINUTOS}min, {problematicos}/{total} eventos)"
        )
        await interromper_rollout(session, flag, motivo=motivo, por="sistema")
        interrompidas.append(flag.codigo)
    if interrompidas:
        await session.commit()
    return interrompidas


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
