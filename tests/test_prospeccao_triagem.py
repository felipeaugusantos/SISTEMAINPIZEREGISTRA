from types import SimpleNamespace

from app.prospeccao_triagem import (
    LIMITE_BUSCA_TRIAGEM,
    ClassificacaoTriagemProspect,
    TitularidadeAmpla,
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
        "ja_e_titular",
        "possui_outra_marca_registrada",
    }


def _ocorrencia(
    titulo: str, numero: str, criterios: list[str], similaridade: float = 0.5, titulares: list | None = None, **kwargs
) -> OcorrenciaBusca:
    score = calcular_score_nominativo(
        criterios=criterios, similaridade=similaridade, processo=numero, titulo=titulo, **kwargs
    )
    processo = SimpleNamespace(titulo=titulo, numero=numero, titulares=titulares or [])
    return OcorrenciaBusca(processo=processo, criterios=criterios, score=score)


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


# --- Achado de 05/09/2026: gate de volume mais preciso e nova classificação
# factual "já é titular" (não afeta app.search.buscar_marcas, usado também
# pela pesquisa formal -- escopo só da triagem do Radar). ---


def test_classificar_volume_alto_explicado_por_termo_comum_e_nao_localizado(monkeypatch) -> None:
    # Antes: >50 ocorrências ia direto para "análise humana necessária", sem
    # olhar para a ocorrência mais relevante. Agora, se a mais próxima só
    # coincide em termo de uso comum (ex.: "MOCOCA" -- radical de alta
    # frequência no corpus), o volume alto é explicado e vira não_localizado.
    from app import prospeccao_triagem as modulo

    monkeypatch.setattr(modulo, "termos_comuns_do_match", lambda titulo, marca: ("mococ",))
    ocorrencias = [_ocorrencia("LATICINIOS MOCOCA", "900123456", ["Radical semelhante"], similaridade=0.2)]
    classificacao, justificativa = modulo.classificar(LIMITE_BUSCA_TRIAGEM + 1, ocorrencias, "ALEA MOCOCA II")

    assert classificacao == ClassificacaoTriagemProspect.NAO_LOCALIZADO
    assert "mococ" in justificativa.lower()


def test_classificar_volume_alto_com_score_forte_ainda_e_relevante(monkeypatch) -> None:
    # Volume alto não deve mais, por si só, esconder um conflito real: se a
    # ocorrência mais próxima tem score forte (sem termo comum isolado), a
    # classificação continua sendo pelo score, não pelo volume.
    from app import prospeccao_triagem as modulo

    monkeypatch.setattr(modulo, "termos_comuns_do_match", lambda titulo, marca: ())
    ocorrencias = [_ocorrencia("PADARIA DO JOAO", "900123456", ["Nome idêntico"], similaridade=1.0)]
    classificacao, _ = modulo.classificar(LIMITE_BUSCA_TRIAGEM + 1, ocorrencias, "PADARIA DO JOAO")

    assert classificacao == ClassificacaoTriagemProspect.RESULTADO_RELEVANTE_LOCALIZADO


def test_classificar_volume_alto_e_score_fraco_ainda_pede_analise_humana(monkeypatch) -> None:
    # Ambíguo de verdade (não é só termo comum, mas também não é forte o
    # bastante) com volume alto continua pedindo análise humana.
    from app import prospeccao_triagem as modulo

    monkeypatch.setattr(modulo, "termos_comuns_do_match", lambda titulo, marca: ())
    ocorrencias = [_ocorrencia("ALGO", "111", ["Aproximação nominativa"])]
    classificacao, justificativa = modulo.classificar(LIMITE_BUSCA_TRIAGEM + 1, ocorrencias, "Termo Genérico")

    assert classificacao == ClassificacaoTriagemProspect.ANALISE_HUMANA_NECESSARIA
    assert str(LIMITE_BUSCA_TRIAGEM) in justificativa


def test_classificar_prospect_ja_e_titular() -> None:
    titular = SimpleNamespace(nome="Padaria do João Ltda")
    ocorrencias = [
        _ocorrencia("PADARIA DO JOAO", "900123456", ["Nome idêntico"], similaridade=1.0, titulares=[titular])
    ]
    classificacao, justificativa = classificar(1, ocorrencias, "Padaria do João", razao_social="Padaria do João Ltda")

    assert classificacao == ClassificacaoTriagemProspect.JA_E_TITULAR
    assert "900123456" in justificativa


