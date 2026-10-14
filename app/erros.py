"""Erros HTTP sem detalhes internos.

Auditoria de seguranca de 13/10/2026 (item 16): respostas 5xx nao podem carregar
a mensagem de uma excecao (nome de bucket, endpoint, dependencia ausente, caminho
de arquivo). O detalhe real vai para o log; o cliente recebe uma mensagem fixa.
"""

import logging

from fastapi import HTTPException

logger = logging.getLogger("ze_registra.erros")

MENSAGEM_ARMAZENAMENTO = "Não foi possível armazenar o arquivo agora. Tente novamente em instantes."


def erro_armazenamento(exc: Exception) -> HTTPException:
    """503 com mensagem fixa para falhas do armazenamento de arquivos (storage.StorageError)."""
    logger.error("Falha ao gravar arquivo no armazenamento: %s", exc, exc_info=exc)
    return HTTPException(status_code=503, detail=MENSAGEM_ARMAZENAMENTO)
