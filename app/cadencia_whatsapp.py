"""Passos de cadência com canal WhatsApp viram mensagens na fila de prospecção (app/mensageria.py).

O texto enviado é a DESCRIÇÃO do passo (se vazia, o título), precedida da saudação com o primeiro nome do lead.
Cada passo vira UMA mensagem, agendada para o dia do passo, com chave estável (aplicar a cadência de novo não
duplica). A mensagem só sai se o telefone não estiver na lista de supressão, em dia útil e horário comercial,
dentro dos limites do número; no momento do envio o lead é conferido de novo (ver ``mensageria.processar_fila``).
"""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.mensageria import enfileirar_mensagem, normalizar_telefone
from app.models import Cadencia, CadenciaPasso, Lead


def montar_texto_cadencia(*, nome: str | None, passo: CadenciaPasso) -> str:
    primeiro_nome = (nome or "").strip().split(" ")[0]
    saudacao = f"Olá, {primeiro_nome}!" if primeiro_nome else "Olá!"
    corpo = (passo.descricao or passo.titulo or "").strip()
    return f"{saudacao}\n\n{corpo}"


def motivo_sem_envio(lead: Lead) -> str | None:
    """Por que este lead NÃO pode receber o passo por WhatsApp (None = pode)."""
    if lead.anonimizado_em is not None or lead.arquivado_em is not None:
        return "lead_indisponivel"
    if lead.cadencia_opt_out_em is not None:
        return "lead_descadastrado"
    if normalizar_telefone(lead.telefone) is None:
        return "telefone_sem_celular_valido"
    return None


async def agendar_whatsapp_do_passo(
    session: AsyncSession, lead: Lead, cadencia: Cadencia, passo: CadenciaPasso, agora: datetime
) -> str | None:
    """Enfileira o passo como mensagem de prospecção. Devolve o motivo de não ter enfileirado, ou None."""
    motivo = motivo_sem_envio(lead)
    if motivo is not None:
        return motivo
    await enfileirar_mensagem(
        session,
        organizacao_id=lead.organizacao_id,
        finalidade="prospeccao",
        telefone=lead.telefone,
        corpo=montar_texto_cadencia(nome=lead.nome, passo=passo),
        chave_idempotencia=f"cadencia:{cadencia.id}:{lead.id}:{passo.id}:whatsapp",
        titular_tipo="lead",
        titular_id=lead.id,
        agendada_para=agora + timedelta(days=passo.dia),
    )
    return None
