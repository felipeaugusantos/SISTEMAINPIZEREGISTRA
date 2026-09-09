import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.saas import SuperAdminDep
from app.auditing import criar_evento_auditoria
from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import VersaoSistema

router = APIRouter(prefix="/v1/admin/versoes-sistema", tags=["versões do sistema"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("production.view"))]

MODULOS_RELEASE = frozenset(
    {
        "plataforma",
        "infraestrutura",
        "seguranca",
        "consulta",
        "leads",
        "crm",
        "prospeccao",
        "processos_monitorados",
        "operacao_juridica",
        "financeiro",
        "validacao",
        "risco",
        "ia",
        "aprendizado",
        "usuarios",
        "rpi",
        "producao",
        "portal_cliente",
        "privacidade",
    }
)
_VERSAO = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,59}$")
_MIGRATION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,63}$")
_SEGREDO = re.compile(
    r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:authorization|password|passwd|senha|token|secret|segredo|client_secret|api_key)"
    r"\s*[:=]\s*\S+|\bbearer\s+[A-Za-z0-9._~+/=-]{16,})"
)


def _texto_seguro(valor: str) -> str:
    texto = valor.strip()
    if _SEGREDO.search(texto):
        raise ValueError("Não inclua credenciais, tokens, senhas ou chaves no cadastro da versão")
    return texto


class EvidenciaTesteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: str = Field(min_length=3, max_length=150)
    resultado: Literal["aprovado", "falhou", "ignorado"]
    resumo: str = Field(min_length=5, max_length=500)
    quantidade: int | None = Field(default=None, ge=0, le=10_000_000)
    executado_em: datetime | None = None

    @field_validator("nome", "resumo")
    @classmethod
    def validar_texto(cls, valor: str) -> str:
        return _texto_seguro(valor)


class VersaoRascunhoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versao: str = Field(min_length=1, max_length=60)
    titulo: str = Field(min_length=5, max_length=180)
    problema_identificado: str = Field(min_length=20, max_length=10_000)
    solucao_aplicada: str = Field(min_length=20, max_length=10_000)
    impacto_usuario: str = Field(min_length=10, max_length=3000)
    documentacao_url: HttpUrl | None = None
    permite_adiar: bool = False
    tipo_atualizacao: Literal["critica", "correcao", "funcionalidade"]
    modulos_afetados: list[str] = Field(min_length=1, max_length=30)
    implantada_em: datetime | None = None
    commit_sha: str = Field(min_length=40, max_length=40)
    migration_revision: str | None = Field(default=None, max_length=64)
    evidencias_testes: list[EvidenciaTesteInput] = Field(min_length=1, max_length=100)
    riscos_conhecidos: list[str] = Field(default_factory=list, max_length=30)
    instrucoes: str = Field(min_length=20, max_length=10_000)
    plano_rollback: str = Field(min_length=20, max_length=10_000)

    @field_validator("versao")
    @classmethod
    def validar_versao(cls, valor: str) -> str:
        versao = valor.strip()
        if not _VERSAO.fullmatch(versao):
            raise ValueError("Versão inválida")
        return versao

    @field_validator("commit_sha")
    @classmethod
    def validar_commit(cls, valor: str) -> str:
        commit = valor.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Informe o SHA completo de 40 caracteres")
        return commit

    @field_validator("migration_revision")
    @classmethod
    def validar_migration(cls, valor: str | None) -> str | None:
        if valor is None or not valor.strip():
            return None
        revision = valor.strip()
        if not _MIGRATION.fullmatch(revision):
            raise ValueError("Revisão de migration inválida")
        return revision

    @field_validator("documentacao_url")
    @classmethod
    def validar_documentacao_url(cls, valor: HttpUrl | None) -> HttpUrl | None:
        if valor is not None and valor.scheme != "https":
            raise ValueError("A documentação deve usar HTTPS")
        return valor

    @field_validator(
        "titulo", "problema_identificado", "solucao_aplicada", "impacto_usuario", "instrucoes", "plano_rollback"
    )
    @classmethod
    def validar_textos(cls, valor: str) -> str:
        return _texto_seguro(valor)

    @field_validator("modulos_afetados")
    @classmethod
    def validar_modulos(cls, valor: list[str]) -> list[str]:
        modulos = list(dict.fromkeys(item.strip().lower() for item in valor if item.strip()))
        invalidos = sorted(set(modulos) - MODULOS_RELEASE)
        if invalidos:
            raise ValueError(f"Módulos desconhecidos: {', '.join(invalidos)}")
        if not modulos:
            raise ValueError("Informe ao menos um módulo afetado")
        return modulos

    @field_validator("riscos_conhecidos")
    @classmethod
    def validar_riscos(cls, valor: list[str]) -> list[str]:
        riscos = [_texto_seguro(item) for item in valor if item.strip()]
        if any(len(item) < 10 or len(item) > 1000 for item in riscos):
            raise ValueError("Cada risco deve possuir entre 10 e 1000 caracteres")
        return riscos

    @field_validator("implantada_em")
    @classmethod
    def validar_implantacao(cls, valor: datetime | None) -> datetime | None:
        if valor is None:
            return None
        implantada = valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)
        if implantada > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("A data de implantação não pode estar no futuro")
        return implantada

    @model_validator(mode="after")
    def critica_nao_pode_ser_adiada(self) -> "VersaoRascunhoInput":
        if self.tipo_atualizacao == "critica" and self.permite_adiar:
            raise ValueError("Uma correção crítica não pode permitir adiamento")
        return self


class PublicarVersaoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmar_publicacao: bool


class ArquivarVersaoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmar_arquivamento: bool
    motivo: str = Field(min_length=20, max_length=2000)

    @field_validator("motivo")
    @classmethod
    def validar_motivo(cls, valor: str) -> str:
        return _texto_seguro(valor)


class VersaoSistemaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    versao: str
    titulo: str
    problema_identificado: str
    solucao_aplicada: str
    impacto_usuario: str
    documentacao_url: HttpUrl | None
    permite_adiar: bool
    tipo_atualizacao: Literal["critica", "correcao", "funcionalidade"]
    modulos_afetados: list[str]
    implantada_em: datetime | None
    commit_sha: str
    migration_revision: str | None
    evidencias_testes: list[EvidenciaTesteInput]
    riscos_conhecidos: list[str]
    instrucoes: str
    plano_rollback: str
    conteudo_hash: str
    status: Literal["rascunho", "publicada", "arquivada"]
    criado_por: str
    criado_em: datetime
    atualizado_em: datetime
    publicado_por: str | None
    publicado_em: datetime | None
    arquivado_por: str | None
    arquivado_em: datetime | None
    arquivamento_motivo: str | None


class ListaVersoesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itens: list[VersaoSistemaResponse]
    total: int
    limite: int
    deslocamento: int


def calcular_hash_versao(dados: VersaoRascunhoInput) -> str:
    payload = dados.model_dump(mode="json", exclude_none=False)
    serializado = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def versao_json(item: VersaoSistema) -> dict:
    return {
        "id": item.id,
        "versao": item.versao,
        "titulo": item.titulo,
        "problema_identificado": item.problema_identificado,
        "solucao_aplicada": item.solucao_aplicada,
        "impacto_usuario": item.impacto_usuario or "Impacto ao usuário não informado nesta versão.",
        "documentacao_url": item.documentacao_url,
        "permite_adiar": item.permite_adiar,
        "tipo_atualizacao": item.tipo_atualizacao,
        "modulos_afetados": item.modulos_afetados,
        "implantada_em": item.implantada_em,
        "commit_sha": item.commit_sha,
        "migration_revision": item.migration_revision,
        "evidencias_testes": item.evidencias_testes,
        "riscos_conhecidos": item.riscos_conhecidos,
        "instrucoes": item.instrucoes,
        "plano_rollback": item.plano_rollback,
        "conteudo_hash": item.conteudo_hash,
        "status": item.status,
        "criado_por": item.criado_por,
        "criado_em": item.criado_em,
        "atualizado_em": item.atualizado_em,
        "publicado_por": item.publicado_por,
        "publicado_em": item.publicado_em,
        "arquivado_por": item.arquivado_por,
        "arquivado_em": item.arquivado_em,
        "arquivamento_motivo": item.arquivamento_motivo,
    }


