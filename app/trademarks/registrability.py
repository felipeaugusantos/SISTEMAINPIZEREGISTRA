from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

VERSAO_MATRIZ = "manual-inpi-2026-06-23-v2"
FONTE_MANUAL = "https://manualdemarcas.inpi.gov.br/projects/manual/wiki/05_Exame_substantivo"

StatusRegra = Literal[
    "atendido", "alerta", "possivel_impedimento", "nao_analisado", "nao_aplicavel"
]


@dataclass(frozen=True, slots=True)
class RegraRegistrabilidade:
    codigo: str
    criterio: str
    status: StatusRegra
    conclusao: str
    evidencia: str
    referencia: str
    automatizavel: bool


def _regra(
    codigo: str,
    criterio: str,
    status: StatusRegra,
    conclusao: str,
    evidencia: str,
    referencia: str,
    *,
    automatizavel: bool,
) -> RegraRegistrabilidade:
    return RegraRegistrabilidade(
        codigo=codigo,
        criterio=criterio,
        status=status,
        conclusao=conclusao,
        evidencia=evidencia,
        referencia=referencia,
        automatizavel=automatizavel,
    )


def _regra_disponibilidade(
    pontuacao: int | None,
    nivel: str | None,
    total_ocorrencias: int,
) -> RegraRegistrabilidade:
    if pontuacao is None or nivel is None:
        return _regra(
            "disponibilidade",
            "Disponibilidade e anterioridades",
            "nao_analisado",
            "Motor de conflito ainda não calculado",
            "Não existe avaliação determinística para esta pesquisa.",
            "Manual 5.11 e arts. 124, 125 e 126 da LPI",
            automatizavel=True,
        )
    if nivel in {"alto", "critico"}:
        return _regra(
            "disponibilidade",
            "Disponibilidade e anterioridades",
            "possivel_impedimento",
            "Anterioridades com potencial impeditivo",
            f"Risco {nivel}, {pontuacao} pontos e {total_ocorrencias} ocorrências localizadas.",
            "Manual 5.11 e art. 124, XIX, da LPI",
            automatizavel=True,
        )
    if nivel == "moderado":
        return _regra(
            "disponibilidade",
            "Disponibilidade e anterioridades",
            "alerta",
            "Conflitos exigem conferência profissional",
            f"Risco moderado, {pontuacao} pontos e {total_ocorrencias} ocorrências localizadas.",
            "Manual 5.11 e art. 124, XIX, da LPI",
            automatizavel=True,
        )
    return _regra(
        "disponibilidade",
        "Disponibilidade e anterioridades",
        "atendido",
        "Nenhum conflito relevante identificado pela triagem",
        f"Risco baixo, {pontuacao} pontos e {total_ocorrencias} ocorrências localizadas.",
        "Manual 5.11 e art. 124, XIX, da LPI",
        automatizavel=True,
    )


def _regra_sinal(marca: str, dados: dict) -> RegraRegistrabilidade:
    forma = dados.get("forma_apresentacao")
    descricao = dados.get("descricao_visual")
    if not forma:
        return _regra(
            "sinal_visual",
            "Sinal visualmente perceptível",
            "nao_analisado",
            "Forma de apresentação não informada",
            f"A busca contém “{marca}”, mas não informa como o sinal será apresentado.",
            "Manual 5, art. 122 da LPI",
            automatizavel=False,
        )
    if forma == "nominativa":
        return _regra(
            "sinal_visual",
            "Sinal visualmente perceptível",
            "atendido",
            "Forma nominativa declarada",
            f"O elemento nominativo informado é “{marca}”.",
            "Manual 5, art. 122 da LPI",
            automatizavel=True,
        )
    return _regra(
        "sinal_visual",
        "Sinal visualmente perceptível",
        "alerta" if descricao else "nao_analisado",
        "Elementos visuais aguardam conferência" if descricao else "Descrição visual pendente",
        descricao or "Descreva o logotipo e seus elementos no assistente.",
        "Manual 5, art. 122 da LPI",
        automatizavel=False,
    )


