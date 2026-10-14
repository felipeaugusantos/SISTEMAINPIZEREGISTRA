"""Administração do consentimento e da fila de mensagens (WhatsApp).

Só gerencia os registros; o envio é feito pelo worker (app/mensageria.py::processar_fila) e permanece
desligado até haver número e provedor configurados.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.mensageria import (
    BASES_LEGAIS,
    FINALIDADES,
    _cancelar_pendentes,
    _enviadas_hoje,
    _limite_diario,
    obter_provedor,
    registrar_consentimento,
    revogar_consentimento,
)
from app.models import ConsentimentoCanal, EventoAuditoria, MensagemSaida
from app.settings import get_settings

router = APIRouter(prefix="/v1/admin/mensageria", tags=["mensageria"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("crm.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("crm.manage"))]


class ConsentimentoInput(BaseModel):
    finalidade: str
    telefone: str = Field(min_length=8, max_length=30)
    origem: str = Field(min_length=2, max_length=60)
    base_legal: str = "consentimento"
    titular_tipo: str | None = Field(default=None, pattern=r"^(cliente_portal|lead)$")
    titular_id: int | None = None
    evidencia: dict = Field(default_factory=dict)


class RevogacaoInput(BaseModel):
    finalidade: str
    telefone: str = Field(min_length=8, max_length=30)
    origem: str = Field(min_length=2, max_length=60, default="atendimento")


def _mascarar(telefone: str, usuario: UsuarioAutenticado) -> str:
    """Quem não pode ver contatos completos vê só os 4 últimos dígitos."""
    return telefone if usuario.pode("leads.pii.view") else f"••••••{telefone[-4:]}"


def _serializar_consentimento(c: ConsentimentoCanal, usuario: UsuarioAutenticado) -> dict:
    return {
        "id": c.id,
        "finalidade": c.finalidade,
        "telefone": _mascarar(c.telefone, usuario),
        "status": c.status,
        "base_legal": c.base_legal,
        "origem": c.origem,
        "titular_tipo": c.titular_tipo,
        "titular_id": c.titular_id,
        "concedido_em": c.concedido_em,
        "revogado_em": c.revogado_em,
    }


def _serializar_mensagem(m: MensagemSaida, usuario: UsuarioAutenticado) -> dict:
    return {
        "id": m.id,
        "finalidade": m.finalidade,
        "telefone": _mascarar(m.telefone, usuario),
        "status": m.status,
        "motivo_bloqueio": m.motivo_bloqueio,
        "tentativas": m.tentativas,
        "agendada_para": m.agendada_para,
        "enviada_em": m.enviada_em,
        "ultimo_erro": m.ultimo_erro,
        "previa": m.corpo[:80],
    }


def _auditar(session: AsyncSession, ator: UsuarioAutenticado, acao: str, recurso: str, detalhes: dict) -> None:
    session.add(
        EventoAuditoria(
            organizacao_id=ator.organizacao_id,
            actor_id=ator.id,
            ator=ator.email,
            acao=acao[:20],
            recurso=recurso,
            sucesso=True,
            status_http=200,
            detalhes=detalhes,
        )
    )


@router.get("/status")
async def status_mensageria(session: SessionDep, usuario: ViewDep) -> dict:
    settings = get_settings()
    agora = datetime.now(UTC)
    pendentes = (
        await session.execute(
            select(func.count())
            .select_from(MensagemSaida)
            .where(MensagemSaida.organizacao_id == usuario.organizacao_id, MensagemSaida.status == "pendente")
        )
    ).scalar_one()
    return {
        "ligado": settings.whatsapp_enabled,
        "provedor_configurado": obter_provedor() is not None,
        "pendentes": pendentes,
        "hoje": {
            finalidade: {
                "enviadas": await _enviadas_hoje(session, usuario.organizacao_id, finalidade, agora),
                "limite": _limite_diario(finalidade),
            }
            for finalidade in ("vigilancia", "prospeccao")
        },
    }


@router.get("/consentimentos")
async def listar_consentimentos(
    session: SessionDep,
    usuario: ViewDep,
    finalidade: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern=r"^(concedido|revogado)$"),
) -> list[dict]:
    consulta = select(ConsentimentoCanal).where(ConsentimentoCanal.organizacao_id == usuario.organizacao_id)
    if finalidade:
        consulta = consulta.where(ConsentimentoCanal.finalidade == finalidade)
    if status:
        consulta = consulta.where(ConsentimentoCanal.status == status)
    linhas = (await session.execute(consulta.order_by(ConsentimentoCanal.atualizado_em.desc()).limit(200))).scalars()
    return [_serializar_consentimento(c, usuario) for c in linhas]


@router.post("/consentimentos", status_code=201)
async def registrar(dados: ConsentimentoInput, session: SessionDep, usuario: ManageDep) -> dict:
    if dados.base_legal not in BASES_LEGAIS or dados.finalidade not in FINALIDADES:
        raise HTTPException(422, "Finalidade ou base legal inválida")
    try:
        registro = await registrar_consentimento(
            session,
            organizacao_id=usuario.organizacao_id,
            finalidade=dados.finalidade,
            telefone=dados.telefone,
            origem=dados.origem,
            registrado_por=usuario.email,
            base_legal=dados.base_legal,
            titular_tipo=dados.titular_tipo,
            titular_id=dados.titular_id,
            evidencia=dados.evidencia,
        )
    except ValueError as erro:
        raise HTTPException(422, str(erro)) from erro
    await session.flush()
    _auditar(session, usuario, "CONSENT_REGISTRAR", f"consentimento:{registro.id}", {"finalidade": dados.finalidade})
    await session.commit()
    return _serializar_consentimento(registro, usuario)


@router.post("/consentimentos/revogar")
async def revogar(dados: RevogacaoInput, session: SessionDep, usuario: ManageDep) -> dict:
    try:
        registro = await revogar_consentimento(
            session,
            organizacao_id=usuario.organizacao_id,
            finalidade=dados.finalidade,
            telefone=dados.telefone,
            origem=dados.origem,
            registrado_por=usuario.email,
        )
    except ValueError as erro:
        raise HTTPException(422, str(erro)) from erro
    await session.flush()
    _auditar(session, usuario, "CONSENT_REVOGAR", f"consentimento:{registro.id}", {"finalidade": dados.finalidade})
    await session.commit()
    return _serializar_consentimento(registro, usuario)


@router.get("/fila")
async def listar_fila(
    session: SessionDep,
    usuario: ViewDep,
    status: str | None = Query(default=None, pattern=r"^(pendente|enviando|enviada|incerta|falhou|bloqueada|cancelada)$"),
) -> list[dict]:
    consulta = select(MensagemSaida).where(MensagemSaida.organizacao_id == usuario.organizacao_id)
    if status:
        consulta = consulta.where(MensagemSaida.status == status)
    linhas = (await session.execute(consulta.order_by(MensagemSaida.criado_em.desc()).limit(100))).scalars()
    return [_serializar_mensagem(m, usuario) for m in linhas]


@router.post("/fila/{mensagem_id}/cancelar")
async def cancelar(mensagem_id: int, session: SessionDep, usuario: ManageDep) -> dict:
    mensagem = (
        await session.execute(
            select(MensagemSaida)
            .where(MensagemSaida.id == mensagem_id, MensagemSaida.organizacao_id == usuario.organizacao_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if mensagem is None:
        raise HTTPException(404, "Mensagem não encontrada")
    if mensagem.status != "pendente":
        raise HTTPException(409, "Só mensagens pendentes podem ser canceladas")
    mensagem.status = "cancelada"
    mensagem.motivo_bloqueio = "cancelada_manual"
    _auditar(session, usuario, "MENSAGEM_CANCELAR", f"mensagem:{mensagem.id}", {})
    await session.commit()
    return _serializar_mensagem(mensagem, usuario)


@router.post("/fila/cancelar-todas")
async def cancelar_todas_de_um_telefone(dados: RevogacaoInput, session: SessionDep, usuario: ManageDep) -> dict:
    """Cancela as pendentes de um telefone numa finalidade, sem mexer no consentimento."""
    from app.mensageria import normalizar_telefone

    numero = normalizar_telefone(dados.telefone)
    if numero is None or dados.finalidade not in FINALIDADES:
        raise HTTPException(422, "Telefone ou finalidade inválida")
    total = await _cancelar_pendentes(session, usuario.organizacao_id, dados.finalidade, numero)
    _auditar(session, usuario, "MENSAGEM_CANCELAR", f"telefone:{numero[-4:]}", {"canceladas": total})
    await session.commit()
    return {"canceladas": total}
