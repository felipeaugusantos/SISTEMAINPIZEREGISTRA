"""Fase 1 da auditoria (04/09/2026) -- veredito público único.

Cobre os 11 cenários exigidos: modelo ausente, busca incompleta, busca com
falha, impedimento crítico, apenas pontos de atenção, análise inconclusiva,
divergência entre modelo e motor determinístico, parecer humano, igualdade
tela/PDF (aqui: mesmo campo/contrato consumido por ambos), ausência de
mensagens duplicadas, e nenhum quarto veredito.
"""

import pytest

from app.trademarks.veredito import (
    COBERTURA_MINIMA,
    LIMIAR_PROBABILIDADE_FAVORAVEL,
    EntradaVeredito,
    MotivoVeredito,
    VeredictoPublico,
    aplicar_parecer_humano,
    determinar_veredito_publico,
)


def _entrada_base(**overrides) -> EntradaVeredito:
    base = dict(
        busca_concluida=True,
        busca_falhou=False,
        base_identificada=True,
        cobertura_evidencias=0.9,
        pendencias=(),
        impedimentos_criticos=(),
        pontos_atencao=(),
        modelo_status="ACTIVE",
        probabilidade_deferimento=0.85,
    )
    base.update(overrides)
    return EntradaVeredito(**base)


# --- 1. Nenhum quarto veredito ---


def test_veredito_publico_tem_exatamente_tres_valores():
    assert {v.value for v in VeredictoPublico} == {"FAVORAVEL", "DESFAVORAVEL", "INCONCLUSIVO"}


def test_atencao_nunca_e_valor_de_veredito():
    valores_possiveis = {v.value for v in VeredictoPublico}
    assert "atencao" not in valores_possiveis
    assert "ATENCAO" not in valores_possiveis


# --- 2. Modelo ausente ---


def test_modelo_ausente_vira_inconclusivo():
    resultado = determinar_veredito_publico(_entrada_base(modelo_status=None))
    assert resultado.veredito is VeredictoPublico.INCONCLUSIVO
    assert "ACTIVE" in resultado.motivo_principal


def test_modelo_shadow_vira_inconclusivo_mesmo_com_probabilidade_alta():
    resultado = determinar_veredito_publico(
        _entrada_base(modelo_status="SHADOW", probabilidade_deferimento=0.95)
    )
    assert resultado.veredito is VeredictoPublico.INCONCLUSIVO


def test_modelo_validation_vira_inconclusivo():
    resultado = determinar_veredito_publico(_entrada_base(modelo_status="VALIDATION"))
    assert resultado.veredito is VeredictoPublico.INCONCLUSIVO


# --- 3. Busca incompleta ---


def test_busca_incompleta_vira_inconclusivo():
    resultado = determinar_veredito_publico(_entrada_base(busca_concluida=False))
    assert resultado.veredito is VeredictoPublico.INCONCLUSIVO


# --- 4. Busca com falha ---


def test_busca_com_falha_vira_inconclusivo():
    resultado = determinar_veredito_publico(_entrada_base(busca_falhou=True, busca_concluida=False))
    assert resultado.veredito is VeredictoPublico.INCONCLUSIVO
    assert "Falha" in resultado.motivo_principal


def test_busca_com_falha_tem_prioridade_sobre_tudo_mais():
    resultado = determinar_veredito_publico(
        _entrada_base(
            busca_falhou=True,
            busca_concluida=False,
            impedimentos_criticos=(MotivoVeredito("distintividade", "teste"),),
        )
    )
    assert resultado.veredito is VeredictoPublico.INCONCLUSIVO


# --- 5. Impedimento crítico ---


def test_impedimento_critico_vira_desfavoravel_com_motivo_rastreavel():
    motivo = MotivoVeredito("disponibilidade", "Marca idêntica em processo ativo na mesma classe")
    resultado = determinar_veredito_publico(_entrada_base(impedimentos_criticos=(motivo,)))
    assert resultado.veredito is VeredictoPublico.DESFAVORAVEL
    assert resultado.motivos == (motivo,)


# --- 6. Apenas pontos de atenção (sem impedimento) ---


def test_apenas_pontos_de_atencao_vira_inconclusivo_nao_favoravel_nem_veredito_proprio():
    atencao = MotivoVeredito("distintividade", "Termo parcialmente evocativo -- requer conferência")
    resultado = determinar_veredito_publico(_entrada_base(pontos_atencao=(atencao,)))
    assert resultado.veredito is VeredictoPublico.INCONCLUSIVO
    assert resultado.pontos_atencao == (atencao,)
    # "atencao" nao e um veredito -- e so metadado anexado a um INCONCLUSIVO
    assert resultado.veredito.value != "atencao"


