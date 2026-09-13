import base64
import hashlib
import re
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.versoes_sistema import MODULOS_RELEASE, ViewDep, _texto_seguro
from app.auditing import criar_evento_auditoria
from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.malware_scan import escanear_upload_ou_rejeitar
from app.models import InteracaoVersaoSistema, Organizacao, ProblemaVersaoSistema, UsuarioOperacoes, VersaoSistema
from app.relatorios import gerar_pdf_atualizacoes
from app.settings import get_settings
from app.storage import StorageError, read_bytes, save_bytes

router = APIRouter(prefix="/v1/admin/atualizacoes", tags=["central de atualizações"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UsuarioDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("dashboard.view"))]
STATUS_PROBLEMA_VALIDOS = frozenset({"aberto", "em_analise", "resolvido"})
# Fase 6, achado do usuário ("sem dados sensíveis"): só tipos de arquivo que
# não costumam carregar segredo embutido (nada de .docx/.zip/executável --
# superfície mínima, e ainda passa pelo ClamAV antes de persistir).
TIPOS_ANEXO_PERMITIDOS = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain"}
)
TAMANHO_MAXIMO_ANEXO = 8 * 1024 * 1024


class ConfirmarLeituraInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmar: bool


class AdiarAvisoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dias: int = Field(ge=1, le=30)


class AtualizarStatusProblemaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["aberto", "em_analise", "resolvido"]


class AnexoProblemaInput(BaseModel):
    """Sempre um arquivo escolhido explicitamente pelo operador -- nunca um
    screenshot/DOM/console/localStorage capturado automaticamente pelo
    backend (achado do usuário, Fase 6)."""

    model_config = ConfigDict(extra="forbid")
    nome: str = Field(min_length=1, max_length=180)
    content_type: str
    conteudo_base64: str = Field(min_length=1)

    @field_validator("content_type")
    @classmethod
    def validar_content_type(cls, valor: str) -> str:
        if valor not in TIPOS_ANEXO_PERMITIDOS:
            raise ValueError(
                f"Tipo de arquivo não permitido para anexo ({valor}). "
                f"Aceitos: {', '.join(sorted(TIPOS_ANEXO_PERMITIDOS))}"
            )
        return valor


class ReportarProblemaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categoria: Literal["erro", "duvida", "regressao"]
    modulo: str | None = Field(default=None, max_length=60)
    descricao: str = Field(min_length=20, max_length=3000)
    etapas_reproduzir: str | None = Field(default=None, max_length=3000)
    resultado_esperado: str | None = Field(default=None, max_length=1000)
    resultado_encontrado: str | None = Field(default=None, max_length=1000)
    gravidade: Literal["baixa", "media", "alta", "critica"] = "media"
    # Aviso explícito na tela (não é filtro automático de conteúdo -- não dá
    # pra "detectar" segredo em texto livre de forma confiável): "não inclua
    # senhas, tokens ou dados de clientes". Ver docstring de
    # AnexoProblemaInput sobre a captura nunca ser automática.
    anexo: AnexoProblemaInput | None = None

    @field_validator("descricao", "etapas_reproduzir", "resultado_esperado", "resultado_encontrado")
    @classmethod
    def validar_texto(cls, valor: str | None) -> str | None:
        return _texto_seguro(valor) if valor else valor

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
    classificacao: Literal["critica", "correcao", "melhoria", "funcionalidade"]
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


