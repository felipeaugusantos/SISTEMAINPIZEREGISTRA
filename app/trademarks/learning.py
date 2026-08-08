import math
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

VERSAO_ATRIBUTOS = "atributos-marcarios-1.0"
VERSAO_ESTIMATIVA = "deferimento-merito-2.0"
ESCOPO_ESTIMATIVA = "deferimento_exame_merito"
QUANTIDADE_BOOTSTRAP = 24
ATRIBUTOS_MODELO = (
    "similaridade_sequencia",
    "jaccard_tokens",
    "jaccard_trigramas",
    "fonetica_igual",
    "classe_identica",
    "afinidade_conhecida",
    "candidato_ativo",
    "alto_renome",
    "quantidade_candidatos_norm",
)
ROTULOS_ATRIBUTOS = {
    "similaridade_sequencia": "semelhança geral do nome",
    "jaccard_tokens": "palavras em comum",
    "jaccard_trigramas": "trechos do nome em comum",
    "fonetica_igual": "semelhança fonética",
    "classe_identica": "classe de Nice idêntica",
    "afinidade_conhecida": "afinidade entre atividades",
    "candidato_ativo": "situação ativa da anterioridade",
    "alto_renome": "marca de alto renome",
    "quantidade_candidatos_norm": "volume de anterioridades",
}


@dataclass(frozen=True, slots=True)
class RotuloExtraido:
    rotulo: str
    alvo_deferimento: bool
    fundamento: str
    confianca: float
    movimentacao: Movimentacao


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


def extrair_rotulo(movimentacoes: list[Movimentacao]) -> RotuloExtraido | None:
    """Extrai apenas decisões de mérito; arquivamentos formais não viram exemplos."""
    ordenadas = sorted(
        movimentacoes,
        key=lambda item: (item.data_rpi, item.numero_rpi, item.id or 0),
        reverse=True,
    )
    for movimento in ordenadas:
        texto = _sem_acentos(f"{movimento.codigo_despacho or ''} {movimento.descricao}")
        if "recurso" in texto and "decisao" not in texto:
            continue
        negativo = any(
            termo in texto
            for termo in (
                "indeferimento do pedido",
                "pedido indeferido",
                "indeferido o pedido",
            )
        )
        positivo = not negativo and any(
            termo in texto
            for termo in (
                "deferimento do pedido",
                "pedido deferido",
                "concessao de registro",
                "registro de marca concedido",
            )
        )
        if positivo:
            return RotuloExtraido("deferida", True, "deferimento", 1.0, movimento)
        if negativo:
            if any(
                termo in texto
                for termo in ("124, xix", "124 xix", "colidencia", "reproducao", "imitacao")
            ):
                fundamento = "conflito_anterior"
                confianca = 0.95
            elif any(
                termo in texto for termo in ("descritiv", "generic", "distintiv", "uso comum")
            ):
                fundamento = "falta_distintividade"
                confianca = 0.9
            elif any(termo in texto for termo in ("124", "proibicao legal", "irregistravel")):
                fundamento = "outra_proibicao"
                confianca = 0.8
            else:
                fundamento = "indeferimento_nao_especificado"
                confianca = 0.65
            return RotuloExtraido("indeferida", False, fundamento, confianca, movimento)
    return None


def extrair_atributos_par(
    marca: str,
    candidata: str,
    classes_marca: list[str],
    classes_candidata: list[str],
    *,
    afinidade_conhecida: bool,
    candidata_ativa: bool,
    alto_renome: bool,
) -> dict[str, float]:
    marca_norm = normalizar_texto(marca)
    candidata_norm = normalizar_texto(candidata)
    tokens_marca = set(marca_norm.split())
    tokens_candidata = set(candidata_norm.split())
    return {
        "similaridade_sequencia": SequenceMatcher(None, marca_norm, candidata_norm).ratio(),
        "jaccard_tokens": _jaccard(tokens_marca, tokens_candidata),
        "jaccard_trigramas": _jaccard(_trigramas(marca_norm), _trigramas(candidata_norm)),
        "fonetica_igual": float(
            bool(marca_norm and _fonetica(marca_norm) == _fonetica(candidata_norm))
        ),
        "classe_identica": float(bool(set(classes_marca) & set(classes_candidata))),
        "afinidade_conhecida": float(afinidade_conhecida),
        "candidato_ativo": float(candidata_ativa),
        "alto_renome": float(alto_renome),
    }


