import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import app.api.financeiro as modulo_financeiro
from app.api.financeiro import (
    CategoriaFinanceira,
    FormaPagamentoFinanceira,
    PlanoContas,
    _parse_data_planilha,
    _parse_valor_planilha,
    importar_lancamentos,
)
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste


def test_parse_valor_planilha_aceita_formatos_br_e_us() -> None:
    assert _parse_valor_planilha("1500,00") == Decimal("1500.00")
    assert _parse_valor_planilha("1.500,00") == Decimal("1500.00")
    assert _parse_valor_planilha("1500.00") == Decimal("1500.00")
    assert _parse_valor_planilha("R$ 250,50") == Decimal("250.50")
    assert _parse_valor_planilha("") is None
    assert _parse_valor_planilha(None) is None
    assert _parse_valor_planilha("abc") is None
    assert _parse_valor_planilha("-10,00") is None
    assert _parse_valor_planilha("0") is None


def test_parse_data_planilha_aceita_br_e_iso() -> None:
    assert _parse_data_planilha("05/10/2026").isoformat() == "2026-10-05"
    assert _parse_data_planilha("2026-10-05").isoformat() == "2026-10-05"
    assert _parse_data_planilha("05-10-2026").isoformat() == "2026-10-05"
    assert _parse_data_planilha("") is None
    assert _parse_data_planilha("lixo") is None


class _ArquivoFake:
    """Dublê mínimo de UploadFile -- mesmo padrão de tests/test_carteira_importacao.py."""

    def __init__(self, conteudo: bytes, filename: str = "lancamentos.csv") -> None:
        self.filename = filename
        self._conteudo = conteudo

    async def read(self, _tamanho: int | None = None) -> bytes:
        return self._conteudo


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/financeiro/lancamentos/importar",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


async def _sem_virus(_conteudo: bytes) -> None:
    return None


def _conta(tipo: str = "pagar") -> PlanoContas:
    return PlanoContas(
        id=1,
        organizacao_id=1,
        codigo="7.1" if tipo == "pagar" else "3.1",
        nome="Despesa operacional" if tipo == "pagar" else "Serviços prestados",
        natureza="despesa" if tipo == "pagar" else "receita",
        grupo_dre="despesas_administrativas" if tipo == "pagar" else "receita_bruta",
        ativo=True,
    )


def test_importar_lancamentos_cria_conta_a_pagar_com_sucesso(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modulo_financeiro, "escanear_upload_ou_rejeitar", _sem_virus)
    session = FakeSession(
        [
            FakeResult(itens=[]),  # categorias da organização
            FakeResult(itens=[]),  # formas de pagamento da organização
            FakeResult(itens=[_conta()]),  # contas contábeis compatíveis
        ]
    )
    usuario = usuario_teste("administrador", {"finance.manage"})
    conteudo = b"descricao;valor;vencimento;plano_contabil\nAluguel do escritorio;1500,00;05/10/2026;7.1\n"

    resultado = asyncio.run(
        importar_lancamentos(_request(), session, usuario, _ArquivoFake(conteudo), tipo="pagar")
    )

    assert resultado == {
        "total_linhas": 1,
        "criados": 1,
        "erros": 0,
        "avisos": 0,
        "exemplos_erros": [],
        "exemplos_avisos": [],
    }
    lancamentos = [x for x in session.adicionados if type(x).__name__ == "LancamentoFinanceiro"]
    parcelas = [x for x in session.adicionados if type(x).__name__ == "ParcelaFinanceira"]
    assert len(lancamentos) == 1
    assert lancamentos[0].descricao == "Aluguel do escritorio"
    assert lancamentos[0].valor_total == Decimal("1500.00")
    assert lancamentos[0].tipo == "pagar"
    assert len(parcelas) == 1
    assert parcelas[0].vencimento.isoformat() == "2026-10-05"
    assert session.commits == 1


def test_importar_lancamentos_reporta_linhas_com_valor_ou_vencimento_invalido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(modulo_financeiro, "escanear_upload_ou_rejeitar", _sem_virus)
    session = FakeSession([FakeResult(itens=[]), FakeResult(itens=[])])
    usuario = usuario_teste("administrador", {"finance.manage"})
    conteudo = (
        b"descricao;valor;vencimento\n"
        b"Sem valor;;05/10/2026\n"
        b"Valor invalido;abc;05/10/2026\n"
        b"Sem vencimento;100,00;\n"
    )

    resultado = asyncio.run(
        importar_lancamentos(_request(), session, usuario, _ArquivoFake(conteudo), tipo="pagar")
    )

    assert resultado["criados"] == 0
    assert resultado["erros"] == 3
    assert not any(type(x).__name__ == "LancamentoFinanceiro" for x in session.adicionados)


