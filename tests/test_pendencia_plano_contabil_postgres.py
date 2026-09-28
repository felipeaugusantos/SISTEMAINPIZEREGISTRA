"""Pendência de plano contábil no aceite do cliente contra o Postgres real.

Os testes com FakeSession não enxergam CHECK/UNIQUE do banco -- um ``tipo``
fora de ck_lembretes_crm_tipo faria o INSERT do lembrete falhar e, com ele,
o aceite inteiro do cliente. Mesmo padrão de test_leads_fase_constraint.py:
engine próprio, transação sempre desfeita no final.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.leads_propostas import _registrar_pendencia_plano_contabil
from app.models import LembreteCRM, PropostaComercial
from app.settings import get_settings


async def test_pendencia_plano_contabil_grava_lembrete_valido_e_idempotente() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conexao:
            transacao = await conexao.begin()
            try:
                organizacao_id = (
                    await conexao.execute(text("SELECT id FROM organizacoes ORDER BY id LIMIT 1"))
                ).scalar_one()
                lead_id = (
                    await conexao.execute(
                        text(
                            "INSERT INTO leads (organizacao_id, nome, email, telefone, empresa, marca, "
                            "origem, aceite_privacidade, status, fase, consentimento_em, "
                            "consentimento_base_legal) VALUES (:org, 'Teste Pendencia', "
                            "'pendencia@example.test', '11999999999', 'Empresa', '', 'geral', true, "
                            "'novo', 'contato_inicial', :agora, 'interesse_legitimo_prospeccao_comercial') RETURNING id"
                        ),
                        {"org": organizacao_id, "agora": datetime.now(UTC)},
                    )
                ).scalar_one()
                session = AsyncSession(bind=conexao)
                proposta = PropostaComercial(id=987654, organizacao_id=organizacao_id, lead_id=lead_id, numero="P-1")

                for _ in range(2):  # Clicksign reenvia o mesmo evento
                    await _registrar_pendencia_plano_contabil(session, proposta, None, "clicksign", "honorários")
                await session.flush()

                total = (
                    await session.execute(
                        select(func.count())
                        .select_from(LembreteCRM)
                        .where(LembreteCRM.idempotency_key == "proposta-sem-plano-contabil:987654")
                    )
                ).scalar_one()
                assert total == 1
            finally:
                await transacao.rollback()
    finally:
        await engine.dispose()
