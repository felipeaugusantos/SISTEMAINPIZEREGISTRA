from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.pesquisas import construir_resumo_publico
from app.ratelimit import RateLimiter
from app.schemas import BrandingConfig
from app.security_ext import validar_forca_senha
from app.tenancy import OrganizacaoAtual, validar_limite_pesquisas, validar_limite_usuarios
from app.trademarks.affinity import avaliar_afinidade
from app.trademarks.learning import validar_modelo_para_cliente
from app.trademarks.nice import mapear_atividade
from tests.conftest import FakeResult, FakeSession
from tests.test_relatorio_pdf import _relatorio_exemplo


def test_resumo_publico_nao_expoe_processos_ou_matriz_interna() -> None:
    resumo = construir_resumo_publico(_relatorio_exemplo(com_prognostico=True))
    payload = resumo.model_dump()

    assert "itens" not in payload
    assert "matriz_afinidade_status" not in payload
    assert payload["total"] == 1
    assert payload["detalhes_internos_disponiveis"] is True


def test_branding_rejeita_cor_e_logo_inseguras() -> None:
    with pytest.raises(ValidationError):
        BrandingConfig(cor_primaria="verde", logo_url="http://exemplo.test/logo.png")

    valido = BrandingConfig(cor_primaria="#006B4F", logo_url="/static/logo.svg")
    assert valido.cor_primaria == "#006B4F"


def test_senha_forte_exige_as_quatro_categorias() -> None:
    assert validar_forca_senha("Marca-Segura-2026!") == "Marca-Segura-2026!"
    with pytest.raises(ValueError):
        validar_forca_senha("somente-minusculas")


@pytest.mark.asyncio
async def test_limite_de_usuarios_do_plano_e_aplicado() -> None:
    organizacao = SimpleNamespace(plano=SimpleNamespace(limites={"usuarios": 2}))
    session = FakeSession([FakeResult(scalar=organizacao), FakeResult(scalar=2)])

    with pytest.raises(HTTPException) as erro:
        await validar_limite_usuarios(session, 1)

    assert erro.value.status_code == 409
    assert "FOR UPDATE" in str(session.executados[0]).upper()


@pytest.mark.asyncio
async def test_limite_de_pesquisas_serializa_criacoes_do_mes() -> None:
    organizacao = OrganizacaoAtual(
        id=42,
        nome="Empresa",
        slug="empresa",
        plano="basico",
        modulos=frozenset({"consulta"}),
        limites={"pesquisas_mes": 10},
        branding={},
    )
    session = FakeSession([FakeResult(scalar=42), FakeResult(scalar=2)])

    await validar_limite_pesquisas(session, organizacao)

    assert "FOR UPDATE" in str(session.executados[0]).upper()


def test_afinidade_media_e_normalizada_como_moderada() -> None:
    resultado = avaliar_afinidade(
        classes_atividade=["35"],
        classes_processo=["41"],
        matriz=[
            SimpleNamespace(
                classe_origem="35",
                classe_destino="41",
                nivel="media",
                justificativa="Comercio relacionado ao servico",
                status_revisao="aprovada",
            )
        ],
    )
    assert resultado.nivel == "moderada"
    assert resultado.rotulo == "Afinidade moderada"


def test_nice_informa_confianca_da_inferencia() -> None:
    candidatos = mapear_atividade("loja e venda de roupas pela internet")
    assert candidatos
    assert candidatos[0].confianca > 0
    assert {item.codigo for item in candidatos} >= {"25", "35"}


def test_modelo_desbalanceado_nao_pode_ser_ativado() -> None:
    modelo = SimpleNamespace(
        metricas={"recall": 0.9, "especificidade": 0.8, "brier": 0.1, "ece": 0.05},
        dataset={
            "total": 1000,
            "teste": 150,
            "bootstrap_modelos": 12,
            "positivos": 980,
            "negativos": 20,
        },
    )
    controle = SimpleNamespace(
        minimo_recall=0.8,
        minimo_especificidade=0.7,
        maximo_brier=0.25,
        maximo_ece=0.12,
        minimo_amostras_modelo=300,
        minimo_amostras_teste=50,
        minimo_revisoes_humanas=30,
    )
    bloqueios = validar_modelo_para_cliente(modelo, controle, revisoes_humanas=0)
    assert any("desbalanceada" in item for item in bloqueios)
    assert any("Revisões humanas insuficientes" in item for item in bloqueios)
    bloqueios_candidato = validar_modelo_para_cliente(
        modelo,
        controle,
        revisoes_humanas=0,
        incluir_revisoes_humanas=False,
    )
    assert not any("Revisões humanas insuficientes" in item for item in bloqueios_candidato)


def test_rate_limit_isola_escopos_e_informa_retry_after() -> None:
    primeiro = RateLimiter(limite=1, janela_segundos=30, escopo="login-teste")
    segundo = RateLimiter(limite=1, janela_segundos=30, escopo="pesquisa-teste")

    primeiro.aplicar("127.0.0.1")
    segundo.aplicar("127.0.0.1")
    with pytest.raises(HTTPException) as erro:
        primeiro.aplicar("127.0.0.1")

    assert erro.value.status_code == 429
    assert int(erro.value.headers["Retry-After"]) >= 1
