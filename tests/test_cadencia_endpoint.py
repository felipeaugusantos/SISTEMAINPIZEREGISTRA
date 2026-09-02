from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Cadencia, CadenciaPasso, EnvioCadenciaEmail, Lead
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste

# --- Fase 9 do plano Leads/CRM (03/09/2026): aplicar_cadencia_lead passa a
# agendar um envio real (EnvioCadenciaEmail) para os passos de canal "email",
# além do LembreteCRM manual de sempre (que continua para todos os canais). ---


def _lead(**kwargs: object) -> Lead:
    base: dict = {
        "id": 9,
        "organizacao_id": 1,
        "nome": "Fulano",
        "email": "fulano@example.com",
        "telefone": "11999998888",
        "marca": "ACME",
        "responsavel_id": None,
    }
    base.update(kwargs)
    return Lead(**base)


def _cadencia_com_dois_passos() -> Cadencia:
    passo_ligacao = CadenciaPasso(id=1, organizacao_id=1, cadencia_id=3, ordem=0, dia=1, canal="ligacao", titulo="Ligar")
    passo_email = CadenciaPasso(id=2, organizacao_id=1, cadencia_id=3, ordem=1, dia=3, canal="email", titulo="E-mail de follow-up")
    return Cadencia(id=3, organizacao_id=1, nome="Padrão", passos=[passo_ligacao, passo_email])


def test_aplicar_cadencia_agenda_envio_real_so_para_o_passo_de_email() -> None:
    lead = _lead()
    cadencia = _cadencia_com_dois_passos()

    session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=cadencia), FakeResult(scalar=101), FakeResult(scalar=102)])
    app.dependency_overrides[get_session] = _override_session(session)
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)

    resposta = TestClient(app).post(
        "/v1/admin/leads/9/aplicar-cadencia",
        json={"cadencia_id": 3},
        headers={"X-CSRF-Token": "csrf-teste"},
    )

    assert resposta.status_code == 200
    assert resposta.json()["criados"] == 2
    inserts_envio = [
        stmt for stmt in session.executados if getattr(getattr(stmt, "table", None), "name", None) == "envios_cadencia_email"
    ]
    inserts_lembrete = [
        stmt for stmt in session.executados if getattr(getattr(stmt, "table", None), "name", None) == "lembretes_crm"
    ]
    assert len(inserts_envio) == 1
    assert len(inserts_lembrete) == 2


def _override_session(session):
    async def _gen():
        yield session

    return _gen


def test_pixel_de_rastreio_retorna_gif_e_registra_abertura() -> None:

    envio = EnvioCadenciaEmail(
        id=1,
        organizacao_id=1,
        lead_id=9,
        cadencia_id=3,
        passo_id=2,
        agendado_para=datetime.now(UTC) - timedelta(hours=1),
        status="enviado",
        aberto_em=None,
    )
    session = FakeSession([FakeResult(scalar=envio)])
    app.dependency_overrides[get_session] = _override_session(session)

    resposta = TestClient(app).get("/v1/cadencias/rastreio/token-qualquer.gif")

    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "image/gif"
    assert envio.aberto_em is not None


def test_pixel_de_rastreio_token_invalido_ainda_devolve_gif() -> None:

    session = FakeSession([FakeResult(scalar=None)])
    app.dependency_overrides[get_session] = _override_session(session)

    resposta = TestClient(app).get("/v1/cadencias/rastreio/token-invalido.gif")

    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "image/gif"
