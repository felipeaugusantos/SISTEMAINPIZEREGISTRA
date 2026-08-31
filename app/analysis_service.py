"""Persistência da análise unificada no relatório versionado já existente."""

from sqlalchemy import select

from app.models import VersaoRelatorioMarca
from app.production import versionar_relatorio
from app.schemas import RelatorioMarcaResponse
from app.trademarks.consolidated import construir_analise_consolidada


async def atualizar_snapshot_analise(session, pesquisa, *, parecer=None, versao_esperada=None):
    ultima = (
        await session.execute(
            select(VersaoRelatorioMarca)
            .where(VersaoRelatorioMarca.pesquisa_id == pesquisa.id)
            .order_by(VersaoRelatorioMarca.numero_versao.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if ultima is None:
        raise ValueError("Gere o resultado da pesquisa antes de analisar a marca.")
    if versao_esperada is not None and ultima.numero_versao != versao_esperada:
        raise ValueError("A análise mudou. Recarregue a página antes de salvar o parecer.")
    relatorio = RelatorioMarcaResponse.model_validate(ultima.payload)
    relatorio.analise_consolidada = construir_analise_consolidada(
        relatorio.model_dump(mode="json"),
        pesquisa.dados_complementares_registrabilidade or {},
        parecer_humano_novo=parecer,
    )
    return await versionar_relatorio(session, relatorio)