def test_importar_lancamentos_receber_sem_cliente_elegivel_cria_sem_vinculo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(modulo_financeiro, "escanear_upload_ou_rejeitar", _sem_virus)
    session = FakeSession(
        [
            FakeResult(itens=[]),  # categorias
            FakeResult(itens=[]),  # formas
            FakeResult(itens=[_conta("receber")]),  # contas contábeis
            FakeResult(scalar=None),  # empresa não encontrada/elegível
        ]
    )
    usuario = usuario_teste("administrador", {"finance.manage"})
    conteudo = b"descricao;valor;vencimento;empresa;plano_contabil\nHonorarios;800,00;10/10/2026;Empresa Desconhecida;3.1\n"

    resultado = asyncio.run(
        importar_lancamentos(_request(), session, usuario, _ArquivoFake(conteudo), tipo="receber")
    )

    assert resultado["criados"] == 1
    assert resultado["avisos"] == 1
    assert "não é cliente elegível" in resultado["exemplos_avisos"][0]
    lancamento = next(x for x in session.adicionados if type(x).__name__ == "LancamentoFinanceiro")
    assert lancamento.empresa_id is None


def test_importar_lancamentos_resolve_categoria_por_nome_e_avisa_se_nao_achar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(modulo_financeiro, "escanear_upload_ou_rejeitar", _sem_virus)
    categoria = CategoriaFinanceira(id=5, organizacao_id=1, nome="Aluguel", tipo="pagar", ativo=True)
    session = FakeSession(
        [
            FakeResult(itens=[categoria]),  # categorias
            FakeResult(itens=[]),  # formas
            FakeResult(itens=[_conta()]),  # contas contábeis
        ]
    )
    usuario = usuario_teste("administrador", {"finance.manage"})
    conteudo = (
        b"descricao;valor;vencimento;categoria;plano_contabil\n"
        b"Aluguel sede;1200,00;05/10/2026;Aluguel;7.1\n"
        b"Outra conta;300,00;05/10/2026;Categoria Inexistente;7.1\n"
    )

    resultado = asyncio.run(
        importar_lancamentos(_request(), session, usuario, _ArquivoFake(conteudo), tipo="pagar")
    )

    assert resultado["criados"] == 2
    assert resultado["avisos"] == 1
    assert "Categoria Inexistente" in resultado["exemplos_avisos"][0]
    lancamentos = [x for x in session.adicionados if type(x).__name__ == "LancamentoFinanceiro"]
    assert lancamentos[0].categoria_id == 5
    assert lancamentos[1].categoria_id is None


def test_importar_lancamentos_rejeita_parcelas_acima_do_limite_da_forma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(modulo_financeiro, "escanear_upload_ou_rejeitar", _sem_virus)
    forma = FormaPagamentoFinanceira(
        id=9, organizacao_id=1, nome="Cartão", permite_parcelamento=True, maximo_parcelas=3, ativo=True
    )
    session = FakeSession(
        [
            FakeResult(itens=[]),  # categorias
            FakeResult(itens=[forma]),  # formas
            FakeResult(itens=[_conta()]),  # contas contábeis
        ]
    )
    usuario = usuario_teste("administrador", {"finance.manage"})
    conteudo = "descricao;valor;vencimento;parcelas;forma;plano_contabil\nCompra;900,00;05/10/2026;6;Cartão;7.1\n".encode()

    resultado = asyncio.run(
        importar_lancamentos(_request(), session, usuario, _ArquivoFake(conteudo), tipo="pagar")
    )

    assert resultado["criados"] == 0
    assert resultado["erros"] == 1
    assert "no máximo 3" in resultado["exemplos_erros"][0]


def test_importar_lancamentos_rejeita_arquivo_infectado(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _rejeitar(_conteudo: bytes) -> None:
        raise HTTPException(status_code=422, detail="Arquivo rejeitado: malware detectado.")

    monkeypatch.setattr(modulo_financeiro, "escanear_upload_ou_rejeitar", _rejeitar)

    async def _sessao():
        yield FakeSession()

    usuario = usuario_teste("administrador", {"finance.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/financeiro/lancamentos/importar",
            data={"tipo": "pagar"},
            files={"arquivo": ("lancamentos.csv", b"descricao;valor;vencimento\nX;100,00;05/10/2026\n", "text/csv")},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 422


def test_modelo_importacao_csv_traz_cabecalho_e_exemplo_por_tipo() -> None:
    usuario = usuario_teste("administrador", {"finance.view"})
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get("/v1/admin/financeiro/lancamentos/modelo-importacao.csv?tipo=receber")
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    assert "Descrição;Valor;Vencimento" in resposta.text
    assert "Cliente Exemplo" in resposta.text
