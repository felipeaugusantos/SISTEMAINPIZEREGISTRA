"""Base comum: engine, reset, seed sintético (2 organizações) e override de autenticação."""
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.auth import UsuarioAutenticado, hash_senha, hash_token  # noqa: E402
from app.models import (  # noqa: E402
    Lead,
    Organizacao,
    PesquisaMarca,
    PlanoSaas,
    UsuarioOperacoes,
)

URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://inpi:inpi@/inpi?host=/tmp/pgdata")
engine = create_async_engine(URL, poolclass=NullPool)
Sessao = async_sessionmaker(engine, expire_on_commit=False)
SENHA = "Senha-Sintetica-1!"
TABELAS = (
    "organizacoes, planos_saas, usuarios_operacoes, leads, pesquisas_marca, "
    "solicitacoes_exclusao_pesquisa, solicitacoes_exclusao_lead, eventos_auditoria"
)
ids: dict = {}


async def _resetar_e_semear() -> None:
    async with engine.begin() as c:
        await c.execute(text(f"TRUNCATE {TABELAS} RESTART IDENTITY CASCADE"))
    async with Sessao() as s:
        plano = PlanoSaas(nome="Plano Teste", codigo="teste", modulos=["leads"], limites={})
        s.add(plano)
        await s.flush()
        for chave, nome in (("a", "Org Sintetica"), ("b", "Org Outra")):
            org = Organizacao(nome=nome, slug=f"org-{chave}", plano_id=plano.id)
            s.add(org)
            await s.flush()
            op = UsuarioOperacoes(organizacao_id=org.id, nome=f"Operador {chave}", usuario=f"op{chave}",
                                  email=f"op{chave}@x.test", perfil="operador", senha_hash=hash_senha(SENHA))
            adm = UsuarioOperacoes(organizacao_id=org.id, nome=f"Admin {chave}", usuario=f"adm{chave}",
                                   email=f"adm{chave}@x.test", perfil="administrador", senha_hash=hash_senha(SENHA))
            s.add_all([op, adm])
            await s.flush()
            leads = []
            for i in range(1, 6):
                lead = Lead(organizacao_id=org.id, nome=f"Lead {i}" if chave == "a" else f"Outro {i}",
                            email=f"l{i}{chave}@x.test", telefone="16999990000", marca=f"Marca {i}",
                            responsavel_id=op.id, proxima_acao_em=datetime(2027, 1, 10, tzinfo=UTC))
                s.add(lead)
                leads.append(lead)
            await s.flush()
            pesq = []
            for i, lead in enumerate(leads[:3]):
                p = PesquisaMarca(organizacao_id=org.id, lead_id=lead.id, marca=f"Marca {i + 1}", tipo_pesquisa="completa")
                s.add(p)
                pesq.append(p)
            await s.flush()
            ids[chave] = dict(org=org.id, op=op.id, adm=adm.id, leads=[x.id for x in leads], pesq=[p.id for p in pesq])
        await s.commit()


def semear() -> dict:
    asyncio.run(_resetar_e_semear())
    return ids


def usuario(chave: str, perfil: str) -> UsuarioAutenticado:
    d = ids[chave]
    base = dict(nome=perfil, usuario=perfil, email=f"{perfil}@x.test", sessao_id=1,
                csrf_hash=hash_token("csrf-sintetico"), alterar_senha=False, organizacao_id=d["org"])
    if perfil == "op":
        return UsuarioAutenticado(id=d["op"], perfil="operador", permissoes=frozenset({"leads.view", "leads.manage"}), **base)
    return UsuarioAutenticado(id=d["adm"], perfil="administrador",
                              permissoes=frozenset({"leads.view", "leads.manage", "leads.delete", "leads.pii.view"}), **base)


def db(q: str, **kw):
    async def run():
        async with Sessao() as s:
            return (await s.execute(text(q), kw)).all()
    return asyncio.run(run())
