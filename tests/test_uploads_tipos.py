import asyncio
import base64
import io
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.uploads as uploads
from app.uploads import (
    ARQUIVOS_PORTAL,
    DOCUMENTOS,
    DOCX,
    JPEG,
    MATERIAIS_MARCA,
    PDF,
    PNG,
    WEBP,
    XLSX,
    detectar_tipo,
    validar_tipo_arquivo,
)
from tests.conftest import FakeSession, usuario_teste

# Auditoria de seguranca de 13/10/2026 (item 15): o tipo do upload é decidido pelos primeiros bytes do
# arquivo, precisa estar na lista do endpoint e a extensão precisa corresponder ao conteúdo.

PDF_BYTES = b"%PDF-1.7\n%conteudo"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16
EXECUTAVEL = b"MZ\x90\x00\x03\x00\x00\x00 programa.exe"


def _zip(arquivos: dict[str, bytes]) -> bytes:
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as pacote:
        for nome, conteudo in arquivos.items():
            pacote.writestr(nome, conteudo)
    return saida.getvalue()


DOCX_BYTES = _zip({"[Content_Types].xml": b"<x/>", "word/document.xml": b"<w/>"})
XLSX_BYTES = _zip({"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<w/>"})


@pytest.mark.parametrize(
    ("conteudo", "esperado"),
    [
        (PDF_BYTES, PDF),
        (PNG_BYTES, PNG),
        (JPEG_BYTES, JPEG),
        (WEBP_BYTES, WEBP),
        (DOCX_BYTES, DOCX),
        (XLSX_BYTES, XLSX),
        (EXECUTAVEL, None),
        (b"texto simples", None),
        (b"", None),
    ],
)
def test_detecta_o_tipo_pelos_primeiros_bytes(conteudo: bytes, esperado: str | None) -> None:
    assert detectar_tipo(conteudo) == esperado


@pytest.mark.parametrize(
    ("nome", "conteudo", "permitidos", "esperado"),
    [
        ("procuracao.pdf", PDF_BYTES, DOCUMENTOS, PDF),
        ("PROCURACAO.PDF", PDF_BYTES, DOCUMENTOS, PDF),  # extensão sem diferenciar maiúsculas
        ("logo.png", PNG_BYTES, MATERIAIS_MARCA, PNG),
        ("foto.jpg", JPEG_BYTES, MATERIAIS_MARCA, JPEG),
        ("foto.jpeg", JPEG_BYTES, MATERIAIS_MARCA, JPEG),
        ("logo.webp", WEBP_BYTES, MATERIAIS_MARCA, WEBP),
        ("contrato.docx", DOCX_BYTES, DOCUMENTOS, DOCX),
        ("planilha.xlsx", XLSX_BYTES, ARQUIVOS_PORTAL, XLSX),
    ],
)
def test_aceita_tipo_permitido_com_extensao_correspondente(nome, conteudo, permitidos, esperado) -> None:
    assert validar_tipo_arquivo(nome, conteudo, permitidos) == esperado


@pytest.mark.parametrize(
    ("nome", "conteudo", "permitidos"),
    [
        ("procuracao.pdf", EXECUTAVEL, DOCUMENTOS),  # executável renomeado
        ("script.pdf", b"<html><script>alert(1)</script></html>", DOCUMENTOS),
        ("notas.txt", b"texto simples", ARQUIVOS_PORTAL),
        ("vazio.pdf", b"", DOCUMENTOS),
        ("imagem.pdf", PNG_BYTES, DOCUMENTOS),  # extensão não corresponde ao conteúdo
        ("documento", PDF_BYTES, DOCUMENTOS),  # sem extensão
        ("logo.docx", DOCX_BYTES, MATERIAIS_MARCA),  # tipo real fora da lista deste endpoint
        ("planilha.xlsx", XLSX_BYTES, DOCUMENTOS),
        ("logo.svg", b"<svg><script>alert(1)</script></svg>", MATERIAIS_MARCA),
    ],
)
def test_recusa_tipo_fora_da_lista_ou_extensao_divergente(nome, conteudo, permitidos) -> None:
    with pytest.raises(HTTPException) as erro:
        validar_tipo_arquivo(nome, conteudo, permitidos)
    assert erro.value.status_code == 422


def test_docx_com_macro_e_recusado() -> None:
    com_macro = _zip({"word/document.xml": b"<w/>", "word/vbaProject.bin": b"\x00"})
    assert detectar_tipo(com_macro) is None


def test_zip_que_nao_e_office_e_recusado() -> None:
    assert detectar_tipo(_zip({"qualquer.txt": b"x"})) is None


def test_zip_corrompido_e_recusado() -> None:
    assert detectar_tipo(b"PK\x03\x04" + b"lixo" * 10) is None


def test_zip_com_entradas_demais_e_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads, "LIMITE_ENTRADAS", 2)
    muitas = _zip({"word/document.xml": b"<w/>", "a.xml": b"1", "b.xml": b"2"})
    assert detectar_tipo(muitas) is None


def test_zip_que_descomprime_alem_do_limite_e_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads, "LIMITE_DESCOMPRIMIDO", 10)
    grande = _zip({"word/document.xml": b"x" * 1000})
    assert detectar_tipo(grande) is None


def test_mensagem_de_recusa_lista_os_formatos_aceitos() -> None:
    with pytest.raises(HTTPException) as erro:
        validar_tipo_arquivo("a.pdf", EXECUTAVEL, MATERIAIS_MARCA)
    assert "PDF" in erro.value.detail and "PNG" in erro.value.detail and "JPEG" in erro.value.detail
    assert "DOCX" not in erro.value.detail


# --- Portfólio de PI: documento em base64 ---


def _chamar_documento_portfolio(nome: str, conteudo: bytes):
    import app.api.portfolio_pi as portfolio

    async def _ativo(*_args, **_kwargs):
        return SimpleNamespace(id=1)

    original = portfolio._obter_ativo
    portfolio._obter_ativo = _ativo
    try:
        dados = portfolio.DocumentoInput(nome=nome, conteudo_base64=base64.b64encode(conteudo).decode())
        return asyncio.run(
            portfolio.adicionar_documento(1, dados, None, FakeSession([]), usuario_teste(perfil="administrador"))
        )
    finally:
        portfolio._obter_ativo = original


def test_portfolio_recusa_executavel_renomeado_antes_de_qualquer_escrita() -> None:
    with pytest.raises(HTTPException) as erro:
        _chamar_documento_portfolio("certificado.pdf", EXECUTAVEL)
    assert erro.value.status_code == 422


def test_portfolio_recusa_documento_acima_de_15_mb() -> None:
    with pytest.raises(HTTPException) as erro:
        _chamar_documento_portfolio("grande.pdf", PDF_BYTES + b"0" * (15 * 1024 * 1024))
    assert erro.value.status_code == 413
