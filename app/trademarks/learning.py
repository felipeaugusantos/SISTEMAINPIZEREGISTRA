import hashlib
import math
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
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
    PesquisaMarca,
    PrevisaoRegistrabilidade,
    Processo,
    RotuloHistoricoMarca,
    TipoProcesso,
    VersaoRelatorioMarca,
    processo_titulares,
)
from app.normalization import normalizar_numero_processo
from app.search import normalizar_texto
from app.trademarks.lexico import ESCALA_FREQUENCIA_TOKEN, STOPWORDS_FREQUENCIA, frequencias_tokens
from app.trademarks.model_status import StatusModelo, normalizar_status_modelo

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
    "antiguidade_candidata_norm",
    "portfolio_titular_candidata_norm",
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
    "antiguidade_candidata_norm": "antiguidade da anterioridade",
    "portfolio_titular_candidata_norm": "porte do portfólio do titular da anterioridade",
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
    return "".join(char for char in unicodedata.normalize("NFKD", valor) if not unicodedata.combining(char)).lower()


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
    frequencias = frequencias_tokens()
    # O termo (não funcional) mais comum dirige a descritividade: se QUALQUER termo é
    # genérico, a marca tende ao descritivo. Termo raro/inventado => frequência baixa.
    frequencia_max = max(
        (frequencias.get(token, 0) for token in tokens if token not in STOPWORDS_FREQUENCIA),
        default=0,
    )
    return {
        "marca_token_unico": float(len(tokens) <= 1),
        "marca_num_tokens_norm": min(1.0, len(tokens) / 5.0),
        "marca_comprimento_norm": min(1.0, comprimento / 20.0),
        "marca_frequencia_max_norm": min(1.0, frequencia_max / ESCALA_FREQUENCIA_TOKEN),
    }


def _evidencias_classificacao(movimentacoes: list[Movimentacao], decisao: Movimentacao) -> tuple[dict[str, str], ...]:
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
        # Arquivamento definitivo (ex.: falta de pagamento da concessao apos um
        # deferimento) e um encerramento formal, nao uma decisao de merito -- se ele
        # for a movimentacao relevante mais recente (varredura e do mais novo pro mais
        # antigo), o processo nunca chegou a um resultado real e nao deve virar exemplo,
        # mesmo que uma decisao de merito mais antiga exista no historico.
        if "desarquiv" not in texto and ("arquivamento" in texto or "arquivado" in texto):
            return None
        positivo_recurso = codigo == "IPAS237" or ("recurso provido" in texto and "deferimento" in texto)
        negativo = codigo == "IPAS024" or any(
            termo in texto
            for termo in (
                "indeferimento do pedido",
                "pedido indeferido",
                "indeferido o pedido",
            )
        )
        positivo = positivo_recurso or (
            not negativo
            and (
                codigo == "IPAS029"
                or any(
                    termo in texto
                    for termo in (
                        "deferimento do pedido",
                        "pedido deferido",
                        "concessao de registro",
                        "registro de marca concedido",
                    )
                )
            )
        )
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


# Anos de antiguidade em que uma anterioridade satura como "consolidada" (art. 124/126
# LPI: quanto mais tempo em vigor sem contestação, maior a presunção de validade que o
# exame do INPI tende a reconhecer). Além disso o sinal já não discrimina mais.
_ESCALA_ANTIGUIDADE_ANOS = 20.0


def antiguidade_norm(data_candidata: date | None, data_referencia: date | None) -> float:
    if data_candidata is None or data_referencia is None:
        return 0.0
    anos = (data_referencia - data_candidata).days / 365.25
    return min(1.0, max(0.0, anos / _ESCALA_ANTIGUIDADE_ANOS))


# Nº de marcas no portfólio de um titular a partir do qual ele satura como "titular
# de portfólio grande" (empresa com marca consolidada, mais provável de gerar
# oposição/indeferimento por conflito real do que uma coincidência isolada de nome).
_ESCALA_PORTFOLIO_TITULAR = 20.0


async def contar_marcas_por_titular(session: AsyncSession, titular_ids: list[int]) -> dict[int, int]:
    """Nº de processos de marca distintos ligados a cada titular_id informado."""
    if not titular_ids:
        return {}
    linhas = await session.execute(
        select(processo_titulares.c.titular_id, func.count(func.distinct(processo_titulares.c.processo_id)))
        .where(processo_titulares.c.titular_id.in_(titular_ids))
        .group_by(processo_titulares.c.titular_id)
    )
    return dict(linhas.all())


