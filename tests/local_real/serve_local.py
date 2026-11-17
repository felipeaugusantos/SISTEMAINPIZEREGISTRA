"""App real em 127.0.0.1:8123 com autenticação por cookie de teste (zr_test_user=op|adm) e dados sintéticos."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h
import uvicorn
from fastapi import Request

from app.auth import obter_usuario_atual
from app.main import app

h.semear()


async def _u(request: Request):
    return h.usuario("a", request.cookies.get("zr_test_user", "op"))


app.dependency_overrides[obter_usuario_atual] = _u
uvicorn.run(app, host="127.0.0.1", port=8123, log_level="warning")
