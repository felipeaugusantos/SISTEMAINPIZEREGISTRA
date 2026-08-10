import json
import math
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload, selectinload

from app.models import (
    AfinidadeClasse,
    ControleAprendizadoMarca,
    ModeloRegistrabilidade,
    Movimentacao,
    ParTreinamentoMarca,
    PrevisaoRegistrabilidade,
    Processo,
    RotuloHistoricoMarca,
    TipoProcesso,
)
from app.normalization import normalizar_numero_processo
from app.search import normalizar_texto

VERSAO_ATRIBUTOS = "atributos-marcarios-1.1"
VERSAO_ESTIMATIVA = "deferimento-merito-2.0"
ESCOPO_ESTIMATIVA = "deferimento_exame_merito"
VERSAO_CLASSIFICADOR_ROTULO = "rotulo-marcario-1.1"
QUANTIDADE_BOOTSTRAP = 24
# Limiar do operador trigram `%` na busca de candidatos do dataset. Com o padrão 0.3
# o `ORDER BY similarity` percorre milhares de matches por alvo (~11s cada). Em 0.4 o
# conjunto encolhe para conflitos genuínos e a query cai para ~160ms, mantendo a
# ordenação por similaridade (candidato mais parecido primeiro).
LIMIAR_SIMILARIDADE_CANDIDATOS = 0.4
# Nota: 'alto_renome' foi removido do modelo. No dataset historico ele era sempre
# False (nao ha status de renome historico confiavel por data de deposito), tornando-se
# uma feature constante -> peso ~0 e skew treino/producao. O alto renome continua sendo
# usado no motor de risco e no relatorio, apenas nao alimenta a estimativa estatistica.
ATRIBUTOS_MODELO = (
    "similaridade_sequencia",
    "jaccard_tokens",
    "jaccard_trigramas",
    "nome_identico",
    "contencao_tokens",
    "fonetica_igual",
    "fonetica_similaridade",
    "prefixo_radical",
    "classe_identica",
    "afinidade_conhecida",
    "candidato_ativo",
    "similaridade_top3_media",
    "conflitos_fortes_norm",
    "conflitos_ativos_norm",
    "marca_token_unico",
    "marca_num_tokens_norm",
    "marca_comprimento_norm",
    "marca_frequencia_max_norm",
    "quantidade_candidatos_norm",
)
ROTULOS_ATRIBUTOS = {
    "similaridade_sequencia": "semelhança geral do nome",
    "jaccard_tokens": "palavras em comum",
    "jaccard_trigramas": "trechos do nome em comum",
    "nome_identico": "nome idêntico",
    "contencao_tokens": "elementos nominativos contidos",
    "fonetica_igual": "semelhança fonética",
    "fonetica_similaridade": "proximidade fonética",
    "prefixo_radical": "radical inicial semelhante",
    "classe_identica": "classe de Nice idêntica",
    "afinidade_conhecida": "afinidade entre atividades",
    "candidato_ativo": "situação ativa da anterioridade",
    "similaridade_top3_media": "média dos três conflitos principais",
    "conflitos_fortes_norm": "quantidade de conflitos fortes",
    "conflitos_ativos_norm": "quantidade de anterioridades ativas",
    "marca_token_unico": "marca de termo único",
    "marca_num_tokens_norm": "quantidade de termos na marca",
    "marca_comprimento_norm": "comprimento da marca",
    "marca_frequencia_max_norm": "termo mais comum no acervo de marcas",
    "quantidade_candidatos_norm": "volume de anterioridades",
}


@dataclass(frozen=True, slots=True)
class RotuloExtraido:
    rotulo: str
    alvo_deferimento: bool
    fundamento: str
    confianca: float
    movimentacao: Movimentacao
    tipo_decisao: str = "merito"
    elegivel_treinamento: bool = True
    motivo_inelegibilidade: str | None = None
    evidencias: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PredicaoModelo:
    probabilidade: float
    probabilidade_inferior: float
    probabilidade_superior: float
    nivel: str
    confianca: float
    confianca_rotulo: str
    cobertura_entrada: float
    fatores: list[dict[str, Any]]


def _sem_acentos(valor: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", valor) if not unicodedata.combining(char)
    ).lower()


def _fonetica(valor: str) -> str:
    texto = normalizar_texto(valor).replace(" ", "")
    substituicoes = (
        ("PH", "F"),
        ("FH", "F"),
        ("QU", "K"),
        ("Q", "K"),
        ("C", "K"),
        ("Y", "I"),
        ("W", "V"),
        ("Z", "S"),
    )
    for origem, destino in substituicoes:
        texto = texto.replace(origem, destino)
    return re.sub(r"(.)\1+", r"\1", texto)


def _jaccard(a: set[str], b: set[str]) -> float:
    uniao = a | b
    return len(a & b) / len(uniao) if uniao else 0.0


def _trigramas(valor: str) -> set[str]:
    texto = f"  {normalizar_texto(valor)}  "
    return {texto[indice : indice + 3] for indice in range(max(0, len(texto) - 2))}


_CAMINHO_LEXICO = Path(__file__).resolve().parent / "data" / "frequencia_tokens.json"
# Frequência (em nº de marcas) na qual um token satura como "muito comum/descritivo".
_ESCALA_FREQUENCIA_TOKEN = 20000.0
# Palavras funcionais ignoradas na frequência: elas aparecem em quase toda marca
# multivocabular e saturariam o sinal de descritividade sem informar distintividade.
_STOPWORDS_FREQUENCIA = frozenset(
    {"DE", "DO", "DA", "DOS", "DAS", "E", "O", "A", "OS", "AS", "EM", "NO", "NA",
     "COM", "PARA", "POR", "UM", "UMA", "AO", "THE", "OF", "AND"}
)


