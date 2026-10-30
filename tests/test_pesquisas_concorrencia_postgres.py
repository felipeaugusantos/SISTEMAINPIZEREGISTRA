"""Concorrencia real: duas sessoes independentes, sem simular locks ou pesquisas."""

import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import consulta, pesquisas
from app.api.consulta import ConsultaOperadorInput
from app.models import PesquisaMarca
from app.schemas import PesquisaMarcaCreate
from app.settings import get_settings
from tests.conftest import usuario_teste

ADMIN_DSN = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://inpi:inpi@localhost:5432/inpi")


@pytest.fixture
async def banco_pesquisas():
    if get_settings().app_env != "test":
        pytest.skip("Escritas de integracao permitidas somente em APP_ENV=test")
    try:
        admin = await asyncpg.connect(ADMIN_DSN)
    except (OSError, asyncpg.PostgresError):
        if os.getenv("CI"):
            pytest.fail("PostgreSQL obrigatorio na CI para validar concorrencia")
        pytest.skip("PostgreSQL de teste indisponivel")
    engine = create_async_engine(ADMIN_DSN.replace("postgresql://", "postgresql+asyncpg://", 1))
    fabrica = async_sessionmaker(engine, expire_on_commit=False)
    sufixo = uuid4().hex[:12]
    org_id = lead_id = usuario_id = None
    try:
        plano_id = await admin.fetchval("SELECT id FROM planos_saas ORDER BY id LIMIT 1")
        org_id = await admin.fetchval(
            "INSERT INTO organizacoes (nome, slug, plano_id) VALUES ($1, $2, $3) RETURNING id",
            f"Concorrencia {sufixo}", f"concorrencia-{sufixo}", plano_id,
        )
        usuario_id = await admin.fetchval(
            "INSERT INTO usuarios_operacoes (organizacao_id, nome, usuario, email, senha_hash, alterar_senha) "
            "VALUES ($1, 'Teste', $2, $3, 'hash-teste', false) RETURNING id",
            org_id, f"concorrencia-{sufixo}", f"operador-{sufixo}@example.test",
        )
        email = f"cliente-{sufixo}@example.test"
        lead_id = await admin.fetchval(
            "INSERT INTO leads (organizacao_id, responsavel_id, nome, email, telefone, marca, origem, "
            "aceite_privacidade, status, fase) "
            "VALUES ($1, $2, 'Cliente', $3, '11999999999', 'NORTE STUDIO', 'operador', true, 'novo', "
            "'relatorio_enviado') RETURNING id",
            org_id, usuario_id, email,
        )
        operador = usuario_teste()
        operador = replace(operador, id=usuario_id, organizacao_id=org_id)
        yield SimpleNamespace(
            admin=admin, fabrica=fabrica, org_id=org_id, lead_id=lead_id, email=email, operador=operador,
        )
    finally:
        await engine.dispose()
        if org_id is not None:
            await admin.execute("DELETE FROM alertas_sistema WHERE organizacao_id=$1", org_id)
            await admin.execute("DELETE FROM pesquisas_marca WHERE organizacao_id=$1", org_id)
            await admin.execute("DELETE FROM leads WHERE organizacao_id=$1", org_id)
            await admin.execute("DELETE FROM usuarios_operacoes WHERE organizacao_id=$1", org_id)
            await admin.execute("DELETE FROM organizacoes WHERE id=$1", org_id)
        await admin.close()


async def _interna(banco, classe=None):
    async with banco.fabrica() as session:
        return (await consulta.criar_consulta(
            ConsultaOperadorInput(
                marca="NORTE STUDIO", nome="Cliente", email=banco.email,
                classes_nice=[classe] if classe else [],
            ), session, banco.operador,
        )).itens[0]


async def _publica(banco):
    async with banco.fabrica() as session:
        return await pesquisas.criar_pesquisa(
            PesquisaMarcaCreate(
                nome="Cliente", email_corporativo=banco.email, telefone="11999999999",
                marca="NORTE STUDIO", atividade="Servicos de tecnologia", aceite_privacidade=True,
            ), session,
            SimpleNamespace(id=banco.org_id, modulos=["consulta"], limites={}, politica_privacidade_versao="1"),
        )


