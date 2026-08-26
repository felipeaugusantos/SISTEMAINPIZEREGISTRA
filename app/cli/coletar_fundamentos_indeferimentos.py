from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict, deque
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select

from app.database import session_factory
from app.models import (
    ClassificacaoMarca,
    EvidenciaDecisaoMarca,
    Processo,
    RotuloHistoricoMarca,
)
from app.trademarks.decision_evidence import (
    CLASSIFIER_VERSION,
    InpiDecisionClient,
    OfficialDecision,
    classificar_fundamento,
)


def _amostra_estratificada(linhas: list[tuple], limite: int) -> list[tuple]:
    grupos: dict[tuple[str, str], deque] = defaultdict(deque)
    for linha in linhas:
        rotulo, processo, classe = linha
        periodo = f"{processo.data_deposito.year}-{(processo.data_deposito.month - 1) // 3 + 1}"
        grupos[(periodo, classe or "sem-classe")].append(linha)
    selecionadas: list[tuple] = []
    chaves = sorted(grupos)
    while chaves and len(selecionadas) < limite:
        restantes: list[tuple[str, str]] = []
        for chave in chaves:
            if grupos[chave] and len(selecionadas) < limite:
                selecionadas.append(grupos[chave].popleft())
            if grupos[chave]:
                restantes.append(chave)
        chaves = restantes
    return selecionadas


async def _candidatos(limite: int, reprocessar: bool, somente_erros: bool) -> list[tuple]:
    async with session_factory() as session:
        classe = (
            select(
                ClassificacaoMarca.processo_id,
                func.min(ClassificacaoMarca.codigo).label("classe"),
            )
            .where(ClassificacaoMarca.sistema.in_(["NCL", "NICE", "nice"]))
            .group_by(ClassificacaoMarca.processo_id)
            .subquery()
        )
        query = (
            select(RotuloHistoricoMarca, Processo, classe.c.classe)
            .join(Processo, Processo.id == RotuloHistoricoMarca.processo_id)
            .outerjoin(classe, classe.c.processo_id == Processo.id)
            .outerjoin(
                EvidenciaDecisaoMarca,
                EvidenciaDecisaoMarca.rotulo_id == RotuloHistoricoMarca.id,
            )
            .where(
                RotuloHistoricoMarca.rotulo == "indeferida",
                RotuloHistoricoMarca.fundamento == "indeferimento_nao_especificado",
                RotuloHistoricoMarca.status_revisao == "pendente",
                Processo.data_deposito.is_not(None),
            )
            .order_by(RotuloHistoricoMarca.data_referencia.desc(), RotuloHistoricoMarca.id)
        )
        if somente_erros:
            query = query.where(EvidenciaDecisaoMarca.status_coleta == "erro")
        elif not reprocessar:
            query = query.where(EvidenciaDecisaoMarca.id.is_(None))
        linhas = (await session.execute(query)).all()
        return _amostra_estratificada(linhas, limite)


async def _salvar_resultado(rotulo_id: int, processo_numero: str, decisao) -> str:
    async with session_factory() as session:
        rotulo = await session.get(RotuloHistoricoMarca, rotulo_id)
        evidencia = await session.scalar(
            select(EvidenciaDecisaoMarca).where(EvidenciaDecisaoMarca.rotulo_id == rotulo_id)
        )
        if evidencia is None:
            evidencia = EvidenciaDecisaoMarca(rotulo_id=rotulo_id, processo_numero=processo_numero)
            session.add(evidencia)
        evidencia.cod_pedido = decisao.cod_pedido
        evidencia.fonte_url = decisao.fonte_url
        evidencia.status_coleta = "coletado" if decisao.fundamento else "revisao"
        evidencia.status_http = 200
        evidencia.tentativas = (evidencia.tentativas or 0) + 1
        evidencia.erro = None
        evidencia.despacho_texto = decisao.despacho_texto
        evidencia.numero_rpi = decisao.numero_rpi
        evidencia.fundamento_sugerido = decisao.fundamento
        evidencia.confianca = decisao.confianca
        evidencia.artigos = decisao.artigos
        evidencia.processos_citados = decisao.processos_citados
        evidencia.hash_conteudo = decisao.hash_conteudo
        evidencia.classificador_versao = CLASSIFIER_VERSION
        evidencia.coletado_em = datetime.now(UTC)
        if rotulo is not None:
            oficial = {
                "tipo": "complemento_despacho_oficial",
                "fonte_url": decisao.fonte_url,
                "hash_sha256": decisao.hash_conteudo,
                "artigos": decisao.artigos,
                "processos_citados": decisao.processos_citados,
                "fundamento_sugerido": decisao.fundamento,
                "confianca": decisao.confianca,
            }
            rotulo.evidencias_classificacao = [
                item
                for item in (rotulo.evidencias_classificacao or [])
                if item.get("tipo") != "complemento_despacho_oficial"
            ] + [oficial]
            if (
                decisao.fundamento
                in {
                    "conflito_anterior",
                    "falta_distintividade",
                    "outra_proibicao",
                }
                and decisao.confianca >= 0.95
            ):
                rotulo.fundamento = decisao.fundamento
                rotulo.origem = "pepi_despacho_oficial"
                rotulo.confianca = decisao.confianca
                rotulo.status_revisao = "documental"
                rotulo.revisor = "regra-documental-inpi"
                rotulo.observacoes_revisao = (
                    f"Fundamento extraído do complemento público do despacho. SHA-256: {decisao.hash_conteudo}"
                )
                rotulo.revisado_em = datetime.now(UTC)
                rotulo.elegivel_treinamento = True
                rotulo.motivo_inelegibilidade = None
                rotulo.classificador_versao = CLASSIFIER_VERSION
        await session.commit()
        return evidencia.status_coleta


