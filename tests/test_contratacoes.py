import asyncio
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.api.contratacoes import ContratacaoInput, contratar_servico
from app.models import ContratacaoServico, PropostaComercial, ServicoFinanceiro
from tests.conftest import FakeResult, FakeSession, usuario_teste

# --- Fase 4 do plano proposta-financeiro (03/09/2026): contratação única por proposta ---


def test_contratar_servico_com_proposta_ja_contratada_devolve_idempotente() -> None:
    servico = ServicoFinanceiro(
        id=1, organizacao_id=1, codigo="REG-MARCA", nome="Registro de marca", valor=Decimal("1000"), ativo=True
    )
    proposta = PropostaComercial(
        id=7, organizacao_id=1, lead_id=9, numero="PROP-TEST", escopo="Registro de marca no INPI"
    )
    contratacao_existente = ContratacaoServico(id=5, organizacao_id=1, proposta_id=7, lancamento_id=9)

    session = FakeSession(
        [
            FakeResult(scalar=None),  # idempotency_key: nenhum lançamento com essa chave ainda
            FakeResult(scalar=servico),
            FakeResult(scalar=proposta),
        ]
    )

    async def _commit_com_conflito() -> None:
        raise IntegrityError("insert", {}, Exception("uq_contratacao_servico_proposta"))

    session.commit = _commit_com_conflito
    session._resultados.append(FakeResult(scalar=contratacao_existente))

    resultado = asyncio.run(
        contratar_servico(
            ContratacaoInput(servico_id=1, proposta_id=7, idempotency_key="chave-nova-diferente"),
            session,
            usuario_teste(),
        )
    )
    assert resultado == {"idempotente": True, "contratacao_id": 5, "lancamento_id": 9}
