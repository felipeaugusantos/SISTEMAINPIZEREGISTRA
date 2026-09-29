import hashlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.malware_scan import escanear_upload_ou_rejeitar
from app.models import EventoAuditoria, Organizacao, PlanoContas

router = APIRouter(prefix="/v1/admin/configuracao/propostas", tags=["configuração de propostas"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]
FinanceViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.view"))]
FinanceManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("finance.manage"))]

# Achado 18.6 da auditoria fina de Propostas (29/09/2026): o sistema vai
# atender outros escritórios, então nada do que o cliente de um escritório
# vê pode sair com a marca ou os preços da Zé Registra. O rodapé padrão
# deixou de citar a plataforma, e os valores padrão (honorários e taxa GRU)
# passaram a ser configuração de cada organização -- antes eram fixos no
# código (1500/415) no backend e na tela.
HONORARIOS_PADRAO_PLATAFORMA = Decimal("1500.00")
TAXA_GRU_PADRAO_PLATAFORMA = Decimal("415.00")

DEFAULTS = {
    "titulo": "PROPOSTA DE REGISTRO DE MARCA",
    # Mesmo texto que o formulário de proposta sempre usou como padrão.
    "escopo_padrao": "Pesquisa, preparação e protocolo de registro de marca no INPI",
    "prazo_texto": "Após o aceite, confirmação do pagamento e recebimento integral dos documentos, o protocolo será realizado em até 24 horas úteis, salvo pendências ou indisponibilidade dos sistemas oficiais do INPI.",
    "condicoes_texto": "O protocolo não representa garantia de concessão. A decisão final pertence ao INPI. A pesquisa e a análise são indicativas e não substituem exame oficial ou análise jurídica especializada.",
    "rodape": "Esta proposta possui versão auditável no sistema.",
    "honorarios_padrao": str(HONORARIOS_PADRAO_PLATAFORMA),
    "taxa_gru_padrao": str(TAXA_GRU_PADRAO_PLATAFORMA),
}


class PropostaConfigInput(BaseModel):
    titulo: str = Field(min_length=5, max_length=180)
    escopo_padrao: str = Field(min_length=3, max_length=2000)
    prazo_texto: str = Field(min_length=10, max_length=3000)
    condicoes_texto: str = Field(min_length=10, max_length=4000)
    rodape: str = Field(min_length=3, max_length=500)
    honorarios_padrao: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000"))
    taxa_gru_padrao: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000"))


def valores_padrao_proposta(org: Organizacao | None) -> tuple[Decimal, Decimal]:
    """(honorários, taxa GRU) padrão da organização para novas propostas."""
    atual = ((org.branding or {}).get("proposta") or {}) if org else {}

    def _decimal(valor: object, padrao: Decimal) -> Decimal:
        try:
            return Decimal(str(valor)) if valor not in (None, "") else padrao
        except (InvalidOperation, ValueError):
            return padrao

    return (
        _decimal(atual.get("honorarios_padrao"), HONORARIOS_PADRAO_PLATAFORMA),
        _decimal(atual.get("taxa_gru_padrao"), TAXA_GRU_PADRAO_PLATAFORMA),
    )


class PropostaPlanosContabeisInput(BaseModel):
    conta_contabil_honorarios_id: int = Field(ge=1)
    conta_contabil_taxa_gru_id: int = Field(ge=1)


def _config_planos(org: Organizacao | None) -> dict:
    if org is None:
        return {"conta_contabil_honorarios_id": None, "conta_contabil_taxa_gru_id": None}
    atual = (org.branding or {}).get("proposta_planos_contabeis") or {}
    return {
        "conta_contabil_honorarios_id": atual.get("conta_contabil_honorarios_id"),
        "conta_contabil_taxa_gru_id": atual.get("conta_contabil_taxa_gru_id"),
    }


async def _planos_receita(session: AsyncSession, organizacao_id: int) -> list[dict]:
    itens = (
        await session.execute(
            select(PlanoContas.id, PlanoContas.codigo, PlanoContas.nome)
            .where(
                PlanoContas.organizacao_id == organizacao_id,
                PlanoContas.natureza == "receita",
                PlanoContas.ativo.is_(True),
            )
            .order_by(PlanoContas.codigo)
        )
    ).all()
    return [{"id": item.id, "codigo": item.codigo, "nome": item.nome} for item in itens]