def test_classificar_titular_diferente_nao_e_ja_titular() -> None:
    titular = SimpleNamespace(nome="Outra Empresa Ltda")
    ocorrencias = [
        _ocorrencia("PADARIA DO JOAO", "900123456", ["Nome idêntico"], similaridade=1.0, titulares=[titular])
    ]
    classificacao, _ = classificar(1, ocorrencias, "Padaria do João", razao_social="Padaria do João Ltda")

    assert classificacao == ClassificacaoTriagemProspect.RESULTADO_RELEVANTE_LOCALIZADO


def test_classificar_sem_razao_social_nao_quebra() -> None:
    ocorrencias = [_ocorrencia("PADARIA DO JOAO", "900123456", ["Nome idêntico"], similaridade=1.0)]
    classificacao, _ = classificar(1, ocorrencias, "Padaria do João")
    assert classificacao == ClassificacaoTriagemProspect.RESULTADO_RELEVANTE_LOCALIZADO


# --- Achado de 08/09/2026: checagem AMPLA de titularidade (qualquer marca do
# prospect no INPI, não só a pesquisada) -- base local (nome_normalizado) e,
# como fallback best-effort, busca ao vivo por CNPJ no site do INPI. ---


def test_classificar_com_titularidade_ampla_e_possui_outra_marca() -> None:
    titular = TitularidadeAmpla(descricao='"BETA TECH" (processo 900999888)')
    classificacao, justificativa = classificar(0, [], "Marca Nova", titular_qualquer_marca=titular)

    assert classificacao == ClassificacaoTriagemProspect.POSSUI_OUTRA_MARCA_REGISTRADA
    assert "BETA TECH" in justificativa


def test_classificar_titularidade_ampla_nao_sobrepoe_ja_e_titular() -> None:
    # JA_E_TITULAR (achado sobre a marca especificamente pesquisada) é mais
    # específico e deve vencer mesmo se a checagem ampla também achou algo.
    titular_ocorrencia = SimpleNamespace(nome="Padaria do João Ltda")
    ocorrencias = [
        _ocorrencia(
            "PADARIA DO JOAO", "900123456", ["Nome idêntico"], similaridade=1.0, titulares=[titular_ocorrencia]
        )
    ]
    titular_ampla = TitularidadeAmpla(descricao='"OUTRA MARCA" (processo 900111222)')
    classificacao, justificativa = classificar(
        1, ocorrencias, "Padaria do João", razao_social="Padaria do João Ltda", titular_qualquer_marca=titular_ampla
    )

    assert classificacao == ClassificacaoTriagemProspect.JA_E_TITULAR
    assert "900123456" in justificativa


def test_classificar_sem_titularidade_ampla_segue_fluxo_normal() -> None:
    classificacao, _ = classificar(0, [], "Marca Nova", titular_qualquer_marca=None)
    assert classificacao == ClassificacaoTriagemProspect.NAO_LOCALIZADO


async def test_buscar_titularidade_ampla_acha_por_razao_social() -> None:
    from app import prospeccao_triagem as modulo

    processo = SimpleNamespace(titulo="BETA TECH", numero="900999888")

    class FakeResultado:
        def scalars(self):
            return self

        def first(self):
            return processo

    class FakeSession:
        async def execute(self, _consulta):
            return FakeResultado()

    resultado = await modulo.buscar_titularidade_ampla(FakeSession(), "Beta Tech Solucoes Ltda", None)

    assert resultado is not None
    assert "BETA TECH" in resultado.descricao
    assert "900999888" in resultado.descricao


async def test_buscar_titularidade_ampla_acha_por_nome_fantasia() -> None:
    from app import prospeccao_triagem as modulo

    processo = SimpleNamespace(titulo="GAMA SOLUCOES", numero="900777666")

    class FakeResultado:
        def scalars(self):
            return self

        def first(self):
            return processo

    class FakeSession:
        async def execute(self, _consulta):
            return FakeResultado()

    resultado = await modulo.buscar_titularidade_ampla(FakeSession(), "Razao Social Generica Ltda", "Gama Solucoes")

    assert resultado is not None
    assert "GAMA SOLUCOES" in resultado.descricao


async def test_buscar_titularidade_ampla_nao_acha_cai_para_busca_ao_vivo_desligada() -> None:
    # Sem nada na base local e sem a flag de busca ao vivo ligada (padrão,
    # settings.prospeccao_titularidade_inpi_ao_vivo_enabled=False),
    # buscar_titularidade_inpi_ao_vivo devolve None -- resultado final None.
    from app import prospeccao_triagem as modulo

    class FakeResultado:
        def scalars(self):
            return self

        def first(self):
            return None

    class FakeSession:
        async def execute(self, _consulta):
            return FakeResultado()

    resultado = await modulo.buscar_titularidade_ampla(
        FakeSession(), "Empresa Sem Marca Ltda", None, "07526557000100"
    )

    assert resultado is None
