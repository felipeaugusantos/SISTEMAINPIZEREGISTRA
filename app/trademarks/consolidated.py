"""Uma conclusão automática por snapshot; revisão humana não altera o score do motor."""

import hashlib
import json
import re
from copy import deepcopy

from app.trademarks.agent import analisar_registrabilidade, resultado_para_dict
from app.trademarks.registrability import construir_matriz_registrabilidade
from app.trademarks.risk import nivel_por_pontuacao

VERSAO_ANALISE = "analise-unificada-1.0"

# Pontuação representativa (ponto médio da faixa) de cada nível categórico do
# motor de risco -- usada só para blender o parecer humano com o score da IA,
# nunca para substituir a pontuação determinística de risk.py.
_SCORE_POR_NIVEL = {"baixo": 12, "moderado": 37, "alto": 62, "critico": 87}
PESO_SCORE_IA = 0.5
PESO_SCORE_HUMANO = 0.5

DISCLAIMERS_ESTRATEGICOS = (
    {
        "codigo": "soberania_inpi",
        "titulo": "Soberania do INPI",
        "texto": (
            "Esta análise reflete probabilidade técnica e mercadológica com base nos dados "
            "disponíveis; a decisão final sobre o registro cabe exclusivamente ao examinador "
            "do INPI, que pode considerar elementos não antecipáveis por este sistema."
        ),
    },
    {
        "codigo": "termos_comuns",
        "titulo": "Marcas mistas e termos de uso comum",
        "texto": (
            "Termos de uso comum, genéricos ou evocativos do segmento presentes na marca não "
            "conferem exclusividade isolada -- a proteção recai sobre o conjunto nominativo e, "
            "quando houver, sobre o conjunto gráfico-nominativo (trade dress)."
        ),
    },
    {
        "codigo": "oposicao_terceiros",
        "titulo": "Risco prático de oposição",
        "texto": (
            "Mesmo quando a viabilidade legal é aceitável, concorrentes do setor podem oferecer "
            "oposição administrativa por razões estratégicas -- o risco de litígio prático deve "
            "ser avaliado separadamente da viabilidade técnica de registro."
        ),
    },
)

_ROTULOS_DIRETRIZ = {
    "deposito_imediato": "Depósito imediato",
    "ajuste_especificacao": "Ajuste de especificação",
    "adequacao_mista": "Adequação de logotipo/mista",
    "inviavel_rebranding": "Inviável — sugerir rebranding",
}


def _diretriz_acao_automatica(decisao: str, nivel_risco: str | None, forma_apresentacao: str | None) -> str:
    """Sugestão estratégica de depósito. Puramente indicativa -- o parecer humano tem
    poder de override total sobre esta sugestão (ver `diretriz_acao_humana` no parecer)."""
    if decisao == "cenario_favoravel":
        return "deposito_imediato"
    if decisao == "cenario_intermediario":
        return "ajuste_especificacao"
    if decisao == "cenario_desfavoravel":
        if forma_apresentacao == "mista":
            # Já tentou reforçar distintividade pelo conjunto gráfico-nominativo e
            # ainda assim o cenário é desfavorável -- pouco resta a ajustar no sinal.
            return "inviavel_rebranding"
        return "adequacao_mista"
    return "ajuste_especificacao"


