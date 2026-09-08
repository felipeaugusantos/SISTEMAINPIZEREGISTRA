from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.auth import UsuarioAutenticado, hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import EventoAuditoria, Organizacao
from tests.conftest import FakeSession, auth_override

BRANDING_COMPLETO = {
    "nome_exibido": "Nome anterior",
    "cor_primaria": "#112233",
    "logo_url": "/static/logo-anterior.png",
    "cnpj": "00.000.000/0000-00",
    "endereco": "Endereço cadastral",
    "proposta": {"titulo": "Proposta preservada"},
    "proposta_template": {"arquivo": "modelo.pdf", "sha256": "hash-preservado"},
    "clicksign": {"habilitado": True, "api_token_enc": "segredo-criptografado"},
    "configuracao_futura": {"habilitada": True, "versao": 7},
}


def _usuario(organizacao_id: int = 1) -> UsuarioAutenticado:
    return UsuarioAutenticado(
        id=1,
        nome="Admin Teste",
        usuario="admin",
        email="admin@teste.local",
        perfil="administrador",
        permissoes=frozenset(),
        alterar_senha=False,
        sessao_id=1,
        csrf_hash=hash_token("csrf-teste"),
        organizacao_id=organizacao_id,
    )


def _organizacao(organizacao_id: int = 1) -> Organizacao:
    return Organizacao(
        id=organizacao_id,
        nome=f"Organização {organizacao_id}",
        slug=f"organizacao-{organizacao_id}",
        branding=deepcopy(BRANDING_COMPLETO),
        retencao_dados_dias=730,
        politica_privacidade_versao="1.0",
    )


def _patch(session: FakeSession, payload: dict, organizacao_id: int = 1):
    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[obter_usuario_atual] = auth_override(_usuario(organizacao_id))
    try:
        return TestClient(app).patch(
            "/v1/admin/confiabilidade/configuracao",
            headers={"X-CSRF-Token": "csrf-teste"},
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("alteracao", "chave_protegida"),
    [
        ({"cor_primaria": "#AABBCC"}, "proposta"),
        ({"logo_url": "/static/logo-nova.png"}, "proposta_template"),
        ({"nome_exibido": "Nome novo"}, "clicksign"),
    ],
)
def test_identidade_visual_preserva_configuracoes_protegidas(alteracao: dict, chave_protegida: str) -> None:
    org = _organizacao()
    valor_anterior = deepcopy(org.branding[chave_protegida])
    session = FakeSession(objetos_get=[org])

    resposta = _patch(session, {"branding": alteracao})

    assert resposta.status_code == 200, resposta.text
    assert org.branding[chave_protegida] == valor_anterior
    assert session.commits == 1


def test_identidade_visual_preserva_campos_desconhecidos_e_dados_cadastrais() -> None:
    org = _organizacao()
    session = FakeSession(objetos_get=[org])

    resposta = _patch(session, {"branding": {"cor_primaria": "#445566"}})

    assert resposta.status_code == 200, resposta.text
    assert org.branding["configuracao_futura"] == {"habilitada": True, "versao": 7}
    assert org.branding["cnpj"] == BRANDING_COMPLETO["cnpj"]
    assert org.branding["endereco"] == BRANDING_COMPLETO["endereco"]
    assert org.branding["cor_primaria"] == "#445566"


@pytest.mark.parametrize("campo_interno", ["clicksign", "proposta", "cnpj", "configuracao_futura"])
def test_patch_rejeita_campos_fora_da_allowlist(campo_interno: str) -> None:
    org = _organizacao()
    branding_anterior = deepcopy(org.branding)
    session = FakeSession(objetos_get=[org])

    resposta = _patch(session, {"branding": {campo_interno: {"alterado": True}}})

    assert resposta.status_code == 422
    assert org.branding == branding_anterior
    assert session.commits == 0


@pytest.mark.parametrize(
    "branding_invalido",
    [
        {"cor_primaria": "verde"},
        {"logo_url": "http://inseguro.test/logo.png"},
        {"nome_exibido": "x"},
    ],
)
def test_configuracao_invalida_retorna_422(branding_invalido: dict) -> None:
    org = _organizacao()
    session = FakeSession(objetos_get=[org])

    resposta = _patch(session, {"branding": branding_invalido})

    assert resposta.status_code == 422
    assert session.commits == 0


def test_patch_altera_somente_a_organizacao_autenticada() -> None:
    class RecordingSession(FakeSession):
        def __init__(self, org: Organizacao) -> None:
            super().__init__(objetos_get=[org])
            self.ids_consultados: list[int] = []

        async def get(self, _modelo, identificador, **_kwargs):
            self.ids_consultados.append(identificador)
            return await super().get(_modelo, identificador, **_kwargs)

    org_alvo = _organizacao(42)
    org_vizinha = _organizacao(99)
    branding_vizinho = deepcopy(org_vizinha.branding)
    session = RecordingSession(org_alvo)

    resposta = _patch(session, {"branding": {"nome_exibido": "Tenant 42"}}, organizacao_id=42)

    assert resposta.status_code == 200, resposta.text
    assert session.ids_consultados == [42]
    assert org_alvo.branding["nome_exibido"] == "Tenant 42"
    assert org_vizinha.branding == branding_vizinho


def test_patch_parcial_nao_redefine_retencao_ou_versao_da_politica() -> None:
    org = _organizacao()
    org.retencao_dados_dias = 900
    org.politica_privacidade_versao = "2.7"
    session = FakeSession(objetos_get=[org])

    resposta = _patch(session, {"branding": {"cor_primaria": "#778899"}})

    assert resposta.status_code == 200, resposta.text
    assert org.retencao_dados_dias == 900
    assert org.politica_privacidade_versao == "2.7"


def test_nova_versao_da_politica_exige_justificativa_e_confirmacao() -> None:
    org = _organizacao()
    session = FakeSession(objetos_get=[org])

    resposta = _patch(session, {"politica_privacidade_versao": "2.0"})

    assert resposta.status_code == 422
    assert org.politica_privacidade_versao == "1.0"
    assert session.commits == 0


def test_nova_versao_da_politica_e_auditada() -> None:
    org = _organizacao()
    session = FakeSession(objetos_get=[org])

    resposta = _patch(
        session,
        {
            "politica_privacidade_versao": "2.0",
            "politica_privacidade_justificativa": (
                "Conteúdo revisado para refletir o tratamento atual de dados pessoais."
            ),
            "confirmar_publicacao_politica": True,
        },
    )

    assert resposta.status_code == 200, resposta.text
    assert org.politica_privacidade_versao == "2.0"
    evento = next(item for item in session.adicionados if isinstance(item, EventoAuditoria))
    assert evento.acao == "PUBLICAR_VERSAO_POLITICA_PRIVACIDADE"
    assert evento.organizacao_id == org.id
    assert evento.detalhes["versao_anterior"] == "1.0"
    assert evento.detalhes["versao_nova"] == "2.0"
