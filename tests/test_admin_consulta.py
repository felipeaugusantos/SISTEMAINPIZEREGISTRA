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
# fluxo comercial jamais capturava um valor real. Decisão do usuário: várias
# classes viram várias PesquisaMarca (uma por classe), todas no mesmo lead --
# não uma lista dentro de uma única pesquisa. ---


class SessaoConsultaComId(FakeSession):
    """Simula o id gerado (uuid4, client-side default) que só session.refresh
    (não flush) preenche de forma confiável nos testes -- um contador
    incremental para distinguir várias pesquisas da mesma requisição."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._proximo_id = 1

    async def refresh(self, obj: object) -> None:
        if isinstance(obj, PesquisaMarca):
            obj.id = f"pesquisa-{self._proximo_id}"
            self._proximo_id += 1


def test_consulta_interna_aceita_classes_nice_validas() -> None:
    dados = ConsultaOperadorInput.model_validate(
        {
            "marca": "NORTE STUDIO",
            "classes_nice": ["25", "35"],
            "nome": "Cliente Teste",
            "email": "cliente@example.com",
        }
    )

    assert dados.classes_nice == ["25", "35"]


def test_consulta_interna_rejeita_classe_nice_invalida() -> None:
    with pytest.raises(ValueError):
        ConsultaOperadorInput.model_validate(
            {"marca": "NORTE STUDIO", "classes_nice": ["99"], "nome": "Cliente Teste", "email": "cliente@example.com"}
        )


def test_consulta_interna_remove_classes_repetidas_e_vazias() -> None:
    dados = ConsultaOperadorInput.model_validate(
        {
            "marca": "NORTE STUDIO",
            "classes_nice": ["25", "25", " ", "35"],
            "nome": "Cliente Teste",
            "email": "cliente@example.com",
        }
    )

    assert dados.classes_nice == ["25", "35"]


def test_consulta_interna_sem_classes_nice_e_opcional() -> None:
    dados = ConsultaOperadorInput.model_validate(
        {"marca": "NORTE STUDIO", "nome": "Cliente Teste", "email": "cliente@example.com"}
    )

    assert dados.classes_nice == []


@pytest.mark.asyncio
async def test_listar_classes_nice_devolve_catalogo_completo() -> None:
    classes = await listar_classes_nice(usuario_teste())

    assert len(classes) == 45
    assert {"codigo": "25", "titulo": classes[24]["titulo"]} in classes


@pytest.mark.asyncio
async def test_consulta_sem_classes_cria_uma_unica_pesquisa_sem_recorte() -> None:
    session = SessaoConsultaComId()

    dados = ConsultaOperadorInput(marca="NORTE STUDIO", nome="Cliente Teste", email="cliente@example.com")
    resultado = await criar_consulta(dados, session, usuario_teste())

    pesquisas = [item for item in session.adicionados if isinstance(item, PesquisaMarca)]
    assert len(pesquisas) == 1
    assert pesquisas[0].classe_nice is None
    assert len(resultado.itens) == 1


@pytest.mark.asyncio
async def test_consulta_com_varias_classes_cria_uma_pesquisa_por_classe() -> None:
    session = SessaoConsultaComId()

    dados = ConsultaOperadorInput(
        marca="NORTE STUDIO", classes_nice=["25", "35"], nome="Cliente Teste", email="cliente@example.com"
    )
    resultado = await criar_consulta(dados, session, usuario_teste())

    pesquisas = [item for item in session.adicionados if isinstance(item, PesquisaMarca)]
    assert len(pesquisas) == 2
    assert {p.classe_nice for p in pesquisas} == {"25", "35"}
    assert all(p.lead_id == pesquisas[0].lead_id for p in pesquisas)
    assert len(resultado.itens) == 2
    assert {item.id for item in resultado.itens} == {"pesquisa-1", "pesquisa-2"}


@pytest.mark.asyncio
async def test_consulta_com_varias_marcas_e_classes_compartilha_oportunidade() -> None:
    session = SessaoConsultaComId()
    dados = ConsultaOperadorInput.model_validate(
        {
            "marcas": [
                {"marca": "NORTE STUDIO", "classes_nice": ["25", "35"]},
                {"marca": "NORTE CAFÉ", "atividade": "cafeteria", "classes_nice": ["30", "43"]},
            ],
            "nome": "Cliente Teste",
            "email": "cliente@example.com",
        }
    )

    resultado = await criar_consulta(dados, session, usuario_teste())

    pesquisas = [item for item in session.adicionados if isinstance(item, PesquisaMarca)]
    assert len(pesquisas) == 4
    assert {(p.marca, p.classe_nice) for p in pesquisas} == {
        ("NORTE STUDIO", "25"),
        ("NORTE STUDIO", "35"),
        ("NORTE CAFÉ", "30"),
        ("NORTE CAFÉ", "43"),
    }
    assert len({p.lead_id for p in pesquisas}) == 1
    assert {(item.marca, item.classe_nice) for item in resultado.itens} == {
        ("NORTE STUDIO", "25"),
        ("NORTE STUDIO", "35"),
        ("NORTE CAFÉ", "30"),
        ("NORTE CAFÉ", "43"),
    }


def test_consulta_exige_marca_no_formato_legado_ou_em_lote() -> None:
    with pytest.raises(ValueError, match="Informe ao menos uma marca"):
        ConsultaOperadorInput.model_validate({"nome": "Cliente Teste", "email": "cliente@example.com"})


def test_consulta_rejeita_combinacao_repetida() -> None:
    with pytest.raises(ValueError, match="Não repita"):
        ConsultaOperadorInput.model_validate(
            {
                "marcas": [
                    {"marca": "NORTE STUDIO", "classes_nice": ["25"]},
                    {"marca": " norte studio ", "classes_nice": ["25"]},
                ],
                "nome": "Cliente Teste",
                "email": "cliente@example.com",
            }
        )


def test_formulario_admin_permite_adicionar_outra_marca() -> None:
    html = Path("app/web/admin-consulta.html").read_text(encoding="utf-8")
    script = Path("app/web/static/admin-consulta.js").read_text(encoding="utf-8")

    assert 'id="consulta-marcas"' in html
    assert 'id="adicionar-marca"' in html
    assert "marcas:" in script
    assert "item.marca" in script
    assert "item.classe_nice" in script


@pytest.mark.asyncio
async def test_consulta_sempre_cria_lead_para_o_comercial() -> None:
    session = SessaoConsultaComId()

    dados = ConsultaOperadorInput(marca="NORTE STUDIO", nome="Cliente Teste", email="cliente@example.com")
    await criar_consulta(dados, session, usuario_teste())

    lead = next(item for item in session.adicionados if isinstance(item, Lead))
    assert lead.email == "cliente@example.com"
    assert lead.nome == "Cliente Teste"
    pesquisa = next(item for item in session.adicionados if isinstance(item, PesquisaMarca))
    assert pesquisa.lead_id == lead.id