@router.get("/relatorio-pdf")
async def gerar_relatorio_pdf(
    session: SessionDep,
    usuario: UsuarioDep,
    de: Annotated[date | None, Query()] = None,
    ate: Annotated[date | None, Query()] = None,
) -> Response:
    # Achado do usuário (12/09/2026): a Central de Atualizações só podia ser
    # lida na tela, sem uma forma simples de exportar/guardar o histórico.
    consulta = (
        select(VersaoSistema)
        .where(VersaoSistema.status == "publicada")
        .order_by(VersaoSistema.implantada_em.desc(), VersaoSistema.id.desc())
        .limit(100)
    )
    if de is not None:
        consulta = consulta.where(VersaoSistema.implantada_em >= datetime.combine(de, time.min, tzinfo=UTC))
    if ate is not None:
        consulta = consulta.where(VersaoSistema.implantada_em <= datetime.combine(ate, time.max, tzinfo=UTC))
    itens = (await session.execute(consulta)).scalars().all()
    organizacao = await session.get(Organizacao, usuario.organizacao_id)
    pdf = gerar_pdf_atualizacoes(
        {
            "organizacao": organizacao.nome if organizacao else None,
            "gerado_em": datetime.now(UTC),
            "gerado_por": usuario.ator,
            "de": de,
            "ate": ate,
            "itens": [
                {
                    "versao": item.versao,
                    "titulo": item.titulo,
                    "classificacao": item.tipo_atualizacao,
                    "impacto_usuario": item.impacto_usuario,
                    "implantada_em": item.implantada_em,
                }
                for item in itens
            ],
        }
    )
    session.add(
        criar_evento_auditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="EXPORTAR_RELATORIO_ATUALIZACOES",
            recurso="central_atualizacoes",
            sucesso=True,
            status_http=200,
            detalhes={"de": de.isoformat() if de else None, "ate": ate.isoformat() if ate else None, "total": len(itens)},
        )
    )
    await session.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="central-de-atualizacoes.pdf"'},
    )


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


def _slug_anexo(valor: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9._-]", "_", valor))[:180] or "anexo"


async def _anexar_arquivo(
    problema: ProblemaVersaoSistema, anexo: AnexoProblemaInput, organizacao_id: int
) -> None:
    try:
        conteudo = base64.b64decode(anexo.conteudo_base64, validate=True)
    except Exception as exc:
        raise HTTPException(422, "Anexo inválido (base64 malformado)") from exc
    if not conteudo:
        raise HTTPException(422, "Anexo vazio")
    if len(conteudo) > TAMANHO_MAXIMO_ANEXO:
        raise HTTPException(413, f"Anexo excede o tamanho máximo de {TAMANHO_MAXIMO_ANEXO // (1024 * 1024)} MB")
    await escanear_upload_ou_rejeitar(conteudo)
    digest = hashlib.sha256(conteudo).hexdigest()
    try:
        caminho = save_bytes(
            f"problemas-versao/{organizacao_id}/{problema.id}/{digest}-{_slug_anexo(anexo.nome)}",
            conteudo,
        )
    except StorageError as exc:
        raise HTTPException(503, str(exc)) from exc
    problema.anexo_nome = anexo.nome
    problema.anexo_caminho = caminho
    problema.anexo_content_type = anexo.content_type
    problema.anexo_tamanho = len(conteudo)
    problema.anexo_hash = digest


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
        etapas_reproduzir=dados.etapas_reproduzir,
        resultado_esperado=dados.resultado_esperado,
        resultado_encontrado=dados.resultado_encontrado,
        gravidade=dados.gravidade,
        status="aberto",
    )
    session.add(problema)
    await session.flush()
    if dados.anexo is not None:
        await _anexar_arquivo(problema, dados.anexo, usuario.organizacao_id)
    session.add(
        criar_evento_auditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="REPORTAR_ATUALIZACAO",
            recurso=f"problema_versao:{problema.id}",
            sucesso=True,
            status_http=201,
            detalhes={
                "versao_id": item.id,
                "categoria": dados.categoria,
                "modulo": dados.modulo,
                "gravidade": dados.gravidade,
                "com_anexo": dados.anexo is not None,
            },
        )
    )
    await session.commit()
    if problema.criado_em is None:
        problema.criado_em = datetime.now(UTC)
    return {"id": problema.id, "status": "aberto", "criado_em": problema.criado_em}