def portfolio_titular_norm(contagens: dict[int, int], titular_ids: list[int]) -> float:
    maior = max((contagens.get(tid, 0) for tid in titular_ids), default=0)
    return min(1.0, maior / _ESCALA_PORTFOLIO_TITULAR)


def extrair_atributos_par(
    marca: str,
    candidata: str,
    classes_marca: list[str],
    classes_candidata: list[str],
    *,
    afinidade_conhecida: bool,
    candidata_ativa: bool,
    antiguidade_candidata_norm: float = 0.0,
    portfolio_titular_candidata_norm: float = 0.0,
) -> dict[str, float]:
    marca_norm = normalizar_texto(marca)
    candidata_norm = normalizar_texto(candidata)
    tokens_marca = set(marca_norm.split())
    tokens_candidata = set(candidata_norm.split())
    fonetica_marca = _fonetica(marca_norm)
    fonetica_candidata = _fonetica(candidata_norm)
    menor_conjunto = min(len(tokens_marca), len(tokens_candidata))
    prefixo_radical = bool(
        len(fonetica_marca) >= 4 and len(fonetica_candidata) >= 4 and fonetica_marca[:4] == fonetica_candidata[:4]
    )
    return {
        "similaridade_sequencia": SequenceMatcher(None, marca_norm, candidata_norm).ratio(),
        "jaccard_tokens": _jaccard(tokens_marca, tokens_candidata),
        "jaccard_trigramas": _jaccard(_trigramas(marca_norm), _trigramas(candidata_norm)),
        "nome_identico": float(bool(marca_norm and marca_norm == candidata_norm)),
        "contencao_tokens": (len(tokens_marca & tokens_candidata) / menor_conjunto if menor_conjunto else 0.0),
        "fonetica_igual": float(bool(fonetica_marca and fonetica_marca == fonetica_candidata)),
        "fonetica_similaridade": SequenceMatcher(None, fonetica_marca, fonetica_candidata).ratio(),
        "prefixo_radical": float(prefixo_radical),
        "classe_identica": float(bool(set(classes_marca) & set(classes_candidata))),
        "afinidade_conhecida": float(afinidade_conhecida),
        "candidato_ativo": float(candidata_ativa),
        "antiguidade_candidata_norm": antiguidade_candidata_norm,
        "portfolio_titular_candidata_norm": portfolio_titular_candidata_norm,
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
    top3 = sorted((par.get("similaridade_sequencia", 0.0) for par in pares), reverse=True)[:3]
    atributos["similaridade_top3_media"] = sum(top3) / len(top3) if top3 else 0.0
    atributos["conflitos_fortes_norm"] = min(
        1.0,
        sum(par.get("similaridade_sequencia", 0.0) >= 0.7 for par in pares) / 5.0,
    )
    atributos["conflitos_ativos_norm"] = min(1.0, sum(par.get("candidato_ativo", 0.0) >= 0.5 for par in pares) / 5.0)
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
        tuple(sorted((item.classe_origem, item.classe_destino))) for item in matriz if item.status_revisao == "aprovada"
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
    processos_candidatos = (
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
                .limit(limite * 3)
            )
        )
        .scalars()
        .all()
    )
    por_classe: dict[bool, list[Processo]] = {True: [], False: []}
    for processo in processos_candidatos:
        extraido = extrair_rotulo(processo.movimentacoes)
        if extraido is not None and processo.data_deposito is not None:
            por_classe[extraido.alvo_deferimento].append(processo)
    alvo_por_classe = max(1, limite // 2)
    processos = por_classe[True][:alvo_por_classe] + por_classe[False][:alvo_por_classe]
    if len(processos) < limite:
        selecionados = {processo.id for processo in processos}
        excedentes = [
            processo
            for processo in processos_candidatos
            if processo.id not in selecionados
            and extrair_rotulo(processo.movimentacoes) is not None
            and processo.data_deposito is not None
        ]
        processos.extend(excedentes[: limite - len(processos)])
    matriz = (await session.execute(select(AfinidadeClasse))).scalars().all()
    afinidades = _pares_afinidade(list(matriz))
    rotulos_processados = 0
    pares_processados = 0
    # Cache de portfólio por titular acumulado ao longo de toda a construção do dataset:
    # titulares se repetem muito entre candidatos de alvos diferentes, então evita reconsultar.
    cache_portfolio_titular: dict[int, int] = {}

    for processo in processos:
        extraido = extrair_rotulo(processo.movimentacoes)
        if extraido is None or processo.data_deposito is None:
            continue
        rotulo = (
            await session.execute(select(RotuloHistoricoMarca).where(RotuloHistoricoMarca.processo_id == processo.id))
        ).scalar_one_or_none()
        if rotulo is None:
            rotulo = RotuloHistoricoMarca(processo_id=processo.id)
            session.add(rotulo)
        if rotulo.status_revisao not in {"aprovada", "documental"}:
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
        # Recalculado sempre (mesmo para rotulos ja enriquecidos via despacho oficial,
        # que pulam o bloco acima): indeferimentos cujo motivo real nao e colidencia com
        # marca anterior (falta de distintividade, outra proibicao, ou motivo ainda nao
        # identificado) nao tem relacao causal com as features de similaridade nominativa
        # usadas pelo modelo. Treinar com eles ensina uma associacao espuria entre "alta
        # similaridade com algum candidato" e "indeferida", derrubando a especificidade.
        if rotulo.rotulo == "indeferida" and rotulo.fundamento != "conflito_anterior":
            rotulo.elegivel_treinamento = False
            rotulo.motivo_inelegibilidade = f"indeferimento_sem_relacao_com_similaridade:{rotulo.fundamento}"
        elif rotulo.status_revisao in {"aprovada", "documental"}:
            rotulo.elegivel_treinamento = True
            rotulo.motivo_inelegibilidade = None
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
                        selectinload(Processo.titulares),
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
        await session.execute(delete(ParTreinamentoMarca).where(ParTreinamentoMarca.rotulo_id == rotulo.id))
        classes_alvo = _classes(processo)
        titular_ids_faltantes = {
            titular.id
            for candidata in candidatos
            for titular in candidata.titulares
            if titular.id not in cache_portfolio_titular
        }
        if titular_ids_faltantes:
            cache_portfolio_titular.update(
                await contar_marcas_por_titular(session, list(titular_ids_faltantes))
            )
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
                antiguidade_candidata_norm=antiguidade_norm(candidata.data_deposito, processo.data_deposito),
                portfolio_titular_candidata_norm=portfolio_titular_norm(
                    cache_portfolio_titular, [titular.id for titular in candidata.titulares]
                ),
            )
            session.add(
                ParTreinamentoMarca(
                    rotulo_id=rotulo.id,
                    processo_candidato_id=candidata.id,
                    atributos=atributos,
                    alvo_conflito=(
                        not extraido.alvo_deferimento if extraido.fundamento == "conflito_anterior" else None
                    ),
                )
            )
            pares_processados += 1
        # Commit periodico em vez de uma unica transacao gigante: da visibilidade real de
        # progresso a quem acompanha de fora (o total de pares so aparece no banco apos o
        # commit) e limita o retrabalho perdido se a execucao cair no meio do caminho.
        if rotulos_processados % 100 == 0:
            await session.commit()
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
    regularizacao: float = 0.05,
) -> dict[str, Any]:
    """Regressão logística ponderada. Cada linha é (atributos, alvo, peso_confianca).

    O peso reflete a confiabilidade do rótulo extraído do despacho: um indeferimento
    genérico (0,65) influencia menos que um deferimento explícito (1,0), evitando que o
    ruído da extração por texto seja tratado como certeza.
    """
    medias = {nome: sum(item[0].get(nome, 0.0) for item in linhas) / len(linhas) for nome in ATRIBUTOS_MODELO}
    desvios = {}
    for nome in ATRIBUTOS_MODELO:
        variancia = sum((item[0].get(nome, 0.0) - medias[nome]) ** 2 for item in linhas) / len(linhas)
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
                nome: (atributos.get(nome, 0.0) - medias[nome]) / desvios[nome] for nome in ATRIBUTOS_MODELO
            }
            previsao = _sigmoid(vies + sum(pesos[nome] * padronizados[nome] for nome in ATRIBUTOS_MODELO))
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
    ordenadas = sorted(set(probabilidades))
    candidatos = [0.0, 1.0]
    candidatos.extend(ordenadas)
    candidatos.extend((a + b) / 2 for a, b in zip(ordenadas, ordenadas[1:], strict=False))
    melhor_limiar, melhor_bal, melhor_equilibrio = 0.5, -1.0, -1.0
    for limiar in sorted(set(candidatos)):
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
        equilibrio = min(recall, especificidade)
        if (bal, equilibrio) > (melhor_bal, melhor_equilibrio):
            melhor_bal, melhor_equilibrio, melhor_limiar = bal, equilibrio, limiar
    return melhor_limiar