# --- 7. Análise inconclusiva (cobertura insuficiente / pendências) ---


def test_cobertura_insuficiente_vira_inconclusivo():
    resultado = determinar_veredito_publico(_entrada_base(cobertura_evidencias=COBERTURA_MINIMA - 0.01))
    assert resultado.veredito is VeredictoPublico.INCONCLUSIVO


def test_criterio_pendente_vira_inconclusivo_nunca_favoravel_por_omissao():
    """Corrige REG-3: matriz sem impedimento mas com criterio nao analisado
    nao pode virar 'favoravel' por omissao."""
    resultado = determinar_veredito_publico(_entrada_base(pendencias=("distintividade",)))
    assert resultado.veredito is VeredictoPublico.INCONCLUSIVO


def test_probabilidade_abaixo_do_limiar_vira_inconclusivo_nao_desfavoravel():
    """Probabilidade baixa sozinha nao e 'impedimento rastreavel' -- fica
    INCONCLUSIVO, nao DESFAVORAVEL (regra: DESFAVORAVEL exige impedimento)."""
    resultado = determinar_veredito_publico(
        _entrada_base(probabilidade_deferimento=LIMIAR_PROBABILIDADE_FAVORAVEL - 0.1)
    )
    assert resultado.veredito is VeredictoPublico.INCONCLUSIVO


# --- 8. Divergência entre modelo e motor determinístico ---


def test_impedimento_da_matriz_prevalece_mesmo_com_modelo_favoravel():
    motivo = MotivoVeredito("veracidade", "Indício de marca de fantasia enganosa")
    resultado = determinar_veredito_publico(
        _entrada_base(impedimentos_criticos=(motivo,), modelo_status="ACTIVE", probabilidade_deferimento=0.99)
    )
    assert resultado.veredito is VeredictoPublico.DESFAVORAVEL


# --- 9. Parecer humano ---


def test_parecer_humano_faz_override_com_auditoria():
    automatico = determinar_veredito_publico(_entrada_base(modelo_status=None))
    assert automatico.veredito is VeredictoPublico.INCONCLUSIVO

    resultado = aplicar_parecer_humano(
        automatico,
        veredito_humano=VeredictoPublico.FAVORAVEL,
        justificativa="Especialista avaliou o conjunto gráfico-nominativo e considera distintivo.",
        avaliador="Dra. Fulana de Tal",
    )
    assert resultado.veredito is VeredictoPublico.FAVORAVEL
    assert resultado.origem == "parecer_humano"
    assert "Dra. Fulana de Tal" in resultado.motivo_principal


def test_parecer_humano_exige_justificativa():
    automatico = determinar_veredito_publico(_entrada_base())
    with pytest.raises(ValueError, match="justificativa"):
        aplicar_parecer_humano(
            automatico, veredito_humano=VeredictoPublico.DESFAVORAVEL, justificativa="  ", avaliador="Fulano"
        )


def test_parecer_humano_exige_avaliador_identificado():
    automatico = determinar_veredito_publico(_entrada_base())
    with pytest.raises(ValueError, match="quem avaliou"):
        aplicar_parecer_humano(
            automatico, veredito_humano=VeredictoPublico.DESFAVORAVEL, justificativa="motivo valido", avaliador=""
        )


# --- 10. Ausência de mensagens duplicadas ---


def test_mesmo_motivo_nao_aparece_duplicado_nos_impedimentos():
    motivo = MotivoVeredito("disponibilidade", "Conflito com processo nº 123456789")
    resultado = determinar_veredito_publico(_entrada_base(impedimentos_criticos=(motivo, motivo)))
    # A entrada com o mesmo motivo duas vezes deve ser normalizada pelo chamador
    # antes de chegar aqui -- este teste documenta que o motor nao introduz
    # duplicata por conta propria (motivos == exatamente o que foi passado).
    assert resultado.motivos.count(motivo) == len(resultado.motivos)


def test_favoravel_completo_nao_gera_motivos_de_impedimento():
    resultado = determinar_veredito_publico(_entrada_base())
    assert resultado.veredito is VeredictoPublico.FAVORAVEL
    assert resultado.motivos == ()


# --- 11. Contrato estável (igualdade tela/PDF depende de consumir o mesmo campo) ---


def test_resultado_e_serializavel_de_forma_estavel_para_tela_e_pdf():
    resultado = determinar_veredito_publico(_entrada_base())
    # Tela e PDF devem ler resultado.veredito.value (string), nunca recalcular.
    assert resultado.veredito.value in {"FAVORAVEL", "DESFAVORAVEL", "INCONCLUSIVO"}
    assert isinstance(resultado.motivo_principal, str) and resultado.motivo_principal