def _regra_liceidade(dados: dict) -> RegraRegistrabilidade:
    oficial = dados.get("usa_simbolo_oficial")
    ofensivo = dados.get("conteudo_potencialmente_ofensivo")
    if oficial is None or ofensivo is None:
        status: StatusRegra = "nao_analisado"
        conclusao = "Declarações de liceidade incompletas"
    elif oficial or ofensivo:
        status = "possivel_impedimento"
        conclusao = "O sinal contém elemento que pode sofrer proibição legal"
    else:
        status = "atendido"
        conclusao = "Nenhum indício declarado na triagem de liceidade"
    return _regra(
        "liceidade",
        "Liceidade",
        status,
        conclusao,
        "Símbolo oficial: "
        f"{_sim_nao(oficial)}. Conteúdo potencialmente ofensivo: {_sim_nao(ofensivo)}.",
        "Manual 5.8 e art. 124, I, III, XI e XIV, da LPI",
        automatizavel=True,
    )


def _regra_distintividade(dados: dict) -> RegraRegistrabilidade:
    generico = dados.get("termo_generico_descritivo")
    if generico is None:
        status: StatusRegra = "nao_analisado"
        conclusao = "Classificação semântica pendente"
    elif generico:
        status = "possivel_impedimento"
        conclusao = "Sinal declarado como genérico ou descritivo"
    else:
        status = "atendido"
        conclusao = "Sinal declarado como não genérico ou meramente descritivo"
    return _regra(
        "distintividade",
        "Distintividade",
        status,
        conclusao,
        dados.get("significado") or "Significado da expressão não informado.",
        "Manual 5.9 e arts. 122 e 124 da LPI",
        automatizavel=True,
    )


def _regra_veracidade(dados: dict) -> RegraRegistrabilidade:
    alegacao = dados.get("possui_alegacao_origem_qualidade")
    comprovavel = dados.get("alegacao_comprovavel")
    if alegacao is None:
        status: StatusRegra = "nao_analisado"
        conclusao = "Alegações do sinal não informadas"
    elif not alegacao:
        status = "atendido"
        conclusao = "Nenhuma alegação de origem ou qualidade declarada"
    elif comprovavel is True:
        status = "alerta"
        conclusao = "Alegação declarada como comprovável; documentos devem ser conferidos"
    elif comprovavel is False:
        status = "possivel_impedimento"
        conclusao = "Alegação sem comprovação pode induzir o consumidor a erro"
    else:
        status = "alerta"
        conclusao = "A comprovação da alegação precisa ser informada"
    return _regra(
        "veracidade",
        "Veracidade",
        status,
        conclusao,
        f"Alegação de origem ou qualidade: {_sim_nao(alegacao)}. "
        f"Comprovação: {_sim_nao(comprovavel)}.",
        "Manual 5.10 e art. 124, X, da LPI",
        automatizavel=True,
    )


def _sim_nao(valor: bool | None) -> str:
    return "sim" if valor is True else "não" if valor is False else "não informado"


# Regras que representam motivos substantivos de (in)deferimento no exame de mérito.
# Excluem regras de estado do sistema/processo (afinidade, especificação, documentos,
# oposições) que não devem, sozinhas, mover o veredito de deferido/indeferido.
IMPEDIMENTO_CODIGOS = (
    "disponibilidade",
    "distintividade",
    "liceidade",
    "veracidade",
    "alto_renome",
    "direitos_terceiros",
)

_RESSALVA_PROGNOSTICO = (
    "Prognóstico indicativo, baseado nos critérios automatizáveis do Manual de Marcas do "
    "INPI. O exame de mérito possui etapas subjetivas (distintividade concreta, "
    "interpretação do examinador) que não podem ser antecipadas com certeza. Não constitui "
    "garantia de registro nem dispensa análise jurídica."
)


