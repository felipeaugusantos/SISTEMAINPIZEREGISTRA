from app.trademarks.registrability import (
    construir_indicador_deterministico,
    construir_matriz_registrabilidade,
    construir_prognostico_registrabilidade,
)


def _por_codigo(matriz: dict) -> dict[str, dict]:
    return {regra["codigo"]: regra for regra in matriz["regras"]}


def _matriz(nivel_risco: str, pontuacao: int, *, alto_renome: bool = False) -> dict:
    return construir_matriz_registrabilidade(
        marca="SINAL FANTASIA",
        atividade="Consultoria",
        classe_nice="35",
        relatorio={
            "total": 3 if alto_renome else 0,
            "matriz_afinidade_status": "validada",
            "classes_atividade": [{"codigo": "35"}],
            "qualidade_base": {"status": "adequada", "avisos": []},
            "itens": [{"numero": "1", "alto_renome": True}] if alto_renome else [],
        },
        pontuacao_risco=pontuacao,
        nivel_risco=nivel_risco,
    )


def test_prognostico_desfavoravel_quando_ha_impedimento_substantivo() -> None:
    prog = construir_prognostico_registrabilidade(_matriz("critico", 82, alto_renome=True))
    assert prog["veredito"] == "desfavoravel"
    criterios = {motivo["criterio"] for motivo in prog["motivos"]}
    assert "Disponibilidade e anterioridades" in criterios
    assert "Alto renome e proteção especial" in criterios


def test_prognostico_atencao_quando_risco_moderado() -> None:
    prog = construir_prognostico_registrabilidade(_matriz("moderado", 55))
    assert prog["veredito"] == "atencao"


def test_prognostico_favoravel_quando_sem_impedimento_substantivo() -> None:
    prog = construir_prognostico_registrabilidade(_matriz("baixo", 10))
    assert prog["veredito"] == "favoravel"
    assert prog["motivos"] == []
    # Critérios que dependem de declaração aparecem como pendências transparentes.
    assert "Distintividade" in prog["pendencias"]
    assert "garantia de registro" in prog["ressalva"]


def test_matriz_aponta_disponibilidade_e_alto_renome_como_possiveis_impedimentos() -> None:
    matriz = construir_matriz_registrabilidade(
        marca="ACME",
        atividade="Serviços de tecnologia",
        classe_nice="42",
        relatorio={
            "total": 7,
            "matriz_afinidade_status": "validada",
            "qualidade_base": {"status": "adequada", "avisos": []},
            "classes_atividade": [{"codigo": "42"}],
            "itens": [{"numero": "123", "alto_renome": True}],
        },
        pontuacao_risco=82,
        nivel_risco="critico",
    )

    regras = _por_codigo(matriz)
    assert matriz["status_geral"] == "possivel_impedimento"
    assert regras["disponibilidade"]["status"] == "possivel_impedimento"
    assert regras["alto_renome"]["status"] == "possivel_impedimento"
    assert regras["especificacao"]["status"] == "atendido"


def test_matriz_nao_trata_criterio_sem_dados_como_favoravel() -> None:
    matriz = construir_matriz_registrabilidade(
        marca="ACME",
        atividade=None,
        classe_nice=None,
        relatorio={},
        pontuacao_risco=None,
        nivel_risco=None,
    )

    regras = _por_codigo(matriz)
    assert regras["liceidade"]["status"] == "nao_analisado"
    assert regras["distintividade"]["status"] == "nao_analisado"
    assert regras["veracidade"]["status"] == "nao_analisado"
    assert regras["legitimidade"]["status"] == "nao_analisado"
    assert matriz["cobertura_percentual"] < 50
    assert "não são considerados favoráveis" in matriz["aviso"]


def test_matriz_classifica_baixo_risco_como_disponibilidade_atendida() -> None:
    matriz = construir_matriz_registrabilidade(
        marca="SINAL FANTASIA",
        atividade="Consultoria",
        classe_nice="35",
        relatorio={
            "total": 1,
            "matriz_afinidade_status": "validada",
            "classes_atividade": [{"codigo": "35"}],
            "qualidade_base": {"status": "adequada", "avisos": []},
            "itens": [],
        },
        pontuacao_risco=12,
        nivel_risco="baixo",
    )

    assert _por_codigo(matriz)["disponibilidade"]["status"] == "atendido"
    assert matriz["status_geral"] == "incompleta"


def test_assistente_preenchido_conclui_criterios_aplicaveis() -> None:
    matriz = construir_matriz_registrabilidade(
        marca="SINAL FANTASIA",
        atividade="Consultoria",
        classe_nice="35",
        relatorio={
            "total": 0,
            "matriz_afinidade_status": "validada",
            "classes_atividade": [{"codigo": "35"}],
            "qualidade_base": {"status": "adequada", "avisos": []},
            "itens": [],
        },
        pontuacao_risco=10,
        nivel_risco="baixo",
        dados_complementares={
            "forma_apresentacao": "nominativa",
            "usa_simbolo_oficial": False,
            "conteudo_potencialmente_ofensivo": False,
            "termo_generico_descritivo": False,
            "significado": "Expressão de fantasia",
            "possui_alegacao_origem_qualidade": False,
            "atividade_compativel": True,
            "requerente_tipo": "pessoa_juridica",
            "atividade_requerente": "Consultoria",
            "usa_nome_ou_imagem_terceiro": False,
            "usa_obra_terceiro": False,
            "usa_indicacao_geografica": False,
            "documentos_obrigatorios_disponiveis": True,
            "deposito_realizado": False,
        },
    )

    assert matriz["cobertura_percentual"] == 100
    assert matriz["contagens"]["nao_analisado"] == 0
    assert matriz["contagens"]["nao_aplicavel"] == 1
    assert matriz["status_geral"] == "sem_impedimento_automatico"


def test_indicador_deterministico_limita_cenario_desfavoravel() -> None:
    indicador = construir_indicador_deterministico(
        _matriz("critico", 82, alto_renome=True),
        82,
    )

    assert indicador["veredito"] == "desfavoravel"
    assert indicador["indice"] <= 40
    assert indicador["faixa_inferior"] <= indicador["indice"]
    assert indicador["faixa_superior"] >= indicador["indice"]
    assert "Não é probabilidade histórica" in indicador["aviso"]


def test_indicador_deterministico_preserva_leitura_favoravel() -> None:
    indicador = construir_indicador_deterministico(_matriz("baixo", 10), 10)

    assert indicador["veredito"] == "favoravel"
    assert indicador["indice"] >= 60
    assert indicador["cobertura_percentual"] > 0
