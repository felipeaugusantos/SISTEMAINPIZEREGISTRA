from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.settings import get_settings

# --- Achado numa auditoria sistemática (08/09/2026, mesmo padrão dos bugs
# de ck_leads_origem_valida, ck_prospect_triagens_classificacao_valida e
# ck_prospects_triagem_marca_status_valido, todos corrigidos nesta sessão):
# o enum FaseLead ganhou 4 fases novas na auditoria de 04/09/2026 (achado
# CRM-11) -- qualificado, aguardando_pagamento, pagamento_confirmado, ganho
# -- mas ck_leads_fase_valida nunca foi atualizada. Corrigido em
# migrations/versions/mg08b3i9u620_corrige_fase_lead_valida.py.
#
# Bate direto no Postgres real (engine próprio, não app.database.engine
# global -- ver comentário em test_leads_origem_constraint.py sobre por
# que), dentro de uma transação com rollback -- nunca persiste nada de
# verdade.

FASES_NOVAS = ("qualificado", "aguardando_pagamento", "pagamento_confirmado", "ganho")


async def _inserir_lead_com_fase(fase: str) -> bool:
    """Devolve True se o INSERT foi aceito pela constraint, False se rejeitado."""
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conexao:
            async with conexao.begin() as transacao:
                organizacao_id = (
                    await conexao.execute(text("SELECT id FROM organizacoes ORDER BY id LIMIT 1"))
                ).scalar_one()
                try:
                    await conexao.execute(
                        text(
                            "INSERT INTO leads (organizacao_id, nome, email, telefone, empresa, marca, "
                            "origem, aceite_privacidade, status, fase, consentimento_em, "
                            "consentimento_base_legal) VALUES (:organizacao_id, 'Teste Constraint Fase', "
                            "'teste@example.test', '11999999999', 'Empresa Teste', '', 'geral', true, "
                            "'novo', :fase, :agora, 'interesse_legitimo_prospeccao_comercial')"
                        ),
                        {"organizacao_id": organizacao_id, "fase": fase, "agora": datetime.now(UTC)},
                    )
                    aceito = True
                except Exception:
                    aceito = False
                finally:
                    await transacao.rollback()
                return aceito
    finally:
        await engine.dispose()


async def test_fases_novas_do_enum_sao_aceitas_pela_constraint() -> None:
    for fase in FASES_NOVAS:
        assert await _inserir_lead_com_fase(fase), f"fase '{fase}' deveria ser aceita pela constraint"


async def test_fase_invalida_continua_rejeitada_pela_constraint() -> None:
    assert not await _inserir_lead_com_fase("fase_inexistente")
