import hashlib
import io
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAtualDep, exigir_csrf
from app.database import get_session
from app.models import (
    AlertaSistema,
    BloqueioRetencao,
    EventoAuditoria,
    Lead,
    Organizacao,
    PesquisaMarca,
    PoliticaRetencao,
    SimulacaoRetencao,
    SolicitacaoPrivacidade,
    UsuarioOperacoes,
)
from app.queueing import enfileirar, status_fila
from app.retencao import politica_retencao_vigente, prazo_retencao_vigente, simular_retencao_leads
from app.storage import StorageError, delete_object, read_bytes, save_bytes

router = APIRouter(prefix="/v1/admin/confiabilidade", tags=["confiabilidade"])
public_router = APIRouter(prefix="/v1/tenant", tags=["tenant"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def exigir_admin(request: Request, usuario: UsuarioAtualDep):
    exigir_csrf(request, usuario)
    if not usuario.pode("production.manage"):
        raise HTTPException(403, "Acesso não autorizado")
    return usuario


AdminDep = Annotated[object, Depends(exigir_admin)]


class IdentidadeVisualPublicaResponse(BaseModel):
    nome: str
    nome_exibido: str | None = None
    cor_primaria: str | None = None
    logo_url: str | None = None
    politica_privacidade_versao: str


@public_router.get("/branding", response_model=IdentidadeVisualPublicaResponse)
async def branding_publico(request: Request, session: SessionDep) -> dict:
    from app.tenancy import resolver_organizacao_publica

    org = await resolver_organizacao_publica(request, session)
    branding = org.branding or {}
    return {
        "nome": org.nome,
        "nome_exibido": branding.get("nome_exibido"),
        "cor_primaria": branding.get("cor_primaria"),
        "logo_url": branding.get("logo_url"),
        "politica_privacidade_versao": org.politica_privacidade_versao,
    }


_COR_HEX_VALIDA = re.compile(r"^#[0-9a-fA-F]{6}$")


@public_router.get("/branding.css")
async def branding_css(request: Request, session: SessionDep) -> Response:
    from app.tenancy import resolver_organizacao_publica

    org = await resolver_organizacao_publica(request, session)
    cor = (org.branding or {}).get("cor_primaria")
    css = f":root{{--forest:{cor}}}" if cor and _COR_HEX_VALIDA.fullmatch(cor) else ""
    # CSS servido como recurso 'self' (nao inline) para respeitar o CSP
    # style-src estrito, que bloqueia style="" e element.style.* sem 'unsafe-inline'.
    return Response(content=css, media_type="text/css", headers={"Cache-Control": "no-store"})


@public_router.get("/logo")
async def logo_publico(request: Request, session: SessionDep) -> Response:
    """Serve a imagem do tenant sem revelar o caminho interno do armazenamento."""
    from app.tenancy import resolver_organizacao_publica

    org = await resolver_organizacao_publica(request, session)
    item = (org.branding or {}).get("logo_asset") or {}
    localizacao = item.get("localizacao")
    if not localizacao:
        raise HTTPException(404, "Logotipo não configurado")
    try:
        conteudo = read_bytes(localizacao)
    except (OSError, StorageError):
        raise HTTPException(404, "Logotipo não encontrado") from None
    return Response(
        content=conteudo,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


CAMPOS_IDENTIDADE_VISUAL = frozenset({"nome_exibido", "cor_primaria", "logo_url"})


class IdentidadeVisualInput(BaseModel):
    """Campos que a tela de confiabilidade tem autorização para alterar."""

    model_config = ConfigDict(extra="forbid")

    nome_exibido: str | None = Field(default=None, min_length=2, max_length=80)
    cor_primaria: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    logo_url: str | None = Field(default=None, max_length=500)

    @field_validator("nome_exibido", "logo_url", mode="before")
    @classmethod
    def limpar_opcionais(cls, valor: str | None) -> str | None:
        return valor.strip() or None if isinstance(valor, str) else valor

    @field_validator("logo_url")
    @classmethod
    def validar_logo(cls, valor: str | None) -> str | None:
        recurso_interno = valor and (
            valor.startswith("/static/")
            or valor == "/v1/tenant/logo"
            or valor.startswith("/v1/tenant/logo?")
        )
        if valor and not (valor.startswith("https://") or recurso_interno):
            raise ValueError("A logo deve usar HTTPS ou um recurso interno autorizado")
        return valor


class ConfiguracaoTenantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branding: IdentidadeVisualInput = Field(default_factory=IdentidadeVisualInput)
    retencao_dados_dias: int | None = Field(default=None, ge=30, le=3650)
    retencao_justificativa: str | None = Field(default=None, min_length=20, max_length=1000)
    retencao_finalidade: str | None = Field(default=None, min_length=10, max_length=1000)
    retencao_base_legal: str | None = Field(default=None, min_length=10, max_length=1000)
    retencao_vigencia_em: datetime | None = None
    confirmar_reducao_retencao: bool = False
    retencao_simulacao_id: int | None = Field(default=None, ge=1)
    politica_privacidade_versao: str | None = Field(default=None, min_length=1, max_length=30)
    politica_privacidade_justificativa: str | None = Field(default=None, min_length=20, max_length=1000)
    confirmar_publicacao_politica: bool = False


class SimulacaoRetencaoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prazo_dias: int | None = Field(default=None, ge=30, le=3650)
    limite: int = Field(default=200, ge=1, le=200)
    deslocamento: int = Field(default=0, ge=0)


class BloqueioRetencaoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(min_length=20, max_length=1000)


TAMANHO_MAXIMO_LOGO = 1024 * 1024
DIMENSAO_MAXIMA_LOGO = 2000


def normalizar_logo(conteudo: bytes) -> tuple[bytes, int, int]:
    """Valida a imagem e a regrava como PNG, removendo metadados e conteúdo ativo."""
    if not conteudo or len(conteudo) > TAMANHO_MAXIMO_LOGO:
        raise ValueError("O logotipo deve ter no máximo 1 MB")
    try:
        with Image.open(io.BytesIO(conteudo)) as origem:
            if origem.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Envie uma imagem PNG, JPEG ou WebP")
            largura, altura = origem.size
            if largura < 32 or altura < 32:
                raise ValueError("O logotipo deve ter pelo menos 32 x 32 pixels")
            if largura > DIMENSAO_MAXIMA_LOGO or altura > DIMENSAO_MAXIMA_LOGO:
                raise ValueError("O logotipo não pode exceder 2000 x 2000 pixels")
            imagem = ImageOps.exif_transpose(origem)
            imagem.load()
            if imagem.mode not in {"RGB", "RGBA"}:
                imagem = imagem.convert("RGBA")
            saida = io.BytesIO()
            imagem.save(saida, format="PNG", optimize=True)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise ValueError("Arquivo de imagem inválido") from exc
    return saida.getvalue(), largura, altura


@router.get("")
async def painel(session: SessionDep, usuario: AdminDep) -> dict:
    org_id = usuario.organizacao_id
    org = await session.get(Organizacao, org_id)
    contagens = {}
    for modelo, chave in (
        (UsuarioOperacoes, "usuarios"),
        (Lead, "leads"),
        (PesquisaMarca, "pesquisas"),
    ):
        contagens[chave] = (
            await session.execute(select(func.count()).select_from(modelo).where(modelo.organizacao_id == org_id))
        ).scalar_one()
    ultima_rotina = (
        await session.execute(
            select(AlertaSistema)
            .where(
                AlertaSistema.organizacao_id == org_id,
                AlertaSistema.codigo.in_(
                    {
                        "PREVISOES_REPROCESSADAS",
                        "AGENTES_REPROCESSADOS",
                        "MODELO_APRENDIZADO_ATIVADO",
                        "MODELO_APRENDIZADO_AGUARDANDO_REVISOES",
                        "MODELO_APRENDIZADO_BLOQUEADO",
                        "MODELO_APRENDIZADO_REPROVADO",
                    }
                ),
            )
            .order_by(AlertaSistema.criado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    politica_vigente = await politica_retencao_vigente(session, org)
    prazo_leads = politica_vigente.prazo_dias if politica_vigente else org.retencao_dados_dias
    politicas = list(
        (
            await session.execute(
                select(PoliticaRetencao)
                .where(PoliticaRetencao.organizacao_id == org_id)
                .order_by(PoliticaRetencao.vigencia_em.desc(), PoliticaRetencao.id.desc())
                .limit(20)
            )
        ).scalars()
    )
    holds_ativos = (
        await session.execute(
            select(func.count())
            .select_from(BloqueioRetencao)
            .where(BloqueioRetencao.organizacao_id == org_id, BloqueioRetencao.ativo.is_(True))
        )
    ).scalar_one()
    return {
        "organizacao": {
            "id": org.id,
            "nome": org.nome,
            "branding": org.branding or {},
            "status": org.status,
            "assinatura_status": org.assinatura_status,
            "trial_ate": org.trial_ate,
            "retencao_dados_dias": org.retencao_dados_dias,
            "politica_privacidade_versao": org.politica_privacidade_versao,
        },
        "uso": contagens,
        "fila": await status_fila(),
        "permissoes": {"superadmin": bool(usuario.superadmin)},
        "retencao": {
            "modo": "revisao_humana",
            "descarte_automatico": False,
            "matriz": [
                {
                    "categoria": "lead",
                    "prazo_dias": prazo_leads,
                    "marco_inicial": politica_vigente.marco_inicial if politica_vigente else "lead.criado_em",
                    "finalidade": (
                        politica_vigente.finalidade
                        if politica_vigente
                        else "Pendente de formalização para o prazo legado."
                    ),
                    "base_legal": (
                        politica_vigente.base_legal
                        if politica_vigente
                        else "Pendente de formalização para o prazo legado."
                    ),
                    "excecoes": (
                        politica_vigente.excecoes
                        if politica_vigente
                        else [
                            "atendimento_ativo",
                            "processo_ativo",
                            "contrato_ativo",
                            "documento_obrigacao_juridica",
                            "legal_hold",
                        ]
                    ),
                    "ativo": politica_vigente.ativo if politica_vigente else True,
                }
            ],
            "legal_holds_ativos": holds_ativos,
            "historico": [
                {
                    "id": politica.id,
                    "categoria": politica.categoria,
                    "prazo_dias": politica.prazo_dias,
                    "marco_inicial": politica.marco_inicial,
                    "finalidade": politica.finalidade,
                    "base_legal": politica.base_legal,
                    "vigencia_em": politica.vigencia_em,
                    "justificativa": politica.justificativa,
                    "excecoes": politica.excecoes,
                    "ativo": politica.ativo,
                    "reducao": politica.reducao,
                    "reducao_confirmada": politica.reducao_confirmada,
                    "criado_por": politica.criado_por,
                    "criado_em": politica.criado_em,
                }
                for politica in politicas
            ],
        },
        "ultima_rotina_aprendizado": (
            {
                "id": ultima_rotina.id,
                "codigo": ultima_rotina.codigo,
                "severidade": ultima_rotina.severidade,
                "mensagem": ultima_rotina.mensagem,
                "detalhes": ultima_rotina.detalhes or {},
                "criado_em": ultima_rotina.criado_em,
            }
            if ultima_rotina is not None
            else None
        ),
    }


@router.patch("/configuracao")
async def configurar(dados: ConfiguracaoTenantInput, request: Request, session: SessionDep, usuario: AdminDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        raise HTTPException(404, "Organização não encontrada")

    branding_atual = dict(org.branding or {})
    identidade_visual = dados.branding.model_dump(exclude_unset=True, include=CAMPOS_IDENTIDADE_VISUAL)
    org.branding = {**branding_atual, **identidade_visual}
    alteracoes_identidade = {
        campo: {"anterior": branding_atual.get(campo), "novo": valor}
        for campo, valor in identidade_visual.items()
        if branding_atual.get(campo) != valor
    }
    if dados.retencao_dados_dias is not None:
        prazo_atual = await prazo_retencao_vigente(session, org)
        novo_prazo = dados.retencao_dados_dias
        if novo_prazo != prazo_atual:
            if (
                not dados.retencao_justificativa
                or not dados.retencao_finalidade
                or not dados.retencao_base_legal
                or dados.retencao_vigencia_em is None
            ):
                raise HTTPException(
                    422,
                    "Alterar a retenção exige justificativa, finalidade, base legal e data de vigência.",
                )
            reducao = novo_prazo < prazo_atual
            if reducao and not dados.confirmar_reducao_retencao:
                raise HTTPException(422, "A redução do prazo exige confirmação explícita.")
            if dados.retencao_simulacao_id is None:
                raise HTTPException(422, "Execute e confirme uma simulação antes de alterar o prazo.")
            simulacao = (
                await session.execute(
                    select(SimulacaoRetencao).where(
                        SimulacaoRetencao.id == dados.retencao_simulacao_id,
                        SimulacaoRetencao.organizacao_id == org.id,
                        SimulacaoRetencao.categoria == "lead",
                        SimulacaoRetencao.prazo_dias == novo_prazo,
                        SimulacaoRetencao.criado_por_id == usuario.id,
                        SimulacaoRetencao.criado_em >= datetime.now(UTC) - timedelta(hours=24),
                        SimulacaoRetencao.usada_em.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if simulacao is None:
                raise HTTPException(422, "Simulação inválida, expirada, já utilizada ou de outro contexto.")
            vigencia = dados.retencao_vigencia_em
            if vigencia.tzinfo is None:
                vigencia = vigencia.replace(tzinfo=UTC)
            politica = PoliticaRetencao(
                organizacao_id=org.id,
                categoria="lead",
                prazo_dias=novo_prazo,
                marco_inicial="lead.criado_em",
                finalidade=dados.retencao_finalidade.strip(),
                base_legal=dados.retencao_base_legal.strip(),
                vigencia_em=vigencia,
                justificativa=dados.retencao_justificativa.strip(),
                excecoes=[
                    "atendimento_ativo",
                    "processo_ativo",
                    "contrato_ativo",
                    "documento_obrigacao_juridica",
                    "legal_hold",
                ],
                ativo=True,
                reducao=reducao,
                reducao_confirmada=bool(reducao and dados.confirmar_reducao_retencao),
                criado_por_id=usuario.id,
                criado_por=usuario.email,
            )
            session.add(politica)
            simulacao.usada_em = datetime.now(UTC)
            if vigencia <= datetime.now(UTC):
                org.retencao_dados_dias = novo_prazo
            session.add(
                EventoAuditoria(
                    organizacao_id=org.id,
                    actor_id=usuario.id,
                    ator=usuario.email,
                    acao="ALTERAR_POLITICA_RETENCAO",
                    recurso="politica_retencao:lead",
                    sucesso=True,
                    status_http=200,
                    detalhes={
                        "prazo_anterior": prazo_atual,
                        "prazo_novo": novo_prazo,
                        "vigencia_em": vigencia.isoformat(),
                        "reducao": reducao,
                    },
                )
            )
    if dados.politica_privacidade_versao is not None:
        nova_versao = dados.politica_privacidade_versao.strip()
        if nova_versao != org.politica_privacidade_versao:
            if not dados.politica_privacidade_justificativa or not dados.confirmar_publicacao_politica:
                raise HTTPException(
                    422,
                    "Publicar nova versão da política exige justificativa e confirmação explícita.",
                )
            versao_anterior = org.politica_privacidade_versao
            org.politica_privacidade_versao = nova_versao
            session.add(
                EventoAuditoria(
                    organizacao_id=org.id,
                    actor_id=usuario.id,
                    ator=usuario.email,
                    acao="PUBLICAR_VERSAO_POLITICA_PRIVACIDADE",
                    recurso="organizacao:politica_privacidade",
                    sucesso=True,
                    status_http=200,
                    detalhes={
                        "versao_anterior": versao_anterior,
                        "versao_nova": nova_versao,
                        "justificativa": dados.politica_privacidade_justificativa.strip(),
                    },
                )
            )
    if alteracoes_identidade:
        session.add(
            EventoAuditoria(
                organizacao_id=org.id,
                actor_id=usuario.id,
                ator=usuario.email,
                acao="ALTERAR_IDENTIDADE_VISUAL",
                recurso="organizacao:branding_publico",
                sucesso=True,
                status_http=200,
                detalhes={"campos": alteracoes_identidade},
            )
        )
    await session.commit()
    return {"status": "ok"}


@router.post("/identidade/logo", status_code=201)
async def enviar_logo(
    session: SessionDep,
    usuario: AdminDep,
    arquivo: Annotated[UploadFile, File()],
) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        raise HTTPException(404, "Organização não encontrada")
    conteudo = await arquivo.read(TAMANHO_MAXIMO_LOGO + 1)
    try:
        normalizado, largura, altura = normalizar_logo(conteudo)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    digest = hashlib.sha256(normalizado).hexdigest()
    chave = f"branding/org-{org.id}/{digest}.png"
    try:
        localizacao = save_bytes(chave, normalizado)
    except (OSError, StorageError) as exc:
        raise HTTPException(503, "Não foi possível armazenar o logotipo") from exc

    branding_anterior = dict(org.branding or {})
    asset_anterior = branding_anterior.get("logo_asset") or {}
    org.branding = {
        **branding_anterior,
        "logo_url": f"/v1/tenant/logo?v={digest[:16]}",
        "logo_asset": {
            "localizacao": localizacao,
            "sha256": digest,
            "tamanho": len(normalizado),
            "largura": largura,
            "altura": altura,
            "formato": "PNG",
            "atualizado_em": datetime.now(UTC).isoformat(),
            "atualizado_por": usuario.email,
        },
    }
    session.add(
        EventoAuditoria(
            organizacao_id=org.id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="ENVIAR_LOGO_IDENTIDADE_VISUAL",
            recurso="organizacao:branding_publico",
            sucesso=True,
            status_http=201,
            detalhes={
                "sha256": digest,
                "tamanho": len(normalizado),
                "largura": largura,
                "altura": altura,
            },
        )
    )
    try:
        await session.commit()
    except Exception:
        delete_object(localizacao)
        raise

    localizacao_anterior = asset_anterior.get("localizacao")
    if localizacao_anterior and localizacao_anterior != localizacao:
        try:
            delete_object(localizacao_anterior)
        except (OSError, StorageError):
            pass
    return {
        "status": "ok",
        "logo_url": org.branding["logo_url"],
        "sha256": digest,
        "tamanho": len(normalizado),
        "largura": largura,
        "altura": altura,
    }


@router.delete("/identidade/logo")
async def remover_logo(session: SessionDep, usuario: AdminDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        raise HTTPException(404, "Organização não encontrada")
    branding = dict(org.branding or {})
    asset = branding.pop("logo_asset", None) or {}
    branding["logo_url"] = None
    org.branding = branding
    session.add(
        EventoAuditoria(
            organizacao_id=org.id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="REMOVER_LOGO_IDENTIDADE_VISUAL",
            recurso="organizacao:branding_publico",
            sucesso=True,
            status_http=200,
            detalhes={"possuia_arquivo_gerenciado": bool(asset)},
        )
    )
    await session.commit()
    localizacao = asset.get("localizacao")
    if localizacao:
        try:
            delete_object(localizacao)
        except (OSError, StorageError):
            pass
    return {"status": "ok", "logo_url": None}


@router.post("/retencao/simular")
async def simular_retencao(
    dados: SimulacaoRetencaoInput,
    session: SessionDep,
    usuario: AdminDep,
) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        raise HTTPException(404, "Organização não encontrada")
    resultado = await simular_retencao_leads(
        session,
        org,
        prazo_dias=dados.prazo_dias,
        limite_amostra=dados.limite,
        deslocamento=dados.deslocamento,
    )
    resumo = {
        **resultado,
        "data_corte": resultado["data_corte"].isoformat(),
        "registro_mais_antigo": (
            resultado["registro_mais_antigo"].isoformat() if resultado["registro_mais_antigo"] else None
        ),
    }
    simulacao = SimulacaoRetencao(
        organizacao_id=usuario.organizacao_id,
        categoria="lead",
        prazo_dias=resultado["prazo_dias"],
        resultado=resumo,
        criado_por_id=usuario.id,
        criado_por=usuario.email,
    )
    session.add(simulacao)
    await session.flush()
    await session.commit()
    return {**resultado, "simulacao_id": simulacao.id}


@router.post("/retencao/leads/{lead_id}/legal-hold", status_code=201)
async def criar_legal_hold(
    lead_id: int,
    dados: BloqueioRetencaoInput,
    session: SessionDep,
    usuario: AdminDep,
) -> dict:
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(404, "Lead não encontrado")
    hold = (
        await session.execute(
            select(BloqueioRetencao).where(
                BloqueioRetencao.organizacao_id == usuario.organizacao_id,
                BloqueioRetencao.categoria == "lead",
                BloqueioRetencao.recurso_id == lead_id,
            )
        )
    ).scalar_one_or_none()
    if hold is None:
        hold = BloqueioRetencao(
            organizacao_id=usuario.organizacao_id,
            categoria="lead",
            recurso_id=lead_id,
            motivo=dados.motivo.strip(),
            ativo=True,
            criado_por_id=usuario.id,
            criado_por=usuario.email,
        )
        session.add(hold)
    else:
        hold.motivo = dados.motivo.strip()
        hold.ativo = True
        hold.liberado_por_id = None
        hold.liberado_por = None
        hold.liberado_em = None
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="ATIVAR_LEGAL_HOLD",
            recurso=f"lead:{lead_id}",
            sucesso=True,
            status_http=201,
            detalhes={},
        )
    )
    await session.commit()
    return {"status": "ativo", "categoria": "lead", "recurso_id": lead_id}


@router.delete("/retencao/leads/{lead_id}/legal-hold")
async def liberar_legal_hold(
    lead_id: int,
    session: SessionDep,
    usuario: AdminDep,
) -> dict:
    hold = (
        await session.execute(
            select(BloqueioRetencao).where(
                BloqueioRetencao.organizacao_id == usuario.organizacao_id,
                BloqueioRetencao.categoria == "lead",
                BloqueioRetencao.recurso_id == lead_id,
                BloqueioRetencao.ativo.is_(True),
            )
        )
    ).scalar_one_or_none()
    if hold is None:
        return {"status": "liberado", "categoria": "lead", "recurso_id": lead_id, "ja_liberado": True}
    hold.ativo = False
    hold.liberado_por_id = usuario.id
    hold.liberado_por = usuario.email
    hold.liberado_em = datetime.now(UTC)
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="LIBERAR_LEGAL_HOLD",
            recurso=f"lead:{lead_id}",
            sucesso=True,
            status_http=200,
            detalhes={},
        )
    )
    await session.commit()
    return {"status": "liberado", "categoria": "lead", "recurso_id": lead_id, "ja_liberado": False}


@router.post("/tarefas/{tipo}", status_code=202)
async def criar_tarefa(tipo: str, request: Request, usuario: AdminDep) -> dict:
    permitidos = {
        "assinaturas.verificar",
        "privacidade.verificar_retencao",
        "registrabilidade.reconciliar_resultados",
        "registrabilidade.reprocessar_previsoes",
        "registrabilidade.reprocessar_agentes",
        "registrabilidade.pipeline_aprendizado",
    }
    if tipo not in permitidos:
        raise HTTPException(422, "Tarefa não permitida")
    if tipo == "registrabilidade.pipeline_aprendizado" and not usuario.superadmin:
        raise HTTPException(403, "Treinamento global exclusivo do superadministrador")
    payload = {
        "organizacao_id": usuario.organizacao_id,
        "solicitado_por": usuario.email,
    }
    try:
        return {"status": "enfileirada", "job": await enfileirar(tipo, payload)}
    except Exception as exc:
        raise HTTPException(503, "Fila indisponível") from exc


@router.post("/privacidade/leads/{lead_id}/solicitar")
async def solicitar_privacidade(lead_id: int, request: Request, session: SessionDep, usuario: AdminDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    item = SolicitacaoPrivacidade(
        organizacao_id=usuario.organizacao_id,
        lead_id=lead.id,
        tipo="anonimizacao",
        solicitado_por=usuario.email,
    )
    session.add(item)
    await session.commit()
    return {"id": item.id, "status": item.status}


@router.get("/privacidade/leads/{lead_id}/exportar")
async def exportar_lead(lead_id: int, session: SessionDep, usuario: AdminDep) -> dict:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    pesquisas = list(
        (
            await session.execute(
                select(PesquisaMarca)
                .where(
                    PesquisaMarca.lead_id == lead.id,
                    PesquisaMarca.organizacao_id == usuario.organizacao_id,
                )
                .order_by(PesquisaMarca.criado_em.desc())
            )
        ).scalars()
    )
    return {
        "nome": lead.nome,
        "email": lead.email,
        "telefone": lead.telefone,
        "empresa": lead.empresa,
        "marca": lead.marca,
        "atividade": lead.atividade,
        "aceite_privacidade": lead.aceite_privacidade,
        "aceite_marketing": lead.aceite_marketing,
        "criado_em": lead.criado_em,
        "pesquisas": [
            {
                "id": pesquisa.id,
                "marca": pesquisa.marca,
                "atividade": pesquisa.atividade,
                "tipo_pesquisa": pesquisa.tipo_pesquisa,
                "classe_nice": pesquisa.classe_nice,
                "criado_em": pesquisa.criado_em,
            }
            for pesquisa in pesquisas
        ],
    }


@router.post("/privacidade/solicitacoes/{solicitacao_id}/concluir")
async def anonimizar(solicitacao_id: int, request: Request, session: SessionDep, usuario: AdminDep) -> dict:
    item = (
        await session.execute(
            select(SolicitacaoPrivacidade).where(
                SolicitacaoPrivacidade.id == solicitacao_id,
                SolicitacaoPrivacidade.organizacao_id == usuario.organizacao_id,
                SolicitacaoPrivacidade.status == "aberta",
            )
        )
    ).scalar_one_or_none()
    if not item or not item.lead_id:
        raise HTTPException(404, "Solicitação aberta não encontrada")
    lead = await session.get(Lead, item.lead_id)
    if not lead or lead.organizacao_id != usuario.organizacao_id:
        raise HTTPException(404, "Lead nÃ£o encontrado")
    marcador = hashlib.sha256(f"{lead.id}:{lead.email}".encode()).hexdigest()[:12]
    pesquisas_desvinculadas = (
        await session.execute(
            update(PesquisaMarca)
            .where(
                PesquisaMarca.lead_id == lead.id,
                PesquisaMarca.organizacao_id == usuario.organizacao_id,
            )
            .values(lead_id=None)
        )
    ).rowcount
    lead.nome = "Titular anonimizado"
    lead.email = f"anonimo-{marcador}@invalid.local"
    lead.telefone = "anonimizado"
    lead.empresa = None
    lead.marca = "Interesse anonimizado"
    lead.atividade = None
    lead.processo_numero = None
    lead.aceite_marketing = False
    lead.aceite_privacidade = False
    lead.responsavel_id = None
    lead.notas = None
    lead.proxima_acao_em = None
    lead.ultimo_contato_em = None
    lead.tags = []
    item.status = "concluida"
    item.concluido_por = usuario.email
    item.concluido_em = datetime.now(UTC)
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.email,
            acao="ANONIMIZAR",
            recurso="lead",
            sucesso=True,
            status_http=200,
            detalhes={
                "lead_id": lead.id,
                "pesquisas_desvinculadas": pesquisas_desvinculadas or 0,
            },
        )
    )
    await session.commit()
    return {"status": "concluida", "lead_id": lead.id}