# --- Auditoria (Fase 3, achado do usuário): "reportar problema" gravava no
# banco, mas não existia nenhuma tela nem endpoint pra ver esses relatos --
# caía num buraco negro visível só via banco direto. Restrito a Tech
# (mesmo nível de app.api.versoes_sistema, Fase 1). ---


@router.get("/problemas")
async def listar_problemas(
    session: SessionDep,
    _: ViewDep,
    status_filtro: Annotated[str | None, Query(alias="status")] = None,
) -> dict:
    if status_filtro is not None and status_filtro not in STATUS_PROBLEMA_VALIDOS:
        raise HTTPException(422, "Status inválido")
    consulta = (
        select(ProblemaVersaoSistema, VersaoSistema.versao, VersaoSistema.titulo, Organizacao.nome, UsuarioOperacoes.nome)
        .join(VersaoSistema, VersaoSistema.id == ProblemaVersaoSistema.versao_sistema_id)
        .join(Organizacao, Organizacao.id == ProblemaVersaoSistema.organizacao_id)
        .join(UsuarioOperacoes, UsuarioOperacoes.id == ProblemaVersaoSistema.usuario_id)
        .order_by(ProblemaVersaoSistema.criado_em.desc())
    )
    if status_filtro is not None:
        consulta = consulta.where(ProblemaVersaoSistema.status == status_filtro)
    linhas = (await session.execute(consulta)).all()
    return {
        "itens": [
            {
                "id": problema.id,
                "versao_sistema_id": problema.versao_sistema_id,
                "versao": versao,
                "versao_titulo": versao_titulo,
                "organizacao_nome": organizacao_nome,
                "usuario_nome": usuario_nome,
                "categoria": problema.categoria,
                "modulo": problema.modulo,
                "descricao": problema.descricao,
                "etapas_reproduzir": problema.etapas_reproduzir,
                "resultado_esperado": problema.resultado_esperado,
                "resultado_encontrado": problema.resultado_encontrado,
                "gravidade": problema.gravidade,
                "anexo": (
                    {
                        "nome": problema.anexo_nome,
                        "content_type": problema.anexo_content_type,
                        "tamanho": problema.anexo_tamanho,
                    }
                    if problema.anexo_caminho
                    else None
                ),
                "status": problema.status,
                "criado_em": problema.criado_em,
            }
            for problema, versao, versao_titulo, organizacao_nome, usuario_nome in linhas
        ]
    }


@router.get("/problemas/{problema_id}/anexo")
async def baixar_anexo_problema(problema_id: int, session: SessionDep, _: ViewDep) -> Response:
    problema = await session.get(ProblemaVersaoSistema, problema_id)
    if problema is None or not problema.anexo_caminho:
        raise HTTPException(404, "Anexo não encontrado")
    try:
        conteudo = read_bytes(problema.anexo_caminho)
    except (StorageError, OSError) as exc:
        raise HTTPException(503, "Não foi possível recuperar o anexo") from exc
    nome = problema.anexo_nome or "anexo"
    return Response(
        content=conteudo,
        media_type=problema.anexo_content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{_slug_anexo(nome)}"'},
    )


@router.patch("/problemas/{problema_id}")
async def atualizar_status_problema(
    problema_id: int, dados: AtualizarStatusProblemaInput, session: SessionDep, usuario: ViewDep
) -> dict:
    problema = await session.get(ProblemaVersaoSistema, problema_id)
    if problema is None:
        raise HTTPException(404, "Relato não encontrado")
    status_anterior = problema.status
    problema.status = dados.status
    session.add(
        criar_evento_auditoria(
            organizacao_id=problema.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="STATUS_PROBLEMA",
            recurso=f"problema_versao:{problema.id}",
            sucesso=True,
            status_http=200,
            detalhes={"status_anterior": status_anterior, "status_novo": dados.status},
        )
    )
    await session.commit()
    return {"id": problema.id, "status": problema.status}
