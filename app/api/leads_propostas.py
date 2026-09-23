"""Propostas comerciais por lead: criação, versionamento, status, pagamento,
protocolo, SLA, assinaturas, PDF e o fluxo público de aceite (link sem sessão
de operador).

Extraído de app/api/leads.py (Fase 3 da reanálise, 12/09/2026) -- domínio
autocontido o suficiente para viver em módulo próprio, reduzindo o arquivo
monólito de leads sem mudar nenhum comportamento. `_lead_da_org`,
`_pendencias_documentos` e `_documentacao_protocolavel` continuam em
app.api.leads porque também são usados pelas rotas de documentos de lá.
"""

import hashlib
import html
import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.leads import _auditar, _documentacao_protocolavel, _lead_da_org, _pendencias_documentos, _prazo_sla_24h
from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip, hash_token
from app.clicksign import configuracao as configuracao_clicksign
from app.clicksign import criar_envelope
from app.crm import aplicar_regras_automacao, avancar_fase_lead, registrar_evento_operacional
from app.database import get_session
from app.emailing import enviar_codigo_confirmacao_proposta, enviar_proposta_email
from app.models import (
    AssinaturaPropostaComercial,
    ContratacaoServico,
    DocumentoLead,
    EventoAuditoria,
    FaseLead,
    LancamentoFinanceiro,
    Lead,
    Organizacao,
    ParcelaFinanceira,
    PesquisaMarca,
    Processo,
    ProcessoMonitorado,
    PropostaComercial,
    TipoProcesso,
    UsuarioOperacoes,
)
from app.normalization import normalizar_numero_processo
from app.proxy import cliente_ip
from app.ratelimit import RateLimiter
from app.relatorios import gerar_pdf_proposta
from app.security_ext import revelar_segredo, validar_totp
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant

