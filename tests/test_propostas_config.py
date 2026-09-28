import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.api.propostas_config as modulo_propostas_config
from app.api.propostas_config import PropostaPlanosContabeisInput, salvar_planos_contabeis_propostas
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Organizacao
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste


def test_salvar_planos_padrao_preserva_demais_configuracoes_da_organizacao() -> None:
    org = Organizacao(id=1, nome="Org", slug="org", plano_id=1, branding={"proposta": {"titulo": "Modelo"}, "clicksign": {"ativo": True}})
    session = FakeSession([FakeResult(itens=[10, 20])], objetos_get=[org])
    resultado = asyncio.run(
        salvar_planos_contabeis_propostas(
            PropostaPlanosContabeisInput(conta_contabil_honorarios_id=10, conta_contabil_taxa_gru_id=20),
            session,
            usuario_teste(),
        )
    )
    assert resultado["status"] == "ok"
    assert org.branding["proposta"] == {"titulo": "Modelo"}
    assert org.branding["clicksign"] == {"ativo": True}
    assert org.branding["proposta_planos_contabeis"] == {
        "conta_contabil_honorarios_id": 10,
        "conta_contabil_taxa_gru_id": 20,
    }
    assert session.commits == 1


def test_salvar_planos_padrao_rejeita_contas_iguais() -> None:
    session = FakeSession()
    with pytest.raises(HTTPException) as erro:
        asyncio.run(
            salvar_planos_contabeis_propostas(
                PropostaPlanosContabeisInput(conta_contabil_honorarios_id=10, conta_contabil_taxa_gru_id=10),
                session,
                usuario_teste(),
            )
        )
    assert erro.value.status_code == 422
    assert session.executados == []

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