@lru_cache(maxsize=1)
def _frequencias_tokens() -> dict[str, int]:
    """Léxico token -> nº de marcas que o contêm, gerado por app.cli.gerar_lexico_frequencia.

    Ausente/vazio => a feature de frequência fica neutra (0), sem quebrar treino/serviço.
    """
    try:
        return json.loads(_CAMINHO_LEXICO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def distintividade_marca(marca: str) -> dict[str, float]:
    """Sinais intrínsecos de distintividade da marca-alvo, derivados do texto.

    Capturam o eixo de indeferimento por falta de distintividade (Art. 124), que as
    features de conflito não enxergam. São calculados só da string + do léxico de
    frequência de corpus, para serem simétricos entre treino e inferência — a mesma
    lição do 'alto_renome' removido. Uma marca curta, de token único e com termos raros
    tende a ser mais distintiva; frase longa, multi-token e com termos muito comuns
    (descritivos/genéricos) tende a ser mais fraca.
    """
    norm = normalizar_texto(marca)
    tokens = norm.split()
    comprimento = len(norm.replace(" ", ""))
    frequencias = _frequencias_tokens()
    # O termo (não funcional) mais comum dirige a descritividade: se QUALQUER termo é
    # genérico, a marca tende ao descritivo. Termo raro/inventado => frequência baixa.
    frequencia_max = max(
        (frequencias.get(token, 0) for token in tokens if token not in _STOPWORDS_FREQUENCIA),
        default=0,
    )
    return {
        "marca_token_unico": float(len(tokens) <= 1),
        "marca_num_tokens_norm": min(1.0, len(tokens) / 5.0),
        "marca_comprimento_norm": min(1.0, comprimento / 20.0),
        "marca_frequencia_max_norm": min(1.0, frequencia_max / _ESCALA_FREQUENCIA_TOKEN),
    }


def _evidencias_classificacao(
    movimentacoes: list[Movimentacao], decisao: Movimentacao
) -> tuple[dict[str, str], ...]:
    evidencias: list[dict[str, str]] = []
    for movimento in sorted(
        movimentacoes,
        key=lambda item: (item.data_rpi, item.numero_rpi, item.id or 0),
        reverse=True,
    ):
        texto = _sem_acentos(f"{movimento.codigo_despacho or ''} {movimento.descricao}")
        tipo = None
        if movimento is decisao:
            tipo = "decisao"
        elif "oposicao" in texto or (movimento.codigo_despacho or "").upper() == "IPAS423":
            tipo = "oposicao"
        elif "exigencia de merito" in texto:
            tipo = "exigencia_merito"
        elif "recurso" in texto:
            tipo = "recurso"
        elif "procedimento judicial" in texto:
            tipo = "procedimento_judicial"
        if tipo is not None:
            evidencias.append(
                {
                    "tipo": tipo,
                    "codigo": movimento.codigo_despacho or "",
                    "descricao": movimento.descricao,
                    "numero_rpi": str(movimento.numero_rpi),
                }
            )
        if len(evidencias) >= 8:
            break
    return tuple(evidencias)


def prioridade_revisao_rotulo(fundamento: str, confianca: float, rotulo: str) -> str:
    if fundamento == "indeferimento_nao_especificado" or confianca < 0.75:
        return "alta"
    if rotulo == "indeferida" or confianca < 0.95:
        return "media"
    return "baixa"


def extrair_rotulo(movimentacoes: list[Movimentacao]) -> RotuloExtraido | None:
    """Extrai apenas decisões de mérito; arquivamentos formais não viram exemplos."""
    ordenadas = sorted(
        movimentacoes,
        key=lambda item: (item.data_rpi, item.numero_rpi, item.id or 0),
        reverse=True,
    )
    for movimento in ordenadas:
        texto = _sem_acentos(f"{movimento.codigo_despacho or ''} {movimento.descricao}")
        codigo = (movimento.codigo_despacho or "").upper()
        if "recurso" in texto and "decisao" not in texto:
            continue
        positivo_recurso = codigo == "IPAS237" or (
            "recurso provido" in texto and "deferimento" in texto
        )
        negativo = codigo == "IPAS024" or any(
            termo in texto
            for termo in (
                "indeferimento do pedido",
                "pedido indeferido",
                "indeferido o pedido",
            )
        )
        positivo = positivo_recurso or (not negativo and (codigo == "IPAS029" or any(
            termo in texto
            for termo in (
                "deferimento do pedido",
                "pedido deferido",
                "concessao de registro",
                "registro de marca concedido",
            )
        )))
        if positivo:
            return RotuloExtraido(
                "deferida",
                True,
                "deferimento_recurso" if positivo_recurso else "deferimento",
                1.0,
                movimento,
                evidencias=_evidencias_classificacao(movimentacoes, movimento),
            )
        if negativo:
            if any(
                termo in texto
                for termo in (
                    "124, xix",
                    "124 xix",
                    "art. 124 inciso xix",
                    "colidencia",
                    "reproducao",
                    "imitacao",
                    "risco de confusao",
                    "associacao com marca alheia",
                )
            ):
                fundamento = "conflito_anterior"
                confianca = 0.95
            elif any(
                termo in texto
                for termo in (
                    "124, vi",
                    "124 vi",
                    "descritiv",
                    "generic",
                    "distintiv",
                    "uso comum",
                    "carater necessario",
                    "carater vulgar",
                )
            ):
                fundamento = "falta_distintividade"
                confianca = 0.9
            elif any(termo in texto for termo in ("124", "proibicao legal", "irregistravel")):
                fundamento = "outra_proibicao"
                confianca = 0.8
            else:
                fundamento = "indeferimento_nao_especificado"
                confianca = 0.65
            return RotuloExtraido(
                "indeferida",
                False,
                fundamento,
                confianca,
                movimento,
                evidencias=_evidencias_classificacao(movimentacoes, movimento),
            )
    return None


def extrair_atributos_par(
    marca: str,
    candidata: str,
    classes_marca: list[str],
    classes_candidata: list[str],
    *,
    afinidade_conhecida: bool,
    candidata_ativa: bool,
) -> dict[str, float]:
    marca_norm = normalizar_texto(marca)
    candidata_norm = normalizar_texto(candidata)
    tokens_marca = set(marca_norm.split())
    tokens_candidata = set(candidata_norm.split())
    fonetica_marca = _fonetica(marca_norm)
    fonetica_candidata = _fonetica(candidata_norm)
    menor_conjunto = min(len(tokens_marca), len(tokens_candidata))
    prefixo_radical = bool(
        len(fonetica_marca) >= 4
        and len(fonetica_candidata) >= 4
        and fonetica_marca[:4] == fonetica_candidata[:4]
    )
    return {
        "similaridade_sequencia": SequenceMatcher(None, marca_norm, candidata_norm).ratio(),
        "jaccard_tokens": _jaccard(tokens_marca, tokens_candidata),
        "jaccard_trigramas": _jaccard(_trigramas(marca_norm), _trigramas(candidata_norm)),
        "nome_identico": float(bool(marca_norm and marca_norm == candidata_norm)),
        "contencao_tokens": (
            len(tokens_marca & tokens_candidata) / menor_conjunto if menor_conjunto else 0.0
        ),
        "fonetica_igual": float(bool(fonetica_marca and fonetica_marca == fonetica_candidata)),
        "fonetica_similaridade": SequenceMatcher(
            None, fonetica_marca, fonetica_candidata
        ).ratio(),
        "prefixo_radical": float(prefixo_radical),
        "classe_identica": float(bool(set(classes_marca) & set(classes_candidata))),
        "afinidade_conhecida": float(afinidade_conhecida),
        "candidato_ativo": float(candidata_ativa),
    }


# Atributos que NÃO vêm dos pares (calculados no nível da amostra na agregação).
_ATRIBUTOS_NAO_PAREADOS = frozenset(
    {
        "quantidade_candidatos_norm",
        "similaridade_top3_media",
        "conflitos_fortes_norm",
        "conflitos_ativos_norm",
        "marca_token_unico",
        "marca_num_tokens_norm",
        "marca_comprimento_norm",
        "marca_frequencia_max_norm",
    }
)


def agregar_atributos(pares: list[dict[str, float]], marca: str = "") -> dict[str, float]:
    atributos = {
        nome: max((par.get(nome, 0.0) for par in pares), default=0.0)
        for nome in ATRIBUTOS_MODELO
        if nome not in _ATRIBUTOS_NAO_PAREADOS
    }
    atributos["quantidade_candidatos_norm"] = min(1.0, len(pares) / 10.0)
    top3 = sorted(
        (par.get("similaridade_sequencia", 0.0) for par in pares), reverse=True
    )[:3]
    atributos["similaridade_top3_media"] = sum(top3) / len(top3) if top3 else 0.0
    atributos["conflitos_fortes_norm"] = min(
        1.0,
        sum(par.get("similaridade_sequencia", 0.0) >= 0.7 for par in pares) / 5.0,
    )
    atributos["conflitos_ativos_norm"] = min(
        1.0, sum(par.get("candidato_ativo", 0.0) >= 0.5 for par in pares) / 5.0
    )
    # Distintividade é propriedade da marca-alvo, não do par; computada aqui uma vez.
    if marca:
        atributos.update(distintividade_marca(marca))
    return atributos


def _candidata_ativa_na_data(processo: Processo, data_referencia: date) -> bool:
    movimentos = [item for item in processo.movimentacoes if item.data_rpi <= data_referencia]
    if not movimentos:
        return True
    rotulo = extrair_rotulo(movimentos)
    return rotulo is None or rotulo.alvo_deferimento


def _classes(processo: Processo) -> list[str]:
    return [item.codigo for item in processo.classificacoes if item.sistema == "nice"]


def _pares_afinidade(matriz: list[AfinidadeClasse]) -> set[tuple[str, str]]:
    return {
        tuple(sorted((item.classe_origem, item.classe_destino)))
        for item in matriz
        if item.status_revisao == "aprovada"
    }


async def construir_dataset_historico(
    session: AsyncSession,
    *,
    limite: int = 3000,
    candidatos_por_processo: int = 12,
) -> tuple[int, int]:
    # Restringe o operador trigram `%` a candidatos genuinamente similares e torna a
    # busca por alvo ~70x mais rápida (ver LIMIAR_SIMILARIDADE_CANDIDATOS).
    # set_limit espera `real`; cast explícito evita ambiguidade de tipo com float8.
    await session.execute(
        text("SELECT set_limit(CAST(:limiar AS real))"),
        {"limiar": LIMIAR_SIMILARIDADE_CANDIDATOS},
    )
    processos = (
        (
            await session.execute(
                select(Processo)
                .join(Movimentacao)
                .where(
                    Processo.tipo == TipoProcesso.MARCA,
                    Processo.titulo.is_not(None),
                    or_(
                        Movimentacao.descricao.ilike("%deferimento do pedido%"),
                        Movimentacao.descricao.ilike("%pedido deferido%"),
                        Movimentacao.descricao.ilike("%indeferimento do pedido%"),
                        Movimentacao.descricao.ilike("%pedido indeferido%"),
                    ),
                )
                .options(
                    selectinload(Processo.movimentacoes),
                    selectinload(Processo.classificacoes),
                    noload(Processo.titulares),
                )
                .distinct()
                .order_by(Processo.data_deposito.desc().nullslast(), Processo.id)
                .limit(limite)
            )
        )
        .scalars()
        .all()
    )
    matriz = (await session.execute(select(AfinidadeClasse))).scalars().all()
    afinidades = _pares_afinidade(list(matriz))
    rotulos_processados = 0
    pares_processados = 0

    for processo in processos:
        extraido = extrair_rotulo(processo.movimentacoes)
        if extraido is None or processo.data_deposito is None:
            continue
        rotulo = (
            await session.execute(
                select(RotuloHistoricoMarca).where(RotuloHistoricoMarca.processo_id == processo.id)
            )
        ).scalar_one_or_none()
        if rotulo is None:
            rotulo = RotuloHistoricoMarca(processo_id=processo.id)
            session.add(rotulo)
        if rotulo.status_revisao != "aprovada":
            rotulo.rotulo = extraido.rotulo
            rotulo.alvo_deferimento = extraido.alvo_deferimento
            rotulo.fundamento = extraido.fundamento
            rotulo.origem = "rpi_automatica"
            rotulo.confianca = extraido.confianca
            rotulo.data_referencia = processo.data_deposito
            rotulo.numero_rpi = extraido.movimentacao.numero_rpi
            rotulo.despacho_codigo = extraido.movimentacao.codigo_despacho
            rotulo.despacho_descricao = extraido.movimentacao.descricao
            rotulo.data_decisao = extraido.movimentacao.data_rpi
            rotulo.tipo_decisao = extraido.tipo_decisao
            rotulo.elegivel_treinamento = extraido.elegivel_treinamento
            rotulo.motivo_inelegibilidade = extraido.motivo_inelegibilidade
            rotulo.classificador_versao = VERSAO_CLASSIFICADOR_ROTULO
            rotulo.evidencias_classificacao = list(extraido.evidencias)
        await session.flush()
        rotulos_processados += 1

        titulo = processo.titulo or ""
        titulo_sql = func.immutable_unaccent(Processo.titulo)
        candidatos = (
            (
                await session.execute(
                    select(Processo)
                    .where(
                        Processo.tipo == TipoProcesso.MARCA,
                        Processo.id != processo.id,
                        Processo.titulo.is_not(None),
                        Processo.data_deposito.is_not(None),
                        Processo.data_deposito < processo.data_deposito,
                        titulo_sql.bool_op("%")(
                            func.immutable_unaccent(titulo),
                        ),
                    )
                    .options(
                        selectinload(Processo.movimentacoes),
                        selectinload(Processo.classificacoes),
                        noload(Processo.titulares),
                    )
                    .order_by(
                        func.similarity(
                            titulo_sql,
                            func.immutable_unaccent(titulo),
                        ).desc(),
                        Processo.data_deposito.desc(),
                    )
                    .limit(candidatos_por_processo)
                )
            )
            .scalars()
            .all()
        )
        await session.execute(
            delete(ParTreinamentoMarca).where(ParTreinamentoMarca.rotulo_id == rotulo.id)
        )
        classes_alvo = _classes(processo)
        for candidata in candidatos:
            classes_candidata = _classes(candidata)
            afinidade = any(
                tuple(sorted((origem, destino))) in afinidades
                for origem in classes_alvo
                for destino in classes_candidata
            )
            atributos = extrair_atributos_par(
                titulo,
                candidata.titulo or "",
                classes_alvo,
                classes_candidata,
                afinidade_conhecida=afinidade,
                candidata_ativa=_candidata_ativa_na_data(candidata, processo.data_deposito),
            )
            session.add(
                ParTreinamentoMarca(
                    rotulo_id=rotulo.id,
                    processo_candidato_id=candidata.id,
                    atributos=atributos,
                    alvo_conflito=(
                        not extraido.alvo_deferimento
                        if extraido.fundamento == "conflito_anterior"
                        else None
                    ),
                )
            )
            pares_processados += 1
    await session.commit()
    return rotulos_processados, pares_processados


def _sigmoid(valor: float) -> float:
    if valor >= 0:
        exp = math.exp(-min(valor, 60))
        return 1 / (1 + exp)
    exp = math.exp(max(valor, -60))
    return exp / (1 + exp)


def _ajustar_logistica(
    linhas: list[tuple[dict[str, float], int, float]],
    *,
    epocas: int = 1200,
    taxa: float = 0.08,
    regularizacao: float = 0.002,
) -> dict[str, Any]:
    """Regressão logística ponderada. Cada linha é (atributos, alvo, peso_confianca).

    O peso reflete a confiabilidade do rótulo extraído do despacho: um indeferimento
    genérico (0,65) influencia menos que um deferimento explícito (1,0), evitando que o
    ruído da extração por texto seja tratado como certeza.
    """
    medias = {
        nome: sum(item[0].get(nome, 0.0) for item in linhas) / len(linhas)
        for nome in ATRIBUTOS_MODELO
    }
    desvios = {}
    for nome in ATRIBUTOS_MODELO:
        variancia = sum((item[0].get(nome, 0.0) - medias[nome]) ** 2 for item in linhas) / len(
            linhas
        )
        desvios[nome] = max(math.sqrt(variancia), 1e-6)
    pesos = {nome: 0.0 for nome in ATRIBUTOS_MODELO}
    vies = 0.0
    positivos = sum(alvo for _, alvo, _ in linhas)
    negativos = len(linhas) - positivos
    pesos_classe = {
        1: len(linhas) / (2 * positivos) if positivos else 1.0,
        0: len(linhas) / (2 * negativos) if negativos else 1.0,
    }
    for _ in range(epocas):
        gradientes = {nome: 0.0 for nome in ATRIBUTOS_MODELO}
        gradiente_vies = 0.0
        for atributos, alvo, confianca in linhas:
            padronizados = {
                nome: (atributos.get(nome, 0.0) - medias[nome]) / desvios[nome]
                for nome in ATRIBUTOS_MODELO
            }
            previsao = _sigmoid(
                vies + sum(pesos[nome] * padronizados[nome] for nome in ATRIBUTOS_MODELO)
            )
            erro = (previsao - alvo) * pesos_classe[alvo] * confianca
            gradiente_vies += erro
            for nome in ATRIBUTOS_MODELO:
                gradientes[nome] += erro * padronizados[nome]
        tamanho = len(linhas)
        vies -= taxa * gradiente_vies / tamanho
        for nome in ATRIBUTOS_MODELO:
            pesos[nome] -= taxa * (gradientes[nome] / tamanho + regularizacao * pesos[nome])
    return {"pesos": pesos, "vies": vies, "medias": medias, "desvios": desvios}


def _probabilidade(atributos: dict[str, float], parametros: dict[str, Any]) -> float:
    linear = float(parametros["vies"])
    for nome, peso in parametros.get("pesos", {}).items():
        media = parametros.get("medias", {}).get(nome, 0.0)
        desvio = max(float(parametros.get("desvios", {}).get(nome, 1.0)), 1e-6)
        padronizado = (atributos.get(nome, 0.0) - media) / desvio
        linear += peso * padronizado
    return _sigmoid(linear)


def _ajustar_platt(probabilidades: list[float], alvos: list[int]) -> dict[str, float]:
    if not probabilidades or len(set(alvos)) < 2:
        return {"a": 1.0, "b": 0.0}
    a, b = 1.0, 0.0
    for _ in range(600):
        grad_a = 0.0
        grad_b = 0.0
        for probabilidade, alvo in zip(probabilidades, alvos, strict=True):
            logit = math.log(max(1e-6, probabilidade) / max(1e-6, 1 - probabilidade))
            calibrada = _sigmoid(a * logit + b)
            erro = calibrada - alvo
            grad_a += erro * logit
            grad_b += erro
        a -= 0.03 * grad_a / len(alvos)
        b -= 0.03 * grad_b / len(alvos)
    return {"a": a, "b": b}


def calibrar(probabilidade: float, calibracao: dict[str, float]) -> float:
    logit = math.log(max(1e-6, probabilidade) / max(1e-6, 1 - probabilidade))
    return _sigmoid(calibracao.get("a", 1.0) * logit + calibracao.get("b", 0.0))


def _percentil(valores: list[float], proporcao: float) -> float:
    if not valores:
        raise ValueError("Não é possível calcular percentil sem valores")
    ordenados = sorted(valores)
    posicao = (len(ordenados) - 1) * proporcao
    inferior = math.floor(posicao)
    superior = math.ceil(posicao)
    if inferior == superior:
        return ordenados[inferior]
    peso = posicao - inferior
    return ordenados[inferior] * (1 - peso) + ordenados[superior] * peso


def _modelos_bootstrap(
    treino: list[tuple[dict[str, float], int, float]], quantidade: int = QUANTIDADE_BOOTSTRAP
) -> list[dict[str, Any]]:
    """Amostra a incerteza do ajuste sem alterar o conjunto temporal de teste."""
    gerador = random.Random(20260808 + len(treino))
    modelos = []
    for _ in range(quantidade):
        amostra = [treino[gerador.randrange(len(treino))] for _ in treino]
        if len({alvo for _, alvo, _ in amostra}) < 2:
            continue
        modelos.append(_ajustar_logistica(amostra, epocas=500))
    return modelos


def _limiar_otimo(probabilidades: list[float], alvos: list[int]) -> float:
    """Corte que maximiza a acurácia balanceada (sensível a probabilidades comprimidas).

    As probabilidades calibradas ficam concentradas numa faixa estreita; o corte fixo
    de 0,5 degenera as previsões. Este limiar é sintonizado na validação, nunca no teste.
    """
    if not probabilidades or len(set(alvos)) < 2:
        return 0.5
    melhor_limiar, melhor_bal = 0.5, -1.0
    for passo in range(20, 71):
        limiar = passo / 100
        tp = fp = tn = fn = 0
        for prob, alvo in zip(probabilidades, alvos, strict=True):
            previsto = int(prob >= limiar)
            tp += int(previsto == 1 and alvo == 1)
            fp += int(previsto == 1 and alvo == 0)
            tn += int(previsto == 0 and alvo == 0)
            fn += int(previsto == 0 and alvo == 1)
        recall = tp / (tp + fn) if tp + fn else 0.0
        especificidade = tn / (tn + fp) if tn + fp else 0.0
        bal = (recall + especificidade) / 2
        if bal > melhor_bal:
            melhor_bal, melhor_limiar = bal, limiar
    return melhor_limiar


def _metricas(
    probabilidades: list[float], alvos: list[int], limiar: float = 0.5
) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for probabilidade, alvo in zip(probabilidades, alvos, strict=True):
        previsto = int(probabilidade >= limiar)
        tp += int(previsto == 1 and alvo == 1)
        fp += int(previsto == 1 and alvo == 0)
        tn += int(previsto == 0 and alvo == 0)
        fn += int(previsto == 0 and alvo == 1)
    total = max(1, len(alvos))
    precisao = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    especificidade = tn / (tn + fp) if tn + fp else 0.0
    brier = sum((p - y) ** 2 for p, y in zip(probabilidades, alvos, strict=True)) / total
    ece = 0.0
    for inicio in (0.0, 0.2, 0.4, 0.6, 0.8):
        itens = [
            (p, y)
            for p, y in zip(probabilidades, alvos, strict=True)
            if inicio <= p < inicio + 0.2 or (inicio == 0.8 and p == 1.0)
        ]
        if itens:
            ece += (
                len(itens)
                / total
                * abs(sum(p for p, _ in itens) / len(itens) - sum(y for _, y in itens) / len(itens))
            )
    return {
        "amostras": len(alvos),
        "acuracia": (tp + tn) / total,
        "precisao": precisao,
        "recall": recall,
        "especificidade": especificidade,
        "acuracia_balanceada": (recall + especificidade) / 2,
        "f1": 2 * precisao * recall / (precisao + recall) if precisao + recall else 0.0,
        "brier": brier,
        "ece": ece,
        "limiar": limiar,
        "matriz_confusao": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


async def treinar_modelo(session: AsyncSession) -> ModeloRegistrabilidade:
    linhas = (
        await session.execute(
            select(RotuloHistoricoMarca, ParTreinamentoMarca, Processo.titulo)
            .outerjoin(
                ParTreinamentoMarca, ParTreinamentoMarca.rotulo_id == RotuloHistoricoMarca.id
            )
            .join(Processo, Processo.id == RotuloHistoricoMarca.processo_id)
            .where(
                RotuloHistoricoMarca.status_revisao != "rejeitada",
                RotuloHistoricoMarca.elegivel_treinamento.is_(True),
            )
            .order_by(RotuloHistoricoMarca.data_referencia, RotuloHistoricoMarca.id)
        )
    ).all()
    agrupadas: dict[int, tuple[RotuloHistoricoMarca, str, list[dict[str, float]]]] = {}
    for rotulo, par, titulo in linhas:
        agrupadas.setdefault(rotulo.id, (rotulo, titulo or "", []))
        if par is not None:
            agrupadas[rotulo.id][2].append(par.atributos)
    amostras = [
        (
            rotulo.data_referencia,
            agregar_atributos(pares, marca=titulo),
            int(rotulo.alvo_deferimento),
            float(rotulo.confianca if rotulo.confianca is not None else 1.0),
        )
        for rotulo, titulo, pares in agrupadas.values()
    ]
    if len(amostras) < 30 or len({item[2] for item in amostras}) < 2:
        raise ValueError("São necessários ao menos 30 rótulos com deferimentos e indeferimentos")
    treino_fim = max(1, int(len(amostras) * 0.70))
    validacao_fim = max(treino_fim + 1, int(len(amostras) * 0.85))
    # O peso de confiança pondera apenas o treino/bootstrap; validação e teste medem
    # desempenho real com pesos neutros para não mascarar a qualidade do modelo.
    treino = [(x, y, peso) for _, x, y, peso in amostras[:treino_fim]]
    validacao = amostras[treino_fim:validacao_fim]
    teste = amostras[validacao_fim:] or amostras[-max(1, len(amostras) // 10) :]
    parametros = _ajustar_logistica(treino)
    parametros["bootstrap_modelos"] = _modelos_bootstrap(treino)
    probs_validacao = [_probabilidade(x, parametros) for _, x, _, _ in validacao]
    calibracao = _ajustar_platt(probs_validacao, [y for _, _, y, _ in validacao])
    probs_validacao_cal = [calibrar(p, calibracao) for p in probs_validacao]
    alvos_validacao = [y for _, _, y, _ in validacao]
    # Corte de decisão sintonizado na validação (as probabilidades calibradas ficam
    # comprimidas; 0,5 degeneraria as previsões). Usado nas métricas e na inferência.
    limiar = _limiar_otimo(probs_validacao_cal, alvos_validacao)
    parametros["limiar_decisao"] = limiar
    probs_teste = [calibrar(_probabilidade(x, parametros), calibracao) for _, x, _, _ in teste]
    metricas = _metricas(probs_teste, [y for _, _, y, _ in teste], limiar)
    metricas["validacao"] = _metricas(probs_validacao_cal, alvos_validacao, limiar)
    versao = f"registrabilidade-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    modelo = ModeloRegistrabilidade(
        versao=versao,
        status="candidato",
        atributos=list(ATRIBUTOS_MODELO),
        parametros=parametros,
        calibracao=calibracao,
        metricas=metricas,
        dataset={
            "versao_atributos": VERSAO_ATRIBUTOS,
            "versao_estimativa": VERSAO_ESTIMATIVA,
            "escopo": ESCOPO_ESTIMATIVA,
            "total": len(amostras),
            "treino": len(treino),
            "validacao": len(validacao),
            "teste": len(teste),
            "positivos": sum(item[2] for item in amostras),
            "negativos": len(amostras) - sum(item[2] for item in amostras),
            "fundamentos": {
                fundamento: sum(
                    item[0].fundamento == fundamento for item in agrupadas.values()
                )
                for fundamento in sorted({item[0].fundamento for item in agrupadas.values()})
            },
            "divisao": "temporal_70_15_15",
            "bootstrap_modelos": len(parametros["bootstrap_modelos"]),
            "corte_dados": max(item[0] for item in amostras).isoformat(),
        },
        corte_treino=amostras[treino_fim - 1][0],
        corte_validacao=amostras[min(validacao_fim - 1, len(amostras) - 1)][0],
    )
    session.add(modelo)
    await session.commit()
    await session.refresh(modelo)
    return modelo


async def ativar_modelo(
    session: AsyncSession, modelo: ModeloRegistrabilidade, administrador: str
) -> None:
    controle = await obter_controle(session)
    revisoes = await session.scalar(
        select(func.count())
        .select_from(PrevisaoRegistrabilidade)
        .where(PrevisaoRegistrabilidade.nivel_humano.is_not(None))
    )
    bloqueios = validar_modelo_para_cliente(modelo, controle, int(revisoes or 0))
    if bloqueios:
        raise ValueError(
            "Modelo reprovado pelos critérios automáticos: " + "; ".join(bloqueios)
        )
    await session.execute(
        update(ModeloRegistrabilidade)
        .where(ModeloRegistrabilidade.status == "ativo")
        .values(status="arquivado")
    )
    modelo.status = "ativo"
    modelo.ativado_em = datetime.now(UTC)
    modelo.ativado_por = administrador
    await session.commit()


def _nivel_probabilidade(probabilidade: float, limiar: float = 0.5) -> str:
    """Níveis espaçados em torno do corte de decisão do modelo (não do 0,5 fixo).

    Como as probabilidades calibradas são comprimidas, ancorar os níveis no limiar
    sintonizado evita que quase tudo caia em 'crítico'.
    """
    if probabilidade >= limiar + 0.12:
        return "favoravel"
    if probabilidade >= limiar:
        return "atencao"
    if probabilidade >= limiar - 0.08:
        return "alto_risco"
    return "critico"


def _cobertura_entrada(atributos: dict[str, float], parametros: dict[str, Any]) -> float:
    distancias = []
    for nome in parametros.get("pesos", {}):
        desvio = float(parametros["desvios"].get(nome, 1.0))
        diferenca = atributos.get(nome, 0.0) - parametros["medias"].get(nome, 0.0)
        if desvio <= 1e-5:
            # Uma variável constante no treino não deve gerar infinito. Se o valor novo
            # divergir, registramos uma penalidade explícita e limitada de extrapolação.
            distancia = 0.0 if abs(diferenca) <= 1e-6 else 4.0
        else:
            distancia = max(-6.0, min(6.0, diferenca / desvio))
        distancias.append(distancia**2)
    distancia_media = math.sqrt(sum(distancias) / max(1, len(distancias)))
    return max(0.0, min(1.0, 1 / (1 + max(0.0, distancia_media - 1))))


def prever(atributos: dict[str, float], modelo: ModeloRegistrabilidade) -> PredicaoModelo:
    probabilidade = calibrar(_probabilidade(atributos, modelo.parametros), modelo.calibracao)
    probabilidades_bootstrap = [
        calibrar(_probabilidade(atributos, parametros), modelo.calibracao)
        for parametros in modelo.parametros.get("bootstrap_modelos", [])
    ]
    if len(probabilidades_bootstrap) >= 10:
        probabilidade_inferior = _percentil(probabilidades_bootstrap, 0.05)
        probabilidade_superior = _percentil(probabilidades_bootstrap, 0.95)
    else:
        # Modelos antigos continuam calculando em sombra, mas recebem intervalo amplo e
        # ficam inelegíveis para apresentação ao cliente até novo treinamento.
        probabilidade_inferior = max(0.0, probabilidade - 0.25)
        probabilidade_superior = min(1.0, probabilidade + 0.25)
    cobertura = _cobertura_entrada(atributos, modelo.parametros)
    largura = probabilidade_superior - probabilidade_inferior
    qualidade_calibracao = 1 - min(1.0, float(modelo.metricas.get("ece", 0.25)) / 0.25)
    confianca = max(
        0.0,
        min(1.0, 0.45 * (1 - largura) + 0.30 * qualidade_calibracao + 0.25 * cobertura),
    )
    confianca_rotulo = "alta" if confianca >= 0.75 else "media" if confianca >= 0.55 else "baixa"
    fatores = []
    nomes_modelo = [
        nome
        for nome in (modelo.atributos or list(modelo.parametros.get("pesos", {})))
        if nome in modelo.parametros.get("pesos", {})
    ]
    for nome in nomes_modelo:
        padronizado = (
            atributos.get(nome, 0.0) - modelo.parametros["medias"][nome]
        ) / modelo.parametros["desvios"][nome]
        impacto = modelo.parametros["pesos"][nome] * padronizado
        fatores.append(
            {
                "atributo": nome,
                "rotulo": ROTULOS_ATRIBUTOS.get(nome, nome.replace("_", " ")),
                "impacto": round(impacto, 4),
                "efeito": "favoravel" if impacto >= 0 else "risco",
            }
        )
    fatores.sort(key=lambda item: abs(item["impacto"]), reverse=True)
    limiar = float(modelo.parametros.get("limiar_decisao", 0.5))
    return PredicaoModelo(
        probabilidade=probabilidade,
        probabilidade_inferior=probabilidade_inferior,
        probabilidade_superior=probabilidade_superior,
        nivel=_nivel_probabilidade(probabilidade, limiar),
        confianca=confianca,
        confianca_rotulo=confianca_rotulo,
        cobertura_entrada=cobertura,
        fatores=fatores[:5],
    )


def validar_modelo_para_cliente(
    modelo: ModeloRegistrabilidade | None,
    controle: ControleAprendizadoMarca,
    revisoes_humanas: int,
) -> list[str]:
    if modelo is None:
        return ["Nenhum modelo ativo"]
    metricas = modelo.metricas or {}
    dataset = modelo.dataset or {}
    bloqueios = []
    if float(metricas.get("recall", 0)) < controle.minimo_recall:
        bloqueios.append("Recall do teste abaixo do mínimo")
    if float(metricas.get("especificidade", 0)) < controle.minimo_especificidade:
        bloqueios.append("Especificidade do teste abaixo do mínimo")
    if float(metricas.get("brier", 1)) > controle.maximo_brier:
        bloqueios.append("Calibração Brier acima do máximo")
    if float(metricas.get("ece", 1)) > controle.maximo_ece:
        bloqueios.append("Erro de calibração ECE acima do máximo")
    if int(dataset.get("total", 0)) < controle.minimo_amostras_modelo:
        bloqueios.append("Amostras históricas insuficientes")
    if int(dataset.get("teste", 0)) < controle.minimo_amostras_teste:
        bloqueios.append("Amostras do teste temporal insuficientes")
    if int(dataset.get("bootstrap_modelos", 0)) < 10:
        bloqueios.append("Modelo sem intervalo bootstrap válido")
    positivos = int(dataset.get("positivos", 0))
    negativos = int(dataset.get("negativos", 0))
    total = int(dataset.get("total", 0))
    if {"positivos", "negativos"}.issubset(dataset) and total and min(positivos, negativos) / total < 0.15:
        bloqueios.append("Distribuição histórica excessivamente desbalanceada")
    return bloqueios


def validar_estimativa_para_cliente(
    resultado: PredicaoModelo,
    modelo: ModeloRegistrabilidade,
    controle: ControleAprendizadoMarca,
    revisoes_humanas: int,
) -> list[str]:
    bloqueios = validar_modelo_para_cliente(modelo, controle, revisoes_humanas)
    if (
        resultado.probabilidade_superior - resultado.probabilidade_inferior
        > controle.largura_maxima_intervalo
    ):
        bloqueios.append("Faixa de incerteza ampla para esta pesquisa")
    if resultado.cobertura_entrada < controle.minima_cobertura:
        bloqueios.append("Pesquisa fora da cobertura histórica adequada")
    return bloqueios


def decidir_exibicao_estimativa(
    controle: ControleAprendizadoMarca,
    alertas_qualidade: list[str],
) -> tuple[str, bool, list[str]]:
    """Dispensa revisão humana, mas preserva os gates técnicos automáticos."""
    if controle.exibir_cliente and not alertas_qualidade:
        return "cliente", True, alertas_qualidade
    return "sombra", False, alertas_qualidade


async def obter_controle(session: AsyncSession) -> ControleAprendizadoMarca:
    controle = await session.get(ControleAprendizadoMarca, 1)
    if controle is None:
        controle = ControleAprendizadoMarca(id=1)
        session.add(controle)
        await session.flush()
    return controle


def elegivel_rollout(pesquisa_id: str, percentual: int) -> bool:
    if percentual >= 100:
        return True
    if percentual <= 0:
        return False
    return int(normalizar_numero_processo(pesquisa_id)[:8] or "0", 16) % 100 < percentual


async def registrar_previsao_sombra(
    session: AsyncSession,
    *,
    pesquisa_id: str,
    pares: list[dict[str, float]],
    marca: str = "",
) -> PrevisaoRegistrabilidade | None:
    controle = await obter_controle(session)
    if not controle.inferencia_habilitada:
        return None
    modelo = (
        await session.execute(
            select(ModeloRegistrabilidade)
            .where(ModeloRegistrabilidade.status == "ativo")
            .order_by(ModeloRegistrabilidade.ativado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if modelo is None:
        return None
    atributos = agregar_atributos(pares, marca=marca)
    resultado = prever(atributos, modelo)
    revisoes = await session.scalar(
        select(func.count())
        .select_from(PrevisaoRegistrabilidade)
        .where(PrevisaoRegistrabilidade.nivel_humano.is_not(None))
    )
    previsao = (
        await session.execute(
            select(PrevisaoRegistrabilidade).where(
                PrevisaoRegistrabilidade.pesquisa_id == pesquisa_id,
                PrevisaoRegistrabilidade.modelo_id == modelo.id,
            )
        )
    ).scalar_one_or_none()
    if previsao is None:
        previsao = PrevisaoRegistrabilidade(pesquisa_id=pesquisa_id, modelo_id=modelo.id)
        session.add(previsao)
    motivos_inelegibilidade = validar_estimativa_para_cliente(
        resultado, modelo, controle, int(revisoes or 0)
    )
    previsao.modo, previsao.elegivel_cliente, motivos_inelegibilidade = decidir_exibicao_estimativa(
        controle, motivos_inelegibilidade
    )
    previsao.probabilidade_deferimento = resultado.probabilidade
    previsao.probabilidade_inferior = resultado.probabilidade_inferior
    previsao.probabilidade_superior = resultado.probabilidade_superior
    previsao.nivel = resultado.nivel
    previsao.confianca = resultado.confianca
    previsao.confianca_rotulo = resultado.confianca_rotulo
    previsao.cobertura_entrada = resultado.cobertura_entrada
    previsao.motivos_inelegibilidade = motivos_inelegibilidade
    previsao.escopo_estimativa = ESCOPO_ESTIMATIVA
    previsao.amostras_referencia = int(modelo.dataset.get("total", 0))
    corte_dados = modelo.dataset.get("corte_dados")
    previsao.corte_dados = date.fromisoformat(corte_dados) if corte_dados else None
    previsao.atributos = atributos
    previsao.fatores_principais = resultado.fatores
    await session.flush()
    return previsao