def apresentacao_analise(analise: dict) -> dict:
    """Detalha o snapshot existente sem recalcular sua decisão nem alterar a aprovação."""
    resultado = analise.get("conclusao_preliminar") or {}
    regras = (analise.get("matriz") or {}).get("regras") or []

    def detalhar(status):
        return [
            {
                "criterio": regra.get("criterio") or regra.get("codigo") or "Critério não identificado",
                "justificativa": regra.get("conclusao") or "Requer avaliação profissional.",
                "evidencia": regra.get("evidencia") or "",
                "referencia": regra.get("referencia") or "",
            }
            for regra in regras
            if regra.get("status") == status
        ]

    impedimentos = detalhar("possivel_impedimento")
    alertas = detalhar("alerta")
    fundamentos = []
    for motivo in resultado.get("motivos") or []:
        normalizado = motivo.strip().lower()
        if normalizado.startswith(("modelo estatístico", "estimativa estatística")):
            continue
        if impedimentos and re.fullmatch(r"\d+ possível\(is\) impedimento\(s\) nas regras do inpi", normalizado):
            continue
        if alertas and re.fullmatch(r"\d+ ponto\(s\) de atenção", normalizado):
            continue
        fundamentos.append(motivo)
    if resultado.get("nivel_risco") in {"alto", "critico"}:
        fundamentos.append("Risco técnico elevado: revise as anterioridades e a composição do risco.")
    situacoes = {
        "cenario_favoravel": ("favoravel", "Favorável", "Cenário técnico favorável, sujeito à revisão profissional."),
        "cenario_desfavoravel": (
            "desfavoravel",
            "Desfavorável",
            "Riscos relevantes exigem revisão antes de prosseguir.",
        ),
        "cenario_intermediario": (
            "inconclusiva",
            "Inconclusiva - requer revisão",
            "Os pontos de atenção impedem uma conclusão favorável ou desfavorável neste momento.",
        ),
        "dados_insuficientes": (
            "inconclusiva",
            "Inconclusiva - dados insuficientes",
            "Complete as informações pendentes antes de concluir a análise.",
        ),
    }
    decisao = resultado.get("decisao")
    if resultado.get("abstencao") and decisao != "cenario_desfavoravel":
        decisao = "dados_insuficientes"
    codigo, rotulo, explicacao = situacoes.get(decisao, situacoes["dados_insuficientes"])
    situacao_ia_preliminar = {"codigo": codigo, "rotulo": rotulo, "explicacao": explicacao}

    veredito_humano = (analise.get("parecer_humano") or {}).get("veredito_humano")
    if veredito_humano:
        # Poder de override explícito do especialista: quando ele registra um veredito
        # direto (não inferido de nivel_humano nem de texto livre), a Situação exibida
        # ao cliente passa a refletir a leitura profissional, não a pré-análise da IA
        # sozinha -- a pré-análise continua visível em situacao_ia_preliminar, para
        # auditoria/transparência, nunca escondida.
        rotulos_humanos = {
            "favoravel": ("Favorável", "Avaliação do especialista: cenário favorável ao registro."),
            "desfavoravel": ("Desfavorável", "Avaliação do especialista: riscos relevantes identificados."),
            "inconclusiva": ("Inconclusiva", "Avaliação do especialista: dados ainda insuficientes para concluir."),
        }
        rotulo_humano, explicacao_humana = rotulos_humanos[veredito_humano]
        codigo, rotulo, explicacao = veredito_humano, rotulo_humano, explicacao_humana

    estatistica = analise.get("estatistica") or {}
    mensagem = estatistica.get("mensagem") or ""
    if not estatistica.get("disponivel") and "restrito" not in mensagem.lower():
        mensagem = (
            "Análise realizada com os critérios técnicos disponíveis, sem apoio estatístico. "
            "A indisponibilidade desse apoio não é um impedimento da marca."
        )
    return {
        "situacao": {
            "codigo": codigo,
            "rotulo": rotulo,
            "explicacao": explicacao,
            "origem": "parecer_humano" if veredito_humano else "analise_automatica",
        },
        "situacao_ia_preliminar": situacao_ia_preliminar,
        "impedimentos": impedimentos,
        "pontos_atencao": alertas,
        "fundamentos_tecnicos": fundamentos,
        "mensagem_apoio": mensagem,
    }