@pytest.mark.parametrize("fluxos", [("interna", "interna"), ("publica", "publica"), ("interna", "publica")])
async def test_requisicoes_simultaneas_reutilizam_uma_pesquisa(banco_pesquisas, monkeypatch, fluxos):
    banco = banco_pesquisas
    # Obriga ambas a ler o mesmo lead antes de qualquer escrita. O restante
    # percorre os endpoints e o PostgreSQL reais, inclusive SELECT FOR UPDATE.
    barreira = asyncio.Barrier(2)
    buscar = consulta.buscar_lead_ativo_por_email

    async def buscar_simultaneamente(*args):
        lead = await buscar(*args)
        await barreira.wait()
        return lead

    monkeypatch.setattr(consulta, "buscar_lead_ativo_por_email", buscar_simultaneamente)
    monkeypatch.setattr(pesquisas, "buscar_lead_ativo_por_email", buscar_simultaneamente)
    enviar_email = AsyncMock()
    monkeypatch.setattr(pesquisas, "enviar_alerta_nova_pesquisa", enviar_email)
    chamadas = {"interna": _interna, "publica": _publica}
    respostas = await asyncio.wait_for(
        asyncio.gather(*(chamadas[fluxo](banco) for fluxo in fluxos)), timeout=15,
    )
    assert respostas[0].id == respostas[1].id
    assert sorted(item.duplicada for item in respostas) == [False, True]
    repetida = next(item for item in respostas if item.duplicada)
    assert repetida.pesquisa_original_id == respostas[0].id
    assert await banco.admin.fetchval(
        "SELECT count(*) FROM pesquisas_marca WHERE lead_id=$1", banco.lead_id,
    ) == 1
    alertas = await banco.admin.fetchval(
        "SELECT count(*) FROM alertas_sistema WHERE organizacao_id=$1 AND codigo='NOVA_PESQUISA'", banco.org_id,
    )
    assert alertas == enviar_email.await_count
    assert alertas <= 1
    if fluxos == ("publica", "publica"):
        assert alertas == 1


async def test_classes_distintas_e_reenvios_preservam_ids(banco_pesquisas):
    banco = banco_pesquisas
    primeira, segunda = await asyncio.wait_for(
        asyncio.gather(_interna(banco, "25"), _interna(banco, "35")), timeout=15,
    )
    assert primeira.id != segunda.id
    assert (await _interna(banco, "25")).id == primeira.id
    assert (await _interna(banco, "35")).id == segunda.id
    assert await banco.admin.fetchval(
        "SELECT count(*) FROM pesquisas_marca WHERE lead_id=$1", banco.lead_id,
    ) == 2


@pytest.mark.parametrize("confirmar", [True, False])
async def test_lock_aguarda_commit_ou_rollback_da_primeira_transacao(banco_pesquisas, confirmar):
    banco = banco_pesquisas
    async with banco.fabrica() as primeira, banco.fabrica() as segunda:
        assert await pesquisas.detectar_pesquisa_duplicada(
            primeira, banco.org_id, banco.lead_id, "NORTE STUDIO",
        ) is None
        original = PesquisaMarca(
            organizacao_id=banco.org_id, lead_id=banco.lead_id, marca="NORTE STUDIO",
            tipo_pesquisa="completa", duplicada=False,
        )
        primeira.add(original)
        await primeira.flush()
        original_id = original.id
        pid = await segunda.scalar(text("SELECT pg_backend_pid()"))
        tarefa = asyncio.create_task(pesquisas.detectar_pesquisa_duplicada(
            segunda, banco.org_id, banco.lead_id, "NORTE STUDIO",
        ))
        try:
            # Confirma a espera no servidor, sem depender de tempo arbitrario
            # nem do autoflush de UPDATE do lead usado pelos endpoints.
            async with asyncio.timeout(5):
                while not await banco.admin.fetchval("SELECT cardinality(pg_blocking_pids($1)) > 0", pid):
                    assert not tarefa.done(), "A segunda transacao ignorou o lock do lead"
                    await asyncio.sleep(0.01)
            if confirmar:
                await primeira.commit()
            else:
                await primeira.rollback()
            assert await asyncio.wait_for(tarefa, timeout=5) == (original_id if confirmar else None)
        finally:
            if not tarefa.done():
                tarefa.cancel()
            await asyncio.gather(tarefa, return_exceptions=True)


async def test_original_ignora_duplicada_legada_e_normaliza_espacos(banco_pesquisas):
    banco = banco_pesquisas
    async with banco.fabrica() as session:
        session.add(PesquisaMarca(
            organizacao_id=banco.org_id, lead_id=banco.lead_id, marca="NORTE STUDIO",
            tipo_pesquisa="completa", duplicada=True,
        ))
        await session.commit()
    original = await _interna(banco)
    assert original.duplicada is False
    await banco.admin.execute("UPDATE pesquisas_marca SET marca='  Norte Studio  ' WHERE id=$1", original.id)
    assert (await _interna(banco)).id == original.id
    async with banco.fabrica() as session:
        registros = (await session.scalars(select(PesquisaMarca).where(
            PesquisaMarca.lead_id == banco.lead_id, PesquisaMarca.duplicada.is_(False),
        ))).all()
    assert [item.id for item in registros] == [original.id]
