"""Armazenamento de documentos com backend local ou objeto S3-compatível.

O backend local permanece como fallback para desenvolvimento. Em produção,
configure STORAGE_BACKEND=s3 e forneça as variáveis do bucket; a integração
usa boto3 opcional para não alterar a instalação mínima do ambiente local.
"""

from __future__ import annotations

import os
from pathlib import Path


class StorageError(RuntimeError):
    pass


def backend() -> str:
    return os.getenv("STORAGE_BACKEND", "local").strip().lower()


def save_bytes(key: str, content: bytes) -> str:
    if backend() == "local":
        root = Path(os.getenv("STORAGE_LOCAL_ROOT", "data/uploads"))
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)
    if backend() != "s3":
        raise StorageError(f"Backend de armazenamento inválido: {backend()}")
    try:
        import boto3
    except ImportError as exc:
        raise StorageError("Instale boto3 para usar STORAGE_BACKEND=s3") from exc
    bucket = os.getenv("STORAGE_S3_BUCKET")
    if not bucket:
        raise StorageError("STORAGE_S3_BUCKET não configurado")
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("STORAGE_S3_ENDPOINT") or None,
        region_name=os.getenv("STORAGE_S3_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("STORAGE_S3_ACCESS_KEY") or None,
        aws_secret_access_key=os.getenv("STORAGE_S3_SECRET_KEY") or None,
    )
    client.put_object(Bucket=bucket, Key=key, Body=content)
    return f"s3://{bucket}/{key}"


def read_bytes(location: str) -> bytes:
    if not location.startswith("s3://"):
        return Path(location).read_bytes()
    try:
        import boto3
    except ImportError as exc:
        raise StorageError("Instale boto3 para usar STORAGE_BACKEND=s3") from exc
    bucket, _, key = location[5:].partition("/")
    if not bucket or not key:
        raise StorageError("Localizacao S3 invalida")
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("STORAGE_S3_ENDPOINT") or None,
        region_name=os.getenv("STORAGE_S3_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("STORAGE_S3_ACCESS_KEY") or None,
        aws_secret_access_key=os.getenv("STORAGE_S3_SECRET_KEY") or None,
    )
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()
