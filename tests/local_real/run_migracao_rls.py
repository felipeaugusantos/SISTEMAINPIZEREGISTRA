"""Aplica a migração NOVA (só no Postgres local), compara com o modelo e testa RLS/tenant com o papel inpi_app."""
import asyncio
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

ARQ = next((Path(__file__).resolve().parents[2] / "migrations" / "versions").glob("ze40a7c1d905_*.py"))
spec = importlib.util.spec_from_file_location("mig", ARQ)
mig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mig)
res: list[tuple[str, bool]] = []


def check(n, c, extra=""):
    res.append((n, bool(c)))
    print(("OK    " if c else "FALHA ") + n, extra if not c else "")


COLS = "select column_name, data_type, is_nullable from information_schema.columns where table_name='solicitacoes_exclusao_lead' order by column_name"
IDX = "select indexname from pg_indexes where tablename='solicitacoes_exclusao_lead' order by 1"


async def main() -> None:
    async with h.engine.begin() as c:
        modelo_cols = (await c.execute(text(COLS))).all()
        modelo_idx = [r[0] for r in (await c.execute(text(IDX))).all()]
        await c.execute(text("DROP TABLE solicitacoes_exclusao_lead"))

        def aplicar(sync_conn):
            with Operations.context(MigrationContext.configure(sync_conn)):
                mig.upgrade()

        await c.run_sync(aplicar)
        mig_cols = (await c.execute(text(COLS))).all()
        mig_idx = [r[0] for r in (await c.execute(text(IDX))).all()]
        check("migração aplicou e colunas = modelo", mig_cols == modelo_cols, f"{mig_cols} vs {modelo_cols}")
        check("índices da migração = modelo", mig_idx == modelo_idx, f"{mig_idx} vs {modelo_idx}")
        rls = (await c.execute(text("select relrowsecurity, relforcerowsecurity from pg_class where relname='solicitacoes_exclusao_lead'"))).one()
        check("RLS habilitado e forçado", tuple(rls) == (True, True))
        # dados como superusuário (ignora RLS): um pedido por organização
        a, b = ids["a"], ids["b"]
        for d, nome in ((a, "Lead 1"), (b, "Outro 1")):
            await c.execute(text("insert into solicitacoes_exclusao_lead (organizacao_id, lead_id, lead_nome, solicitado_por, motivo) values (:o,:l,:n,'t','motivo teste')"), {"o": d["org"], "l": d["leads"][0], "n": nome})
    # como inpi_app, sem tenant -> nada; com tenant -> só o próprio
    async with h.engine.connect() as c:
        await c.execute(text("SET ROLE inpi_app"))
        n0 = (await c.execute(text("select count(*) from solicitacoes_exclusao_lead"))).scalar_one()
        check("sem tenant definido: RLS não devolve linhas", n0 == 0, str(n0))
        await c.execute(text("select set_config('app.organizacao_id', :o, false)"), {"o": str(a["org"])})
        r = (await c.execute(text("select organizacao_id from solicitacoes_exclusao_lead"))).scalars().all()
        check("tenant A enxerga só A", r == [a["org"]], str(r))
        try:
            await c.execute(text("insert into solicitacoes_exclusao_lead (organizacao_id, lead_id, lead_nome, solicitado_por, motivo) values (:o,:l,'x','t','m')"), {"o": b["org"], "l": b["leads"][1]})
            ok = False
        except Exception:
            ok = True
        await c.rollback()
        check("tenant A não consegue inserir em B (WITH CHECK)", ok)
        await c.execute(text("SET ROLE inpi_app"))
        await c.execute(text("select set_config('app.organizacao_id', :o, false)"), {"o": str(a["org"])})
        try:
            await c.execute(text("insert into solicitacoes_exclusao_lead (organizacao_id, lead_id, lead_nome, solicitado_por, motivo) values (:o,:l,'dup','t','m')"), {"o": a["org"], "l": a["leads"][0]})
            dup = False
        except Exception:
            dup = True
        await c.rollback()
        check("índice único parcial barra 2ª pendente do mesmo lead", dup)
    # downgrade e reaplicar
    async with h.engine.begin() as c:
        await c.run_sync(lambda sc: _down(sc))
        existe = (await c.execute(text("select to_regclass('solicitacoes_exclusao_lead')"))).scalar_one()
        check("downgrade remove a tabela", existe is None)
        await c.run_sync(lambda sc: _up(sc))
        check("upgrade de novo funciona", (await c.execute(text("select to_regclass('solicitacoes_exclusao_lead')"))).scalar_one() is not None)


def _down(sc):
    with Operations.context(MigrationContext.configure(sc)):
        mig.downgrade()


def _up(sc):
    with Operations.context(MigrationContext.configure(sc)):
        mig.upgrade()


ids = h.semear()
asyncio.run(main())
f = [n for n, ok in res if not ok]
print(f"RESUMO {len(res) - len(f)}/{len(res)}")
sys.exit(1 if f else 0)