def construir_analise_consolidada(
    relatorio: dict, dados_complementares: dict | None = None, *, parecer_humano_novo: dict | None = None
) -> dict:
    dados = deepcopy(dados_complementares or {})
    entrada = {
        chave: valor
        for chave, valor in relatorio.items()
        if chave not in {"analise_consolidada", "versao", "schema_versao", "gerado_em", "conteudo_hash"}
    }
    assinatura = hashlib.sha256(
        json.dumps(
            {"relatorio": entrada, "complementos": dados, "motor": VERSAO_ANALISE},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()
    matriz = construir_matriz_registrabilidade(
        marca=relatorio.get("marca", ""),
        atividade=relatorio.get("atividade"),
        classe_nice=relatorio.get("classe_nice"),
        relatorio=entrada,
        pontuacao_risco=relatorio.get("risco_pontuacao"),
        nivel_risco=relatorio.get("risco_nivel"),
        dados_complementares=dados,
    )
    estimativa = relatorio.get("estimativa_registrabilidade") or {}
    estatistica_disponivel = (
        relatorio.get("estimativa_status") == "disponivel"
        and estimativa.get("modelo_status") == "ACTIVE"
        and estimativa.get("probabilidade_deferimento") is not None
        and float(estimativa.get("cobertura_entrada") or 0) >= 0.60
    )
    resultado = analisar_registrabilidade(
        matriz=matriz,
        pontuacao_risco=relatorio.get("risco_pontuacao"),
        nivel_risco=relatorio.get("risco_nivel"),
        previsao=estimativa if estatistica_disponivel else None,
    )
    titulos = {
        "dados_insuficientes": "Análise preliminar com informações pendentes",
        "cenario_desfavoravel": "Riscos relevantes identificados",
        "cenario_intermediario": "Pontos de atenção identificados",
        "cenario_favoravel": "Cenário preliminar favorável",
    }
    recomendacoes = {
        "dados_insuficientes": "Complementar as informações indicadas e submeter as evidências ao especialista.",
        "cenario_desfavoravel": "Revisar os conflitos e possíveis impedimentos antes de definir a estratégia de depósito.",
        "cenario_intermediario": "Revisar as anterioridades e a especificação de produtos e serviços.",
        "cenario_favoravel": "Submeter a análise à revisão humana antes de qualquer decisão de depósito.",
    }
    pendencias = [
        {
            "criterio": regra.get("criterio") or regra.get("codigo") or "Critério pendente",
            "descricao": regra.get("conclusao") or regra.get("justificativa") or "Requer avaliação técnica.",
        }
        for regra in matriz.get("regras", [])
        if regra.get("status") == "nao_analisado"
    ]
    anterior = relatorio.get("analise_consolidada") or {}
    if parecer_humano_novo is not None:
        parecer = parecer_humano_novo
    elif anterior.get("entrada_hash") == assinatura:
        parecer = deepcopy(anterior.get("parecer_humano"))
    else:
        parecer = None

    score_ia = relatorio.get("risco_pontuacao")
    score_final = None
    nivel_final = None
    if parecer and parecer.get("nivel") and score_ia is not None:
        score_humano = _SCORE_POR_NIVEL[parecer["nivel"]]
        score_final = round(score_ia * PESO_SCORE_IA + score_humano * PESO_SCORE_HUMANO)
        nivel_final = nivel_por_pontuacao(score_final)

    codigo_diretriz = _diretriz_acao_automatica(resultado.decisao, relatorio.get("risco_nivel"), dados.get("forma_apresentacao"))
    diretriz_origem = "automatica"
    if parecer and parecer.get("diretriz_acao_humana"):
        codigo_diretriz = parecer["diretriz_acao_humana"]
        diretriz_origem = "humana"

    return {
        "versao_motor": VERSAO_ANALISE,
        "entrada_hash": assinatura,
        "titulo": titulos[resultado.decisao],
        "conclusao_preliminar": resultado_para_dict(resultado),
        "recomendacao": recomendacoes[resultado.decisao],
        "analise_conjunto": {
            "marca": relatorio.get("marca"),
            "atividade": relatorio.get("atividade"),
            "significado_informado": dados.get("significado"),
            "produtos_servicos": dados.get("produtos_servicos"),
            "descricao_visual": dados.get("descricao_visual"),
            "aviso": "A interpretação do conjunto e do mercado depende das evidências e da revisão do especialista.",
        },
        "anterioridades": [
            {
                chave: item.get(chave)
                for chave in (
                    "numero",
                    "titulo",
                    "situacao",
                    "criterios_encontro",
                    "afinidade_classes",
                    "relevancia_rotulo",
                )
            }
            for item in relatorio.get("itens", [])[:20]
        ],
        "pendencias": pendencias,
        "matriz": matriz,
        "estatistica": {
            "disponivel": estatistica_disponivel,
            "mensagem": (
                "Estimativa histórica de apoio; não é garantia de registro."
                if estatistica_disponivel
                else "Estimativa estatística indisponível: modelo ausente, em validação ou sem elegibilidade. "
                "A análise utiliza as evidências da pesquisa e os critérios técnicos disponíveis."
            ),
            "estimativa": deepcopy(estimativa) if estatistica_disponivel else None,
        },
        "fontes": {"ultima_rpi": relatorio.get("ultima_rpi"), "qualidade_base": relatorio.get("qualidade_base")},
        "dados_complementares": dados,
        "parecer_humano": parecer,
        "score_composto": {
            "score_ia": score_ia,
            "score_humano": _SCORE_POR_NIVEL.get(parecer["nivel"]) if parecer and parecer.get("nivel") else None,
            "score_final": score_final,
            "nivel_final": nivel_final,
            "peso_ia": PESO_SCORE_IA,
            "peso_humano": PESO_SCORE_HUMANO,
            "explicacao": (
                "Combina a pontuação determinística da IA com o nível atribuído pelo especialista "
                "em partes iguais; só é calculado depois que o parecer humano é registrado."
            ),
        },
        "diretriz_acao": {
            "codigo": codigo_diretriz,
            "rotulo": _ROTULOS_DIRETRIZ[codigo_diretriz],
            "origem": diretriz_origem,
        },
        "disclaimers": list(DISCLAIMERS_ESTRATEGICOS),
    }


def analise_para_exibicao(relatorio: dict, *, versao: int, validado_por=None, validado_em=None) -> dict:
    """Tela e PDF usam o mesmo snapshot; documentos legados ganham uma leitura compatível."""
    armazenada = relatorio.get("analise_consolidada")
    analise = deepcopy(armazenada) if armazenada else construir_analise_consolidada(relatorio)
    analise["versao_relatorio"] = versao
    analise["legado"] = not bool(armazenada)
    # Uma interpretação nova de um snapshot legado nunca herda chancela técnica antiga.
    validada = bool(armazenada and analise.get("parecer_humano") and validado_por and validado_em)
    analise["revisao"] = {
        "validada": validada,
        "validado_por": validado_por if validada else None,
        "validado_em": validado_em.isoformat() if validada and hasattr(validado_em, "isoformat") else None,
    }
    analise["apresentacao"] = apresentacao_analise(analise)
    return analise
