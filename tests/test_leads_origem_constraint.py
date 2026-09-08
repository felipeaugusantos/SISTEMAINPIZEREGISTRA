from datetime import UTC, datetime

from sqlalchemy import text

from app.database import engine

# --- Achado do usuário (08/09/2026): "Converter em lead" no Radar de
# Prospecção sempre falhava com 500 (CheckViolationError em
# ck_leads_origem_valida) porque converter_prospect_em_lead
# (app/api/prospeccao.py) grava origem="prospeccao" desde que o endpoint foi
# criado, mas esse valor nunca foi incluído na constraint do banco -- bug
# pré-existente que os testes com FakeSession nunca pegaram, porque
# FakeSession não valida constraints reais do Postgres. Corrigido em
# migrations/versions/ke86z1g7s408_origem_prospeccao_lead.py.
#
# Este teste bate direto no Postgres real (app.database.engine, já
# migrado pela suíte -- ver docker-entrypoint/compose.yaml service "test"),
# não usa FakeSession, para garantir que esse tipo de divergência entre
# código e schema seja pega de novo se reaparecer. Roda tudo dentro de uma
# transação com rollback -- nunca persiste nada de verdade.


async def test_origem_prospeccao_e_aceita_pela_constraint_do_banco() -> None:
    async with engine.connect() as conexao:
        async with conexao.begin() as transacao:
            organizacao_id = (
                await conexao.execute(text("SELECT id FROM organizacoes ORDER BY id LIMIT 1"))
            ).scalar_one()
            resultado = await conexao.execute(
                text(
                    "INSERT INTO leads (organizacao_id, nome, email, telefone, empresa, documento, marca, "
                    "origem, aceite_privacidade, status, fase, consentimento_em, consentimento_base_legal) "
                    "VALUES (:organizacao_id, 'Teste Constraint Origem', 'teste@example.test', '11999999999', "
                    "'Empresa Teste', '00000000000100', '', 'prospeccao', true, 'novo', 'contato_inicial', "
                    ":agora, 'interesse_legitimo_prospeccao_comercial') RETURNING id"
                ),
                {"organizacao_id": organizacao_id, "agora": datetime.now(UTC)},
            )
            assert resultado.scalar_one() is not None
            await transacao.rollback()


async def test_origem_invalida_continua_rejeitada_pela_constraint() -> None:
    # Garante que a correção não afrouxou a constraint além do necessário --
    # um valor qualquer fora da lista permitida continua sendo rejeitado.
    async with engine.connect() as conexao:
        async with conexao.begin() as transacao:
            organizacao_id = (
                await conexao.execute(text("SELECT id FROM organizacoes ORDER BY id LIMIT 1"))
            ).scalar_one()
            try:
                await conexao.execute(
                    text(
                        "INSERT INTO leads (organizacao_id, nome, email, telefone, empresa, documento, marca, "
                        "origem, aceite_privacidade, status, fase, consentimento_em, consentimento_base_legal) "
                        "VALUES (:organizacao_id, 'Teste Constraint Origem', 'teste@example.test', "
                        "'11999999999', 'Empresa Teste', '00000000000100', '', 'origem_inexistente', true, "
                        "'novo', 'contato_inicial', :agora, 'interesse_legitimo_prospeccao_comercial')"
                    ),
                    {"organizacao_id": organizacao_id, "agora": datetime.now(UTC)},
                )
                falhou = False
            except Exception:
                falhou = True
            finally:
                await transacao.rollback()
            assert falhou, "origem fora da lista permitida deveria ter sido rejeitada pela constraint"
