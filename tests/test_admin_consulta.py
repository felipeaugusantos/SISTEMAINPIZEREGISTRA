from pathlib import Path

import pytest
from conftest import FakeSession, usuario_teste

from app.api.consulta import ConsultaOperadorInput, criar_consulta
from app.models import Lead, PesquisaMarca


def test_consulta_interna_aceita_atividade_ausente() -> None:
    dados = ConsultaOperadorInput.model_validate({"marca": "NORTE STUDIO"})

    assert dados.atividade is None


def test_consulta_interna_normaliza_atividade_vazia() -> None:
    dados = ConsultaOperadorInput.model_validate(
        {"marca": "NORTE STUDIO", "atividade": "   "}
    )

    assert dados.atividade is None


def test_formulario_admin_nao_exige_atividade() -> None:
    html = Path("app/web/admin-consulta.html").read_text(encoding="utf-8")

    campo = html.split('name="atividade"', 1)[1].split(">", 1)[0]
    assert "required" not in campo
    assert "(opcional)" in html


@pytest.mark.asyncio
async def test_consulta_sem_email_nao_cria_lead_vazio() -> None:
    class SessaoConsulta(FakeSession):
        async def refresh(self, obj: object) -> None:
            if isinstance(obj, PesquisaMarca):
                obj.id = "pesquisa-sem-lead"

    session = SessaoConsulta()

    resposta = await criar_consulta(
        ConsultaOperadorInput(marca="NORTE STUDIO"), session, usuario_teste()
    )

    assert resposta.lead_id is None
    assert not any(isinstance(item, Lead) for item in session.adicionados)
    pesquisa = next(item for item in session.adicionados if isinstance(item, PesquisaMarca))
    assert pesquisa.atividade is None
    assert pesquisa.lead_id is None
