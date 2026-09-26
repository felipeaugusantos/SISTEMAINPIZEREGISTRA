"""Webhook unificado de pagamento e criação de cobrança Pix/boleto --
achado FASE7-1/2 da auditoria (04/09/2026).

Antes existiam DOIS handlers de webhook financeiro divergentes:
- app/api/contratacoes.py::receber_webhook_gateway (removido) -- baixava a
  parcela de verdade, mas só entendia o payload de EventoCobrancaSandbox.
- app/api/escritorio.py::receber_webhook (removido) -- só logava o evento
  em WebhookFinanceiro, nunca baixava nada.

Um único caminho agora, atrás da interface de adaptador (app/pagamentos.py):
verifica assinatura -> interpreta o evento (normalizado, independente do
PSP) -> loga em WebhookFinanceiro (idempotente por organizacao_id+referencia,
fonte única de verdade de eventos de pagamento) -> baixa a parcela e gera
comissão quando o evento é "pago". Nunca dispara cobrança real -- Pix/boleto
"reais" aguardam credenciais de um PSP (decisão registrada com o usuário).
"""

import hashlib
import json
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.financeiro import _atualizar_status, _cancelar_comissao_da_parcela, _gerar_comissao_se_aplicavel
from app.api.leads_propostas import sincronizar_pagamento_proposta_por_id
from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import LancamentoFinanceiro, ParcelaFinanceira, WebhookFinanceiro
from app.pagamentos import AdaptadorIndisponivelError, obter_adaptador
from app.tenancy import aplicar_contexto_tenant

