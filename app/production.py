import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PesquisaMarca, VersaoRelatorioMarca
from app.schemas import RelatorioMarcaResponse
from app.trademarks.analysis_workflow import EstadoAnalise

SCHEMA_RELATORIO = "relatorio-marca-4.3"


def _hash_conteudo(relatorio: RelatorioMarcaResponse) -> str:
    conteudo = relatorio.model_dump(
        mode="json",
        exclude={"versao", "schema_versao", "gerado_em", "conteudo_hash"},
    )
    serializado = json.dumps(
        conteudo,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


async def versionar_relatorio(
    session: AsyncSession,
    relatorio: RelatorioMarcaResponse,
) -> RelatorioMarcaResponse:
    pesquisa = await session.get(PesquisaMarca, relatorio.id, with_for_update=True)
    conteudo_hash = _hash_conteudo(relatorio)
    ultima = (
        await session.execute(
            select(VersaoRelatorioMarca)
            .where(VersaoRelatorioMarca.pesquisa_id == relatorio.id)
            .order_by(VersaoRelatorioMarca.numero_versao.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        ultima is not None
        and ultima.conteudo_hash == conteudo_hash
        and ultima.schema_versao == SCHEMA_RELATORIO
    ):
        return RelatorioMarcaResponse.model_validate(ultima.payload)

    gerado_em = datetime.now(UTC)
    versao = 1 if ultima is None else ultima.numero_versao + 1
    versionado = relatorio.model_copy(
        update={
            "versao": versao,
            "schema_versao": SCHEMA_RELATORIO,
            "gerado_em": gerado_em,
            "conteudo_hash": conteudo_hash,
        }
    )
    session.add(
        VersaoRelatorioMarca(
            pesquisa_id=relatorio.id,
            numero_versao=versao,
            schema_versao=SCHEMA_RELATORIO,
            conteudo_hash=conteudo_hash,
            payload=versionado.model_dump(mode="json"),
            gerado_em=gerado_em,
        )
    )
    if pesquisa is not None:
        pesquisa.analysis_state = EstadoAnalise.PENDING_REVIEW.value
        pesquisa.validated_by = None
        pesquisa.validated_at = None
        pesquisa.analysis_notes = "Nova versão disponível para revisão humana obrigatória."
        pesquisa.relatorio_completo_gerado_em = None
        pesquisa.relatorio_completo_gerado_por = None
    return versionado