async def _salvar_erro(rotulo_id: int, processo_numero: str, erro: Exception) -> None:
    async with session_factory() as session:
        evidencia = await session.scalar(
            select(EvidenciaDecisaoMarca).where(EvidenciaDecisaoMarca.rotulo_id == rotulo_id)
        )
        if evidencia is None:
            evidencia = EvidenciaDecisaoMarca(rotulo_id=rotulo_id, processo_numero=processo_numero)
            session.add(evidencia)
        evidencia.status_coleta = (
            "coletado"
            if evidencia.despacho_texto and evidencia.fundamento_sugerido
            else "revisao"
            if evidencia.despacho_texto
            else "erro"
        )
        evidencia.tentativas = (evidencia.tentativas or 0) + 1
        evidencia.erro = str(erro)[:2000]
        await session.commit()


async def _reclassificar_existentes() -> int:
    async with session_factory() as session:
        linhas = (
            (
                await session.execute(
                    select(EvidenciaDecisaoMarca).where(
                        EvidenciaDecisaoMarca.despacho_texto.is_not(None),
                        EvidenciaDecisaoMarca.status_coleta.in_(["revisao", "coletado"]),
                    )
                )
            )
            .scalars()
            .all()
        )
    alteradas = 0
    for evidencia in linhas:
        classificacao = classificar_fundamento(evidencia.despacho_texto or "")
        if (
            classificacao.fundamento == evidencia.fundamento_sugerido
            and classificacao.artigos == (evidencia.artigos or [])
            and classificacao.processos_citados == (evidencia.processos_citados or [])
        ):
            continue
        decisao = OfficialDecision(
            processo_numero=evidencia.processo_numero,
            cod_pedido=evidencia.cod_pedido or "",
            fonte_url=evidencia.fonte_url or "",
            despacho_texto=evidencia.despacho_texto or "",
            numero_rpi=evidencia.numero_rpi,
            fundamento=classificacao.fundamento,
            confianca=classificacao.confianca,
            artigos=classificacao.artigos,
            processos_citados=classificacao.processos_citados,
            hash_conteudo=evidencia.hash_conteudo or "",
        )
        await _salvar_resultado(evidencia.rotulo_id, evidencia.processo_numero, decisao)
        alteradas += 1
    return alteradas


async def executar(args: argparse.Namespace) -> None:
    if args.reclassificar:
        quantidade = await _reclassificar_existentes()
        print(f"Evidências reclassificadas: {quantidade}")
        if args.somente_reclassificar:
            return
    candidatos = await _candidatos(args.limite, args.reprocessar, args.somente_erros)
    print(f"Amostra estratificada: {len(candidatos)} indeferimentos")
    if args.dry_run:
        for rotulo, processo, classe in candidatos[:20]:
            print(rotulo.id, processo.numero, processo.data_deposito, classe or "sem-classe")
        return
    contagens = defaultdict(int)
    fila: asyncio.Queue = asyncio.Queue()
    for candidato in candidatos:
        fila.put_nowait(candidato)
    processados = 0
    bloqueado = asyncio.Event()
    trava = asyncio.Lock()

    async def trabalhar() -> None:
        nonlocal processados
        async with InpiDecisionClient(timeout=args.timeout) as cliente:
            while not fila.empty() and not bloqueado.is_set():
                try:
                    rotulo, processo, _classe = fila.get_nowait()
                except asyncio.QueueEmpty:
                    return
                chave = "erro"
                captcha = False
                try:
                    decisao = None
                    for tentativa in range(1, args.tentativas + 1):
                        try:
                            decisao = await cliente.coletar(processo.numero, rotulo.numero_rpi)
                            break
                        except (httpx.TimeoutException, httpx.HTTPStatusError):
                            if tentativa == args.tentativas:
                                raise
                            await asyncio.sleep(min(8.0, 2**tentativa))
                    assert decisao is not None
                    status = await _salvar_resultado(rotulo.id, processo.numero, decisao)
                    chave = decisao.fundamento or status
                except RuntimeError as exc:
                    await _salvar_erro(rotulo.id, processo.numero, exc)
                    captcha = "CAPTCHA" in str(exc)
                    if captcha:
                        bloqueado.set()
                        print(f"Coleta interrompida com segurança: {exc}", flush=True)
                except Exception as exc:  # noqa: BLE001 - lote registra e prossegue
                    await _salvar_erro(rotulo.id, processo.numero, exc)
                finally:
                    fila.task_done()
                async with trava:
                    contagens[chave] += 1
                    processados += 1
                    if processados % 10 == 0 or processados == len(candidatos):
                        print(
                            f"{processados}/{len(candidatos)} · {dict(contagens)}",
                            flush=True,
                        )
                if captcha:
                    return
                await asyncio.sleep(args.atraso)

    await asyncio.gather(*(trabalhar() for _ in range(args.concorrencia)))
    print(f"Resultado final: {dict(contagens)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coleta fundamentos públicos de indeferimento no detalhe oficial do INPI."
    )
    parser.add_argument("--limite", type=int, default=500)
    parser.add_argument("--atraso", type=float, default=0.6)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--tentativas", type=int, default=3)
    parser.add_argument("--concorrencia", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--reprocessar", action="store_true")
    parser.add_argument("--somente-erros", action="store_true")
    parser.add_argument("--reclassificar", action="store_true")
    parser.add_argument("--somente-reclassificar", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(executar(args))


if __name__ == "__main__":
    main()
