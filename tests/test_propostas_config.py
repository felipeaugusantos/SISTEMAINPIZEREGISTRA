import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.api.propostas_config as modulo_propostas_config
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from tests.conftest import FakeSession, auth_override, usuario_teste

# --- Achado da varredura ampla do sistema (18/09/2026): o upload de template
# PDF de proposta aceitava o arquivo sem nenhuma varredura antivírus,
# diferente dos demais pontos de upload do sistema (portal do cliente,
# central de atualizações). ---


def test_importar_template_pdf_rejeita_arquivo_infectado(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _rejeitar(_conteudo: bytes) -> None:
        raise HTTPException(status_code=422, detail="Arquivo rejeitado: malware detectado.")

    monkeypatch.setattr(modulo_propostas_config, "escanear_upload_ou_rejeitar", _rejeitar)

    async def _sessao() -> FakeSession:
        yield FakeSession()

    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/configuracao/propostas/template-pdf",
            files={"arquivo": ("modelo.pdf", b"%PDF-1.4\nconteudo", "application/pdf")},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 422