logger = logging.getLogger("ze_registra.leads_propostas")
router = APIRouter(tags=["leads"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LeadsViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
LeadsManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]

# Dupla validação do aceite de proposta (orientação jurídica, 15/09/2026):
# o clique no link público sozinho só prova posse do link -- um código de
# confirmação por e-mail (canal já cadastrado, nunca digitado nessa hora)
# é o segundo fator. Limitadores dedicados (mesma classe já usada em
# login/reset de senha, app/ratelimit.py) evitam força bruta no código e
# reenvio em excesso, escopados por proposta (não por IP -- o cliente pode
# estar em rede compartilhada/móvel).
CODIGO_CONFIRMACAO_MINUTOS = 15
CODIGO_CONFIRMACAO_TENTATIVAS_MAXIMAS = 5
_LIMITADOR_REENVIO_CODIGO_PROPOSTA = RateLimiter(limite=1, janela_segundos=60, escopo="proposta-codigo-reenvio")


def _gerar_codigo_confirmacao() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _pagina_codigo(token: str, *, aviso: str | None = None) -> HTMLResponse:
    aviso_html = f"<p class='aviso'>{html.escape(aviso)}</p>" if aviso else ""
    return HTMLResponse(
        f"""<!doctype html><html lang='pt-BR'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Confirme o aceite</title><style>body{{font:16px Arial;color:#17231c;background:#f5f7f5;margin:0;padding:24px}}main{{max-width:480px;margin:auto;background:white;padding:36px;border-radius:18px;border:1px solid #d8ddd6}}h1{{font-family:Georgia,serif;font-size:26px}}.muted{{color:#5b665f}}.aviso{{color:#a33128;font-weight:700}}.button{{display:inline-block;background:#086044;color:#fff;padding:13px 20px;border-radius:9px;text-decoration:none;border:0;font-weight:700;cursor:pointer;width:100%}}input{{width:100%;box-sizing:border-box;padding:13px;font-size:20px;letter-spacing:4px;text-align:center;border:1px solid #d8ddd6;border-radius:9px;margin-bottom:14px}}</style>
        <main><h1>Confirme o aceite</h1><p class='muted'>Enviamos um código de 6 dígitos para o seu e-mail cadastrado. Ele vale por {CODIGO_CONFIRMACAO_MINUTOS} minutos.</p>
        {aviso_html}
        <form method='post' action='/propostas/{token}/confirmar'><input name='codigo' inputmode='numeric' maxlength='6' placeholder='000000' autofocus required><button class='button' type='submit'>Confirmar aceite</button></form>
        <form method='post' action='/propostas/{token}/aceitar'><button class='button' type='submit' style='background:transparent;color:#086044;border:1px solid #086044'>Reenviar código</button></form>
        </main></html>"""
    )


def _pagina_aceite_confirmado() -> HTMLResponse:
    return HTMLResponse(
        "<h1>Proposta aceita</h1><p>Recebemos seu aceite. Nossa equipe dará continuidade ao atendimento.</p>"
    )


class PropostaInput(BaseModel):
    pesquisa_id: str | None = Field(default=None, min_length=36, max_length=36)
    pesquisa_ids: list[str] = Field(default_factory=list, max_length=50)
    validade_em: date | None = None
    marca: str | None = Field(default=None, max_length=4000)
    classes: str | None = Field(default=None, max_length=4000)
    escopo: str = Field(default="Registro de marca no INPI", min_length=5, max_length=4000)
    honorarios: Decimal | None = Field(default=None, ge=0)
    taxa_gru: Decimal | None = Field(default=None, ge=0)
    condicoes_pagamento: str | None = Field(default=None, max_length=2000)
    observacoes: str | None = Field(default=None, max_length=4000)


# Valores padrão do rascunho comercial quando a proposta é criada diretamente
# pelo funil, sem interromper o usuário para preencher um formulário.
HONORARIOS_PROPOSTA_PADRAO = Decimal("1500.00")
TAXA_GRU_PROPOSTA_PADRAO = Decimal("415.00")
CONDICOES_PROPOSTA_PADRAO = "50% na contratação e 50% no protocolo"


def _resumir_pesquisas_proposta(pesquisas: list[PesquisaMarca]) -> tuple[str | None, str | None]:
    """Consolida marcas/classes escolhidas sem perder o vínculo detalhado em ``dados``."""
    por_marca: dict[str, dict[str, object]] = {}
    for pesquisa in pesquisas:
        chave = pesquisa.marca.strip().casefold()
        grupo = por_marca.setdefault(chave, {"marca": pesquisa.marca.strip(), "classes": []})
        classes = grupo["classes"]
        if pesquisa.classe_nice and pesquisa.classe_nice not in classes:
            classes.append(pesquisa.classe_nice)
    if not por_marca:
        return None, None
    marcas = [str(grupo["marca"]) for grupo in por_marca.values()]
    if len(marcas) == 1:
        classes = sorted(por_marca[next(iter(por_marca))]["classes"], key=int)
        return marcas[0], ", ".join(classes) if classes else None
    resumo_classes = []
    for grupo in por_marca.values():
        classes = sorted(grupo["classes"], key=int)
        sufixo = f"NCL {', '.join(classes)}" if classes else "todas as classes"
        resumo_classes.append(f"{grupo['marca']}: {sufixo}")
    return "; ".join(marcas), "; ".join(resumo_classes)


class PropostaStatusInput(BaseModel):
    status: Literal["rascunho", "enviada", "visualizada", "aceita", "recusada", "expirada", "cancelada"]
    motivo: str | None = Field(default=None, max_length=500)
    # Achado do usuário (23/09/2026): "Registrar aceite" no admin mudava o
    # status pra "aceita" e disparava toda a automação (contratação, SLA,
    # avanço de fase) sem nenhuma evidência de que o cliente realmente
    # aceitou -- só um log de auditoria genérico, diferente dos fluxos com
    # o próprio cliente (link público/portal), que exigem segundo fator e
    # gravam AssinaturaPropostaComercial. Reaproveita o MFA que o operador
    # já usa pra logar (TOTP do Google Authenticator) como prova de que
    # essa pessoa específica, autenticada, está afirmando o aceite.
    codigo_mfa: str | None = Field(default=None, min_length=6, max_length=20)


# Achado 8 do plano proposta-financeiro (Fase 2, 03/09/2026): antes o status
# aceitava qualquer valor do Literal incondicionalmente -- uma proposta aceita
# podia voltar para rascunho, ou um estado terminal (recusada/expirada/
# cancelada) podia "reviver". Mapa de transições permitidas a partir de cada
# status atual; manter o mesmo status é sempre permitido (idempotente).
TRANSICOES_STATUS_PROPOSTA: dict[str, set[str]] = {
    "rascunho": {"enviada", "cancelada"},
    "enviada": {"visualizada", "aceita", "recusada", "expirada", "cancelada"},
    "visualizada": {"aceita", "recusada", "expirada", "cancelada"},
    "aceita": {"cancelada"},
    "recusada": set(),
    "expirada": set(),
    "cancelada": set(),
}


class PropostaProtocoloInput(BaseModel):
    responsavel_protocolo_id: int
    protocolo_numero: str | None = Field(default=None, max_length=80)
    comprovante_id: int | None = None
    motivo_atraso: str | None = Field(default=None, max_length=2000)


def _atualizar_sla_proposta(proposta: PropostaComercial, agora: datetime | None = None) -> str:
    agora = agora or datetime.now(UTC)
    if proposta.protocolo_em:
        proposta.sla_status = "protocolado"
    elif proposta.status != "aceita":
        proposta.sla_status = "aguardando_aceite"
    elif proposta.pagamento_status != "confirmado":
        proposta.sla_status = "aguardando_pagamento"
    elif not proposta.sla_inicio_em:
        proposta.sla_status = "aguardando_documentos"
    elif proposta.sla_prazo_em and agora > proposta.sla_prazo_em:
        proposta.sla_status = "vencido"
    else:
        proposta.sla_status = "em_prazo"
    return proposta.sla_status


# --- Propostas comerciais por lead ----------------------------------------


def _proposta_dict(proposta: PropostaComercial, org: Organizacao | None = None) -> dict:
    branding = (org.branding or {}) if org else {}
    return {
        "id": proposta.id,
        "lead_id": proposta.lead_id,
        "pesquisa_id": proposta.pesquisa_id,
        "numero": proposta.numero,
        "versao": proposta.versao,
        "status": proposta.status,
        "validade_em": proposta.validade_em,
        "marca": proposta.marca,
        "classes": proposta.classes,
        "escopo": proposta.escopo,
        "honorarios": proposta.honorarios,
        "taxa_gru": proposta.taxa_gru,
        "total": (proposta.honorarios or 0) + (proposta.taxa_gru or 0),
        "condicoes_pagamento": proposta.condicoes_pagamento,
        "observacoes": proposta.observacoes,
        "pesquisas": (proposta.dados or {}).get("pesquisas") or [],
        "cliente": {"nome": (proposta.dados or {}).get("cliente") or "cliente"},
        "enviado_em": proposta.enviado_em,
        "aceito_em": proposta.aceito_em,
        "public_aceito_ip_registrado": bool(proposta.public_aceito_ip_hash),
        "pagamento_status": proposta.pagamento_status,
        "pagamento_confirmado_em": proposta.pagamento_confirmado_em,
        "pagamento_confirmado_por": proposta.pagamento_confirmado_por,
        "sla_inicio_em": proposta.sla_inicio_em,
        "sla_prazo_em": proposta.sla_prazo_em,
        "sla_status": _atualizar_sla_proposta(proposta),
        "responsavel_protocolo_id": proposta.responsavel_protocolo_id,
        "protocolo_numero": proposta.protocolo_numero,
        "protocolo_em": proposta.protocolo_em,
        "protocolo_motivo_atraso": proposta.protocolo_motivo_atraso,
        "protocolo_comprovante_id": proposta.protocolo_comprovante_id,
        "juridico_recebido_em": proposta.juridico_recebido_em,
        "juridico_recebido_por_id": proposta.juridico_recebido_por_id,
        "juridico_recebido_por": proposta.juridico_recebido_por,
        "criado_em": proposta.criado_em,
        # Propostas exibem somente o nome fantasia institucional.
        "empresa": {"nome": "Zé Registra"},
        "configuracao": branding.get("proposta") or {},
    }


@router.get("/v1/admin/leads/{lead_id}/propostas")
async def listar_propostas(lead_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    lead = await _lead_da_org(session, lead_id, usuario.organizacao_id)
    org = await session.get(Organizacao, usuario.organizacao_id)
    itens = (
        (
            await session.execute(
                select(PropostaComercial)
                .where(
                    PropostaComercial.lead_id == lead,
                    PropostaComercial.organizacao_id == usuario.organizacao_id,
                )
                .order_by(desc(PropostaComercial.criado_em))
            )
        )
        .scalars()
        .all()
    )
    return {"propostas": [_proposta_dict(item, org) for item in itens]}


@router.post("/v1/admin/leads/{lead_id}/propostas", status_code=status.HTTP_201_CREATED)
async def criar_proposta(lead_id: int, dados: PropostaInput, session: SessionDep, usuario: LeadsManageDep) -> dict:
    lead = await _lead_da_org(session, lead_id, usuario.organizacao_id)
    lead_obj = (
        await session.execute(select(Lead).where(Lead.id == lead, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one()
    ids = list(dict.fromkeys([item for item in dados.pesquisa_ids if item]))
    if dados.pesquisa_id and dados.pesquisa_id not in ids:
        ids.insert(0, dados.pesquisa_id)
    pesquisa_query = select(PesquisaMarca).where(
        PesquisaMarca.lead_id == lead,
        PesquisaMarca.organizacao_id == usuario.organizacao_id,
    )
    if ids:
        pesquisa_query = pesquisa_query.where(PesquisaMarca.id.in_(ids))
    pesquisas = list((await session.execute(pesquisa_query.order_by(PesquisaMarca.criado_em.desc()))).scalars())
    if ids and len(pesquisas) != len(ids):
        raise HTTPException(
            status_code=422,
            detail="A pesquisa informada não pertence a esta oportunidade.",
        )
    pesquisa = pesquisas[0] if pesquisas else None
    marcas_resumo, classes_resumo = _resumir_pesquisas_proposta(pesquisas)
    proposta = PropostaComercial(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead,
        pesquisa_id=pesquisa.id if pesquisa else None,
        numero="TEMP",
        validade_em=dados.validade_em,
        marca=dados.marca or marcas_resumo,
        classes=dados.classes or classes_resumo,
        escopo=dados.escopo.strip(),
        honorarios=dados.honorarios if dados.honorarios is not None else HONORARIOS_PROPOSTA_PADRAO,
        taxa_gru=dados.taxa_gru if dados.taxa_gru is not None else TAXA_GRU_PROPOSTA_PADRAO,
        condicoes_pagamento=dados.condicoes_pagamento or CONDICOES_PROPOSTA_PADRAO,
        observacoes=dados.observacoes,
        criado_por=usuario.id,
        dados={
            "cliente": lead_obj.nome,
            "email": lead_obj.email,
            "pesquisas": [{"id": item.id, "marca": item.marca, "classes": item.classe_nice} for item in pesquisas],
            "protocolo_prazo": "24 horas úteis",
        },
    )
    session.add(proposta)
    await session.flush()
    proposta.numero = f"PROP-{datetime.now(UTC).year}-{proposta.id:06d}"
    await session.commit()
    org = await session.get(Organizacao, usuario.organizacao_id)
    return _proposta_dict(proposta, org)


@router.post("/v1/admin/propostas/{proposta_id}/nova-versao", status_code=status.HTTP_201_CREATED)
async def criar_nova_versao_proposta(
    proposta_id: int,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    anterior = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    # Achado 9 do plano proposta-financeiro (Fase 5, 03/09/2026): nova versão
    # mantém o mesmo número-base da proposta anterior -- só ``versao`` avança.
    # Antes cada versão ganhava um número novo, apesar de já existir o campo
    # ``versao`` para isso. Ver migration fk91l2m3n529 (unicidade passou a
    # incluir a versão).
    nova = PropostaComercial(
        organizacao_id=anterior.organizacao_id,
        lead_id=anterior.lead_id,
        pesquisa_id=anterior.pesquisa_id,
        numero=anterior.numero,
        versao=anterior.versao + 1,
        status="rascunho",
        validade_em=anterior.validade_em,
        marca=anterior.marca,
        classes=anterior.classes,
        escopo=anterior.escopo,
        honorarios=anterior.honorarios,
        taxa_gru=anterior.taxa_gru,
        condicoes_pagamento=anterior.condicoes_pagamento,
        observacoes=anterior.observacoes,
        dados=dict(anterior.dados or {}),
        criado_por=usuario.id,
    )
    session.add(nova)
    await session.flush()
    _auditar(
        session,
        usuario,
        request,
        "nova_versao_proposta",
        f"proposta:{nova.id}",
        {"origem_id": anterior.id, "versao": nova.versao},
    )
    await session.commit()
    return _proposta_dict(nova, await session.get(Organizacao, usuario.organizacao_id))


@router.patch("/v1/admin/propostas/{proposta_id}/status")
async def atualizar_status_proposta(
    proposta_id: int,
    dados: PropostaStatusInput,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    if dados.status != proposta.status and dados.status not in TRANSICOES_STATUS_PROPOSTA.get(
        proposta.status, set()
    ):
        raise HTTPException(
            status_code=422,
            detail=f"Não é possível mudar o status de '{proposta.status}' para '{dados.status}'",
        )
    if dados.status == "cancelada" and not (dados.motivo and dados.motivo.strip()):
        raise HTTPException(status_code=422, detail="Informe o motivo do cancelamento")
    if dados.status == "aceita" and dados.status != proposta.status:
        # "Registrar aceite" manual precisa de prova de que foi essa pessoa,
        # autenticada, que confirmou o aceite -- mesmo TOTP que o operador já
        # usa pra logar (achado do usuário, 23/09/2026).
        if not usuario.mfa_ativo:
            raise HTTPException(
                status_code=422,
                detail="Configure o autenticador (MFA) no seu perfil para registrar aceite manual de proposta.",
            )
        codigo_mfa = (dados.codigo_mfa or "").strip()
        if not codigo_mfa:
            raise HTTPException(
                status_code=401, detail="Informe o código do seu autenticador para confirmar o aceite."
            )
        operador = await session.get(UsuarioOperacoes, usuario.id)
        if operador is None or not operador.mfa_segredo or not validar_totp(
            revelar_segredo(operador.mfa_segredo), codigo_mfa
        ):
            raise HTTPException(status_code=401, detail="Código do autenticador inválido.")
    agora = datetime.now(UTC)
    proposta.status = dados.status
    if dados.status == "cancelada":
        proposta.dados = {
            **(proposta.dados or {}),
            "cancelamento": {"motivo": dados.motivo.strip(), "em": agora.isoformat(), "por": usuario.ator},
        }
    if dados.status == "enviada":
        proposta.enviado_em = agora
        lead = (
            await session.execute(
                select(Lead).where(Lead.id == proposta.lead_id, Lead.organizacao_id == usuario.organizacao_id)
            )
        ).scalar_one_or_none()
        if lead and lead.fase:
            await avancar_fase_lead(session, lead, "proposta_enviada", usuario.nome or "sistema")
            await aplicar_regras_automacao(session, lead, "fase", "proposta_enviada", usuario.nome or "sistema")
    elif dados.status == "aceita":
        proposta.aceito_em = agora
        proposta.sla_status = "aguardando_pagamento"
        # Mesmo fluxo de evidência do aceite pelo cliente (link público/
        # portal): grava public_aceito_em/IP e uma AssinaturaPropostaComercial,
        # só que com segundo_fator_canal="totp" (código do operador, não
        # e-mail do cliente) -- sem isso, um aceite "manual" ficava
        # indistinguível de uma mudança de status qualquer.
        if proposta.public_aceito_em is None:
            proposta.public_aceito_em = agora
            proposta.public_aceito_ip_hash = hash_ip(cliente_ip(request))
        await criar_contratacao_automatica_proposta(session, proposta, "admin")
        assinatura_hash = hashlib.sha256(
            "|".join(
                str(valor or "")
                for valor in (
                    proposta.numero,
                    proposta.versao,
                    proposta.marca,
                    proposta.classes,
                    proposta.escopo,
                    proposta.honorarios,
                    proposta.taxa_gru,
                    proposta.condicoes_pagamento,
                )
            ).encode("utf-8")
        ).hexdigest()
        try:
            async with session.begin_nested():
                session.add(
                    AssinaturaPropostaComercial(
                        organizacao_id=proposta.organizacao_id,
                        proposta_id=proposta.id,
                        versao=proposta.versao,
                        hash_documento=assinatura_hash,
                        ip_hash=proposta.public_aceito_ip_hash,
                        provedor="admin",
                        segundo_fator_canal="totp",
                        segundo_fator_confirmado_em=agora,
                    )
                )
                await session.flush()
        except IntegrityError:
            pass
        lead = (
            await session.execute(
                select(Lead).where(Lead.id == proposta.lead_id, Lead.organizacao_id == usuario.organizacao_id)
            )
        ).scalar_one_or_none()
        if lead and lead.fase:
            await avancar_fase_lead(session, lead, "proposta_aceita", usuario.nome or "sistema")
            await aplicar_regras_automacao(session, lead, "fase", "proposta_aceita", usuario.nome or "sistema")
    _auditar(
        session,
        usuario,
        request,
        "status_proposta",
        f"proposta:{proposta.id}",
        {"status": dados.status, "motivo": dados.motivo} if dados.motivo else {"status": dados.status},
    )
    await session.commit()
    org = await session.get(Organizacao, usuario.organizacao_id)
    return _proposta_dict(proposta, org)


async def _proposta_da_org(session: AsyncSession, proposta_id: int, organizacao_id: int) -> PropostaComercial:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    return proposta


async def calcular_pagamento_status_proposta(session: AsyncSession, organizacao_id: int, proposta_id: int) -> str:
    """Deriva o status de pagamento a partir dos ``LancamentoFinanceiro``
    vinculados à proposta -- nunca é atribuído livremente.

    Achado 1/3 do plano proposta-financeiro (Fase 3, 03/09/2026): antes
    ``pagamento_status`` era setado direto via PATCH, sem nenhuma baixa
    financeira correspondente. O financeiro passa a ser a fonte de verdade.
    """
    status_lancamentos = (
        (
            await session.execute(
                select(LancamentoFinanceiro.status).where(
                    LancamentoFinanceiro.organizacao_id == organizacao_id,
                    LancamentoFinanceiro.proposta_id == proposta_id,
                )
            )
        )
        .scalars()
        .all()
    )
    ativos = [status for status in status_lancamentos if status != "cancelado"]
    if not ativos:
        return "cancelado" if status_lancamentos else "pendente"
    if all(status == "pago" for status in ativos):
        return "confirmado"
    if any(status in ("pago", "parcial") for status in ativos):
        return "parcial"
    return "pendente"


async def sincronizar_pagamento_proposta(session: AsyncSession, proposta: PropostaComercial) -> None:
    """Recalcula ``pagamento_status`` da proposta a partir do financeiro e
    reflete a mudança no SLA. Chamado após qualquer baixa/estorno/cancelamento
    de um lançamento vinculado, e sob demanda via ``PATCH .../pagamento``."""
    novo_status = await calcular_pagamento_status_proposta(session, proposta.organizacao_id, proposta.id)
    status_anterior = proposta.pagamento_status
    if novo_status == status_anterior and novo_status != "confirmado":
        return
    proposta.pagamento_status = novo_status
    proposta.pagamento_confirmado_em = (
        proposta.pagamento_confirmado_em or datetime.now(UTC) if novo_status == "confirmado" else None
    )
    if novo_status != "confirmado":
        proposta.pagamento_confirmado_por_id = None
        proposta.pagamento_confirmado_por = None
        proposta.pagamento_confirmado_ip_hash = None
    documentos_ok = await _documentacao_protocolavel(session, proposta)
    if (
        novo_status == "confirmado"
        and proposta.status == "aceita"
        and not proposta.sla_inicio_em
        and documentos_ok
    ):
        proposta.sla_inicio_em = proposta.pagamento_confirmado_em
        proposta.sla_prazo_em = _prazo_sla_24h(proposta.sla_inicio_em)
    if novo_status == "confirmado" and proposta.status == "aceita" and not documentos_ok:
        proposta.sla_status = "aguardando_documentos"
    _atualizar_sla_proposta(proposta)
    if novo_status == "confirmado" and proposta.status == "aceita":
        lead = (
            await session.execute(
                select(Lead).where(
                    Lead.id == proposta.lead_id,
                    Lead.organizacao_id == proposta.organizacao_id,
                )
            )
        ).scalar_one_or_none()
        if lead is not None:
            mudou_fase = await avancar_fase_lead(
                session, lead, FaseLead.PAGAMENTO_CONFIRMADO.value, "Financeiro"
            )
            if status_anterior != "confirmado" or mudou_fase:
                registrar_evento_operacional(
                    session,
                    organizacao_id=proposta.organizacao_id,
                    dominio="juridico",
                    tipo="juridico.encaminhamento_disponivel",
                    entidade_tipo="lead",
                    entidade_id=proposta.lead_id,
                    ator="Financeiro",
                    payload={
                        "proposta_id": proposta.id,
                        "descricao": "Pagamento confirmado; serviço disponível para recebimento jurídico",
                    },
                )


async def sincronizar_pagamento_proposta_por_id(
    session: AsyncSession, organizacao_id: int, proposta_id: int
) -> None:
    """Wrapper para chamar de fora deste módulo (ex.: financeiro.py) sem
    precisar carregar a proposta antes."""
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is not None:
        await sincronizar_pagamento_proposta(session, proposta)


async def criar_contratacao_automatica_proposta(session: AsyncSession, proposta: PropostaComercial, origem: str) -> None:
    """Gera a contratação financeira do aceite (``ContratacaoServico`` +
    ``LancamentoFinanceiro`` + 1 parcela), usando o valor ASSINADO da
    proposta (honorários + taxa GRU) -- nunca um preço de catálogo.

    Achados 4 e 5 do plano proposta-financeiro (Fase 4, 03/09/2026):
    ``POST /financeiro/contratacoes`` sempre usava ``ServicoFinanceiro.valor``,
    e nada impedia duas contratações para a mesma proposta. Idempotente (não
    cria uma segunda linha se já existir uma para esta proposta) e protegido
    também por constraint de unicidade em ``contratacoes_servicos.proposta_id``.
    A condição de pagamento é texto livre, não estruturado -- gera 1 parcela
    à vista pelo valor total; o operador pode reparcelar manualmente em
    ``app/api/financeiro.py`` quando a condição combinada exigir isso.
    """
    total = (proposta.honorarios or 0) + (proposta.taxa_gru or 0)
    if total <= 0:
        # Achado médio da auditoria financeira (15/09/2026): sem isto, uma
        # proposta aceita com honorarios+taxa_gru <= 0 nao deixava nenhum
        # rastro -- nao dava pra diferenciar depois um servico
        # legitimamente gratuito de um erro de preenchimento de valor. Não
        # bloqueia o aceite (pode ser legítimo); só torna o caso auditável.
        registrar_evento_operacional(
            session,
            organizacao_id=proposta.organizacao_id,
            dominio="financeiro",
            tipo="financeiro.proposta_sem_valor",
            entidade_tipo="lead",
            entidade_id=proposta.lead_id,
            ator=f"Aceite via {origem}",
            payload={
                "proposta_id": proposta.id,
                "honorarios": str(proposta.honorarios or 0),
                "taxa_gru": str(proposta.taxa_gru or 0),
                "descricao": "Proposta aceita sem valor (honorarios+taxa_gru <= 0) -- nenhum lançamento financeiro foi gerado",
            },
        )
        return
    existente = (
        await session.execute(
            select(ContratacaoServico.id).where(
                ContratacaoServico.organizacao_id == proposta.organizacao_id,
                ContratacaoServico.proposta_id == proposta.id,
            )
        )
    ).scalar_one_or_none()
    if existente is None:
        lancamento = LancamentoFinanceiro(
            organizacao_id=proposta.organizacao_id,
            lead_id=proposta.lead_id,
            proposta_id=proposta.id,
            idempotency_key=f"proposta-aceite:{proposta.id}",
            tipo="receber",
            descricao=f"Honorários — Proposta {proposta.numero}",
            competencia=date.today(),
            valor_total=total,
            status="aberto",
            criado_por=f"aceite:{origem}",
        )
        # Achado alto da auditoria financeira (15/09/2026): o SELECT acima
        # (linha "existente = ...") e o INSERT abaixo nao sao atomicos --
        # duas aceitacoes quase simultaneas da mesma proposta (ex.: duplo
        # clique no link publico) passavam as duas pelo "existente is None"
        # e a segunda estourava IntegrityError sem tratamento (a proteção de
        # última linha é a UniqueConstraint em contratacoes_servicos.proposta_id).
        # begin_nested() (SAVEPOINT) permite capturar só esse conflito sem
        # descartar o resto da transação em andamento (ex.: proposta.status
        # já alterado pelo chamador) -- ROLLBACK simples descartaria tudo.
        try:
            async with session.begin_nested():
                session.add(lancamento)
                await session.flush()
                session.add(
                    ParcelaFinanceira(
                        organizacao_id=proposta.organizacao_id,
                        lancamento_id=lancamento.id,
                        numero=1,
                        vencimento=date.today(),
                        valor=total,
                    )
                )
                session.add(
                    ContratacaoServico(
                        organizacao_id=proposta.organizacao_id,
                        lead_id=proposta.lead_id,
                        proposta_id=proposta.id,
                        lancamento_id=lancamento.id,
                    )
                )
                registrar_evento_operacional(
                    session,
                    organizacao_id=proposta.organizacao_id,
                    dominio="financeiro",
                    tipo="financeiro.proposta_recebida",
                    entidade_tipo="lead",
                    entidade_id=proposta.lead_id,
                    ator=f"Aceite via {origem}",
                    payload={
                        "proposta_id": proposta.id,
                        "lancamento_id": lancamento.id,
                        "valor": str(total),
                        "descricao": "Proposta aceita enviada automaticamente ao contas a receber",
                    },
                )
                await session.flush()
        except IntegrityError:
            # Outra requisicao concorrente ja criou a contratacao para esta
            # proposta -- idempotente por design (mesmo comportamento do
            # caminho "existente is not None" acima), nao é erro do operador.
            pass

    # A contratação é a passagem objetiva do comercial para o financeiro.
    # Centralizar as fases aqui cobre aceite administrativo, link público,
    # portal e Clicksign com o mesmo comportamento e sem regressão de fase.
    lead = (
        await session.execute(
            select(Lead).where(
                Lead.id == proposta.lead_id,
                Lead.organizacao_id == proposta.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if lead is not None:
        ator = f"Aceite via {origem}"
        await avancar_fase_lead(session, lead, FaseLead.PROPOSTA_ACEITA.value, ator)
        await avancar_fase_lead(session, lead, FaseLead.AGUARDANDO_PAGAMENTO.value, ator)


@router.patch("/v1/admin/propostas/{proposta_id}/pagamento")
async def atualizar_pagamento_proposta(
    proposta_id: int,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    """Recalcula ``pagamento_status`` a partir do financeiro vinculado à
    proposta -- não aceita mais um status arbitrário no corpo da requisição.

    Achado 1/3 do plano proposta-financeiro (Fase 3): registre a baixa em
    ``POST /v1/admin/financeiro/parcelas/{id}/baixar`` (com o lançamento
    apontando para esta proposta); esse endpoint só reflete o resultado.
    """
    proposta = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    anterior = proposta.pagamento_status
    await sincronizar_pagamento_proposta(session, proposta)
    _auditar(
        session,
        usuario,
        request,
        "pagamento_proposta",
        f"proposta:{proposta.id}",
        {"status_anterior": anterior, "status_atual": proposta.pagamento_status},
    )
    await session.commit()
    return _proposta_dict(proposta, await session.get(Organizacao, usuario.organizacao_id))


async def _tentar_vincular_processo_ao_protocolar(
    session: AsyncSession, organizacao_id: int, lead_id: int, numero_protocolo: str
) -> None:
    """Achado "Ruptura 1" da auditoria completa do CRM (06/09/2026): o número
    do protocolo digitado aqui era o mesmo que precisava ser digitado de
    novo depois na carteira só para vincular o processo ao lead. Propaga
    para Lead.processo_numero (fonte que a carteira já usa para casar
    processo→lead automaticamente, ver _leads_por_numero_processo em
    app/api/carteira.py) e tenta vincular na hora -- best effort, já que a
    RPI normalmente ainda não publicou o processo neste momento; o caso
    comum continua sendo resolvido depois, na carteira, já sem precisar
    digitar o número de novo."""
    numero_normalizado = normalizar_numero_processo(numero_protocolo)
    if not numero_normalizado:
        return
    processo = (
        await session.execute(
            select(Processo).where(
                Processo.numero_normalizado == numero_normalizado, Processo.tipo == TipoProcesso.MARCA
            )
        )
    ).scalar_one_or_none()
    if processo is None:
        return
    existente = (
        await session.execute(
            select(ProcessoMonitorado).where(
                ProcessoMonitorado.organizacao_id == organizacao_id,
                ProcessoMonitorado.processo_id == processo.id,
            )
        )
    ).scalar_one_or_none()
    if existente is None:
        session.add(
            ProcessoMonitorado(
                organizacao_id=organizacao_id,
                processo_id=processo.id,
                lead_id=lead_id,
                status="ativo",
                origem="protocolo_proposta",
                vinculado_por="sistema (protocolo da proposta)",
            )
        )
    elif existente.lead_id is None:
        existente.lead_id = lead_id


@router.patch("/v1/admin/propostas/{proposta_id}/protocolo")
async def registrar_protocolo_proposta(
    proposta_id: int,
    dados: PropostaProtocoloInput,
    request: Request,
    session: SessionDep,
    usuario: LeadsManageDep,
) -> dict:
    proposta = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    responsavel = (
        await session.execute(
            select(UsuarioOperacoes).where(
                UsuarioOperacoes.id == dados.responsavel_protocolo_id,
                UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if responsavel is None:
        raise HTTPException(status_code=422, detail="Responsável pelo protocolo inválido")
    numero = (dados.protocolo_numero or "").strip() or None
    motivo = (dados.motivo_atraso or "").strip() or None
    agora = datetime.now(UTC)
    if not numero and not motivo:
        raise HTTPException(status_code=422, detail="Informe o número do protocolo ou o motivo do atraso")
    if numero:
        if proposta.status != "aceita" or proposta.pagamento_status != "confirmado":
            raise HTTPException(
                status_code=422,
                detail="O protocolo exige proposta aceita e pagamento confirmado.",
            )
        pendencias = await _pendencias_documentos(session, proposta)
        if pendencias:
            raise HTTPException(
                status_code=422,
                detail=f"Existem documentos pendentes: {', '.join(pendencias)}.",
            )
        if dados.comprovante_id is None:
            raise HTTPException(status_code=422, detail="Anexe o comprovante do protocolo.")
        if proposta.sla_prazo_em and agora > proposta.sla_prazo_em and not motivo:
            raise HTTPException(
                status_code=422,
                detail="Informe o motivo obrigatório do atraso antes de registrar o protocolo.",
            )
    if dados.comprovante_id is not None:
        comprovante = (
            await session.execute(
                select(DocumentoLead).where(
                    DocumentoLead.id == dados.comprovante_id,
                    DocumentoLead.lead_id == proposta.lead_id,
                    DocumentoLead.organizacao_id == usuario.organizacao_id,
                )
            )
        ).scalar_one_or_none()
        if comprovante is None:
            raise HTTPException(status_code=422, detail="Comprovante não encontrado para este lead")
    proposta.responsavel_protocolo_id = responsavel.id
    proposta.protocolo_numero = numero
    proposta.protocolo_motivo_atraso = motivo
    proposta.protocolo_comprovante_id = dados.comprovante_id
    proposta.protocolo_em = agora if numero else None
    if numero and proposta.lead_id is not None:
        lead_do_protocolo = await session.get(Lead, proposta.lead_id)
        if lead_do_protocolo is not None:
            if not lead_do_protocolo.processo_numero:
                lead_do_protocolo.processo_numero = numero
            # Fluxos antigos podem chegar direto ao protocolo sem o clique
            # explícito de recebimento no painel jurídico. Nesse caso, o ato
            # de protocolar também formaliza o recebimento e fecha a passagem.
            if proposta.juridico_recebido_em is None:
                proposta.juridico_recebido_em = agora
                proposta.juridico_recebido_por_id = usuario.id
                proposta.juridico_recebido_por = usuario.ator
            await avancar_fase_lead(session, lead_do_protocolo, FaseLead.GANHO.value, usuario.ator)
            await avancar_fase_lead(session, lead_do_protocolo, FaseLead.PROTOCOLO_INPI.value, usuario.ator)
        await _tentar_vincular_processo_ao_protocolar(session, usuario.organizacao_id, proposta.lead_id, numero)
    _atualizar_sla_proposta(proposta)
    _auditar(
        session,
        usuario,
        request,
        "protocolo_proposta",
        f"proposta:{proposta.id}",
        {
            "protocolo_numero": numero,
            "motivo_atraso": motivo,
            "responsavel_id": responsavel.id,
        },
    )
    await session.commit()
    return _proposta_dict(proposta, await session.get(Organizacao, usuario.organizacao_id))


@router.get("/v1/admin/propostas/{proposta_id}/sla")
async def obter_sla_proposta(proposta_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    proposta = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    status_sla = _atualizar_sla_proposta(proposta)
    pendencias = await _pendencias_documentos(session, proposta)
    return {
        "proposta_id": proposta.id,
        "status": status_sla,
        "inicio_em": proposta.sla_inicio_em,
        "prazo_em": proposta.sla_prazo_em,
        "responsavel_id": proposta.responsavel_protocolo_id,
        "protocolo_numero": proposta.protocolo_numero,
        "protocolo_em": proposta.protocolo_em,
        "motivo_atraso": proposta.protocolo_motivo_atraso,
        "aceito_em": proposta.aceito_em,
        "pagamento_confirmado_em": proposta.pagamento_confirmado_em,
        "pagamento_confirmado_por": proposta.pagamento_confirmado_por,
        "public_aceito_ip_registrado": bool(proposta.public_aceito_ip_hash),
        "documentos_pendentes": pendencias,
    }


@router.get("/v1/admin/propostas/{proposta_id}/assinaturas")
async def listar_assinaturas_proposta(proposta_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    proposta = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    assinaturas = (
        (
            await session.execute(
                select(AssinaturaPropostaComercial)
                .where(
                    AssinaturaPropostaComercial.proposta_id == proposta.id,
                    AssinaturaPropostaComercial.organizacao_id == usuario.organizacao_id,
                )
                .order_by(AssinaturaPropostaComercial.assinado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "proposta_id": proposta.id,
        "assinaturas": [
            {
                "id": item.id,
                "versao": item.versao,
                "hash": item.hash_documento,
                "ip_registrado": bool(item.ip_hash),
                "assinado_em": item.assinado_em,
                "provedor": item.provedor,
            }
            for item in assinaturas
        ],
    }


@router.get("/v1/admin/propostas/{proposta_id}/pendencias")
async def obter_pendencias_proposta(proposta_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    proposta = await _proposta_da_org(session, proposta_id, usuario.organizacao_id)
    pendencias = await _pendencias_documentos(session, proposta)
    return {
        "proposta_id": proposta.id,
        "status": proposta.status,
        "pagamento_status": proposta.pagamento_status,
        "documentos_pendentes": pendencias,
        "sla_liberado": not pendencias and proposta.status == "aceita" and proposta.pagamento_status == "confirmado",
    }


@router.get("/v1/admin/propostas/{proposta_id}/documento")
async def documento_proposta(proposta_id: int, session: SessionDep, usuario: LeadsViewDep) -> dict:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    org = await session.get(Organizacao, usuario.organizacao_id)
    return {
        "proposta": _proposta_dict(proposta, org),
        "texto": (
            f"PROPOSTA DE REGISTRO DE MARCA\\n\\n{org.nome}\\n{(org.branding or {}).get('cnpj', '')}\\n"
            f"{(org.branding or {}).get('endereco', '')}\\n"
            f"Telefone: {(org.branding or {}).get('telefone', org.telefone_contato or '')}\\n"
            f"E-mail: {(org.branding or {}).get('email', org.email_contato or '')}\\n"
            f"Site: {(org.branding or {}).get('site', '')}\\n\\n"
            f"Cliente: {proposta.dados.get('cliente', '')}\\nMarca: {proposta.marca or 'A definir'}\\n"
            f"Classes: {proposta.classes or 'A definir'}\\n\\n{proposta.escopo}\\n\\n"
            "Após aceite, pagamento e recebimento dos documentos, o protocolo será realizado em até 24 horas úteis.\\n"
            "O protocolo não representa garantia de concessão; a decisão pertence ao INPI."
        ),
    }


async def _proposta_por_token(session: AsyncSession, token: str) -> PropostaComercial | None:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    proposta = (
        await session.execute(select(PropostaComercial).where(PropostaComercial.public_token_hash == digest))
    ).scalar_one_or_none()
    if proposta is None or not proposta.public_token_expira_em or proposta.public_token_expira_em < datetime.now(UTC):
        return None
    # Link publico chega sem sessao de operador -- resolve o tenant a partir da
    # propria proposta antes de qualquer leitura/escrita adicional protegida por RLS.
    await aplicar_contexto_tenant(session, proposta.organizacao_id)
    return proposta


@router.get("/v1/admin/propostas/{proposta_id}/pdf")
async def pdf_proposta(proposta_id: int, session: SessionDep, usuario: LeadsViewDep) -> Response:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    org = await session.get(Organizacao, usuario.organizacao_id)
    pdf = gerar_pdf_proposta(_proposta_dict(proposta, org))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="proposta-{proposta.numero}.pdf"'},
    )


@router.post("/v1/admin/propostas/{proposta_id}/link")
async def criar_link_proposta(proposta_id: int, session: SessionDep, usuario: LeadsManageDep) -> dict:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    token = secrets.token_urlsafe(40)
    proposta.public_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    proposta.public_token_expira_em = datetime.now(UTC) + timedelta(days=7)
    await session.commit()
    base = get_settings().app_public_url.rstrip("/")
    return {"link": f"{base}/propostas/{token}", "expira_em": proposta.public_token_expira_em}


@router.post("/v1/admin/propostas/{proposta_id}/enviar")
async def enviar_link_proposta(proposta_id: int, session: SessionDep, usuario: LeadsManageDep) -> dict:
    proposta = (
        await session.execute(
            select(PropostaComercial).where(
                PropostaComercial.id == proposta_id,
                PropostaComercial.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if proposta is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == proposta.lead_id, Lead.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if lead is None or not lead.email:
        raise HTTPException(status_code=422, detail="O lead não possui e-mail cadastrado")
    token = secrets.token_urlsafe(40)
    proposta.public_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    proposta.public_token_expira_em = datetime.now(UTC) + timedelta(days=7)
    proposta.status = "enviada"
    proposta.enviado_em = datetime.now(UTC)
    await session.commit()
    org = await session.get(Organizacao, usuario.organizacao_id)
    link = f"{get_settings().app_public_url.rstrip('/')}/propostas/{token}"
    pdf = gerar_pdf_proposta(_proposta_dict(proposta, org))
    clicksign = configuracao_clicksign(org)
    if clicksign["enabled"]:
        try:
            ids = await criar_envelope(pdf, f"Proposta {proposta.numero}", lead.email, lead.nome, org)
            proposta.dados = {**(proposta.dados or {}), "clicksign": ids}
            await session.commit()
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Não foi possível enviar à Clicksign: {type(exc).__name__}"
            ) from exc
    await enviar_proposta_email(lead.email, lead.nome, link, pdf, proposta.numero)
    return {
        "link": link,
        "destinatario": lead.email,
        "expira_em": proposta.public_token_expira_em,
        "clicksign": proposta.dados.get("clicksign") if proposta.dados else None,
    }


@router.get("/propostas/{token}", response_class=HTMLResponse, include_in_schema=False)
async def visualizar_proposta_publica(token: str, session: SessionDep) -> HTMLResponse:
    proposta = await _proposta_por_token(session, token)
    if proposta is None:
        return HTMLResponse(
            "<h1>Link expirado</h1><p>Solicite uma nova proposta ao atendimento.</p>",
            status_code=404,
        )
    org = await session.get(Organizacao, proposta.organizacao_id)

    def safe(value: object) -> str:
        return html.escape(str(value or ""))

    def moeda(valor: object) -> str:
        return f"R$ {float(valor or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    if proposta.status == "enviada":
        proposta.status = "visualizada"
        await session.commit()
    total = (proposta.honorarios or 0) + (proposta.taxa_gru or 0)
    # Achado 5 do plano proposta-financeiro (Fase 1, 03/09/2026): antes o link
    # público só mostrava marca/classes/escopo -- o cliente aceitava sem ver
    # valores, condições de pagamento ou validade da proposta.
    validade_html = (
        f"<p class='muted'>Proposta válida até {proposta.validade_em.strftime('%d/%m/%Y')}.</p>"
        if proposta.validade_em
        else ""
    )
    return HTMLResponse(
        f"""<!doctype html><html lang='pt-BR'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Proposta {safe(proposta.numero)} - {safe(org.nome)}</title><style>body{{font:16px Arial;color:#17231c;background:#f5f7f5;margin:0;padding:24px}}main{{max-width:760px;margin:auto;background:white;padding:36px;border-radius:18px;border:1px solid #d8ddd6}}h1{{font-family:Georgia,serif}}.muted{{color:#5b665f}}.button{{display:inline-block;background:#086044;color:#fff;padding:13px 20px;border-radius:9px;text-decoration:none;border:0;font-weight:700;cursor:pointer}}</style>
        <main><p class='muted'>{safe(org.nome)}</p><h1>Proposta de registro de marca</h1><p>Proposta <strong>{safe(proposta.numero)}</strong> · versão {proposta.versao}</p>
        <h2>Marca</h2><p>{safe(proposta.marca or "A definir")} · Classes {safe(proposta.classes or "A definir")}</p><h2>Escopo</h2><p>{safe(proposta.escopo)}</p>
        <h2>Valores</h2><p>Honorários: {safe(moeda(proposta.honorarios))}<br>Taxa GRU: {safe(moeda(proposta.taxa_gru))}<br><strong>Total: {safe(moeda(total))}</strong></p>
        <h2>Condições de pagamento</h2><p>{safe(proposta.condicoes_pagamento or "A combinar com o atendimento")}</p>
        {validade_html}
        <p class='muted'>Após aceite, pagamento e documentação completa, o protocolo será realizado em até 24 horas úteis. O protocolo não garante a concessão da marca.</p>
        <form method='post' action='/propostas/{token}/aceitar'><button class='button' type='submit'>Aceitar proposta</button></form></main></html>"""
    )


@router.post("/propostas/{token}/aceitar", response_class=HTMLResponse, include_in_schema=False)
async def aceitar_proposta_publica(token: str, request: Request, session: SessionDep) -> HTMLResponse:
    """Primeiro passo do aceite com dupla validação: gera e envia o código
    de confirmação por e-mail. Chamado de novo (botão "Reenviar código")
    gera um código novo, sujeito ao cooldown do limitador de reenvio."""
    proposta = await _proposta_por_token(session, token)
    if proposta is None:
        return HTMLResponse("<h1>Link expirado</h1>", status_code=404)
    if proposta.status not in ("enviada", "visualizada", "aceita"):
        return HTMLResponse(
            "<h1>Proposta indisponível</h1><p>Solicite uma nova versão ao atendimento.</p>",
            status_code=409,
        )
    # Achado 7 do plano proposta-financeiro (Fase 1): validade_em nunca era
    # checada -- só o token de 7 dias. Uma proposta já aceita continua
    # idempotente mesmo depois de vencer (não desfaz um aceite já registrado).
    if proposta.public_aceito_em is None and proposta.validade_em and proposta.validade_em < datetime.now(UTC).date():
        return HTMLResponse(
            "<h1>Proposta expirada</h1><p>Esta proposta não está mais disponível para aceite. "
            "Solicite uma nova versão ao atendimento.</p>",
            status_code=409,
        )
    if proposta.public_aceito_em is not None:
        return _pagina_aceite_confirmado()
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == proposta.lead_id, Lead.organizacao_id == proposta.organizacao_id)
        )
    ).scalar_one_or_none()
    if lead is None or not lead.email:
        return HTMLResponse(
            "<h1>Não foi possível continuar</h1><p>Cadastro sem e-mail para confirmação. "
            "Solicite ajuda ao atendimento.</p>",
            status_code=422,
        )
    try:
        _LIMITADOR_REENVIO_CODIGO_PROPOSTA.aplicar(f"proposta:{proposta.id}")
    except HTTPException:
        return _pagina_codigo(token, aviso="Aguarde um instante antes de pedir um novo código.")
    codigo = _gerar_codigo_confirmacao()
    proposta.codigo_confirmacao_hash = hash_token(codigo)
    proposta.codigo_confirmacao_expira_em = datetime.now(UTC) + timedelta(minutes=CODIGO_CONFIRMACAO_MINUTOS)
    proposta.codigo_confirmacao_tentativas = 0
    proposta.codigo_confirmacao_enviado_em = datetime.now(UTC)
    await session.commit()
    try:
        await enviar_codigo_confirmacao_proposta(lead.email, lead.nome, codigo, proposta.numero)
    except Exception:
        # Achado da varredura ampla do sistema (18/09/2026): falha de envio
        # (SMTP fora do ar, etc.) não ficava registrada em lugar nenhum --
        # o cliente via o aviso de erro, mas a equipe não tinha como saber
        # que aconteceu sem o cliente reclamar.
        logger.exception("Falha ao enviar código de confirmação da proposta %s", proposta.numero)
        return HTMLResponse(
            "<h1>Não foi possível enviar o código</h1><p>Tente novamente em instantes ou "
            "solicite ajuda ao atendimento.</p>",
            status_code=502,
        )
    return _pagina_codigo(token)


@router.post("/propostas/{token}/confirmar", response_class=HTMLResponse, include_in_schema=False)
async def confirmar_codigo_proposta(
    token: str, request: Request, session: SessionDep, codigo: Annotated[str, Form()]
) -> HTMLResponse:
    """Segundo passo do aceite: valida o código de 6 dígitos e, se bater,
    finaliza o aceite (mesma lógica que antes vivia direto em
    aceitar_proposta_publica -- só que agora com o segundo fator confirmado)."""
    proposta = await _proposta_por_token(session, token)
    if proposta is None:
        return HTMLResponse("<h1>Link expirado</h1>", status_code=404)
    if proposta.public_aceito_em is not None:
        return _pagina_aceite_confirmado()
    if not proposta.codigo_confirmacao_hash or not proposta.codigo_confirmacao_expira_em:
        return _pagina_codigo(token, aviso="Peça um novo código para continuar.")
    if proposta.codigo_confirmacao_expira_em < datetime.now(UTC):
        return _pagina_codigo(token, aviso="Código expirado. Peça um novo código.")
    if proposta.codigo_confirmacao_tentativas >= CODIGO_CONFIRMACAO_TENTATIVAS_MAXIMAS:
        return _pagina_codigo(token, aviso="Muitas tentativas com este código. Peça um novo código.")
    codigo_normalizado = (codigo or "").strip()
    if not codigo_normalizado or hash_token(codigo_normalizado) != proposta.codigo_confirmacao_hash:
        proposta.codigo_confirmacao_tentativas += 1
        await session.commit()
        return _pagina_codigo(token, aviso="Código incorreto. Confira seu e-mail e tente de novo.")

    agora = datetime.now(UTC)
    proposta.public_aceito_em = agora
    proposta.aceito_em = agora
    proposta.public_aceito_ip_hash = hash_ip(cliente_ip(request))
    proposta.status = "aceita"
    proposta.sla_status = "aguardando_pagamento"
    proposta.codigo_confirmacao_hash = None
    proposta.codigo_confirmacao_expira_em = None
    proposta.codigo_confirmacao_tentativas = 0
    await criar_contratacao_automatica_proposta(session, proposta, "link_publico")
    assinatura_hash = hashlib.sha256(
        "|".join(
            str(valor or "")
            for valor in (
                proposta.numero,
                proposta.versao,
                proposta.marca,
                proposta.classes,
                proposta.escopo,
                proposta.honorarios,
                proposta.taxa_gru,
                proposta.condicoes_pagamento,
            )
        ).encode("utf-8")
    ).hexdigest()
    # Achado médio da Fase 8 (auditoria jurídica, 15/09/2026): o gate
    # "public_aceito_em is not None" lá em cima e este INSERT não são
    # atômicos -- duas submissões quase simultâneas do mesmo código de
    # confirmação (ex.: duplo clique, ou o cliente reenvia o formulário)
    # passavam as duas pelo "is not None" e criavam duas linhas de
    # evidência pra mesma versão da proposta. Mesmo padrão de
    # begin_nested()/IntegrityError já usado em
    # criar_contratacao_automatica_proposta (PR #48, Fase 7) pra essa
    # mesma classe de corrida -- proteção de última linha é a
    # UniqueConstraint (proposta_id, versao) da migration d4e5f6a7b8c9.
    try:
        async with session.begin_nested():
            session.add(
                AssinaturaPropostaComercial(
                    organizacao_id=proposta.organizacao_id,
                    proposta_id=proposta.id,
                    versao=proposta.versao,
                    hash_documento=assinatura_hash,
                    ip_hash=proposta.public_aceito_ip_hash,
                    provedor="link_publico",
                    segundo_fator_canal="email",
                    segundo_fator_confirmado_em=agora,
                )
            )
            await session.flush()
    except IntegrityError:
        pass
    session.add(
        EventoAuditoria(
            organizacao_id=proposta.organizacao_id,
            ator="cliente_link",
            acao="aceitar_proposta",
            recurso=f"proposta:{proposta.id}",
            resource_type="proposta",
            resource_id=str(proposta.id),
            sucesso=True,
            status_http=200,
            ip_hash=proposta.public_aceito_ip_hash,
            detalhes={"origem": "link_publico", "proposta": proposta.numero, "segundo_fator": "email"},
        )
    )
    registrar_evento_operacional(
        session,
        organizacao_id=proposta.organizacao_id,
        dominio="crm",
        tipo="crm.proposta_aceita",
        entidade_tipo="lead",
        entidade_id=proposta.lead_id,
        ator="cliente_link",
        payload={"proposta_id": proposta.id, "aceito_em": proposta.aceito_em.isoformat()},
    )
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == proposta.lead_id, Lead.organizacao_id == proposta.organizacao_id)
        )
    ).scalar_one_or_none()
    if lead is not None:
        await avancar_fase_lead(session, lead, "proposta_aceita", "Cliente via link")
    await session.commit()
    return _pagina_aceite_confirmado()
