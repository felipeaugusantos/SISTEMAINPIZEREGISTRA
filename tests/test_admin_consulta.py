from pathlib import Path

import pytest
from conftest import FakeSession, usuario_teste

from app.api.consulta import ConsultaOperadorInput, criar_consulta, listar_classes_nice
from app.models import Lead, PesquisaMarca


def test_consulta_interna_aceita_atividade_ausente() -> None:
    dados = ConsultaOperadorInput.model_validate(
        {"marca": "NORTE STUDIO", "nome": "Cliente Teste", "email": "cliente@example.com"}
    )

    assert dados.atividade is None


def test_consulta_interna_normaliza_atividade_vazia() -> None:
    dados = ConsultaOperadorInput.model_validate(
        {
            "marca": "NORTE STUDIO",
            "atividade": "   ",
            "nome": "Cliente Teste",
            "email": "cliente@example.com",
        }
    )

    assert dados.atividade is None


def test_consulta_interna_exige_nome_e_email() -> None:
    with pytest.raises(ValueError):
        ConsultaOperadorInput.model_validate({"marca": "NORTE STUDIO"})


def test_formulario_admin_nao_exige_atividade() -> None:
    html = Path("app/web/admin-consulta.html").read_text(encoding="utf-8")

    campo = html.split('name="atividade"', 1)[1].split(">", 1)[0]
    assert "required" not in campo
    assert "(opcional)" in html


# --- Achado da auditoria completa do CRM (06/09/2026, item 4): classe_nice
# existia no modelo e já era usada pelo motor de busca/risco, mas nenhum
# fluxo comercial jamais capturava um valor real. ---


def test_consulta_interna_aceita_classe_nice_valida() -> None:
    dados = ConsultaOperadorInput.model_validate(
        {"marca": "NORTE STUDIO", "classe_nice": "25", "nome": "Cliente Teste", "email": "cliente@example.com"}
    )

    assert dados.classe_nice == "25"


def test_consulta_interna_rejeita_classe_nice_invalida() -> None:
    with pytest.raises(ValueError):
        ConsultaOperadorInput.model_validate(
            {"marca": "NORTE STUDIO", "classe_nice": "99", "nome": "Cliente Teste", "email": "cliente@example.com"}
        )


def test_consulta_interna_classe_nice_vazia_vira_none() -> None:
    dados = ConsultaOperadorInput.model_validate(
        {"marca": "NORTE STUDIO", "classe_nice": "  ", "nome": "Cliente Teste", "email": "cliente@example.com"}
    )

    assert dados.classe_nice is None


def test_consulta_interna_sem_classe_nice_e_opcional() -> None:
    dados = ConsultaOperadorInput.model_validate(
        {"marca": "NORTE STUDIO", "nome": "Cliente Teste", "email": "cliente@example.com"}
    )

    assert dados.classe_nice is None


@pytest.mark.asyncio
async def test_listar_classes_nice_devolve_catalogo_completo() -> None:
    classes = await listar_classes_nice(usuario_teste())

    assert len(classes) == 45
    assert {"codigo": "25", "titulo": classes[24]["titulo"]} in classes


@pytest.mark.asyncio
async def test_consulta_propaga_classe_nice_para_a_pesquisa() -> None:
    class SessaoConsulta(FakeSession):
        async def refresh(self, obj: object) -> None:
            if isinstance(obj, PesquisaMarca):
                obj.id = "pesquisa-com-classe"

    session = SessaoConsulta()

    dados = ConsultaOperadorInput(
        marca="NORTE STUDIO", classe_nice="25", nome="Cliente Teste", email="cliente@example.com"
    )
    await criar_consulta(dados, session, usuario_teste())

    pesquisa = next(item for item in session.adicionados if isinstance(item, PesquisaMarca))
    assert pesquisa.classe_nice == "25"


@pytest.mark.asyncio
async def test_consulta_sempre_cria_lead_para_o_comercial() -> None:
    class SessaoConsulta(FakeSession):
        async def refresh(self, obj: object) -> None:
            if isinstance(obj, PesquisaMarca):
                obj.id = "pesquisa-com-lead"

    session = SessaoConsulta()

    dados = ConsultaOperadorInput(marca="NORTE STUDIO", nome="Cliente Teste", email="cliente@example.com")
    await criar_consulta(dados, session, usuario_teste())

    lead = next(item for item in session.adicionados if isinstance(item, Lead))
    assert lead.email == "cliente@example.com"
    assert lead.nome == "Cliente Teste"
    pesquisa = next(item for item in session.adicionados if isinstance(item, PesquisaMarca))
    assert pesquisa.lead_id == lead.id
