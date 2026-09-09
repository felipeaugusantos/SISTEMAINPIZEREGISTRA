from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.versoes_sistema import MODULOS_RELEASE, _texto_seguro
from app.auditing import criar_evento_auditoria
from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import InteracaoVersaoSistema, ProblemaVersaoSistema, VersaoSistema
from app.settings import get_settings

router = APIRouter(prefix="/v1/admin/atualizacoes", tags=["central de atualizações"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UsuarioDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("dashboard.view"))]


class ConfirmarLeituraInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmar: bool


class AdiarAvisoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dias: int = Field(ge=1, le=30)


class ReportarProblemaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categoria: Literal["erro", "duvida", "regressao"]
    modulo: str | None = Field(default=None, max_length=60)
    descricao: str = Field(min_length=20, max_length=3000)

    @field_validator("descricao")
    @classmethod
    def validar_descricao(cls, valor: str) -> str:
        return _texto_seguro(valor)

    @field_validator("modulo")
    @classmethod
    def validar_modulo(cls, valor: str | None) -> str | None:
        if valor is None or not valor.strip():
            return None
        modulo = valor.strip().lower()
        if modulo not in MODULOS_RELEASE:
            raise ValueError("Módulo desconhecido")
        return modulo


class EvidenciasPublicas(BaseModel):
    aprovadas: int
    falharam: int
    ignoradas: int
    total: int


class EstadoAtualizacao(BaseModel):
    confirmada_em: datetime | None
    adiada_ate: datetime | None


