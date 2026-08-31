from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AvaliacaoRiscoMarca,
    ExecucaoAgenteRegistrabilidade,
    ModeloRegistrabilidade,
    PesquisaMarca,
    PrevisaoRegistrabilidade,
    Processo,
    VersaoRelatorioMarca,
)
from app.normalization import normalizar_numero_processo
from app.trademarks.learning import extrair_rotulo
from app.trademarks.registrability import construir_matriz_registrabilidade

VERSAO_AGENTE = "registrabilidade-2.0"
COBERTURA_MINIMA = 0.60


@dataclass(frozen=True, slots=True)
class ResultadoAgenteRegistrabilidade:
    versao_agente: str
    status: str
    decisao: str
    abstencao: bool
    cobertura: float
    confianca: float | None
    probabilidade_deferimento: float | None
    probabilidade_inferior: float | None
    probabilidade_superior: float | None
    nivel_risco: str | None
    pontuacao_risco: int | None
    motivos: list[str]
    fatores_principais: list[dict[str, Any]]
    aviso: str


def _valor(objeto: object | None, nome: str, padrao: Any = None) -> Any:
    if objeto is None:
        return padrao
    if isinstance(objeto, dict):
        return objeto.get(nome, padrao)
    return getattr(objeto, nome, padrao)


def analisar_registrabilidade(
    *,
    matriz: dict[str, Any],
    pontuacao_risco: int | None,
    nivel_risco: str | None,
    previsao: PrevisaoRegistrabilidade | dict[str, Any] | None,
) -> ResultadoAgenteRegistrabilidade:
    """Consolida regras e estatistica sem permitir que a previsao sobreponha impedimentos."""
    cobertura_matriz = float(matriz.get("cobertura_percentual") or 0) / 100
    cobertura_modelo = float(_valor(previsao, "cobertura_entrada", 0) or 0)
    # A ausência de estatística não elimina a cobertura das evidências técnicas.
    cobertura = round(cobertura_matriz, 4)
    probabilidade = _valor(previsao, "probabilidade_deferimento")
    inferior = _valor(previsao, "probabilidade_inferior")
    superior = _valor(previsao, "probabilidade_superior")
    confianca = _valor(previsao, "confianca")
    fatores = list(_valor(previsao, "fatores_principais", []) or [])
    contagens = matriz.get("contagens") or {}
    impedimentos = int(contagens.get("possivel_impedimento") or 0)
    alertas = int(contagens.get("alerta") or 0)
    pendentes = int(contagens.get("nao_analisado") or 0)
    motivos: list[str] = []

    if impedimentos:
        motivos.append(f"{impedimentos} possível(is) impedimento(s) nas regras do INPI")
    if alertas:
        motivos.append(f"{alertas} ponto(s) de atenção")
    if pendentes:
        motivos.append(f"{pendentes} critério(s) ainda não analisado(s)")
    if previsao is None:
        motivos.append("modelo estatístico indisponível")
    elif cobertura_modelo < COBERTURA_MINIMA:
        motivos.append("estimativa estatística sem cobertura suficiente; indicador omitido")
        probabilidade = inferior = superior = confianca = None
        fatores = []
    if cobertura < COBERTURA_MINIMA:
        motivos.append(f"cobertura técnica de {round(cobertura * 100)}% abaixo do mínimo de 60%")

    abstencao = cobertura < COBERTURA_MINIMA or pontuacao_risco is None or nivel_risco is None
    if impedimentos or nivel_risco in {"alto", "critico"}:
        # Pendências não escondem riscos já demonstrados.
        decisao = "cenario_desfavoravel"
        status = "revisao_humana"
    elif abstencao:
        decisao = "dados_insuficientes"
        status = "revisao_humana"
    elif alertas or pendentes or nivel_risco == "moderado":
        decisao = "cenario_intermediario"
        status = "revisao_humana"
    elif probabilidade is None:
        decisao = "cenario_favoravel"
        status = "concluida"
    elif probabilidade is not None and probabilidade >= 0.70:
        decisao = "cenario_favoravel"
        status = "concluida"
    elif probabilidade is not None and probabilidade >= 0.45:
        decisao = "cenario_intermediario"
        status = "revisao_humana"
    else:
        decisao = "cenario_desfavoravel"
        status = "revisao_humana"

    return ResultadoAgenteRegistrabilidade(
        versao_agente=VERSAO_AGENTE,
        status=status,
        decisao=decisao,
        abstencao=abstencao,
        cobertura=cobertura,
        confianca=confianca,
        probabilidade_deferimento=probabilidade,
        probabilidade_inferior=inferior,
        probabilidade_superior=superior,
        nivel_risco=nivel_risco,
        pontuacao_risco=pontuacao_risco,
        motivos=motivos,
        fatores_principais=fatores,
        aviso=(
            "Estimativa indicativa e auditável. Não garante o registro e não substitui "
            "o exame de mérito realizado pelo INPI."
        ),
    )


def _hash_snapshot(snapshot: dict[str, Any]) -> str:
    serializado = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