def construir_prognostico_registrabilidade(matriz: dict) -> dict:
    """Traduz a matriz determinística num veredito claro de deferido/indeferido.

    Usa apenas os critérios substantivos (IMPEDIMENTO_CODIGOS). Regras não analisadas
    viram pendências transparentes, mas não forçam o veredito.
    """
    por_codigo = {regra.get("codigo"): regra for regra in matriz.get("regras") or []}
    relevantes = [por_codigo[codigo] for codigo in IMPEDIMENTO_CODIGOS if codigo in por_codigo]
    impedimentos = [r for r in relevantes if r["status"] == "possivel_impedimento"]
    atencoes = [r for r in relevantes if r["status"] == "alerta"]
    pendencias = [r["criterio"] for r in relevantes if r["status"] == "nao_analisado"]

    if impedimentos:
        veredito = "desfavoravel"
        titulo = "Risco de indeferimento"
        resumo = (
            "A triagem encontrou possíveis impedimentos que, se confirmados no exame de "
            "mérito do INPI, tendem ao indeferimento."
        )
        destaques = impedimentos
    elif atencoes:
        veredito = "atencao"
        titulo = "Deferimento possível, com ressalvas"
        resumo = (
            "Não há impedimento evidente, mas existem pontos que precisam de ajuste ou "
            "conferência para melhorar a chance de deferimento."
        )
        destaques = atencoes
    else:
        veredito = "favoravel"
        titulo = "Tendência de deferimento"
        resumo = (
            "A triagem automática não encontrou anterioridades impeditivas nem impedimentos "
            "legais aparentes nos critérios substantivos analisados."
        )
        destaques = []

    motivos = [
        {
            "criterio": regra["criterio"],
            "conclusao": regra["conclusao"],
            "referencia": regra["referencia"],
        }
        for regra in destaques
    ]
    return {
        "veredito": veredito,
        "titulo": titulo,
        "resumo": resumo,
        "motivos": motivos,
        "pendencias": pendencias,
        "versao_matriz": matriz.get("versao"),
        "ressalva": _RESSALVA_PROGNOSTICO,
    }


def construir_indicador_deterministico(
    matriz: dict,
    pontuacao_risco: int | None,
) -> dict:
    """Produz um índice técnico auditável sem se apresentar como probabilidade estatística."""
    prognostico = construir_prognostico_registrabilidade(matriz)
    cobertura = int(matriz.get("cobertura_percentual") or 0)
    risco = max(0, min(100, int(pontuacao_risco if pontuacao_risco is not None else 50)))
    base = 100 - risco
    fator_cobertura = max(0.25, min(1.0, cobertura / 100))
    indice = round(50 + (base - 50) * fator_cobertura)
    if prognostico["veredito"] == "desfavoravel":
        indice = min(indice, 40)
    elif prognostico["veredito"] == "favoravel":
        indice = max(indice, 60)
    else:
        indice = max(41, min(59, indice))
    incerteza = max(10, min(30, round(10 + (100 - cobertura) * 0.2)))
    return {
        "indice": indice,
        "faixa_inferior": max(0, indice - incerteza),
        "faixa_superior": min(100, indice + incerteza),
        "cobertura_percentual": cobertura,
        "veredito": prognostico["veredito"],
        "titulo": prognostico["titulo"],
        "resumo": prognostico["resumo"],
        "pendencias": prognostico["pendencias"],
        "aviso": (
            "Índice de viabilidade técnica derivado das regras e evidências disponíveis. "
            "Não é probabilidade histórica nem garantia de decisão do INPI."
        ),
    }


