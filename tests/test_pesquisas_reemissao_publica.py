"""Revisão de segurança (12/11/2026) — reemissão de acesso ao relatório no
fluxo público (POST /v1/pesquisas-marca).

Contexto: o formulário público não comprova a posse do e-mail informado.
Dois problemas existiam na publicação atual:

1. Oráculo de enumeração: reenviar o par marca+e-mail devolvia
   duplicada=True e o id da pesquisa original, confirmando que "a pessoa
   dona daquele e-mail pesquisou aquela marca" -- informação comercial
   sensível (intenção de registro).
2. Sobrescrita do contato: o reenvio substituía nome, telefone, empresa,
   marca e atividade do lead no CRM, permitindo que terceiros alterassem
   dados de um lead sabendo apenas o e-mail.

A correção torna a resposta pública uniforme (reenvio e pesquisa nova têm o
mesmo formato), preserva o acesso imediato ao relatório (produto gratuito)
e passa a só preencher campos vazios do lead, sem tocar lead convertido
(mesma regra do achado H7 do POST /v1/leads).

Limite documentado: quem conhece o par marca+e-mail ainda recebe um token
curto do relatório original (o relatório é o produto gratuito, gerável por
qualquer visitante). O fechamento completo exige comprovação de posse da
caixa, prevista no fluxo de confirmação por e-mail (link estável revogável).
"""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app
from app.models import Lead, PesquisaMarca, StatusLead
from app.public_report_tokens import validar_token_relatorio
from tests.conftest import FakeSession


class _PesquisaSession(FakeSession):
    async def refresh(self, obj) -> None:
        await super().refresh(obj)
        if isinstance(obj, PesquisaMarca):
            obj.id = "pesquisa-nova"


def _session_override(session: FakeSession):
    async def override():
        yield session

    return override


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "nome": "Contato Teste",
        "empresa": "Empresa Teste",
        "email_corporativo": "contato@empresa.test",
        "telefone": "16999999999",
        "marca": "Marca Teste",
        "atividade": "Serviços de tecnologia",
        "aceite_privacidade": True,
        "aceite_marketing": False,
        "website": "",
    }
    base.update(overrides)
    return base


def _lead_existente(**kwargs: object) -> Lead:
    base: dict[str, object] = {
        "id": 7,
        "organizacao_id": 1,
        "nome": "Nome Original",
        "email": "contato@empresa.test",
        "telefone": "11111111111",
        "empresa": "Empresa Original",
        "marca": "Marca Original",
        "atividade": "Atividade original",
        "status": StatusLead.NOVO,
        "aceite_marketing": True,
    }
    base.update(kwargs)
    return Lead(**base)


def _preparar(monkeypatch, *, lead, original) -> None:
    async def nenhum(*_args, **_kwargs):
        return None

    async def empresa_nenhuma(*_args, **_kwargs):
        return None

    async def lead_encontrado(*_args, **_kwargs):
        return lead

    async def original_encontrado(*_args, **_kwargs):
        return original

    async def fase_alterada(*_args, **_kwargs):
        return True

    async def politica(*_args, **_kwargs):
        return SimpleNamespace(email_alerta_leads="equipe@example.test")

    monkeypatch.setattr("app.api.pesquisas.validar_limite_pesquisas", nenhum)
    monkeypatch.setattr("app.api.pesquisas.obter_ou_criar_empresa", empresa_nenhuma)
    monkeypatch.setattr("app.api.pesquisas.buscar_lead_ativo_por_email", lead_encontrado)
    monkeypatch.setattr("app.api.pesquisas.detectar_pesquisa_duplicada", original_encontrado)
    monkeypatch.setattr("app.api.pesquisas.avancar_fase_lead", fase_alterada)
    monkeypatch.setattr("app.api.pesquisas.enviar_alerta_nova_pesquisa", nenhum)
    monkeypatch.setattr("app.api.pesquisas.obter_politica_crm", politica)


def _post(cliente: TestClient, **overrides: object) -> dict:
    resposta = cliente.post("/v1/pesquisas-marca", json=_payload(**overrides))
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def test_reenvio_nao_revela_duplicidade_e_mantem_acesso_ao_relatorio(monkeypatch) -> None:
    """Oráculo fechado: o reenvio devolve o mesmo formato de uma pesquisa
    nova (sem duplicada=True nem pesquisa_original_id), ainda ancorado na
    pesquisa original e com token válido para ela."""
    _preparar(monkeypatch, lead=_lead_existente(), original="pesquisa-original")
    app.dependency_overrides[get_session] = _session_override(_PesquisaSession())
    try:
        corpo = _post(TestClient(app))
    finally:
        app.dependency_overrides.clear()

    assert corpo["id"] == "pesquisa-original"
    assert corpo["relatorio_url"] == "/relatorios/pesquisa-original"
    assert corpo["duplicada"] is False
    assert corpo["pesquisa_original_id"] is None
    assert corpo["relatorio_token"]
    # O token emitido é válido para a pesquisa original, no tenant da resposta.
    validar_token_relatorio(corpo["relatorio_token"], "pesquisa-original", _oid_do_token(corpo["relatorio_token"]))