async def registrar_execucao_agente(
    session: AsyncSession,
    *,
    pesquisa: PesquisaMarca,
    matriz: dict[str, Any],
    relatorio: dict[str, Any],
    pontuacao_risco: int | None,
    nivel_risco: str | None,
    previsao: PrevisaoRegistrabilidade | None,
    modelo_versao: str | None,
) -> ExecucaoAgenteRegistrabilidade:
    resultado = analisar_registrabilidade(
        matriz=matriz,
        pontuacao_risco=pontuacao_risco,
        nivel_risco=nivel_risco,
        previsao=previsao,
    )
    entrada = {
        "marca": pesquisa.marca,
        "atividade": pesquisa.atividade,
        "classe_nice": pesquisa.classe_nice,
        "tipo_pesquisa": pesquisa.tipo_pesquisa,
        "dados_complementares": pesquisa.dados_complementares_registrabilidade or {},
        "ultima_rpi": relatorio.get("ultima_rpi"),
        "modelo": modelo_versao,
        "versao_matriz": matriz.get("versao"),
        "versao_agente": VERSAO_AGENTE,
        "matriz_snapshot": matriz,
        "relatorio_snapshot": relatorio,
        "risco_snapshot": {"pontuacao": pontuacao_risco, "nivel": nivel_risco},
        "previsao_snapshot": {
            chave: _valor(previsao, chave) for chave in (
                "id", "probabilidade_deferimento", "probabilidade_inferior", "probabilidade_superior",
                "cobertura_entrada", "confianca", "elegivel_cliente",
            )
        },
    }
    hash_entrada = _hash_snapshot(entrada)
    existente = (
        await session.execute(
            select(ExecucaoAgenteRegistrabilidade).where(
                ExecucaoAgenteRegistrabilidade.pesquisa_id == pesquisa.id,
                ExecucaoAgenteRegistrabilidade.hash_entrada == hash_entrada,
            )
        )
    ).scalar_one_or_none()
    if existente is not None:
        return existente

    dados = pesquisa.dados_complementares_registrabilidade or {}
    execucao = ExecucaoAgenteRegistrabilidade(
        organizacao_id=pesquisa.organizacao_id,
        pesquisa_id=pesquisa.id,
        versao_agente=VERSAO_AGENTE,
        hash_entrada=hash_entrada,
        status=resultado.status,
        decisao=resultado.decisao,
        abstencao=resultado.abstencao,
        cobertura=resultado.cobertura,
        confianca=resultado.confianca,
        probabilidade_deferimento=resultado.probabilidade_deferimento,
        probabilidade_inferior=resultado.probabilidade_inferior,
        probabilidade_superior=resultado.probabilidade_superior,
        nivel_risco=resultado.nivel_risco,
        pontuacao_risco=resultado.pontuacao_risco,
        motivos=resultado.motivos,
        fatores_principais=resultado.fatores_principais,
        entrada_estruturada=entrada,
        evidencias={
            "busca": relatorio.get("evidencias_busca") or {},
            "qualidade_base": relatorio.get("qualidade_base") or {},
            "total_ocorrencias": relatorio.get("total") or 0,
            "principais_conflitos": relatorio.get("itens", [])[:20],
        },
        regras=matriz,
        versoes_fontes={
            "agente": VERSAO_AGENTE,
            "matriz": matriz.get("versao"),
            "manual_inpi_atualizado_em": matriz.get("manual_atualizado_em"),
            "modelo": modelo_versao,
            "rpi": relatorio.get("ultima_rpi"),
        },
        numero_pedido=(dados.get("numero_pedido") or None),
    )
    session.add(execucao)
    await session.flush()
    return execucao