class AtualizacaoPublica(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    versao: str
    titulo: str
    problema: str
    correcao: str
    impacto_usuario: str
    classificacao: Literal["critica", "correcao", "funcionalidade"]
    modulos_afetados: list[str]
    documentacao_url: str | None
    evidencias: EvidenciasPublicas
    implantada_em: datetime
    leitura_obrigatoria: bool
    pode_adiar: bool
    estado: EstadoAtualizacao


class CentralAtualizacoesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    versao_implantada: str
    atualizacao_implantada_id: int | None
    novidades: list[AtualizacaoPublica]


class InteracaoResponse(BaseModel):
    confirmado_em: datetime | None
    adiado_ate: datetime | None


class ProblemaCriadoResponse(BaseModel):
    id: int
    status: Literal["aberto"]
    criado_em: datetime


def _evidencias_publicas(evidencias: list[dict] | None) -> dict:
    contagem = Counter(str(item.get("resultado")) for item in (evidencias or []))
    return {
        "aprovadas": contagem["aprovado"],
        "falharam": contagem["falhou"],
        "ignoradas": contagem["ignorado"],
        "total": sum(contagem.values()),
    }


def atualizacao_publica(
    item: VersaoSistema,
    interacao: InteracaoVersaoSistema | None,
) -> dict:
    """Projeção deliberadamente restrita; nunca reutilizar versao_json aqui."""
    obrigatoria = item.tipo_atualizacao in {"critica", "correcao"}
    return {
        "id": item.id,
        "versao": item.versao,
        "titulo": item.titulo,
        "problema": item.problema_identificado,
        "correcao": item.solucao_aplicada,
        "impacto_usuario": item.impacto_usuario or "Esta versão não exige mudança no seu fluxo de trabalho.",
        "classificacao": item.tipo_atualizacao,
        "modulos_afetados": list(item.modulos_afetados or []),
        "documentacao_url": item.documentacao_url,
        "evidencias": _evidencias_publicas(item.evidencias_testes),
        "implantada_em": item.implantada_em,
        "leitura_obrigatoria": obrigatoria,
        "pode_adiar": bool(item.permite_adiar and item.tipo_atualizacao != "critica"),
        "estado": {
            "confirmada_em": interacao.confirmado_em if interacao else None,
            "adiada_ate": interacao.adiado_ate if interacao else None,
        },
    }


async def _versao_publicada(session: AsyncSession, versao_id: int) -> VersaoSistema:
    item = await session.get(VersaoSistema, versao_id)
    if item is None or item.status != "publicada":
        raise HTTPException(404, "Atualização publicada não encontrada")
    return item


async def _interacao(
    session: AsyncSession,
    item: VersaoSistema,
    usuario: UsuarioAutenticado,
) -> InteracaoVersaoSistema:
    existente = (
        await session.execute(
            select(InteracaoVersaoSistema).where(
                InteracaoVersaoSistema.versao_sistema_id == item.id,
                InteracaoVersaoSistema.organizacao_id == usuario.organizacao_id,
                InteracaoVersaoSistema.usuario_id == usuario.id,
            )
        )
    ).scalar_one_or_none()
    if existente:
        return existente
    novo = InteracaoVersaoSistema(
        versao_sistema_id=item.id,
        organizacao_id=usuario.organizacao_id,
        usuario_id=usuario.id,
    )
    session.add(novo)
    await session.flush()
    return novo


@router.get("", response_model=CentralAtualizacoesResponse)
async def listar_atualizacoes(session: SessionDep, usuario: UsuarioDep) -> dict:
    consulta = (
        select(VersaoSistema, InteracaoVersaoSistema)
        .outerjoin(
            InteracaoVersaoSistema,
            and_(
                InteracaoVersaoSistema.versao_sistema_id == VersaoSistema.id,
                InteracaoVersaoSistema.organizacao_id == usuario.organizacao_id,
                InteracaoVersaoSistema.usuario_id == usuario.id,
            ),
        )
        .where(VersaoSistema.status == "publicada")
        .order_by(VersaoSistema.implantada_em.desc(), VersaoSistema.id.desc())
        .limit(100)
    )
    linhas = (await session.execute(consulta)).all()
    versao_runtime = get_settings().app_version
    atual_id = next((item.id for item, _ in linhas if item.versao == versao_runtime), None)
    return {
        "versao_implantada": versao_runtime,
        "atualizacao_implantada_id": atual_id,
        "novidades": [atualizacao_publica(item, interacao) for item, interacao in linhas],
    }


@router.post("/{versao_id}/confirmar-leitura", response_model=InteracaoResponse)
async def confirmar_leitura(
    versao_id: int,
    dados: ConfirmarLeituraInput,
    session: SessionDep,
    usuario: UsuarioDep,
) -> dict:
    if not dados.confirmar:
        raise HTTPException(422, "Confirme expressamente a leitura")
    item = await _versao_publicada(session, versao_id)
    interacao = await _interacao(session, item, usuario)
    if interacao.confirmado_em is None:
        interacao.confirmado_em = datetime.now(UTC)
        interacao.adiado_ate = None
        session.add(
            criar_evento_auditoria(
                organizacao_id=usuario.organizacao_id,
                actor_id=usuario.id,
                ator=usuario.email,
                acao="LER_ATUALIZACAO",
                recurso=f"versao_sistema:{item.id}",
                sucesso=True,
                status_http=200,
                detalhes={"versao": item.versao},
            )
        )
        await session.commit()
    return {"confirmado_em": interacao.confirmado_em, "adiado_ate": interacao.adiado_ate}


@router.post("/{versao_id}/adiar", response_model=InteracaoResponse)
async def adiar_aviso(
    versao_id: int,
    dados: AdiarAvisoInput,
    session: SessionDep,
    usuario: UsuarioDep,
) -> dict:
    item = await _versao_publicada(session, versao_id)
    if item.tipo_atualizacao == "critica" or not item.permite_adiar:
        raise HTTPException(409, "Esta atualização não permite adiamento")
    interacao = await _interacao(session, item, usuario)
    if interacao.confirmado_em is not None:
        raise HTTPException(409, "A leitura desta atualização já foi confirmada")
    interacao.adiado_ate = datetime.now(UTC) + timedelta(days=dados.dias)
    session.add(
        criar_evento_auditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="ADIAR_ATUALIZACAO",
            recurso=f"versao_sistema:{item.id}",
            sucesso=True,
            status_http=200,
            detalhes={"versao": item.versao, "dias": dados.dias},
        )
    )
    await session.commit()
    return {"confirmado_em": None, "adiado_ate": interacao.adiado_ate}


@router.post("/{versao_id}/problemas", status_code=status.HTTP_201_CREATED, response_model=ProblemaCriadoResponse)
async def reportar_problema(
    versao_id: int,
    dados: ReportarProblemaInput,
    session: SessionDep,
    usuario: UsuarioDep,
) -> dict:
    item = await _versao_publicada(session, versao_id)
    if dados.modulo and dados.modulo not in (item.modulos_afetados or []):
        raise HTTPException(422, "O módulo informado não pertence a esta atualização")
    problema = ProblemaVersaoSistema(
        versao_sistema_id=item.id,
        organizacao_id=usuario.organizacao_id,
        usuario_id=usuario.id,
        categoria=dados.categoria,
        modulo=dados.modulo,
        descricao=dados.descricao,
        status="aberto",
    )
    session.add(problema)
    await session.flush()
    session.add(
        criar_evento_auditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="REPORTAR_ATUALIZACAO",
            recurso=f"problema_versao:{problema.id}",
            sucesso=True,
            status_http=201,
            detalhes={"versao_id": item.id, "categoria": dados.categoria, "modulo": dados.modulo},
        )
    )
    await session.commit()
    if problema.criado_em is None:
        problema.criado_em = datetime.now(UTC)
    return {"id": problema.id, "status": "aberto", "criado_em": problema.criado_em}
