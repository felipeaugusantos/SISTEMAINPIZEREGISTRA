"""Validação de tipo de arquivo enviado (lista de permitidos + assinatura real).

Auditoria de segurança de 13/10/2026 (item 15): os uploads só tinham limite de tamanho e
antivírus, e gravavam o ``content_type`` declarado pelo cliente. Aqui o tipo é determinado
pelos PRIMEIROS BYTES do arquivo (não pelo nome nem pelo cabeçalho do navegador), precisa
estar na lista do endpoint e a extensão do nome precisa corresponder ao conteúdo. O tipo
real devolvido é o que deve ser gravado como ``content_type``.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import HTTPException

PDF = "application/pdf"
PNG = "image/png"
JPEG = "image/jpeg"
WEBP = "image/webp"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

EXTENSOES: dict[str, frozenset[str]] = {
    PDF: frozenset({".pdf"}),
    PNG: frozenset({".png"}),
    JPEG: frozenset({".jpg", ".jpeg"}),
    WEBP: frozenset({".webp"}),
    DOCX: frozenset({".docx"}),
    XLSX: frozenset({".xlsx"}),
}

NOMES_AMIGAVEIS = {PDF: "PDF", PNG: "PNG", JPEG: "JPEG", WEBP: "WEBP", DOCX: "DOCX", XLSX: "XLSX"}

# Conjuntos por finalidade de upload.
DOCUMENTOS = frozenset({PDF, PNG, JPEG, DOCX})
MATERIAIS_MARCA = frozenset({PDF, PNG, JPEG, WEBP})
ARQUIVOS_PORTAL = frozenset({PDF, PNG, JPEG, WEBP, DOCX, XLSX})

# Um .docx/.xlsx é um zip: limita descompressão e quantidade de entradas (zip bomb).
LIMITE_DESCOMPRIMIDO = 200 * 1024 * 1024
LIMITE_ENTRADAS = 5000


# Parte principal e tipo de conteúdo que o pacote OOXML precisa declarar. O sufixo "main+xml" sem
# "macroEnabled" também exclui .docm/.xlsm e modelos (.dotx/.xltx) pelo próprio tipo declarado.
PARTE_PRINCIPAL = {
    DOCX: ("word/document.xml", "wordprocessingml.document.main+xml"),
    XLSX: ("xl/workbook.xml", "spreadsheetml.sheet.main+xml"),
}
LIMITE_CONTENT_TYPES = 1024 * 1024


def _ler_parte(pacote: zipfile.ZipFile, nome: str, limite: int, *, guardar: bool = True) -> bytes | None:
    """Lê a parte até o fim -- o zipfile confere o CRC no EOF --, em pedaços e com teto de tamanho.

    Devolve o conteúdo (ou b"" se ``guardar`` for False, para partes grandes que só precisam do CRC
    conferido sem ocupar memória); None se estiver corrompida, ausente ou acima do limite.
    """
    lido = 0
    pedacos: list[bytes] = []
    try:
        with pacote.open(nome) as parte:
            while pedaco := parte.read(65536):
                lido += len(pedaco)
                if lido > limite:
                    return None
                if guardar:
                    pedacos.append(pedaco)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError, RuntimeError):
        return None
    return b"".join(pedacos)


def _zip_office(conteudo: bytes) -> str | None:
    """DOCX/XLSX só se for um pacote OOXML íntegro, sem macro e sem zip bomb.

    Não basta existir uma entrada com o nome certo (infolist só lê a lista de arquivos): o
    [Content_Types].xml precisa declarar o tipo do documento principal e as duas partes são lidas
    por inteiro, o que confere o CRC (revisão do Codex no PR #190).
    """
    try:
        with zipfile.ZipFile(io.BytesIO(conteudo)) as pacote:
            entradas = pacote.infolist()
            if len(entradas) > LIMITE_ENTRADAS or sum(item.file_size for item in entradas) > LIMITE_DESCOMPRIMIDO:
                return None
            nomes = {item.filename for item in entradas}
            if any(nome.lower().endswith("vbaproject.bin") for nome in nomes) or "[Content_Types].xml" not in nomes:
                return None
            for tipo, (parte, tipo_declarado) in PARTE_PRINCIPAL.items():
                if parte not in nomes:
                    continue
                declarados = _ler_parte(pacote, "[Content_Types].xml", LIMITE_CONTENT_TYPES)
                if declarados is None or tipo_declarado.encode() not in declarados:
                    return None
                if _ler_parte(pacote, parte, LIMITE_DESCOMPRIMIDO, guardar=False) is None:
                    return None
                return tipo
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
        return None
    return None


def detectar_tipo(conteudo: bytes) -> str | None:
    """Tipo MIME pela assinatura (magic bytes); None se não for um dos tipos conhecidos."""
    if conteudo.startswith(b"%PDF-"):
        return PDF
    if conteudo.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if conteudo.startswith(b"\xff\xd8\xff"):
        return JPEG
    if conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP":
        return WEBP
    if conteudo.startswith(b"PK\x03\x04"):
        return _zip_office(conteudo)
    return None


def validar_tipo_arquivo(nome: str | None, conteudo: bytes, permitidos: frozenset[str]) -> str:
    """Devolve o tipo real (para gravar em content_type) ou levanta 422.

    Recusa: conteúdo vazio, tipo desconhecido ou fora da lista do endpoint, e extensão
    que não corresponde ao conteúdo (ex.: executável renomeado para .pdf).
    """
    if not conteudo:
        raise HTTPException(status_code=422, detail="Arquivo vazio")
    tipo = detectar_tipo(conteudo)
    if tipo is None or tipo not in permitidos:
        aceitos = ", ".join(NOMES_AMIGAVEIS[item] for item in sorted(permitidos, key=NOMES_AMIGAVEIS.get))
        raise HTTPException(status_code=422, detail=f"Tipo de arquivo não permitido. Envie um destes formatos: {aceitos}.")
    extensao = Path(nome or "").suffix.lower()
    if extensao not in EXTENSOES[tipo]:
        raise HTTPException(status_code=422, detail="A extensão do arquivo não corresponde ao conteúdo enviado")
    return tipo
