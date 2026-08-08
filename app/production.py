import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ControleProducao, VersaoRelatorioMarca
from app.schemas import RelatorioMarcaResponse
from app.settings import Settings

SCHEMA_RELATORIO = "relatorio-marca-4.2"


def elegivel_rollout(chave: str, percentual: int) -> bool:
    if percentual <= 0:
        return False
    if percentual >= 100:
        return True
    faixa = int(hashlib.sha256(chave.encode("utf-8")).hexdigest()[:8], 16) % 100
    return faixa < percentual


async def obter_controle_producao(session: AsyncSession) -> ControleProducao:
    controle = await session.get(ControleProducao, 1)
    if controle is not None:
        return controle
    controle = ControleProducao(id=1, ia_habilitada=False, ia_rollout_percentual=0)
    session.add(controle)
    await session.flush()
    return controle


def ia_efetivamente_habilitada(
    settings: Settings,
    controle: ControleProducao,
) -> bool:
    return bool(
        settings.ai_explanations_enabled and settings.openai_api_key and controle.ia_habilitada
    )


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
    return versionado