async def _obter_versao(session: AsyncSession, versao_id: int) -> VersaoSistema:
    item = await session.get(VersaoSistema, versao_id)
    if item is None:
        raise HTTPException(404, "Versão do sistema não encontrada")
    return item


def _aplicar_rascunho(item: VersaoSistema, dados: VersaoRascunhoInput) -> None:
    item.versao = dados.versao
    item.titulo = dados.titulo
    item.problema_identificado = dados.problema_identificado
    item.solucao_aplicada = dados.solucao_aplicada
    item.impacto_usuario = dados.impacto_usuario
    item.documentacao_url = str(dados.documentacao_url) if dados.documentacao_url else None
    item.permite_adiar = dados.permite_adiar
    item.tipo_atualizacao = dados.tipo_atualizacao
    item.modulos_afetados = dados.modulos_afetados
    item.implantada_em = dados.implantada_em
    item.commit_sha = dados.commit_sha
    item.migration_revision = dados.migration_revision
    item.evidencias_testes = [evidencia.model_dump(mode="json") for evidencia in dados.evidencias_testes]
    item.riscos_conhecidos = dados.riscos_conhecidos
    item.instrucoes = dados.instrucoes
    item.plano_rollback = dados.plano_rollback
    item.conteudo_hash = calcular_hash_versao(dados)


async def _versao_duplicada(
    session: AsyncSession,
    versao: str,
    *,
    ignorar_id: int | None = None,
) -> bool:
    consulta = select(VersaoSistema.id).where(VersaoSistema.versao == versao)
    if ignorar_id is not None:
        consulta = consulta.where(VersaoSistema.id != ignorar_id)
    return (await session.execute(consulta)).scalar_one_or_none() is not None


async def _commit_ou_conflito(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "A versão informada já existe") from exc


@router.get("", response_model=ListaVersoesResponse)
async def listar_versoes(
    session: SessionDep,
    _: ViewDep,
    status_filtro: Annotated[Literal["rascunho", "publicada", "arquivada"] | None, Query(alias="status")] = None,
    tipo: Literal["critica", "correcao", "funcionalidade"] | None = None,
    limite: Annotated[int, Query(ge=1, le=100)] = 50,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    filtros = []
    if status_filtro:
        filtros.append(VersaoSistema.status == status_filtro)
    if tipo:
        filtros.append(VersaoSistema.tipo_atualizacao == tipo)
    total = int(
        (await session.execute(select(func.count()).select_from(VersaoSistema).where(*filtros))).scalar_one()
    )
    itens = list(
        (
            await session.execute(
                select(VersaoSistema)
                .where(*filtros)
                .order_by(VersaoSistema.implantada_em.desc().nullslast(), VersaoSistema.id.desc())
                .limit(limite)
                .offset(deslocamento)
            )
        ).scalars()
    )
    return {
        "itens": [versao_json(item) for item in itens],
        "total": total,
        "limite": limite,
        "deslocamento": deslocamento,
    }


@router.get("/{versao_id}", response_model=VersaoSistemaResponse)
async def consultar_versao(versao_id: int, session: SessionDep, _: ViewDep) -> dict:
    return versao_json(await _obter_versao(session, versao_id))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=VersaoSistemaResponse)
async def criar_versao(
    dados: VersaoRascunhoInput,
    session: SessionDep,
    usuario: SuperAdminDep,
) -> dict:
    if await _versao_duplicada(session, dados.versao):
        raise HTTPException(409, "A versão informada já existe")
    item = VersaoSistema(status="rascunho", criado_por_id=usuario.id, criado_por=usuario.email)
    _aplicar_rascunho(item, dados)
    session.add(item)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "A versão informada já existe") from exc
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="CRIAR_RELEASE",
            recurso=f"versao_sistema:{item.id}",
            sucesso=True,
            status_http=201,
            detalhes={"versao": item.versao, "tipo": item.tipo_atualizacao, "hash": item.conteudo_hash},
        )
    )
    await _commit_ou_conflito(session)
    return versao_json(item)