def _metricas(probabilidades: list[float], alvos: list[int], limiar: float = 0.5) -> dict[str, Any]:
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
    calibracao_por_faixa = []
    for inicio in (0.0, 0.2, 0.4, 0.6, 0.8):
        itens = [
            (p, y)
            for p, y in zip(probabilidades, alvos, strict=True)
            if inicio <= p < inicio + 0.2 or (inicio == 0.8 and p == 1.0)
        ]
        if itens:
            probabilidade_media = sum(p for p, _ in itens) / len(itens)
            taxa_observada = sum(y for _, y in itens) / len(itens)
            erro_faixa = abs(probabilidade_media - taxa_observada)
            ece += len(itens) / total * erro_faixa
            calibracao_por_faixa.append(
                {
                    "inicio": inicio,
                    "fim": min(1.0, inicio + 0.2),
                    "amostras": len(itens),
                    "probabilidade_media": probabilidade_media,
                    "taxa_observada": taxa_observada,
                    "erro_absoluto": erro_faixa,
                }
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
        "calibracao_por_faixa": calibracao_por_faixa,
        "limiar": limiar,
        "matriz_confusao": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


def _distribuicoes_dataset(
    agrupadas: dict[int, tuple[RotuloHistoricoMarca, Processo, list[dict[str, float]]]],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    por_classe: dict[str, dict[str, int]] = {}
    por_periodo: dict[str, dict[str, int]] = {}

    def adicionar(destino: dict[str, dict[str, int]], chave: str, deferida: bool) -> None:
        contagem = destino.setdefault(chave, {"total": 0, "deferidas": 0, "indeferidas": 0})
        contagem["total"] += 1
        contagem["deferidas" if deferida else "indeferidas"] += 1

    for rotulo, processo, _ in agrupadas.values():
        for classe in sorted(
            {item.codigo for item in processo.classificacoes if item.sistema == "nice" and item.codigo}
        ) or ["SEM_CLASSE"]:
            adicionar(por_classe, classe, bool(rotulo.alvo_deferimento))
        adicionar(por_periodo, str(rotulo.data_referencia.year), bool(rotulo.alvo_deferimento))
    return por_classe, por_periodo


async def treinar_modelo(session: AsyncSession) -> ModeloRegistrabilidade:
    linhas = (
        await session.execute(
            select(RotuloHistoricoMarca, ParTreinamentoMarca, Processo)
            .outerjoin(ParTreinamentoMarca, ParTreinamentoMarca.rotulo_id == RotuloHistoricoMarca.id)
            .join(Processo, Processo.id == RotuloHistoricoMarca.processo_id)
            .where(
                RotuloHistoricoMarca.status_revisao != "rejeitada",
                RotuloHistoricoMarca.elegivel_treinamento.is_(True),
            )
            .options(selectinload(Processo.classificacoes))
            .order_by(RotuloHistoricoMarca.data_referencia, RotuloHistoricoMarca.id)
        )
    ).all()
    agrupadas: dict[int, tuple[RotuloHistoricoMarca, Processo, list[dict[str, float]]]] = {}
    for rotulo, par, processo in linhas:
        agrupadas.setdefault(rotulo.id, (rotulo, processo, []))
        if par is not None:
            agrupadas[rotulo.id][2].append(par.atributos)
    amostras = [
        (
            rotulo.data_referencia,
            agregar_atributos(pares, marca=titulo),
            int(rotulo.alvo_deferimento),
            float(rotulo.confianca if rotulo.confianca is not None else 1.0),
        )
        for rotulo, processo, pares in agrupadas.values()
        for titulo in [processo.titulo or ""]
    ]
    if len(amostras) < 30 or len({item[2] for item in amostras}) < 2:
        raise ValueError("São necessários ao menos 30 rótulos com deferimentos e indeferimentos")
    treino_fim = max(1, int(len(amostras) * 0.70))
    validacao_fim = max(treino_fim + 1, int(len(amostras) * 0.85))
    # Confiança e balanceamento de classe ponderam apenas treino/bootstrap. A regressão
    # aplica o balanceamento internamente; validação e teste ficam sem pesos.
    treino_bruto = amostras[:treino_fim]
    contagem_classes = {classe: sum(item[2] == classe for item in treino_bruto) for classe in (0, 1)}
    pesos_classes = {
        classe: len(treino_bruto) / (2 * max(1, quantidade)) for classe, quantidade in contagem_classes.items()
    }
    treino = [(x, y, peso) for _, x, y, peso in treino_bruto]
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
    distribuicao_classes, distribuicao_periodos = _distribuicoes_dataset(agrupadas)
    identidade_dataset = "|".join(
        f"{rotulo.id}:{processo.id}:{rotulo.data_referencia.isoformat()}:{int(bool(rotulo.alvo_deferimento))}"
        for rotulo, processo, _ in sorted(agrupadas.values(), key=lambda item: item[0].id)
    )
    dataset_hash = hashlib.sha256(identidade_dataset.encode("utf-8")).hexdigest()
    versao = f"registrabilidade-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    modelo = ModeloRegistrabilidade(
        versao=versao,
        status=StatusModelo.SHADOW.value,
        atributos=list(ATRIBUTOS_MODELO),
        parametros=parametros,
        calibracao=calibracao,
        metricas=metricas,
        dataset={
            "versao_atributos": VERSAO_ATRIBUTOS,
            "versao_estimativa": VERSAO_ESTIMATIVA,
            "dataset_version": f"dataset-{dataset_hash[:16]}",
            "dataset_sha256": dataset_hash,
            "escopo": ESCOPO_ESTIMATIVA,
            "total": len(amostras),
            "treino": len(treino),
            "validacao": len(validacao),
            "teste": len(teste),
            "positivos": sum(item[2] for item in amostras),
            "negativos": len(amostras) - sum(item[2] for item in amostras),
            "fundamentos": {
                fundamento: sum(item[0].fundamento == fundamento for item in agrupadas.values())
                for fundamento in sorted({item[0].fundamento for item in agrupadas.values()})
            },
            "distribuicao_por_classe": distribuicao_classes,
            "distribuicao_por_periodo": distribuicao_periodos,
            "divisao": "temporal_70_15_15",
            "bootstrap_modelos": len(parametros["bootstrap_modelos"]),
            "balanceamento_treino": {
                "contagens": contagem_classes,
                "pesos": pesos_classes,
            },
            "corte_dados": max(item[0] for item in amostras).isoformat(),
        },
        corte_treino=amostras[treino_fim - 1][0],
        corte_validacao=amostras[min(validacao_fim - 1, len(amostras) - 1)][0],
    )
    controle = await obter_controle(session)
    revisoes_humanas = int(
        await session.scalar(
            select(func.count())
            .select_from(PrevisaoRegistrabilidade)
            .where(PrevisaoRegistrabilidade.nivel_humano.is_not(None))
        )
        or 0
    )
    bloqueios_qualidade = validar_modelo_para_cliente(
        modelo,
        controle,
        revisoes_humanas,
        incluir_revisoes_humanas=False,
    )
    modelo.status = StatusModelo.SHADOW.value if bloqueios_qualidade else StatusModelo.VALIDATION.value
    modelo.dataset = {
        **modelo.dataset,
        "bloqueios_qualidade": bloqueios_qualidade,
        "revisoes_humanas": revisoes_humanas,
    }
    session.add(modelo)
    await session.commit()
    await session.refresh(modelo)
    return modelo


async def ativar_modelo(session: AsyncSession, modelo: ModeloRegistrabilidade, administrador: str) -> dict[str, Any]:
    if normalizar_status_modelo(modelo.status) is not StatusModelo.VALIDATION:
        raise ValueError("Somente modelos em VALIDATION podem ser promovidos para ACTIVE")
    controle = await obter_controle(session)
    revisoes = await session.scalar(
        select(func.count())
        .select_from(PrevisaoRegistrabilidade)
        .where(PrevisaoRegistrabilidade.nivel_humano.is_not(None))
    )
    bloqueios = validar_modelo_para_cliente(modelo, controle, int(revisoes or 0))
    if bloqueios:
        raise ValueError("Modelo bloqueado pelos gates de ativação: " + "; ".join(bloqueios))
    await session.execute(
        update(ModeloRegistrabilidade)
        .where(ModeloRegistrabilidade.status == StatusModelo.ACTIVE.value)
        .values(status=StatusModelo.DISABLED.value)
    )
    modelo.status = StatusModelo.ACTIVE.value
    modelo.ativado_em = datetime.now(UTC)
    modelo.ativado_por = administrador
    controle.inferencia_habilitada = True
    await session.flush()
    return await reprocessar_previsoes_pendentes(session)


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
        padronizado = (atributos.get(nome, 0.0) - modelo.parametros["medias"][nome]) / modelo.parametros["desvios"][
            nome
        ]
        impacto = modelo.parametros["pesos"][nome] * padronizado
        fatores.append(
            {
                "atributo": nome,
                "rotulo": ROTULOS_ATRIBUTOS.get(nome, nome.replace("_", " ")),
                "valor_entrada": round(float(atributos.get(nome, 0.0)), 6),
                "media_referencia": round(float(modelo.parametros["medias"][nome]), 6),
                "peso_modelo": round(float(modelo.parametros["pesos"][nome]), 6),
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
    *,
    incluir_revisoes_humanas: bool = True,
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
    if incluir_revisoes_humanas and revisoes_humanas < controle.minimo_revisoes_humanas:
        bloqueios.append(f"Revisões humanas insuficientes ({revisoes_humanas}/{controle.minimo_revisoes_humanas})")
    if int(dataset.get("bootstrap_modelos", 0)) < 10:
        bloqueios.append("Modelo sem intervalo bootstrap válido")
    if not dataset.get("distribuicao_por_classe"):
        bloqueios.append("Distribuição por classe Nice não documentada")
    if not dataset.get("distribuicao_por_periodo"):
        bloqueios.append("Distribuição temporal não documentada")
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
    if resultado.probabilidade_superior - resultado.probabilidade_inferior > controle.largura_maxima_intervalo:
        bloqueios.append("Faixa de incerteza ampla para esta pesquisa")
    if resultado.cobertura_entrada < controle.minima_cobertura:
        bloqueios.append("Pesquisa fora da cobertura histórica adequada")
    return bloqueios


def decidir_exibicao_estimativa(
    controle: ControleAprendizadoMarca,
    alertas_qualidade: list[str],
    *,
    modelo_status: str,
) -> tuple[str, bool, list[str]]:
    """Expõe somente modelo ACTIVE aprovado nos gates técnicos e humanos."""
    if (
        normalizar_status_modelo(modelo_status) is StatusModelo.ACTIVE
        and controle.exibir_cliente
        and not alertas_qualidade
    ):
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
    modelo_candidato: ModeloRegistrabilidade | None = None,
) -> PrevisaoRegistrabilidade | None:
    controle = await obter_controle(session)
    if modelo_candidato is None and not controle.inferencia_habilitada:
        return None
    modelo = modelo_candidato
    if modelo is None:
        modelo = (
            await session.execute(
                select(ModeloRegistrabilidade)
                .where(ModeloRegistrabilidade.status == StatusModelo.ACTIVE.value)
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
    motivos_inelegibilidade = validar_estimativa_para_cliente(resultado, modelo, controle, int(revisoes or 0))
    previsao.modo, previsao.elegivel_cliente, motivos_inelegibilidade = decidir_exibicao_estimativa(
        controle,
        motivos_inelegibilidade,
        modelo_status=modelo.status,
    )
    if modelo_candidato is not None:
        previsao.modo = "sombra"
        previsao.elegivel_cliente = False
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


def _pares_de_relatorio(payload: dict[str, Any]) -> list[dict[str, float]]:
    classes_atividade = [
        str(item.get("codigo")) for item in payload.get("classes_atividade") or [] if item.get("codigo")
    ]
    pares = []
    for item in payload.get("itens") or []:
        afinidade = item.get("afinidade_classes") or {}
        classes_processo = [
            str(classe.get("codigo"))
            for classe in item.get("classificacoes") or []
            if classe.get("sistema") == "nice" and classe.get("codigo")
        ]
        data_deposito_item = item.get("data_deposito")
        pares.append(
            extrair_atributos_par(
                str(payload.get("marca") or ""),
                str(item.get("titulo") or ""),
                classes_atividade,
                classes_processo,
                afinidade_conhecida=afinidade.get("nivel") in {"identica", "alta", "moderada"},
                candidata_ativa=item.get("relevancia_situacao") == "ativa",
                antiguidade_candidata_norm=antiguidade_norm(
                    date.fromisoformat(data_deposito_item) if data_deposito_item else None,
                    datetime.now(UTC).date(),
                ),
            )
        )
    return pares


async def reprocessar_previsoes_pendentes(
    session: AsyncSession,
    *,
    organizacao_id: int | None = None,
    limite: int | None = None,
    modelo_candidato: ModeloRegistrabilidade | None = None,
) -> dict[str, Any]:
    modelo = modelo_candidato
    if modelo is None:
        modelo = (
            await session.execute(
                select(ModeloRegistrabilidade)
                .where(ModeloRegistrabilidade.status == StatusModelo.ACTIVE.value)
                .order_by(ModeloRegistrabilidade.ativado_em.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if modelo is None:
        return {
            "status": "sem_modelo_ativo",
            "processadas": 0,
            "ignoradas_sem_relatorio": 0,
        }
    sem_previsao = (
        ~select(PrevisaoRegistrabilidade.id)
        .where(
            PrevisaoRegistrabilidade.pesquisa_id == PesquisaMarca.id,
            PrevisaoRegistrabilidade.modelo_id == modelo.id,
        )
        .exists()
    )
    filtros = [sem_previsao]
    if organizacao_id is not None:
        filtros.append(PesquisaMarca.organizacao_id == organizacao_id)
    consulta = select(PesquisaMarca).where(*filtros).order_by(PesquisaMarca.criado_em)
    if limite is not None:
        consulta = consulta.limit(limite)
    pesquisas = (await session.execute(consulta)).scalars().all()
    processadas = 0
    ignoradas = 0
    for pesquisa in pesquisas:
        versao = (
            await session.execute(
                select(VersaoRelatorioMarca)
                .where(VersaoRelatorioMarca.pesquisa_id == pesquisa.id)
                .order_by(VersaoRelatorioMarca.numero_versao.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if versao is None:
            ignoradas += 1
            continue
        previsao = await registrar_previsao_sombra(
            session,
            pesquisa_id=pesquisa.id,
            pares=_pares_de_relatorio(versao.payload or {}),
            marca=pesquisa.marca,
            modelo_candidato=modelo_candidato,
        )
        processadas += int(previsao is not None)
    await session.commit()
    return {
        "status": "concluido",
        "modelo": modelo.versao,
        "processadas": processadas,
        "ignoradas_sem_relatorio": ignoradas,
        "pendentes_consideradas": len(pesquisas),
    }


async def executar_pipeline_aprendizado(
    session: AsyncSession,
    *,
    administrador: str,
    limite_dataset: int = 3000,
    candidatos_por_processo: int = 12,
) -> dict[str, Any]:
    rotulos, pares = await construir_dataset_historico(
        session,
        limite=limite_dataset,
        candidatos_por_processo=candidatos_por_processo,
    )
    modelo = await treinar_modelo(session)
    controle = await obter_controle(session)
    revisoes_humanas = int(
        await session.scalar(
            select(func.count())
            .select_from(PrevisaoRegistrabilidade)
            .where(PrevisaoRegistrabilidade.nivel_humano.is_not(None))
        )
        or 0
    )
    bloqueios_ativacao = validar_modelo_para_cliente(
        modelo,
        controle,
        revisoes_humanas,
    )
    # Treinamento e validação nunca promovem automaticamente para ACTIVE. A exposição
    # exige uma ação explícita posterior do superadministrador.
    ativado = False
    reprocessamento: dict[str, Any] = {"status": "modelo_em_shadow", "processadas": 0}
    if modelo.status in {StatusModelo.SHADOW.value, StatusModelo.VALIDATION.value}:
        reprocessamento = await reprocessar_previsoes_pendentes(
            session,
            modelo_candidato=modelo,
        )
    return {
        "rotulos_processados": rotulos,
        "pares_processados": pares,
        "modelo_id": modelo.id,
        "modelo_versao": modelo.versao,
        "modelo_status": modelo.status,
        "ativado": ativado,
        "bloqueios": bloqueios_ativacao,
        "reprocessamento": reprocessamento,
    }