def _oid_do_token(token: str) -> int:
    import base64
    import json

    payload_codificado = token.split(".", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(payload_codificado + "=" * (-len(payload_codificado) % 4)))
    return payload["oid"]


def test_reenvio_tem_o_mesmo_formato_de_pesquisa_nova(monkeypatch) -> None:
    """Uniformidade: comparadas lado a lado, pesquisa nova e reenvio expõem
    exatamente os mesmos campos, sem marcador de duplicidade."""
    _preparar(monkeypatch, lead=None, original=None)
    app.dependency_overrides[get_session] = _session_override(_PesquisaSession())
    try:
        cliente = TestClient(app)
        nova = _post(cliente)
    finally:
        app.dependency_overrides.clear()

    _preparar(monkeypatch, lead=_lead_existente(), original="pesquisa-original")
    app.dependency_overrides[get_session] = _session_override(_PesquisaSession())
    try:
        reenvio = _post(cliente)
    finally:
        app.dependency_overrides.clear()

    assert set(nova) == set(reenvio)
    for campo, valor in reenvio.items():
        if valor is None or nova[campo] is None:
            continue
        assert isinstance(valor, type(nova[campo])), campo


def test_reenvio_nao_sobrescreve_dados_do_lead(monkeypatch) -> None:
    """Terceiros não alteram mais o contato: campos preenchidos são
    preservados; aceite de marketing continua só acumulando."""
    lead = _lead_existente()
    _preparar(monkeypatch, lead=lead, original="pesquisa-original")
    app.dependency_overrides[get_session] = _session_override(_PesquisaSession())
    try:
        _post(
            TestClient(app),
            nome="Nome Trocado",
            empresa="Empresa Intrusa",
            telefone="22900000000",
            marca="Marca Teste",
            atividade="Atividade intrusa",
        )
    finally:
        app.dependency_overrides.clear()

    assert lead.nome == "Nome Original"
    assert lead.empresa == "Empresa Original"
    assert lead.telefone == "11111111111"
    assert lead.marca == "Marca Original"
    assert lead.atividade == "Atividade original"
    assert lead.aceite_marketing is True


def test_reenvio_preenche_apenas_campos_vazios_do_lead(monkeypatch) -> None:
    lead = _lead_existente(telefone=None, empresa=None, atividade=None)
    _preparar(monkeypatch, lead=lead, original="pesquisa-original")
    app.dependency_overrides[get_session] = _session_override(_PesquisaSession())
    try:
        _post(TestClient(app))
    finally:
        app.dependency_overrides.clear()

    assert lead.telefone == "16999999999"
    assert lead.empresa == "Empresa Teste"
    assert lead.atividade == "Serviços de tecnologia"
    assert lead.nome == "Nome Original"


def test_lead_convertido_nao_e_alterado_pelo_formulario_publico(monkeypatch) -> None:
    """Espelha o achado H7 do POST /v1/leads neste fluxo: negócio já ganho
    não recebe mutação alguma pelo formulário público."""
    lead = _lead_existente(status=StatusLead.CONVERTIDO, telefone=None)
    _preparar(monkeypatch, lead=lead, original="pesquisa-original")
    app.dependency_overrides[get_session] = _session_override(_PesquisaSession())
    try:
        _post(TestClient(app), nome="Nome Trocado")
    finally:
        app.dependency_overrides.clear()

    assert lead.nome == "Nome Original"
    assert lead.telefone is None


def test_pesquisa_nova_continua_registrando_e_alertando(monkeypatch) -> None:
    """Fluxo novo inalterado: cria pesquisa, dispara o alerta interno e
    devolve token da pesquisa recém-criada."""
    chamadas = {"alerta": 0}

    async def alerta(*_args, **_kwargs):
        chamadas["alerta"] += 1

    _preparar(monkeypatch, lead=None, original=None)
    monkeypatch.setattr("app.api.pesquisas.enviar_alerta_nova_pesquisa", alerta)
    app.dependency_overrides[get_session] = _session_override(_PesquisaSession())
    try:
        corpo = _post(TestClient(app))
    finally:
        app.dependency_overrides.clear()

    assert corpo["id"] == "pesquisa-nova"
    assert corpo["duplicada"] is False
    assert corpo["pesquisa_original_id"] is None
    validar_token_relatorio(corpo["relatorio_token"], "pesquisa-nova", _oid_do_token(corpo["relatorio_token"]))
    assert chamadas["alerta"] == 1
