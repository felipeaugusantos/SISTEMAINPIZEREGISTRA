import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ORDEM_FASE_LEAD, EmpresaCRM, HistoricoFaseLead, Lead


async def avancar_fase_lead(
    session: AsyncSession, lead: Lead, nova_fase: str, por: str, forcar: bool = False
) -> bool:
    """Move o lead para ``nova_fase`` e registra a transição no histórico.

    Automático (``forcar=False``) só avança no funil — nunca retrocede. Manual
    (``forcar=True``) permite qualquer fase. Retorna ``True`` se houve mudança.
    """
    if nova_fase not in ORDEM_FASE_LEAD or lead.fase == nova_fase:
        return False
    if not forcar and ORDEM_FASE_LEAD.index(nova_fase) <= ORDEM_FASE_LEAD.index(lead.fase):
        return False
    lead.fase = nova_fase
    session.add(
        HistoricoFaseLead(
            organizacao_id=lead.organizacao_id, lead_id=lead.id, fase=nova_fase, por=por
        )
    )
    return True


def normalizar_empresa(nome: str) -> str:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", nome)
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"\s+", " ", sem_acentos.strip()).casefold()


async def obter_ou_criar_empresa(
    session: AsyncSession, organizacao_id: int, nome: str | None
) -> EmpresaCRM | None:
    nome_limpo = re.sub(r"\s+", " ", (nome or "").strip())
    if not nome_limpo:
        return None
    normalizado = normalizar_empresa(nome_limpo)
    empresa = (
        await session.execute(
            select(EmpresaCRM).where(
                EmpresaCRM.organizacao_id == organizacao_id,
                EmpresaCRM.nome_normalizado == normalizado,
            )
        )
    ).scalar_one_or_none()
    if empresa is None:
        empresa = EmpresaCRM(
            organizacao_id=organizacao_id,
            nome=nome_limpo,
            nome_normalizado=normalizado,
        )
        session.add(empresa)
        await session.flush()
    return empresa


async def buscar_lead_ativo_por_email(
    session: AsyncSession, organizacao_id: int, email: str
) -> Lead | None:
    """Localiza o contato ativo pelo identificador único usado pelo banco.

    A empresa da pesquisa não faz parte da chave única do contato. O vínculo
    específico com a empresa fica registrado na própria pesquisa.
    """
    email_normalizado = email.strip().lower()
    if not email_normalizado:
        return None
    return (
        await session.execute(
            select(Lead)
            .where(
                Lead.organizacao_id == organizacao_id,
                Lead.arquivado_em.is_(None),
                func.lower(Lead.email) == email_normalizado,
            )
            .order_by(Lead.atualizado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
