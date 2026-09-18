import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.malware_scan import escanear_upload_ou_rejeitar
from app.models import Organizacao

router = APIRouter(prefix="/v1/admin/configuracao/propostas", tags=["configuração de propostas"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]

DEFAULTS = {
    "titulo": "PROPOSTA DE REGISTRO DE MARCA",
    "escopo_padrao": "Registro de marca no INPI",
    "prazo_texto": "Após o aceite, confirmação do pagamento e recebimento integral dos documentos, o protocolo será realizado em até 24 horas úteis, salvo pendências ou indisponibilidade dos sistemas oficiais do INPI.",
    "condicoes_texto": "O protocolo não representa garantia de concessão. A decisão final pertence ao INPI. A pesquisa e a análise são indicativas e não substituem exame oficial ou análise jurídica especializada.",
    "rodape": "Esta proposta foi gerada pelo Zé Registra e possui versão auditável no sistema.",
}


class PropostaConfigInput(BaseModel):
    titulo: str = Field(min_length=5, max_length=180)
    escopo_padrao: str = Field(min_length=3, max_length=2000)
    prazo_texto: str = Field(min_length=10, max_length=3000)
    condicoes_texto: str = Field(min_length=10, max_length=4000)
    rodape: str = Field(min_length=3, max_length=500)


def _config(org: Organizacao) -> dict:
    atual = (org.branding or {}).get("proposta") or {}
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
    org.branding = {**(org.branding or {}), "proposta": dados.model_dump()}
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
