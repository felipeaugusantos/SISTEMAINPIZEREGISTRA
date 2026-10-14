import ast
import logging
from pathlib import Path

import pytest

from app.erros import MENSAGEM_ARMAZENAMENTO, erro_armazenamento
from app.storage import StorageError

RAIZ = Path(__file__).resolve().parent.parent / "app"

# Auditoria de seguranca de 13/10/2026 (item 16): respostas 5xx nao podem carregar
# a mensagem de uma excecao (bucket, endpoint, dependencia, caminho).


def _status_5xx(no: ast.Call) -> bool:
    candidatos = list(no.args[:1]) + [kw.value for kw in no.keywords if kw.arg == "status_code"]
    for valor in candidatos:
        if isinstance(valor, ast.Constant) and isinstance(valor.value, int) and valor.value >= 500:
            return True
        if isinstance(valor, ast.Attribute) and valor.attr.startswith("HTTP_5"):
            return True
    return False


def _detail(no: ast.Call) -> ast.expr | None:
    for kw in no.keywords:
        if kw.arg == "detail":
            return kw.value
    return no.args[1] if len(no.args) > 1 else None


def _violacoes() -> list[str]:
    achadas = []
    for arquivo in sorted(RAIZ.rglob("*.py")):
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if not (isinstance(no, ast.Call) and getattr(no.func, "id", getattr(no.func, "attr", "")) == "HTTPException"):
                continue
            if not _status_5xx(no):
                continue
            detalhe = _detail(no)
            if detalhe is not None and not (isinstance(detalhe, ast.Constant) and isinstance(detalhe.value, str)):
                achadas.append(f"{arquivo.relative_to(RAIZ.parent)}:{no.lineno}")
    return achadas


def test_nenhum_httpexception_5xx_usa_detalhe_dinamico() -> None:
    assert _violacoes() == [], "HTTPException 5xx com detail dinâmico (str(exc), f-string...) em: " + ", ".join(_violacoes())


def test_erro_armazenamento_nao_expoe_a_excecao(caplog: pytest.LogCaptureFixture) -> None:
    segredo = "bucket-interno-xyz: Instale boto3 em /opt/app/.venv"
    with caplog.at_level(logging.ERROR, logger="ze_registra.erros"):
        erro = erro_armazenamento(StorageError(segredo))
    assert erro.status_code == 503
    assert erro.detail == MENSAGEM_ARMAZENAMENTO
    assert "bucket" not in str(erro.detail)
    assert segredo in caplog.text  # o detalhe real vai só para o log