async def executar_agente_para_pesquisa(
    session: AsyncSession,
    pesquisa: PesquisaMarca,
) -> ExecucaoAgenteRegistrabilidade | None:
    """Cria ou reutiliza a execução correspondente ao snapshot técnico mais recente."""
    versao = (
        await session.execute(
            select(VersaoRelatorioMarca)
            .where(VersaoRelatorioMarca.pesquisa_id == pesquisa.id)
            .order_by(VersaoRelatorioMarca.numero_versao.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if versao is None:
        return None

    avaliacao = (
        await session.execute(
            select(AvaliacaoRiscoMarca)
            .where(AvaliacaoRiscoMarca.pesquisa_id == pesquisa.id)
            .order_by(AvaliacaoRiscoMarca.calculado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    previsao_linha = (
        await session.execute(
            select(PrevisaoRegistrabilidade, ModeloRegistrabilidade)
            .join(
                ModeloRegistrabilidade,
                ModeloRegistrabilidade.id == PrevisaoRegistrabilidade.modelo_id,
            )
            .where(PrevisaoRegistrabilidade.pesquisa_id == pesquisa.id)
            .order_by(PrevisaoRegistrabilidade.calculado_em.desc())
            .limit(1)
        )
    ).first()
    previsao, modelo = previsao_linha if previsao_linha is not None else (None, None)
    relatorio = versao.payload or {}
    matriz = construir_matriz_registrabilidade(
        marca=pesquisa.marca,
        atividade=pesquisa.atividade,
        classe_nice=pesquisa.classe_nice,
        relatorio=relatorio,
        pontuacao_risco=avaliacao.pontuacao if avaliacao is not None else None,
        nivel_risco=avaliacao.nivel if avaliacao is not None else None,
        dados_complementares=pesquisa.dados_complementares_registrabilidade or {},
    )
    return await registrar_execucao_agente(
        session,
        pesquisa=pesquisa,
        matriz=matriz,
        relatorio=relatorio,
        pontuacao_risco=avaliacao.pontuacao if avaliacao is not None else None,
        nivel_risco=avaliacao.nivel if avaliacao is not None else None,
        previsao=previsao,
        modelo_versao=modelo.versao if modelo is not None else None,
    )


async def reprocessar_agentes_pendentes(
    session: AsyncSession,
    *,
    organizacao_id: int | None = None,
) -> dict[str, int | str]:
    """Preenche pesquisas que têm relatório, mas ainda não possuem execução do agente."""
    consulta = select(PesquisaMarca).where(
        exists(select(VersaoRelatorioMarca.id).where(VersaoRelatorioMarca.pesquisa_id == PesquisaMarca.id)),
        ~exists(
            select(ExecucaoAgenteRegistrabilidade.id).where(
                ExecucaoAgenteRegistrabilidade.pesquisa_id == PesquisaMarca.id
            )
        ),
    )
    if organizacao_id is not None:
        consulta = consulta.where(PesquisaMarca.organizacao_id == organizacao_id)
    pesquisas = list((await session.execute(consulta.order_by(PesquisaMarca.criado_em))).scalars().all())

    processadas = 0
    for pesquisa in pesquisas:
        if await executar_agente_para_pesquisa(session, pesquisa) is not None:
            processadas += 1
    await session.flush()
    return {
        "status": "concluido",
        "pendentes_encontradas": len(pesquisas),
        "processadas": processadas,
    }


def execucao_para_dict(execucao: ExecucaoAgenteRegistrabilidade) -> dict[str, Any]:
    return {
        "id": execucao.id,
        "versao_agente": execucao.versao_agente,
        "status": execucao.status,
        "decisao": execucao.decisao,
        "abstencao": execucao.abstencao,
        "cobertura": execucao.cobertura,
        "confianca": execucao.confianca,
        "probabilidade_deferimento": execucao.probabilidade_deferimento,
        "probabilidade_inferior": execucao.probabilidade_inferior,
        "probabilidade_superior": execucao.probabilidade_superior,
        "nivel_risco": execucao.nivel_risco,
        "pontuacao_risco": execucao.pontuacao_risco,
        "motivos": execucao.motivos or [],
        "fatores_principais": execucao.fatores_principais or [],
        "versoes_fontes": execucao.versoes_fontes or {},
        "numero_pedido": execucao.numero_pedido,
        "resultado_real": execucao.resultado_real,
        "resultado_fundamento": execucao.resultado_fundamento,
        "resultado_data_referencia": execucao.resultado_data_referencia,
        "resultado_numero_rpi": execucao.resultado_numero_rpi,
        "resultado_sincronizado_em": execucao.resultado_sincronizado_em,
        "criado_em": execucao.criado_em,
        "aviso": (
            "Estimativa indicativa e auditável. Não garante o registro e não substitui "
            "o exame de mérito realizado pelo INPI."
        ),
    }


async def reconciliar_resultados_reais(session: AsyncSession, *, organizacao_id: int | None = None) -> dict[str, int]:
    consulta = select(ExecucaoAgenteRegistrabilidade).where(
        ExecucaoAgenteRegistrabilidade.numero_pedido.is_not(None),
        ExecucaoAgenteRegistrabilidade.resultado_real.is_(None),
    )
    if organizacao_id is not None:
        consulta = consulta.where(ExecucaoAgenteRegistrabilidade.organizacao_id == organizacao_id)
    execucoes = list((await session.execute(consulta)).scalars())
    encontrados = 0
    concluidos = 0
    for execucao in execucoes:
        numero = normalizar_numero_processo(execucao.numero_pedido or "")
        if not numero:
            continue
        processo = (
            await session.execute(select(Processo).where(Processo.numero_normalizado == numero).limit(1))
        ).scalar_one_or_none()
        if processo is None:
            continue
        encontrados += 1
        rotulo = extrair_rotulo(processo.movimentacoes)
        if rotulo is None:
            continue
        execucao.resultado_real = rotulo.rotulo
        execucao.resultado_fundamento = rotulo.fundamento
        execucao.resultado_data_referencia = rotulo.movimentacao.data_rpi
        execucao.resultado_numero_rpi = rotulo.movimentacao.numero_rpi
        execucao.resultado_sincronizado_em = datetime.now(UTC)
        concluidos += 1
    await session.flush()
    return {
        "pendentes": len(execucoes),
        "processos_encontrados": encontrados,
        "concluidos": concluidos,
    }


def resultado_para_dict(resultado: ResultadoAgenteRegistrabilidade) -> dict[str, Any]:
    return asdict(resultado)