def agregar_atributos(pares: list[dict[str, float]]) -> dict[str, float]:
    atributos = {
        nome: max((par.get(nome, 0.0) for par in pares), default=0.0)
        for nome in ATRIBUTOS_MODELO
        if nome != "quantidade_candidatos_norm"
    }
    atributos["quantidade_candidatos_norm"] = min(1.0, len(pares) / 10.0)
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
    limite: int = 500,
    candidatos_por_processo: int = 8,
) -> tuple[int, int]:
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
                alto_renome=False,
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
    linhas: list[tuple[dict[str, float], int]],
    *,
    epocas: int = 1200,
    taxa: float = 0.08,
    regularizacao: float = 0.002,
) -> dict[str, Any]:
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
    positivos = sum(alvo for _, alvo in linhas)
    negativos = len(linhas) - positivos
    pesos_classe = {
        1: len(linhas) / (2 * positivos) if positivos else 1.0,
        0: len(linhas) / (2 * negativos) if negativos else 1.0,
    }
    for _ in range(epocas):
        gradientes = {nome: 0.0 for nome in ATRIBUTOS_MODELO}
        gradiente_vies = 0.0
        for atributos, alvo in linhas:
            padronizados = {
                nome: (atributos.get(nome, 0.0) - medias[nome]) / desvios[nome]
                for nome in ATRIBUTOS_MODELO
            }
            previsao = _sigmoid(
                vies + sum(pesos[nome] * padronizados[nome] for nome in ATRIBUTOS_MODELO)
            )
            erro = (previsao - alvo) * pesos_classe[alvo]
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
    for nome in ATRIBUTOS_MODELO:
        padronizado = (atributos.get(nome, 0.0) - parametros["medias"][nome]) / parametros[
            "desvios"
        ][nome]
        linear += parametros["pesos"][nome] * padronizado
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
    treino: list[tuple[dict[str, float], int]], quantidade: int = QUANTIDADE_BOOTSTRAP
) -> list[dict[str, Any]]:
    """Amostra a incerteza do ajuste sem alterar o conjunto temporal de teste."""
    gerador = random.Random(20260808 + len(treino))
    modelos = []
    for _ in range(quantidade):
        amostra = [treino[gerador.randrange(len(treino))] for _ in treino]
        if len({alvo for _, alvo in amostra}) < 2:
            continue
        modelos.append(_ajustar_logistica(amostra, epocas=500))
    return modelos


def _metricas(probabilidades: list[float], alvos: list[int]) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for probabilidade, alvo in zip(probabilidades, alvos, strict=True):
        previsto = int(probabilidade >= 0.5)
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
        "matriz_confusao": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


async def treinar_modelo(session: AsyncSession) -> ModeloRegistrabilidade:
    linhas = (
        await session.execute(
            select(RotuloHistoricoMarca, ParTreinamentoMarca)
            .outerjoin(
                ParTreinamentoMarca, ParTreinamentoMarca.rotulo_id == RotuloHistoricoMarca.id
            )
            .where(RotuloHistoricoMarca.status_revisao != "rejeitada")
            .order_by(RotuloHistoricoMarca.data_referencia, RotuloHistoricoMarca.id)
        )
    ).all()
    agrupadas: dict[int, tuple[RotuloHistoricoMarca, list[dict[str, float]]]] = {}
    for rotulo, par in linhas:
        agrupadas.setdefault(rotulo.id, (rotulo, []))
        if par is not None:
            agrupadas[rotulo.id][1].append(par.atributos)
    amostras = [
        (rotulo.data_referencia, agregar_atributos(pares), int(rotulo.alvo_deferimento))
        for rotulo, pares in agrupadas.values()
    ]
    if len(amostras) < 30 or len({item[2] for item in amostras}) < 2:
        raise ValueError("São necessários ao menos 30 rótulos com deferimentos e indeferimentos")
    treino_fim = max(1, int(len(amostras) * 0.70))
    validacao_fim = max(treino_fim + 1, int(len(amostras) * 0.85))
    treino = [(x, y) for _, x, y in amostras[:treino_fim]]
    validacao = amostras[treino_fim:validacao_fim]
    teste = amostras[validacao_fim:] or amostras[-max(1, len(amostras) // 10) :]
    parametros = _ajustar_logistica(treino)
    parametros["bootstrap_modelos"] = _modelos_bootstrap(treino)
    probs_validacao = [_probabilidade(x, parametros) for _, x, _ in validacao]
    calibracao = _ajustar_platt(probs_validacao, [y for _, _, y in validacao])
    probs_teste = [calibrar(_probabilidade(x, parametros), calibracao) for _, x, _ in teste]
    metricas = _metricas(probs_teste, [y for _, _, y in teste])
    metricas["validacao"] = _metricas(
        [calibrar(p, calibracao) for p in probs_validacao],
        [y for _, _, y in validacao],
    )
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
    await session.execute(
        update(ModeloRegistrabilidade)
        .where(ModeloRegistrabilidade.status == "ativo")
        .values(status="arquivado")
    )
    modelo.status = "ativo"
    modelo.ativado_em = datetime.now(UTC)
    modelo.ativado_por = administrador
    await session.commit()


def _nivel_probabilidade(probabilidade: float) -> str:
    if probabilidade >= 0.75:
        return "favoravel"
    if probabilidade >= 0.55:
        return "atencao"
    if probabilidade >= 0.35:
        return "alto_risco"
    return "critico"


def _cobertura_entrada(atributos: dict[str, float], parametros: dict[str, Any]) -> float:
    distancias = []
    for nome in ATRIBUTOS_MODELO:
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
    for nome in ATRIBUTOS_MODELO:
        padronizado = (
            atributos.get(nome, 0.0) - modelo.parametros["medias"][nome]
        ) / modelo.parametros["desvios"][nome]
        impacto = modelo.parametros["pesos"][nome] * padronizado
        fatores.append(
            {
                "atributo": nome,
                "rotulo": ROTULOS_ATRIBUTOS[nome],
                "impacto": round(impacto, 4),
                "efeito": "favoravel" if impacto >= 0 else "risco",
            }
        )
    fatores.sort(key=lambda item: abs(item["impacto"]), reverse=True)
    return PredicaoModelo(
        probabilidade=probabilidade,
        probabilidade_inferior=probabilidade_inferior,
        probabilidade_superior=probabilidade_superior,
        nivel=_nivel_probabilidade(probabilidade),
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
    if revisoes_humanas < controle.minimo_revisoes_humanas:
        bloqueios.append("Revisões humanas insuficientes")
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
    """Publica a estimativa preliminar; qualidade e revisão geram alertas, não bloqueios."""
    if controle.exibir_cliente:
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
    atributos = agregar_atributos(pares)
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