router_webhook = APIRouter(prefix="/v1/webhooks/pagamentos", tags=["webhooks pagamentos"])
router_admin = APIRouter(prefix="/v1/admin/financeiro/pagamentos", tags=["financeiro"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.manage"))]


class CobrancaInput(BaseModel):
    parcela_id: int
    tipo: Literal["pix", "boleto"]


@router_webhook.post("/{adaptador}")
async def receber_webhook_pagamento(
    adaptador: str, request: Request, session: SessionDep, x_signature: str | None = Header(default=None)
) -> dict:
    try:
        adaptador_obj = obter_adaptador(adaptador)
    except AdaptadorIndisponivelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    corpo = await request.body()
    if not adaptador_obj.verificar_assinatura(corpo, x_signature):
        raise HTTPException(status_code=401, detail="Assinatura do webhook inválida")

    try:
        payload = json.loads(corpo)
        evento = adaptador_obj.interpretar_webhook(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # O ID do evento do PSP é preferido. No sandbox (ou em provedores sem ID),
    # o hash canônico distingue estados sucessivos da mesma cobrança e mantém
    # idempotência para reenvios do mesmo conteúdo.
    identidade_evento = evento.evento_id or hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if len(identidade_evento) > 220:
        raise HTTPException(status_code=422, detail="Identificador do evento inválido")
    chave_idempotencia = f"{adaptador}:{identidade_evento}"

    await aplicar_contexto_tenant(session, evento.organizacao_id)

    existente = (
        await session.execute(
            select(WebhookFinanceiro).where(
                WebhookFinanceiro.organizacao_id == evento.organizacao_id,
                WebhookFinanceiro.chave_idempotencia == chave_idempotencia,
            )
        )
    ).scalar_one_or_none()
    if existente is not None:
        return {"idempotente": True, "status": existente.status}

    session.add(
        WebhookFinanceiro(
            organizacao_id=evento.organizacao_id,
            referencia=evento.referencia,
            chave_idempotencia=chave_idempotencia,
            evento=f"pagamento.{evento.status}",
            # Persistir apenas campos normalizados necessários à auditoria;
            # não reter o corpo arbitrário do PSP (que pode conter PII).
            payload={"parcela_id": evento.parcela_id, "valor": str(evento.valor), "evento_id": evento.evento_id},
            status=evento.status,
        )
    )

    if evento.status == "pago":
        parcela = (
            await session.execute(
                select(ParcelaFinanceira)
                .where(
                    ParcelaFinanceira.id == evento.parcela_id,
                    ParcelaFinanceira.organizacao_id == evento.organizacao_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if parcela is None:
            raise HTTPException(status_code=404, detail="Parcela não encontrada")
        if parcela.lancamento.tipo != "receber":
            raise HTTPException(status_code=422, detail="Webhook de pagamento só pode baixar conta a receber")
        if parcela.status != "paga":
            if Decimal(parcela.valor) != evento.valor:
                raise HTTPException(status_code=422, detail="Valor do webhook diverge da parcela")
            parcela.valor_pago = evento.valor
            parcela.pago_em = datetime.now(UTC).date()
            parcela.status = "paga"
            await _atualizar_status(parcela.lancamento)
            await _gerar_comissao_se_aplicavel(session, parcela)
            if parcela.lancamento.proposta_id:
                await sincronizar_pagamento_proposta_por_id(
                    session, evento.organizacao_id, parcela.lancamento.proposta_id
                )
    elif evento.status == "estornado":
        # Achado FASE7-4 da auditoria (04/09/2026): o PSP também pode
        # notificar um estorno depois do "pago" -- reverte a baixa (mesma
        # lógica de app/api/financeiro.py::estornar, aplicada aqui porque o
        # estorno veio do próprio gateway, não de uma ação manual de
        # operador). Idempotente: se a parcela já não estava paga, não faz
        # nada (não existe o que reverter).
        parcela = (
            await session.execute(
                select(ParcelaFinanceira)
                .where(
                    ParcelaFinanceira.id == evento.parcela_id,
                    ParcelaFinanceira.organizacao_id == evento.organizacao_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if parcela is None:
            raise HTTPException(status_code=404, detail="Parcela não encontrada")
        if parcela.lancamento.tipo != "receber":
            raise HTTPException(status_code=422, detail="Webhook de pagamento só pode estornar conta a receber")
        if parcela.status == "paga":
            parcela.valor_pago, parcela.pago_em, parcela.forma_pagamento = Decimal(0), None, None
            parcela.forma_pagamento_id = None
            parcela.status = "aberta"
            await _atualizar_status(parcela.lancamento)
            await _cancelar_comissao_da_parcela(session, parcela.id)
            if parcela.lancamento.proposta_id:
                await sincronizar_pagamento_proposta_por_id(
                    session, evento.organizacao_id, parcela.lancamento.proposta_id
                )

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        repetido = (
            await session.execute(
                select(WebhookFinanceiro).where(
                    WebhookFinanceiro.organizacao_id == evento.organizacao_id,
                    WebhookFinanceiro.chave_idempotencia == chave_idempotencia,
                )
            )
        ).scalar_one_or_none()
        if repetido is not None:
            return {"idempotente": True, "status": repetido.status}
        raise
    return {"idempotente": False, "status": evento.status}


@router_admin.post("/{adaptador}/cobrancas", status_code=201)
async def criar_cobranca_pagamento(
    adaptador: str, dados: CobrancaInput, session: SessionDep, usuario: ManageDep
) -> dict:
    try:
        adaptador_obj = obter_adaptador(adaptador)
    except AdaptadorIndisponivelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    parcela = (
        await session.execute(
            select(ParcelaFinanceira)
            .join(LancamentoFinanceiro, LancamentoFinanceiro.id == ParcelaFinanceira.lancamento_id)
            .where(
                ParcelaFinanceira.id == dados.parcela_id,
                ParcelaFinanceira.organizacao_id == usuario.organizacao_id,
                LancamentoFinanceiro.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if parcela is None:
        raise HTTPException(status_code=404, detail="Parcela não encontrada")
    if parcela.lancamento.tipo != "receber":
        raise HTTPException(status_code=422, detail="Cobrança externa só pode ser criada para conta a receber")
    if parcela.status == "paga":
        raise HTTPException(status_code=409, detail="Parcela já está paga")

    cobranca = await adaptador_obj.criar_cobranca(
        tipo=dados.tipo,
        valor=Decimal(parcela.valor),
        referencia=f"parcela-{parcela.id}-{secrets.token_hex(4)}",
        descricao=f"Parcela {parcela.numero}",
        vencimento=parcela.vencimento,
    )
    return {
        "referencia_externa": cobranca.referencia_externa,
        "tipo": cobranca.tipo,
        "qr_code": cobranca.qr_code,
        "linha_digitavel": cobranca.linha_digitavel,
        "url_boleto": cobranca.url_boleto,
    }