@router.put("/{versao_id}", response_model=VersaoSistemaResponse)
async def editar_versao(
    versao_id: int,
    dados: VersaoRascunhoInput,
    session: SessionDep,
    usuario: SuperAdminDep,
) -> dict:
    item = await _obter_versao(session, versao_id)
    if item.status != "rascunho":
        raise HTTPException(409, "Versões publicadas ou arquivadas são imutáveis; crie uma nova versão")
    if await _versao_duplicada(session, dados.versao, ignorar_id=item.id):
        raise HTTPException(409, "A versão informada já existe")
    hash_anterior = item.conteudo_hash
    _aplicar_rascunho(item, dados)
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="EDITAR_RELEASE",
            recurso=f"versao_sistema:{item.id}",
            sucesso=True,
            status_http=200,
            detalhes={
                "versao": item.versao,
                "hash_anterior": hash_anterior,
                "hash_novo": item.conteudo_hash,
            },
        )
    )
    await _commit_ou_conflito(session)
    return versao_json(item)


@router.post("/{versao_id}/publicar", response_model=VersaoSistemaResponse)
async def publicar_versao(
    versao_id: int,
    dados: PublicarVersaoInput,
    session: SessionDep,
    usuario: SuperAdminDep,
) -> dict:
    if not dados.confirmar_publicacao:
        raise HTTPException(422, "Confirme expressamente a publicação")
    item = await _obter_versao(session, versao_id)
    if item.status == "publicada":
        return versao_json(item)
    if item.status != "rascunho":
        raise HTTPException(409, "Uma versão arquivada não pode ser publicada")
    if item.implantada_em is None:
        raise HTTPException(422, "Informe a data real de implantação antes de publicar")
    if not any(evidencia.get("resultado") == "aprovado" for evidencia in item.evidencias_testes):
        raise HTTPException(422, "Registre ao menos uma evidência de teste aprovada antes de publicar")
    agora = datetime.now(UTC)
    item.status = "publicada"
    item.publicado_por_id = usuario.id
    item.publicado_por = usuario.email
    item.publicado_em = agora
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="PUBLICAR_RELEASE",
            recurso=f"versao_sistema:{item.id}",
            sucesso=True,
            status_http=200,
            detalhes={
                "versao": item.versao,
                "commit": item.commit_sha,
                "migration": item.migration_revision,
                "hash": item.conteudo_hash,
            },
        )
    )
    await session.commit()
    return versao_json(item)


@router.post("/{versao_id}/arquivar", response_model=VersaoSistemaResponse)
async def arquivar_versao(
    versao_id: int,
    dados: ArquivarVersaoInput,
    session: SessionDep,
    usuario: SuperAdminDep,
) -> dict:
    if not dados.confirmar_arquivamento:
        raise HTTPException(422, "Confirme expressamente o arquivamento")
    item = await _obter_versao(session, versao_id)
    if item.status == "arquivada":
        return versao_json(item)
    if item.status != "publicada":
        raise HTTPException(409, "Somente uma versão publicada pode ser arquivada")
    item.status = "arquivada"
    item.arquivado_por_id = usuario.id
    item.arquivado_por = usuario.email
    item.arquivado_em = datetime.now(UTC)
    item.arquivamento_motivo = dados.motivo
    session.add(
        criar_evento_auditoria(
            organizacao_id=None,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="ARQUIVAR_RELEASE",
            recurso=f"versao_sistema:{item.id}",
            sucesso=True,
            status_http=200,
            detalhes={
                "versao": item.versao,
                "motivo_hash": hashlib.sha256(dados.motivo.encode("utf-8")).hexdigest(),
            },
        )
    )
    await session.commit()
    return versao_json(item)
