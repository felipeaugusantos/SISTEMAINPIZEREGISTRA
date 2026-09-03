from types import SimpleNamespace

from app.prospeccao_triagem import (
    LIMITE_BUSCA_TRIAGEM,
    ClassificacaoTriagemProspect,
    classificar,
    extrair_marca_candidata,
)
from app.search import OcorrenciaBusca
from app.search_ranking import calcular_score_nominativo

# --- Fase 4 do Radar de Prospecção (03/09/2026) -- triagem de marca --------
#
# Garantia central exigida pelo usuário: a triagem NUNCA pode devolver algo
# equivalente a "disponível"/"livre". Testado explicitamente abaixo.


def test_classificacao_nunca_inclui_disponivel_ou_livre() -> None:
    valores = {item.value for item in ClassificacaoTriagemProspect}
    proibidas = {"disponivel", "livre", "disponivel_para_registro", "sem_conflito"}
    assert valores.isdisjoint(proibidas)
    assert valores == {
        "nao_localizado",
        "resultado_semelhante",
        "resultado_relevante_localizado",
        "inconclusivo",
        "analise_humana_necessaria",
    }


def _ocorrencia(titulo: str, numero: str, criterios: list[str], similaridade: float = 0.5, **kwargs) -> OcorrenciaBusca:
    score = calcular_score_nominativo(
        criterios=criterios, similaridade=similaridade, processo=numero, titulo=titulo, **kwargs
    )
    return OcorrenciaBusca(processo=SimpleNamespace(titulo=titulo, numero=numero), criterios=criterios, score=score)


def test_extrair_marca_candidata_prefere_nome_fantasia() -> None:
    assert extrair_marca_candidata("PADARIA DO JOAO LTDA", "Padaria Sabor & Cia") == "Padaria Sabor & Cia"


def test_extrair_marca_candidata_remove_sufixos_societarios() -> None:
    assert extrair_marca_candidata("PADARIA DO JOAO LTDA", None) == "PADARIA DO JOAO"
    assert extrair_marca_candidata("TECH SOLUCOES S/A", None) == "TECH SOLUCOES"
    assert extrair_marca_candidata("FULANO COMERCIO ME", None) == "FULANO COMERCIO"


def test_extrair_marca_candidata_so_sufixos_retorna_none() -> None:
    assert extrair_marca_candidata("LTDA ME", None) is None
    assert extrair_marca_candidata("", None) is None


def test_classificar_zero_resultados_e_nao_localizado() -> None:
    classificacao, justificativa = classificar(0, [], "Marca Nova")
    assert classificacao == ClassificacaoTriagemProspect.NAO_LOCALIZADO
    assert "Marca Nova" in justificativa


def test_classificar_muitos_resultados_pede_analise_humana() -> None:
    ocorrencias = [_ocorrencia("ALGO", "111", ["Aproximação nominativa"])]
    classificacao, justificativa = classificar(LIMITE_BUSCA_TRIAGEM + 1, ocorrencias, "Termo Genérico")
    assert classificacao == ClassificacaoTriagemProspect.ANALISE_HUMANA_NECESSARIA
    assert "50" in justificativa or str(LIMITE_BUSCA_TRIAGEM) in justificativa


def test_classificar_nome_identico_e_resultado_relevante() -> None:
    ocorrencias = [_ocorrencia("PADARIA DO JOAO", "900123456", ["Nome idêntico"], similaridade=1.0)]
    classificacao, justificativa = classificar(1, ocorrencias, "PADARIA DO JOAO")
    assert classificacao == ClassificacaoTriagemProspect.RESULTADO_RELEVANTE_LOCALIZADO
    assert "900123456" in justificativa


def test_classificar_radical_semelhante_e_resultado_semelhante(monkeypatch) -> None:
    # Score isolado do desconto de termo comum (testado à parte) -- só o
    # limiar de faixa importa aqui.
    from app import prospeccao_triagem as modulo

    monkeypatch.setattr(modulo, "termos_comuns_do_match", lambda titulo, marca: ())
    ocorrencias = [_ocorrencia("PADARIA DO JOAOZINHO", "900123456", ["Radical semelhante"], similaridade=0.6)]
    classificacao, _ = modulo.classificar(1, ocorrencias, "PADARIA DO JOAO")
    assert classificacao == ClassificacaoTriagemProspect.RESULTADO_SEMELHANTE


def test_classificar_aproximacao_fraca_e_inconclusivo(monkeypatch) -> None:
    from app import prospeccao_triagem as modulo

    monkeypatch.setattr(modulo, "termos_comuns_do_match", lambda titulo, marca: ())
    ocorrencias = [_ocorrencia("OUTRA COISA", "900123456", ["Aproximação nominativa"], similaridade=0.1)]
    classificacao, _ = modulo.classificar(1, ocorrencias, "PADARIA DO JOAO")
    assert classificacao == ClassificacaoTriagemProspect.INCONCLUSIVO


def test_classificar_match_so_por_termo_comum_rebaixa_para_inconclusivo(monkeypatch) -> None:
    # Mesmo com score na faixa de "resultado_semelhante", se o match inteiro
    # nasceu de termo de uso comum (achado da Frente 1 do motor de peso
    # semântico, já em produção), a triagem não pode soar mais confiante do
    # que realmente é -- rebaixa para inconclusivo.
    from app import prospeccao_triagem as modulo

    monkeypatch.setattr(modulo, "termos_comuns_do_match", lambda titulo, marca: ("plus",))
    ocorrencias = [_ocorrencia("BETA PLUS", "900123456", ["Elemento do nome"], similaridade=0.8)]
    classificacao, justificativa = modulo.classificar(1, ocorrencias, "ALFA PLUS")

    assert classificacao == ClassificacaoTriagemProspect.INCONCLUSIVO
    assert "plus" in justificativa.lower()
