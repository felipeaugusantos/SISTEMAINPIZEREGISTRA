"""Scripts de atendimento comercial -- item 1 da lista de melhorias de
produto pedida pelo usuário (15/09/2026): modelo de 1º atendimento, com
opção de adicionar mais modelos manualmente.

Reaproveita o padrão já usado em app/api/email_leads_config.py e
app/api/propostas_config.py -- texto guardado em Organizacao.branding
(JSON), sem tabela nova nem migration. Diferença: aqui é uma lista (o
pedido original inclui "adicionar mais modelos manualmente"), não um
único texto por organização.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import Organizacao

router = APIRouter(prefix="/v1/admin/configuracao/scripts-atendimento", tags=["scripts de atendimento"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]

ITENS_PADRAO = [
    {
        "id": "primeiro-atendimento",
        "titulo": "Primeiro atendimento",
        "corpo": (
            "Olá, {{lead.nome}}, tudo bem? Meu nome é [seu nome] e falo da Zé Registra.\n\n"
            "Vi que você tem interesse em proteger uma marca — posso te fazer algumas perguntas rápidas?\n"
            "- Qual é o nome da marca?\n"
            "- Qual produto ou serviço ela identifica?\n"
            "- A marca já está sendo usada no mercado?\n"
            "- Você possui CPF ou CNPJ para o titular do pedido?\n"
            "- Já chegou a protocolar algo no INPI antes?\n\n"
            "Com essas informações eu já consigo te dar um retorno sobre viabilidade e "
            "próximos passos. É importante deixar claro que a concessão do registro é "
            "decisão exclusiva do INPI — nosso trabalho é pesquisar, orientar e acompanhar "
            "o processo com atenção técnica."
        ),
    }
]


class ScriptAtendimentoInput(BaseModel):
    titulo: str = Field(min_length=3, max_length=120)
    corpo: str = Field(min_length=10, max_length=5000)


def _itens(org: Organizacao | None) -> list[dict]:
    itens = (org.branding or {}).get("scripts_atendimento") if org else None
    return list(itens) if itens else [dict(item) for item in ITENS_PADRAO]


@router.get("")
async def listar_scripts(session: SessionDep, usuario: ViewDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    return {"itens": _itens(org)}


@router.post("")
async def criar_script(dados: ScriptAtendimentoInput, session: SessionDep, usuario: ManageDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organização não encontrada")
    itens = _itens(org)
    novo = {"id": secrets.token_hex(8), **dados.model_dump()}
    itens.append(novo)
    org.branding = {**(org.branding or {}), "scripts_atendimento": itens}
    await session.commit()
    return {"itens": itens}


@router.put("/{item_id}")
async def editar_script(item_id: str, dados: ScriptAtendimentoInput, session: SessionDep, usuario: ManageDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organização não encontrada")
    itens = _itens(org)
    alvo = next((item for item in itens if item["id"] == item_id), None)
    if alvo is None:
        raise HTTPException(status_code=404, detail="Script não encontrado")
    alvo.update(dados.model_dump())
    org.branding = {**(org.branding or {}), "scripts_atendimento": itens}
    await session.commit()
    return {"itens": itens}


@router.delete("/{item_id}")
async def excluir_script(item_id: str, session: SessionDep, usuario: ManageDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organização não encontrada")
    itens = [item for item in _itens(org) if item["id"] != item_id]
    org.branding = {**(org.branding or {}), "scripts_atendimento": itens}
    await session.commit()
    return {"itens": itens}