@router.get("/planos-contabeis")
async def obter_planos_contabeis_propostas(session: SessionDep, usuario: FinanceViewDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    return {**_config_planos(org), "itens": await _planos_receita(session, usuario.organizacao_id)}


@router.put("/planos-contabeis")
async def salvar_planos_contabeis_propostas(
    dados: PropostaPlanosContabeisInput,
    session: SessionDep,
    usuario: FinanceManageDep,
) -> dict:
    if dados.conta_contabil_honorarios_id == dados.conta_contabil_taxa_gru_id:
        raise HTTPException(422, "Selecione contas contábeis diferentes para honorários e taxa GRU/INPI.")
    ids = {dados.conta_contabil_honorarios_id, dados.conta_contabil_taxa_gru_id}
    encontrados = set(
        (
            await session.execute(
                select(PlanoContas.id).where(
                    PlanoContas.id.in_(ids),
                    PlanoContas.organizacao_id == usuario.organizacao_id,
                    PlanoContas.natureza == "receita",
                    PlanoContas.ativo.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if encontrados != ids:
        raise HTTPException(422, "Os dois planos precisam ser contas ativas de receita da organização.")
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        raise HTTPException(404, "Organização não encontrada.")
    branding = dict(org.branding or {})
    anterior = _config_planos(org)
    configuracao = dados.model_dump()
    branding["proposta_planos_contabeis"] = configuracao
    org.branding = branding
    session.add(
        EventoAuditoria(
            organizacao_id=org.id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="ALTERAR_CONFIGURACAO",
            recurso="organizacao:proposta_planos_contabeis",
            sucesso=True,
            status_http=200,
            detalhes={"anterior": anterior, "novo": configuracao},
        )
    )
    await session.commit()
    return {"status": "ok", **configuracao}


def _config(org: Organizacao) -> dict:
    atual = {chave: valor for chave, valor in ((org.branding or {}).get("proposta") or {}).items() if valor is not None}
    return {**DEFAULTS, **atual}


@router.get("")
async def obter_configuracao(session: SessionDep, usuario: ManageDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    return _config(org) if org else DEFAULTS


@router.put("")
async def salvar_configuracao(dados: PropostaConfigInput, session: SessionDep, usuario: ManageDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        return {"status": "ok"}
    # mode="json": branding é uma coluna JSON e Decimal não é serializável.
    org.branding = {**(org.branding or {}), "proposta": dados.model_dump(mode="json")}
    await session.commit()
    return {"status": "ok", "configuracao": _config(org)}


@router.post("/template-pdf")
async def importar_template_pdf(session: SessionDep, usuario: ManageDep, arquivo: UploadFile = File(...)) -> dict:
    if arquivo.content_type != "application/pdf":
        raise HTTPException(422, "Envie um arquivo PDF")
    conteudo = await arquivo.read()
    if not conteudo.startswith(b"%PDF") or len(conteudo) > 15 * 1024 * 1024:
        raise HTTPException(422, "PDF inválido ou maior que 15 MB")
    # Achado da varredura ampla do sistema (18/09/2026): este era um dos
    # poucos endpoints de upload sem a varredura antivírus já usada em
    # app/api/portal_cliente.py/app/api/atualizacoes.py.
    await escanear_upload_ou_rejeitar(conteudo)
    digest = hashlib.sha256(conteudo).hexdigest()
    pasta = Path("data/proposta-templates")
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"org-{usuario.organizacao_id}-{digest}.pdf"
    caminho.write_bytes(conteudo)
    org = await session.get(Organizacao, usuario.organizacao_id)
    branding = dict(org.branding or {})
    branding["proposta_template"] = {
        "arquivo": caminho.name,
        "sha256": digest,
        "tamanho": len(conteudo),
        "importado_em": datetime.now(UTC).isoformat(),
        "importado_por": usuario.email,
    }
    org.branding = branding
    await session.commit()
    return {"status": "ok", "arquivo": caminho.name, "sha256": digest, "tamanho": len(conteudo)}


@router.get("/template-pdf")
async def baixar_template_pdf(session: SessionDep, usuario: ManageDep) -> FileResponse:
    org = await session.get(Organizacao, usuario.organizacao_id)
    item = (org.branding or {}).get("proposta_template") if org else None
    caminho = Path("data/proposta-templates") / item["arquivo"] if item else None
    if not caminho or not caminho.is_file():
        raise HTTPException(404, "Nenhum modelo PDF importado")
    return FileResponse(caminho, media_type="application/pdf", filename=caminho.name)