def construir_matriz_registrabilidade(
    *,
    marca: str,
    atividade: str | None,
    classe_nice: str | None,
    relatorio: dict,
    pontuacao_risco: int | None,
    nivel_risco: str | None,
    dados_complementares: dict | None = None,
) -> dict:
    dados = dados_complementares or {}
    qualidade = relatorio.get("qualidade_base") or {}
    classes = relatorio.get("classes_atividade") or []
    itens = relatorio.get("itens") or []
    total = int(relatorio.get("total") or 0)
    matriz_afinidade = relatorio.get("matriz_afinidade_status")
    avisos = qualidade.get("avisos") or []

    if classe_nice or classes:
        status_classe: StatusRegra = "alerta" if avisos else "atendido"
        conclusao_classe = (
            "Classificação disponível, com pendências de qualidade"
            if avisos
            else "Classificação encontrada para a atividade informada"
        )
        evidencia_classe = (
            f"Classe declarada: {classe_nice}."
            if classe_nice
            else f"{len(classes)} classe(s) sugerida(s)."
        )
    else:
        status_classe = "alerta"
        conclusao_classe = "Produtos, serviços ou classe precisam ser definidos"
        evidencia_classe = "A pesquisa não possui classe declarada nem sugestão automática."

    if matriz_afinidade == "validada":
        status_afinidade: StatusRegra = "atendido"
        conclusao_afinidade = "Matriz de afinidade validada"
    else:
        status_afinidade = "alerta"
        conclusao_afinidade = "Afinidade mercadológica ainda requer validação"

    alto_renome = [item for item in itens if item.get("alto_renome")]
    if alto_renome:
        status_renome: StatusRegra = "possivel_impedimento"
        conclusao_renome = "Marca de alto renome localizada entre os conflitos"
        evidencia_renome = f"{len(alto_renome)} ocorrência(s) com proteção especial."
    else:
        status_renome = "atendido"
        conclusao_renome = "Nenhuma ocorrência de alto renome identificada"
        evidencia_renome = "Resultado limitado à base sincronizada e aos conflitos exibidos."

    atividade_compativel = dados.get("atividade_compativel")
    if atividade_compativel is True:
        status_legitimidade: StatusRegra = "atendido"
        conclusao_legitimidade = "Atividade declarada como compatível"
    elif atividade_compativel is False:
        status_legitimidade = "possivel_impedimento"
        conclusao_legitimidade = "Atividade declarada como incompatível"
    else:
        status_legitimidade = "nao_analisado"
        conclusao_legitimidade = "Compatibilidade da atividade pendente"

    direitos = (
        dados.get("usa_nome_ou_imagem_terceiro"),
        dados.get("usa_obra_terceiro"),
        dados.get("usa_indicacao_geografica"),
    )
    if any(valor is True for valor in direitos):
        status_direitos: StatusRegra = "alerta"
        conclusao_direitos = "Há elemento de terceiro que exige autorização ou pesquisa"
    elif all(valor is False for valor in direitos):
        status_direitos = "atendido"
        conclusao_direitos = "Nenhum direito adicional de terceiro foi declarado"
    else:
        status_direitos = "nao_analisado"
        conclusao_direitos = "Declarações sobre direitos de terceiros incompletas"

    documentos = dados.get("documentos_obrigatorios_disponiveis")
    status_documentos: StatusRegra = (
        "atendido" if documentos is True else "alerta" if documentos is False else "nao_analisado"
    )
    conclusao_documentos = (
        "Documentos declarados como disponíveis"
        if documentos is True
        else "Há documentos obrigatórios pendentes"
        if documentos is False
        else "Checklist documental ainda não preenchido"
    )

    deposito = bool(dados.get("deposito_realizado"))
    oposicao = dados.get("oposicao_identificada")
    if not deposito:
        status_oposicao: StatusRegra = "nao_aplicavel"
        conclusao_oposicao = "Oposição somente pode ocorrer após a publicação do depósito"
    elif oposicao is True:
        status_oposicao = "possivel_impedimento"
        conclusao_oposicao = "Oposição identificada; manifestação deve ser avaliada"
    elif oposicao is False:
        status_oposicao = "atendido"
        conclusao_oposicao = "Nenhuma oposição identificada no acompanhamento"
    else:
        status_oposicao = "alerta"
        conclusao_oposicao = "Processo depositado; acompanhamento da RPI necessário"

    regras = [
        _regra_sinal(marca, dados),
        _regra_liceidade(dados),
        _regra_distintividade(dados),
        _regra_veracidade(dados),
        _regra_disponibilidade(pontuacao_risco, nivel_risco, total),
        _regra(
            "especificacao",
            "Produtos, serviços e Classificação de Nice",
            status_classe,
            conclusao_classe,
            evidencia_classe,
            "Manual 5.4 e art. 25 da Portaria INPI/PR nº 8/2022",
            automatizavel=True,
        ),
        _regra(
            "afinidade",
            "Afinidade mercadológica",
            status_afinidade,
            conclusao_afinidade,
            f"Status da matriz: {matriz_afinidade or 'não informado'}.",
            "Manual 5.11.2 e art. 26 da Portaria INPI/PR nº 8/2022",
            automatizavel=True,
        ),
        _regra(
            "alto_renome",
            "Alto renome e proteção especial",
            status_renome,
            conclusao_renome,
            evidencia_renome,
            "Manual 5.11.6 e art. 125 da LPI",
            automatizavel=True,
        ),
        _regra(
            "legitimidade",
            "Legitimidade do requerente",
            status_legitimidade,
            conclusao_legitimidade,
            f"Requerente: {dados.get('requerente_tipo') or 'não informado'}. "
            f"Atividade: {dados.get('atividade_requerente') or atividade or 'não informada'}.",
            "Manual 5.5 e art. 128 da LPI",
            automatizavel=True,
        ),
        _regra(
            "direitos_terceiros",
            "Outros direitos de terceiros",
            status_direitos,
            conclusao_direitos,
            "Nome ou imagem: "
            f"{_sim_nao(direitos[0])}. Obra: {_sim_nao(direitos[1])}. "
            f"Indicação geográfica: {_sim_nao(direitos[2])}. "
            f"Autorizações: {_sim_nao(dados.get('possui_autorizacoes'))}.",
            "Manual 5.11.8 a 5.11.16 e art. 124 da LPI",
            automatizavel=True,
        ),
        _regra(
            "documentos",
            "Documentos obrigatórios",
            status_documentos,
            conclusao_documentos,
            f"Disponibilidade documental: {_sim_nao(documentos)}.",
            "Manual 5.6 e 5.7",
            automatizavel=True,
        ),
        _regra(
            "oposicoes",
            "Oposições",
            status_oposicao,
            conclusao_oposicao,
            f"Depósito: {_sim_nao(deposito)}. Pedido: "
            f"{dados.get('numero_pedido') or 'não informado'}.",
            "Manual 5.12 e art. 158 da LPI",
            automatizavel=True,
        ),
    ]

    contagens = {
        status: sum(regra.status == status for regra in regras)
        for status in (
            "atendido",
            "alerta",
            "possivel_impedimento",
            "nao_analisado",
            "nao_aplicavel",
        )
    }
    aplicaveis = len(regras) - contagens["nao_aplicavel"]
    analisadas = aplicaveis - contagens["nao_analisado"]
    if contagens["possivel_impedimento"]:
        conclusao = "Possíveis impedimentos identificados"
        status_geral = "possivel_impedimento"
    elif contagens["alerta"]:
        conclusao = "Análise contém pontos de atenção"
        status_geral = "alerta"
    elif contagens["nao_analisado"]:
        conclusao = "Análise ainda possui critérios pendentes"
        status_geral = "incompleta"
    else:
        conclusao = "Nenhum impedimento automatizável identificado"
        status_geral = "sem_impedimento_automatico"

    return {
        "versao": VERSAO_MATRIZ,
        "manual_atualizado_em": "2026-06-23",
        "fonte": FONTE_MANUAL,
        "status_geral": status_geral,
        "conclusao": conclusao,
        "cobertura_percentual": round(analisadas / aplicaveis * 100) if aplicaveis else 100,
        "contagens": contagens,
        "regras": [asdict(regra) for regra in regras],
        "aviso": (
            "A matriz verifica apenas evidências disponíveis. Itens não analisados exigem "
            "informações adicionais ou avaliação profissional e não são considerados favoráveis."
        ),
    }
