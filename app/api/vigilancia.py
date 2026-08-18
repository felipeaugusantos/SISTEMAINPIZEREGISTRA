import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal_cliente import ClientDep
from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import (
    ClientePortal,
    ColidenciaVigilancia,
    HistoricoAlertaVigilancia,
    Lead,
    PreferenciaVigilancia,
    Processo,
    VigilanciaExecucao,
)
from app.vigilancia import enfileirar_alerta

router = APIRouter(tags=["vigilancia preventiva"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]


class PreferenciaInput(BaseModel):
    frequencia: str = Field(default="semanal", pattern="^(semanal|diaria|mensal)$")
    classes_nice: list[str] = Field(default_factory=list, max_length=50)
    codigos_viena: list[str] = Field(default_factory=list, max_length=100)
    canais: list[str] = Field(default_factory=lambda: ["portal"], max_length=10)
    ativo: bool = True


class RevisaoInput(BaseModel):
    status: str = Field(pattern="^(aprovado|descartado)$")
    justificativa: str = Field(min_length=3, max_length=2000)


class FalsoPositivoInput(BaseModel):
    motivo: str = Field(min_length=3, max_length=2000)


def _preferencia_dict(item: PreferenciaVigilancia) -> dict:
    return {
        "id": item.id,
        "frequencia": item.frequencia,
        "classes_nice": item.classes_nice,
        "codigos_viena": item.codigos_viena,
        "canais": item.canais,
        "ativo": item.ativo,
    }


@router.get("/v1/portal/vigilancia/preferencias")
async def obter_preferencias(cliente: ClientDep, session: SessionDep) -> dict:
    item = (await session.execute(select(PreferenciaVigilancia).where(
        PreferenciaVigilancia.organizacao_id == cliente.organizacao_id,
        PreferenciaVigilancia.cliente_id == cliente.id,
    ))).scalar_one_or_none()
    if item is None:
        return {"preferencias": {"frequencia": "semanal", "classes_nice": [], "codigos_viena": [], "canais": ["portal"], "ativo": True}}
    return {"preferencias": _preferencia_dict(item)}


@router.put("/v1/portal/vigilancia/preferencias")
async def salvar_preferencias(dados: PreferenciaInput, cliente: ClientDep, session: SessionDep) -> dict:
    item = (await session.execute(select(PreferenciaVigilancia).where(
        PreferenciaVigilancia.organizacao_id == cliente.organizacao_id,
        PreferenciaVigilancia.cliente_id == cliente.id,
    ))).scalar_one_or_none()
    if item is None:
        item = PreferenciaVigilancia(organizacao_id=cliente.organizacao_id, cliente_id=cliente.id, **dados.model_dump())
        session.add(item)
    else:
        for campo, valor in dados.model_dump().items():
            setattr(item, campo, valor)
    await session.commit()
    return {"preferencias": _preferencia_dict(item)}


@router.get("/v1/portal/vigilancia/colidencias")
async def listar_colidencias_cliente(cliente: ClientDep, session: SessionDep) -> dict:
    itens = (await session.execute(select(ColidenciaVigilancia).where(
        ColidenciaVigilancia.organizacao_id == cliente.organizacao_id,
        ColidenciaVigilancia.cliente_id == cliente.id,
    ).order_by(ColidenciaVigilancia.criado_em.desc()))).scalars().all()
    return {"colidencias": [{"id": i.id, "processo_id": i.processo_id, "rpi_numero": i.rpi_numero, "score_risco": i.score_risco, "status": i.status, "falso_positivo": i.falso_positivo, "evidencias": i.evidencias, "justificativa": i.justificativa, "revisado_em": i.revisado_em, "comunicada_em": i.comunicada_em, "criado_em": i.criado_em} for i in itens]}


@router.post("/v1/admin/vigilancia/clientes/{cliente_id}/executar")
async def executar_vigilancia(cliente_id: int, session: SessionDep, operador: ManageDep) -> dict:
    cliente = (await session.execute(select(ClientePortal).where(
        ClientePortal.id == cliente_id, ClientePortal.organizacao_id == operador.organizacao_id,
    ))).scalar_one_or_none()
    if cliente is None:
        raise HTTPException(status_code=404, detail="Cliente nao encontrado")
    lead = await session.get(Lead, cliente.lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead do cliente nao encontrado")
    preferencia = (await session.execute(select(PreferenciaVigilancia).where(
        PreferenciaVigilancia.organizacao_id == cliente.organizacao_id,
        PreferenciaVigilancia.cliente_id == cliente.id,
        PreferenciaVigilancia.ativo.is_(True),
    ))).scalar_one_or_none()
    marca = (lead.marca or "").strip()
    termos = [t for t in re.findall(r"[\wÀ-ÿ]+", marca.lower()) if len(t) >= 3]
    if not termos:
        return {"encontradas": 0, "criadas": 0, "revisao_humana_obrigatoria": True}
    condicoes = [func.lower(Processo.titulo).contains(t) for t in termos]
    processos = (await session.execute(select(Processo).where(*condicoes).limit(100))).scalars().all()
    classes_configuradas = set(preferencia.classes_nice or []) if preferencia else set()
    viena_configurada = set(preferencia.codigos_viena or []) if preferencia else set()
    if classes_configuradas or viena_configurada:
        processos = [
            processo for processo in processos
            if (classes_configuradas and {c.codigo for c in processo.classificacoes if c.sistema == "nice"}.intersection(classes_configuradas))
            or (viena_configurada and {c.codigo for c in processo.classificacoes if c.sistema == "vienna"}.intersection(viena_configurada))
        ]
    criadas = 0
    for processo in processos:
        existente = (await session.execute(select(ColidenciaVigilancia).where(
            ColidenciaVigilancia.organizacao_id == cliente.organizacao_id,
            ColidenciaVigilancia.cliente_id == cliente.id,
            ColidenciaVigilancia.processo_id == processo.id,
        ))).scalar_one_or_none()
        if existente:
            continue
        nice = sorted({c.codigo for c in processo.classificacoes if c.sistema == "nice"}.intersection(classes_configuradas))
        viena = sorted({c.codigo for c in processo.classificacoes if c.sistema == "vienna"}.intersection(viena_configurada))
        score = min(99.0, 55.0 + 10.0 * len(termos) + (8.0 if nice else 0.0) + (8.0 if viena else 0.0))
        session.add(ColidenciaVigilancia(
            organizacao_id=cliente.organizacao_id,
            cliente_id=cliente.id,
            processo_id=processo.id,
            score_risco=score,
            evidencias={"regra": "vigilancia_preventiva", "marca_monitorada": marca, "processo": processo.numero, "titulo": processo.titulo, "termos_coincidentes": termos, "classes_nice": nice, "codigos_viena": viena},
            justificativa="Coincidencia identificada por nome e filtros configurados; requer revisao humana.",
        ))
        criadas += 1
    await session.commit()
    return {"encontradas": len(processos), "criadas": criadas, "revisao_humana_obrigatoria": True}


@router.patch("/v1/admin/vigilancia/colidencias/{colidencia_id}")
async def revisar_colidencia(colidencia_id: int, dados: RevisaoInput, request: Request, session: SessionDep, operador: ManageDep) -> dict:
    item = (await session.execute(select(ColidenciaVigilancia).where(
        ColidenciaVigilancia.id == colidencia_id,
        ColidenciaVigilancia.organizacao_id == operador.organizacao_id,
    ))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Colidencia nao encontrada")
    if not item.evidencias or not item.evidencias.get("regra"):
        raise HTTPException(status_code=409, detail="Colidencia sem regra e evidencias nao pode ser comunicada")
    status_anterior = item.status
    agora = datetime.now(UTC)
    item.status, item.revisado_por, item.revisado_em, item.justificativa = dados.status, operador.id, agora, dados.justificativa
    if dados.status == "aprovado":
        item.aprovado_por, item.aprovado_em = operador.id, agora
    preferencia = (await session.execute(select(PreferenciaVigilancia).where(
        PreferenciaVigilancia.organizacao_id == item.organizacao_id,
        PreferenciaVigilancia.cliente_id == item.cliente_id,
        PreferenciaVigilancia.ativo.is_(True),
    ))).scalar_one_or_none()
    comunicacao_liberada = dados.status == "aprovado" and preferencia is not None and bool(preferencia.canais)
    if comunicacao_liberada and status_anterior != "aprovado":
        canal = next((c for c in (preferencia.canais or []) if c in {"portal", "email", "whatsapp"}), None)
        if canal:
            await enfileirar_alerta(session, item, preferencia, canal)
        item.comunicada_em = agora
    await session.commit()
    return {"id": item.id, "status": item.status, "comunicacao_liberada": comunicacao_liberada}


@router.post("/v1/admin/vigilancia/colidencias/{colidencia_id}/comunicar")
async def comunicar_colidencia(colidencia_id: int, request: Request, session: SessionDep, operador: ManageDep) -> dict:
    item = (await session.execute(select(ColidenciaVigilancia).where(
        ColidenciaVigilancia.id == colidencia_id, ColidenciaVigilancia.organizacao_id == operador.organizacao_id
    ))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Colidencia nao encontrada")
    preferencia = (await session.execute(select(PreferenciaVigilancia).where(
        PreferenciaVigilancia.organizacao_id == item.organizacao_id,
        PreferenciaVigilancia.cliente_id == item.cliente_id,
        PreferenciaVigilancia.ativo.is_(True),
    ))).scalar_one_or_none()
    if preferencia is None:
        raise HTTPException(status_code=409, detail="Regra de vigilancia inativa ou inexistente")
    canais = [c for c in (preferencia.canais or []) if c in {"portal", "email", "whatsapp"}]
    if not canais:
        raise HTTPException(status_code=409, detail="Nenhum canal configurado")
    alertas = []
    for canal in canais:
        try:
            alertas.append(await enfileirar_alerta(session, item, preferencia, canal))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    item.comunicada_em = datetime.now(UTC)
    await session.commit()
    return {"id": item.id, "alertas": [{"id": a.id, "canal": a.canal, "status": a.status} for a in alertas]}


@router.post("/v1/admin/vigilancia/colidencias/{colidencia_id}/falso-positivo")
async def marcar_falso_positivo(colidencia_id: int, dados: FalsoPositivoInput, session: SessionDep, operador: ManageDep) -> dict:
    item = (await session.execute(select(ColidenciaVigilancia).where(
        ColidenciaVigilancia.id == colidencia_id, ColidenciaVigilancia.organizacao_id == operador.organizacao_id
    ))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Colidencia nao encontrada")
    agora = datetime.now(UTC)
    item.falso_positivo, item.falso_positivo_motivo = True, dados.motivo
    item.status, item.revisado_por, item.revisado_em, item.justificativa = "descartado", operador.id, agora, dados.motivo
    await session.commit()
    return {"id": item.id, "status": item.status, "falso_positivo": True}


@router.post("/v1/admin/vigilancia/executar-semanal")
async def executar_semanal(session: SessionDep, operador: ManageDep) -> dict:
    from app.vigilancia import executar_vigilancia_semanal
    return await executar_vigilancia_semanal(session, operador.organizacao_id)


@router.get("/v1/admin/vigilancia/relatorios")
async def relatorios_vigilancia(session: SessionDep, operador: ManageDep) -> dict:
    execucoes = (await session.execute(select(VigilanciaExecucao).where(
        VigilanciaExecucao.organizacao_id == operador.organizacao_id
    ).order_by(VigilanciaExecucao.iniciado_em.desc()).limit(52))).scalars().all()
    alertas = (await session.execute(select(HistoricoAlertaVigilancia).where(
        HistoricoAlertaVigilancia.organizacao_id == operador.organizacao_id
    ).order_by(HistoricoAlertaVigilancia.criado_em.desc()).limit(200))).scalars().all()
    return {"execucoes": [{"chave": e.chave, "status": e.status, "encontrados": e.encontrados, "criados": e.criados, "finalizado_em": e.finalizado_em} for e in execucoes],
            "historico_alertas": [{"id": a.id, "colidencia_id": a.colidencia_id, "canal": a.canal, "status": a.status, "criado_em": a.criado_em, "enviado_em": a.enviado_em} for a in alertas]}
