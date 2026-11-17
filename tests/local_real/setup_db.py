"""Cria o schema no Postgres local (create_all) e o papel inpi_app usado pelos testes de RLS."""
import asyncio

from _harness import engine
from sqlalchemy import text

import app.models  # noqa: F401
from app.database import Base


async def main() -> None:
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
        await c.execute(text("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='inpi_app') THEN CREATE ROLE inpi_app NOLOGIN; END IF; END $$"))
        await c.execute(text("GRANT ALL ON ALL TABLES IN SCHEMA public TO inpi_app"))
        await c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app"))
    print("schema ok")


asyncio.run(main())
