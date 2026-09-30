"""Pedido do usuário (30/09/2026): a importação do cache nacional de empresas
só mostrava "Em andamento". A resposta passa a trazer o percentual, calculado
a partir da etapa gravada pelo worker, e a tela mostra uma barra de progresso."""

from datetime import UTC, datetime
from pathlib import Path

from app.schemas import ImportacaoCnpjRfbResponse, percentual_importacao_cnpj

WEB = Path(__file__).resolve().parents[1] / "app" / "web"


def _resposta(status: str, etapa: str | None) -> ImportacaoCnpjRfbResponse:
    return ImportacaoCnpjRfbResponse(
        id=1,
        status=status,
        periodo=None,
        etapa_atual=etapa,
        total_processados=0,
        total_validos=0,
        erro=None,
        solicitado_por="admin",
        solicitado_em=datetime.now(UTC),
        concluido_em=None,
    )


def test_percentual_acompanha_as_etapas_do_etl() -> None:
    assert percentual_importacao_cnpj(None) == 0
    assert percentual_importacao_cnpj("Período selecionado: 2026-09") == 1
    assert percentual_importacao_cnpj("Carregando empresas 1/10 (123 no índice)") == 5
    assert percentual_importacao_cnpj("Carregando empresas 10/10 (9 no índice)") == 40
    assert percentual_importacao_cnpj("Processando estabelecimentos 5/10") == 69
    assert percentual_importacao_cnpj("Processando estabelecimentos 10/10") == 98


def test_resposta_traz_o_percentual_conforme_o_status() -> None:
    assert _resposta("executando", "Processando estabelecimentos 5/10").percentual == 69
    assert _resposta("concluido", "Concluído").percentual == 100
    assert _resposta("erro", "Carregando empresas 3/10").percentual is None


def test_tela_mostra_a_barra_de_progresso() -> None:
    script = (WEB / "static" / "admin-prospeccao.js").read_text(encoding="utf-8")
    assert "ultima.percentual" in script
    assert '<progress max="100"' in script
    html = (WEB / "admin-prospeccao.html").read_text(encoding="utf-8")
    assert "/static/admin-prospeccao.js?v=14" in html
    assert "/static/admin-prospeccao.css?v=10" in html
