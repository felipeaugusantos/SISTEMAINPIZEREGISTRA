"""Ciclo baixar -> estornar -> baixar da comissão contra o Postgres real.

Os testes com FakeSession não enxergam o UNIQUE(parcela_id) do banco: antes da
correção, baixar de novo uma parcela estornada tentava inserir uma segunda
comissão e o commit falhava com violação de uq_comissao_financeira_parcela.
Mesmo padrão de test_pendencia_plano_contabil_postgres.py: engine próprio,
transação externa sempre desfeita no final (a sessão usa SAVEPOINT, então os
commits do endpoint não persistem nada).
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.requests import Request

from app.api.financeiro import BaixaCreate, Justificativa, baixar, estornar, pagar_comissao
from app.models import (
    ComissaoFinanceira,
    EventoAuditoria,
    FormaPagamentoFinanceira,
    HistoricoFinanceiro,
    LancamentoFinanceiro,
    ParcelaFinanceira,
)
from app.settings import get_settings
from tests.conftest import usuario_teste


def _sessao(conexao) -> AsyncSession:
    """Uma sessão por requisição, como em produção (sem objetos já carregados de chamadas anteriores)."""
    return AsyncSession(bind=conexao, join_transaction_mode="create_savepoint", expire_on_commit=False)


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "headers": [], "client": ("127.0.0.1", 50000)})


async def _preparar(session: AsyncSession, conexao, organizacao_id: int):
    lead_id = (
        await conexao.execute(
            text(
                "INSERT INTO usuarios_operacoes (nome, usuario, email, senha_hash, organizacao_id, percentual_comissao) "
                "VALUES ('Vendedor Teste', 'vend-comissao', 'vend-comissao@example.test', 'x', :org, 10) RETURNING id"
            ),
            {"org": organizacao_id},
        )
    ).scalar_one()
    operador_id = lead_id
    lead_id = (
        await conexao.execute(
            text(
                "INSERT INTO leads (organizacao_id, nome, email, telefone, marca, status, aceite_privacidade, "
                "responsavel_id) VALUES (:org, 'Lead Comissao', 'lead-comissao@example.test', '11999999999', '', "
                "'novo', true, :resp) RETURNING id"
            ),
            {"org": organizacao_id, "resp": operador_id},
        )
    ).scalar_one()
    forma = FormaPagamentoFinanceira(organizacao_id=organizacao_id, nome="PIX teste")
    lancamento = LancamentoFinanceiro(
        organizacao_id=organizacao_id,
        lead_id=lead_id,
        tipo="receber",
        descricao="Honorários",
        competencia=date(2026, 10, 1),
        valor_total=Decimal("1000.00"),
        criado_por="teste",
    )
    session.add_all([forma, lancamento])
    await session.flush()
    parcela = ParcelaFinanceira(
        organizacao_id=organizacao_id,
        lancamento_id=lancamento.id,
        numero=1,
        vencimento=date(2026, 10, 27),
        valor=Decimal("1000.00"),
    )
    session.add(parcela)
    await session.flush()
    usuario = usuario_teste()
    object.__setattr__(usuario, "organizacao_id", organizacao_id)
    object.__setattr__(usuario, "id", operador_id)
    return usuario, forma, parcela, operador_id


async def _comissoes(session: AsyncSession, parcela_id: int) -> list[ComissaoFinanceira]:
    session.expire_all()
    return list(
        (await session.execute(select(ComissaoFinanceira).where(ComissaoFinanceira.parcela_id == parcela_id)))
        .scalars()
        .all()
    )


async def _auditoria(session: AsyncSession, acao: str, recurso: str) -> list[EventoAuditoria]:
    return list(
        (
            await session.execute(
                select(EventoAuditoria).where(EventoAuditoria.acao == acao, EventoAuditoria.recurso == recurso)
            )
        )
        .scalars()
        .all()
    )


async def test_baixar_estornar_baixar_reutiliza_a_comissao_sem_perder_auditoria() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conexao:
            transacao = await conexao.begin()
            try:
                organizacao_id = (
                    await conexao.execute(text("SELECT id FROM organizacoes ORDER BY id LIMIT 1"))
                ).scalar_one()
                session = _sessao(conexao)
                usuario, forma, parcela, operador_id = await _preparar(session, conexao, organizacao_id)
                parcela_id, lancamento_id, forma_id = parcela.id, parcela.lancamento_id, forma.id
                baixa = BaixaCreate(valor_pago=Decimal("1000.00"), pago_em=date(2026, 10, 27), forma_pagamento_id=forma_id)
                motivo = Justificativa(motivo="Pagamento lançado por engano")

                # 1ª baixa: nasce a comissão (10% de 1.000).
                assert await baixar(parcela_id, baixa, _request(), _sessao(conexao), usuario) == {"status": "paga"}
                [comissao] = await _comissoes(session, parcela_id)
                comissao_id = comissao.id
                assert (comissao.status, comissao.valor_comissao, comissao.usuario_id) == ("pendente", Decimal("100.00"), operador_id)

                # Comissão paga, depois estorno da baixa: fica "cancelada" e mantém quem/quando pagou.
                await pagar_comissao(comissao_id, _request(), _sessao(conexao), usuario)
                assert await estornar(parcela_id, motivo, _request(), _sessao(conexao), usuario) == {"status": "aberta"}
                [comissao] = await _comissoes(session, parcela_id)
                assert comissao.status == "cancelada"
                assert comissao.pago_em is not None
                pago_em_original, pago_por_original = comissao.pago_em, comissao.pago_por

                # 2ª baixa: a MESMA linha volta a pendente (sem violar o UNIQUE e sem duplicar).
                assert await baixar(parcela_id, baixa, _request(), _sessao(conexao), usuario) == {"status": "paga"}
                [comissao] = await _comissoes(session, parcela_id)
                assert comissao.id == comissao_id
                assert (comissao.status, comissao.valor_comissao) == ("pendente", Decimal("100.00"))
                assert comissao.pago_em is None and comissao.pago_por is None

                # A auditoria preserva o pagamento anterior que a linha deixou de carregar.
                [reativacao] = await _auditoria(session, "reativar_comissao", f"comissao-financeira:{comissao_id}")
                anterior = reativacao.detalhes["estado_anterior"]
                assert anterior["pago_em"] == str(pago_em_original)
                assert anterior["pago_por"] == pago_por_original
                assert anterior["valor_comissao"] == "100.00"
                assert len(await _auditoria(session, "estornar_baixa", f"parcela-financeira:{parcela_id}")) == 1
                acoes = (
                    await session.execute(
                        select(HistoricoFinanceiro.acao)
                        .where(HistoricoFinanceiro.parcela_id == parcela_id)
                        .order_by(HistoricoFinanceiro.id)
                    )
                ).scalars().all()
                assert list(acoes) == ["baixa", "estorno", "baixa"]

                # Terceira volta: o ciclo se repete sem criar linha nova.
                await estornar(parcela_id, motivo, _request(), _sessao(conexao), usuario)
                await baixar(parcela_id, baixa, _request(), _sessao(conexao), usuario)
                [comissao] = await _comissoes(session, parcela_id)
                assert comissao.id == comissao_id and comissao.status == "pendente"
                total = (
                    await session.execute(select(func.count()).select_from(ComissaoFinanceira).where(ComissaoFinanceira.lancamento_id == lancamento_id))
                ).scalar_one()
                assert total == 1
            finally:
                await transacao.rollback()
    finally:
        await engine.dispose()


async def test_requisicoes_repetidas_nao_duplicam_nem_reativam_comissao() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conexao:
            transacao = await conexao.begin()
            try:
                organizacao_id = (
                    await conexao.execute(text("SELECT id FROM organizacoes ORDER BY id LIMIT 1"))
                ).scalar_one()
                session = _sessao(conexao)
                usuario, forma, parcela, _ = await _preparar(session, conexao, organizacao_id)
                parcela_id, forma_id = parcela.id, forma.id
                baixa = BaixaCreate(valor_pago=Decimal("1000.00"), pago_em=date(2026, 10, 27), forma_pagamento_id=forma_id)
                motivo = Justificativa(motivo="Pagamento lançado por engano")

                await baixar(parcela_id, baixa, _request(), _sessao(conexao), usuario)
                with pytest.raises(HTTPException) as repetida:  # reenvio da mesma baixa
                    await baixar(parcela_id, baixa, _request(), _sessao(conexao), usuario)
                assert repetida.value.status_code == 409
                assert len(await _comissoes(session, parcela_id)) == 1

                await estornar(parcela_id, motivo, _request(), _sessao(conexao), usuario)
                with pytest.raises(HTTPException) as repetido:  # reenvio do mesmo estorno
                    await estornar(parcela_id, motivo, _request(), _sessao(conexao), usuario)
                assert repetido.value.status_code == 409
                [comissao] = await _comissoes(session, parcela_id)
                assert comissao.status == "cancelada"

                await baixar(parcela_id, baixa, _request(), _sessao(conexao), usuario)
                with pytest.raises(HTTPException):
                    await baixar(parcela_id, baixa, _request(), _sessao(conexao), usuario)
                [comissao] = await _comissoes(session, parcela_id)
                assert comissao.status == "pendente"
                assert len(await _auditoria(session, "reativar_comissao", f"comissao-financeira:{comissao.id}")) == 1
            finally:
                await transacao.rollback()
    finally:
        await engine.dispose()
