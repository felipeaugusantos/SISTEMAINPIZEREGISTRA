import hashlib
import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.confiabilidade import AdminDep
from app.database import get_session
from app.models import EventoAuditoria, Lead, Organizacao, PoliticaPrivacidade
from app.tenancy import OrganizacaoPublicaDep

router = APIRouter(prefix="/v1/admin/politicas-privacidade", tags=["políticas de privacidade"])
public_router = APIRouter(prefix="/v1/tenant", tags=["tenant"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
_VERSAO_VALIDA = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,29}$")


def calcular_hash_politica(conteudo: str | None, documento_referencia: str | None) -> str:
    origem = f"conteudo:{conteudo}" if conteudo is not None else f"referencia:{documento_referencia}"
    return hashlib.sha256(origem.encode("utf-8")).hexdigest()


class PoliticaRascunhoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versao: str = Field(min_length=1, max_length=30)
    conteudo: str | None = Field(default=None, min_length=100, max_length=100_000)
    documento_referencia: str | None = Field(default=None, min_length=2, max_length=500)
    motivo_alteracao: str = Field(min_length=20, max_length=2000)
    requer_novo_consentimento: bool = False

    @field_validator("versao")
    @classmethod
    def validar_versao(cls, valor: str) -> str:
        versao = valor.strip()
        if not _VERSAO_VALIDA.fullmatch(versao):
            raise ValueError("Use letras, números, ponto, hífen ou sublinhado na versão")
        return versao

    @field_validator("conteudo", "documento_referencia", mode="before")
    @classmethod
    def limpar_documento(cls, valor: str | None) -> str | None:
        return valor.strip() or None if isinstance(valor, str) else valor

    @field_validator("documento_referencia")
    @classmethod
    def validar_referencia(cls, valor: str | None) -> str | None:
        if valor and not (valor.startswith("https://") or valor.startswith("/")):
            raise ValueError("A referência deve usar HTTPS ou um caminho público interno")
        return valor

    @model_validator(mode="after")
    def validar_fonte_documento(self):
        if (self.conteudo is None) == (self.documento_referencia is None):
            raise ValueError("Informe somente o conteúdo ou somente a referência do documento")
        return self


class PublicarPoliticaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vigencia_em: datetime | None = None
    confirmar_publicacao: bool


def politica_json(item: PoliticaPrivacidade, *, incluir_documento: bool = True) -> dict:
    dados = {
        "id": item.id,
        "versao": item.versao,
        "status": item.status,
        "sha256": item.sha256,
        "criado_em": item.criado_em,
        "publicado_em": item.publicado_em,
        "vigencia_em": item.vigencia_em,
        "criado_por": item.criado_por,
        "aprovado_por": item.aprovado_por,
        "motivo_alteracao": item.motivo_alteracao,
        "requer_novo_consentimento": item.requer_novo_consentimento,
    }
    if incluir_documento:
        dados["conteudo"] = item.conteudo
        dados["documento_referencia"] = item.documento_referencia
    return dados


async def obter_politica(
    session: AsyncSession,
    organizacao_id: int,
    politica_id: int,
) -> PoliticaPrivacidade:
    item = (
        await session.execute(
            select(PoliticaPrivacidade).where(
                PoliticaPrivacidade.id == politica_id,
                PoliticaPrivacidade.organizacao_id == organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Política de privacidade não encontrada")
    return item


@router.get("")
async def listar_politicas(session: SessionDep, usuario: AdminDep) -> dict:
    itens = list(
        (
            await session.execute(
                select(PoliticaPrivacidade)
                .where(PoliticaPrivacidade.organizacao_id == usuario.organizacao_id)
                .order_by(PoliticaPrivacidade.criado_em.desc(), PoliticaPrivacidade.id.desc())
            )
        ).scalars()
    )
    vigente = next((item for item in itens if item.status == "publicada"), None)
    pendentes = 0
    if vigente is not None and vigente.requer_novo_consentimento:
        pendentes = (
            await session.execute(
                select(func.count())
                .select_from(Lead)
                .where(
                    Lead.organizacao_id == usuario.organizacao_id,
                    Lead.consentimento_base_legal == "consentimento_titular",
                    or_(
                        Lead.consentimento_versao_termo.is_(None),
                        Lead.consentimento_versao_termo != vigente.versao,
                    ),
                    Lead.anonimizado_em.is_(None),
                )
            )
        ).scalar_one()
    return {
        "vigente": politica_json(vigente) if vigente else None,
        "politicas": [politica_json(item) for item in itens],
        "consentimentos_pendentes": pendentes,
        "regra_novo_consentimento": (
            "Quando marcado, titulares com versão anterior permanecem vinculados ao aceite original e "
            "devem aceitar a versão vigente em uma nova interação pública. Ações internas não renovam consentimento."
        ),
    }


@router.post("", status_code=201)
async def criar_rascunho(
    dados: PoliticaRascunhoInput,
    session: SessionDep,
    usuario: AdminDep,
) -> dict:
    existente = (
        await session.execute(
            select(PoliticaPrivacidade.id).where(
                PoliticaPrivacidade.organizacao_id == usuario.organizacao_id,
                PoliticaPrivacidade.versao == dados.versao,
            )
        )
    ).scalar_one_or_none()
    if existente is not None:
        raise HTTPException(409, "Esta versão já existe; crie uma nova versão")
    item = PoliticaPrivacidade(
        organizacao_id=usuario.organizacao_id,
        versao=dados.versao,
        status="rascunho",
        conteudo=dados.conteudo,
        documento_referencia=dados.documento_referencia,
        sha256=calcular_hash_politica(dados.conteudo, dados.documento_referencia),
        criado_por_id=usuario.id,
        criado_por=usuario.email,
        motivo_alteracao=dados.motivo_alteracao.strip(),
        requer_novo_consentimento=dados.requer_novo_consentimento,
    )
    session.add(item)
    await session.flush()
    await session.commit()
    return politica_json(item)


@router.put("/{politica_id}")
async def editar_rascunho(
    politica_id: int,
    dados: PoliticaRascunhoInput,
    session: SessionDep,
    usuario: AdminDep,
) -> dict:
    item = await obter_politica(session, usuario.organizacao_id, politica_id)
    if item.status != "rascunho":
        raise HTTPException(409, "Uma política publicada ou revogada é imutável")
    if dados.versao != item.versao:
        duplicada = (
            await session.execute(
                select(PoliticaPrivacidade.id).where(
                    PoliticaPrivacidade.organizacao_id == usuario.organizacao_id,
                    PoliticaPrivacidade.versao == dados.versao,
                    PoliticaPrivacidade.id != item.id,
                )
            )
        ).scalar_one_or_none()
        if duplicada is not None:
            raise HTTPException(409, "Esta versão já existe; crie uma nova versão")
    item.versao = dados.versao
    item.conteudo = dados.conteudo
    item.documento_referencia = dados.documento_referencia
    item.sha256 = calcular_hash_politica(dados.conteudo, dados.documento_referencia)
    item.motivo_alteracao = dados.motivo_alteracao.strip()
    item.requer_novo_consentimento = dados.requer_novo_consentimento
    await session.commit()
    return politica_json(item)


@router.post("/{politica_id}/publicar")
async def publicar_politica(
    politica_id: int,
    dados: PublicarPoliticaInput,
    session: SessionDep,
    usuario: AdminDep,
) -> dict:
    if not dados.confirmar_publicacao:
        raise HTTPException(422, "Confirme expressamente a publicação")
    item = await obter_politica(session, usuario.organizacao_id, politica_id)
    if item.status == "publicada":
        return politica_json(item)
    if item.status != "rascunho":
        raise HTTPException(409, "Uma política revogada não pode ser republicada")
    agora = datetime.now(UTC)
    vigencia = dados.vigencia_em or agora
    if vigencia.tzinfo is None:
        vigencia = vigencia.replace(tzinfo=UTC)
    if vigencia > agora:
        raise HTTPException(422, "A publicação agendada ainda não é suportada; use uma vigência atual ou passada")

    atual = (
        await session.execute(
            select(PoliticaPrivacidade)
            .where(
                PoliticaPrivacidade.organizacao_id == usuario.organizacao_id,
                PoliticaPrivacidade.status == "publicada",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if atual is not None:
        atual.status = "revogada"
        await session.flush()

    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        raise HTTPException(404, "Organização não encontrada")
    item.status = "publicada"
    item.publicado_em = agora
    item.vigencia_em = vigencia
    item.aprovado_por_id = usuario.id
    item.aprovado_por = usuario.email
    org.politica_privacidade_versao = item.versao
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="PUBLICAR_POLITICA",
            recurso=f"politica_privacidade:{item.id}",
            sucesso=True,
            status_http=200,
            detalhes={
                "versao": item.versao,
                "sha256": item.sha256,
                "vigencia_em": vigencia.isoformat(),
                "versao_revogada": atual.versao if atual else None,
                "requer_novo_consentimento": item.requer_novo_consentimento,
            },
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Outra política foi publicada simultaneamente") from exc
    return politica_json(item)


@public_router.get("/politica-privacidade/vigente")
async def politica_publica_vigente(
    organizacao: OrganizacaoPublicaDep,
    session: SessionDep,
) -> dict:
    item = (
        await session.execute(
            select(PoliticaPrivacidade).where(
                PoliticaPrivacidade.organizacao_id == organizacao.id,
                PoliticaPrivacidade.status == "publicada",
                PoliticaPrivacidade.vigencia_em <= datetime.now(UTC),
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Política de privacidade vigente não encontrada")
    return {
        "versao": item.versao,
        "conteudo": item.conteudo,
        "documento_referencia": item.documento_referencia,
        "sha256": item.sha256,
        "publicado_em": item.publicado_em,
        "vigencia_em": item.vigencia_em,
        "requer_novo_consentimento": item.requer_novo_consentimento,
    }
