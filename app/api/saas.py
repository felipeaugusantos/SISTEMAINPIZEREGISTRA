import asyncio
import hashlib
import hmac
import json
import re
import secrets
import string
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auditing import criar_evento_auditoria
from app.auth import (
    UsuarioAtualDep,
    UsuarioAutenticado,
    exigir_csrf,
    hash_senha,
    normalizar_modulos_plano,
)
from app.database import get_session
from app.models import (
    AlertaSistema,
    ConviteOrganizacao,
    CredencialIntegracao,
    DominioOrganizacao,
    EventoAssinaturaStripe,
    EventoCobrancaSandbox,
    Lead,
    Organizacao,
    PesquisaMarca,
    PlanoSaas,
    PoliticaJuridica,
    SessaoOperacoes,
    UsuarioOperacoes,
)
from app.schemas import BrandingConfig

router = APIRouter(prefix="/v1/admin/saas", tags=["saas"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def exigir_superadmin(request: Request, usuario: UsuarioAtualDep) -> UsuarioAutenticado:
    exigir_csrf(request, usuario)
    if not usuario.superadmin:
        raise HTTPException(403, "Acesso exclusivo do superadministrador da plataforma")
    return usuario


SuperAdminDep = Annotated[UsuarioAutenticado, Depends(exigir_superadmin)]


def _stripe_checkout_configurado() -> bool:
    from app.settings import get_settings

    cfg = get_settings()
    return bool(cfg.stripe_saas_enabled and cfg.stripe_saas_secret_key and cfg.stripe_saas_webhook_secret)


class PlanoInput(BaseModel):
    nome: str = Field(min_length=2, max_length=100)
    codigo: str = Field(pattern=r"^[a-z0-9-]{2,50}$")
    descricao: str | None = Field(default=None, max_length=500)
    modulos: list[str] = Field(default_factory=list)
    # Somente limites que o produto realmente aplica podem ser configurados.
    # Zero mantém a semântica legada de ilimitado; negativos são inválidos.
    limites: dict[str, int] = Field(default_factory=dict)
    stripe_price_mensal_id: str | None = Field(default=None, max_length=150)
    stripe_price_anual_id: str | None = Field(default=None, max_length=150)

    @field_validator("limites")
    @classmethod
    def validar_limites_implementados(cls, valor: dict[str, int]) -> dict[str, int]:
        suportados = {"usuarios", "pesquisas_mes"}
        desconhecidos = sorted(set(valor) - suportados)
        if desconhecidos:
            raise ValueError(f"Limites ainda não implementados: {', '.join(desconhecidos)}")
        invalidos = sorted(chave for chave, limite in valor.items() if limite < 0)
        if invalidos:
            raise ValueError(f"Limites não podem ser negativos: {', '.join(invalidos)}")
        return valor


class PlanoPrecosInput(BaseModel):
    stripe_price_mensal_id: str | None = Field(default=None, max_length=150)
    stripe_price_anual_id: str | None = Field(default=None, max_length=150)


class OrganizacaoInput(BaseModel):
    nome: str = Field(min_length=2, max_length=180)
    slug: str = Field(pattern=r"^[a-z0-9-]{2,80}$")
    plano_id: int
    documento: str | None = Field(default=None, max_length=30)
    email_contato: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    telefone_contato: str | None = Field(default=None, max_length=30)
    administrador_nome: str = Field(min_length=2, max_length=150)
    administrador_usuario: str = Field(pattern=r"^[a-zA-Z0-9._-]{2,80}$")
    modulos_liberados: list[str] | None = None

    @field_validator("email_contato")
    @classmethod
    def email_minusculo(cls, valor: str) -> str:
        return valor.strip().lower()

    @field_validator("slug", mode="before")
    @classmethod
    def normalizar_slug(cls, valor: str) -> str:
        texto = unicodedata.normalize("NFKD", str(valor or ""))
        texto = "".join(char for char in texto if not unicodedata.combining(char))
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", texto).strip("-").lower()
        return slug[:80]


class OrganizacaoUpdate(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=180)
    documento: str | None = Field(default=None, max_length=30)
    plano_id: int | None = None
    status: str | None = Field(default=None, pattern=r"^(ativa|trial|suspensa|cancelada)$")
    assinatura_status: str | None = Field(default=None, max_length=30)
    email_contato: str | None = Field(default=None, max_length=254)
    telefone_contato: str | None = Field(default=None, max_length=30)
    branding: BrandingConfig | None = None
    modulos_liberados: list[str] | None = None
    # Fase 5 (liberacao gradual de feature flags): marca a organizacao
    # usada para testar novas flags antes de qualquer outra audiencia
    # (estagio "ambiente_interno" -- ver app.feature_flags).
    ambiente_interno: bool | None = None


class DominioInput(BaseModel):
    dominio: str = Field(min_length=3, max_length=255)

    @field_validator("dominio")
    @classmethod
    def normalizar(cls, valor: str) -> str:
        dominio = valor.strip().lower().removeprefix("https://").removeprefix("http://").split("/", 1)[0]
        if not re.fullmatch(r"[a-z0-9.-]+", dominio):
            raise ValueError("Dominio invalido")
        return dominio


class CredencialInput(BaseModel):
    nome: str = Field(min_length=2, max_length=120)


class ConviteInput(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    perfil: str = Field(
        default="operador",
        pattern=r"^(administrador|ceo|tech|gestor|analista|operador|auditor|financeiro)$",
    )
    permissoes: list[str] = []


class CobrancaSandboxInput(BaseModel):
    evento: str = Field(pattern=r"^(trial_iniciado|pagamento_aprovado|pagamento_falhou|cancelamento)$")
    dias_trial: int = Field(default=14, ge=1, le=90)


def _senha_temporaria() -> str:
    alfabeto = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alfabeto) for _ in range(20))


def _org_json(org: Organizacao, usuarios: int = 0, leads: int = 0, pesquisas: int = 0) -> dict:
    return {
        "id": org.id,
        "nome": org.nome,
        "slug": org.slug,
        "documento": org.documento,
        "status": org.status,
        "assinatura_status": org.assinatura_status,
        "email_contato": org.email_contato,
        "telefone_contato": org.telefone_contato,
        "modulos_liberados": sorted(normalizar_modulos_plano(org.modulos_liberados))
        if org.modulos_liberados is not None
        else None,
        "branding": org.branding or {},
        "ambiente_interno": org.ambiente_interno,
        "criado_em": org.criado_em,
        "trial_ate": org.trial_ate,
        "retencao_dados_dias": org.retencao_dados_dias,
        "politica_privacidade_versao": org.politica_privacidade_versao,
        "plano": {
            "id": org.plano.id,
            "nome": org.plano.nome,
            "codigo": org.plano.codigo,
            "modulos": sorted(normalizar_modulos_plano(org.plano.modulos)),
            "limites": org.plano.limites,
        },
        "uso": {"usuarios": usuarios, "leads": leads, "pesquisas": pesquisas},
    }


async def _auditar(
    session: AsyncSession,
    ator: UsuarioAutenticado,
    acao: str,
    recurso: str,
    detalhes: dict,
    *,
    before_state: dict | None = None,
) -> None:
    resource_type, _, resource_id = recurso.partition(":")
    session.add(
        criar_evento_auditoria(
            organizacao_id=ator.organizacao_id,
            actor_id=ator.id,
            ator=ator.email,
            acao=acao[:20],
            recurso=recurso,
            resource_type=resource_type,
            resource_id=resource_id or None,
            sucesso=True,
            status_http=200,
            detalhes=detalhes,
            before_state=before_state,
            after_state=detalhes or None,
        )
    )


@router.get("")
async def painel(session: SessionDep, _: SuperAdminDep) -> dict:
    organizacoes = list(
        (
            await session.execute(
                select(Organizacao).options(selectinload(Organizacao.plano)).order_by(Organizacao.nome)
            )
        ).scalars()
    )
    planos = list((await session.execute(select(PlanoSaas).order_by(PlanoSaas.nome))).scalars())
    usos = {}
    for modelo, chave in (
        (UsuarioOperacoes, "usuarios"),
        (Lead, "leads"),
        (PesquisaMarca, "pesquisas"),
    ):
        linhas = (
            await session.execute(select(modelo.organizacao_id, func.count()).group_by(modelo.organizacao_id))
        ).all()
        for org_id, total in linhas:
            usos.setdefault(org_id, {})[chave] = total
    return {
        "organizacoes": [_org_json(o, **usos.get(o.id, {})) for o in organizacoes],
        "planos": [
            {
                "id": p.id,
                "nome": p.nome,
                "codigo": p.codigo,
                "descricao": p.descricao,
                "modulos": sorted(normalizar_modulos_plano(p.modulos)),
                "limites": p.limites,
                "ativo": p.ativo,
                "stripe_price_mensal_id": p.stripe_price_mensal_id,
                "stripe_price_anual_id": p.stripe_price_anual_id,
            }
            for p in planos
        ],
    }


@router.post("/planos", status_code=status.HTTP_201_CREATED)
async def criar_plano(dados: PlanoInput, session: SessionDep, ator: SuperAdminDep) -> dict:
    if (await session.execute(select(PlanoSaas.id).where(PlanoSaas.codigo == dados.codigo))).scalar_one_or_none():
        raise HTTPException(409, "Codigo de plano ja cadastrado")
    plano = PlanoSaas(**dados.model_dump())
    session.add(plano)
    await session.flush()
    await _auditar(session, ator, "CRIAR_PLANO", f"plano:{plano.id}", dados.model_dump())
    await session.commit()
    return {"id": plano.id, "codigo": plano.codigo}


@router.patch("/planos/{plano_id}/precos-stripe")
async def configurar_precos_stripe(
    plano_id: int, dados: PlanoPrecosInput, session: SessionDep, ator: SuperAdminDep
) -> dict:
    plano = await session.get(PlanoSaas, plano_id)
    if plano is None:
        raise HTTPException(404, "Plano não encontrado")
    plano.stripe_price_mensal_id = dados.stripe_price_mensal_id.strip() if dados.stripe_price_mensal_id else None
    plano.stripe_price_anual_id = dados.stripe_price_anual_id.strip() if dados.stripe_price_anual_id else None
    await _auditar(
        session,
        ator,
        "PRECOS_STRIPE",
        f"plano:{plano.id}",
        {"mensal_configurado": bool(plano.stripe_price_mensal_id), "anual_configurado": bool(plano.stripe_price_anual_id)},
    )
    await session.commit()
    return {"id": plano.id, "mensal_configurado": bool(plano.stripe_price_mensal_id), "anual_configurado": bool(plano.stripe_price_anual_id)}


@router.post("/organizacoes", status_code=status.HTTP_201_CREATED)
async def criar_organizacao(dados: OrganizacaoInput, session: SessionDep, ator: SuperAdminDep) -> dict:
    duplicada = (
        await session.execute(
            select(Organizacao.id).where(
                or_(
                    Organizacao.slug == dados.slug,
                    Organizacao.documento == dados.documento if dados.documento else False,
                )
            )
        )
    ).scalar_one_or_none()
    if duplicada:
        raise HTTPException(409, "Organizacao ja cadastrada")
    plano = await session.get(PlanoSaas, dados.plano_id)
    if not plano or not plano.ativo:
        raise HTTPException(422, "Plano invalido")
    usuario_login = dados.administrador_usuario.lower()
    if (
        await session.execute(
            select(UsuarioOperacoes.id).where(
                or_(
                    UsuarioOperacoes.usuario == usuario_login,
                    UsuarioOperacoes.email == dados.email_contato,
                )
            )
        )
    ).scalar_one_or_none():
        raise HTTPException(409, "Usuario ou email administrativo ja cadastrado")
    org = Organizacao(
        nome=dados.nome.strip(),
        slug=dados.slug,
        documento=dados.documento,
        plano_id=dados.plano_id,
        email_contato=dados.email_contato,
        telefone_contato=dados.telefone_contato,
        modulos_liberados=dados.modulos_liberados,
        status="suspensa" if _stripe_checkout_configurado() else "ativa",
        assinatura_status="aguardando_pagamento" if _stripe_checkout_configurado() else "manual",
        billing_provider="stripe" if _stripe_checkout_configurado() else None,
    )
    session.add(org)
    await session.flush()
    # Achado FASE3-1 da auditoria (04/09/2026): organizações sem linha em
    # politicas_juridicas caem no padrão desligado (False) em
    # obter_politica_juridica -- decisão antiga (achado 5.3, 01/09/2026)
    # para não travar retroativamente quem já operava sem essas exigências.
    # Organização NOVA não tem esse problema: nasce com o padrão seguro.
    session.add(
        PoliticaJuridica(
            organizacao_id=org.id,
            exigir_evidencia_conclusao=True,
            exigir_segunda_pessoa_critico=True,
        )
    )
    senha = _senha_temporaria()
    admin = UsuarioOperacoes(
        organizacao_id=org.id,
        nome=dados.administrador_nome.strip(),
        usuario=usuario_login,
        email=dados.email_contato,
        perfil="administrador",
        senha_hash=hash_senha(senha),
        alterar_senha=True,
        criado_por=ator.email,
    )
    session.add(admin)
    await _auditar(
        session,
        ator,
        "CRIAR_ORG",
        f"organizacao:{org.id}",
        {"slug": org.slug, "plano": plano.codigo},
    )
    await session.commit()
    return {
        "organizacao_id": org.id,
        "administrador_usuario": admin.usuario,
        "senha_temporaria": senha,
    }


@router.patch("/organizacoes/{organizacao_id}")
async def atualizar_organizacao(
    organizacao_id: int,
    dados: OrganizacaoUpdate,
    session: SessionDep,
    ator: SuperAdminDep,
) -> dict:
    org = (
        await session.execute(
            select(Organizacao).options(selectinload(Organizacao.plano)).where(Organizacao.id == organizacao_id)
        )
    ).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Organizacao nao encontrada")
    alteracoes = dados.model_dump(exclude_unset=True)
    anterior = {campo: getattr(org, campo) for campo in alteracoes}
    for campo, valor in alteracoes.items():
        setattr(org, campo, valor)
    await _auditar(
        session,
        ator,
        "ALTERAR_ORG",
        f"organizacao:{org.id}",
        alteracoes,
        before_state=anterior,
    )
    await session.commit()
    return {"status": "ok"}


@router.get("/organizacoes/{organizacao_id}/onboarding")
async def status_onboarding(organizacao_id: int, session: SessionDep, ator: SuperAdminDep) -> dict:
    """Checklist operacional para ativacao de uma nova empresa SaaS."""
    org = (
        await session.execute(
            select(Organizacao).options(selectinload(Organizacao.plano)).where(Organizacao.id == organizacao_id)
        )
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organizacao nao encontrada")
    usuarios = await session.scalar(
        select(func.count())
        .select_from(UsuarioOperacoes)
        .where(
            UsuarioOperacoes.organizacao_id == organizacao_id,
            UsuarioOperacoes.ativo.is_(True),
        )
    )
    dominios = await session.scalar(
        select(func.count())
        .select_from(DominioOrganizacao)
        .where(
            DominioOrganizacao.organizacao_id == organizacao_id,
            DominioOrganizacao.ativo.is_(True),
            DominioOrganizacao.verificado_em.is_not(None),
        )
    )
    itens = {
        "cadastro": bool(org.nome and org.email_contato),
        "plano": org.plano_id is not None,
        "modulos": bool(org.modulos_liberados or (org.plano and org.plano.modulos)),
        "administrador": bool(usuarios),
        "dominio": bool(dominios),
        "branding": bool(org.branding),
    }
    concluidos = sum(itens.values())
    return {
        "organizacao_id": organizacao_id,
        "status": "concluido" if concluidos == len(itens) else "pendente",
        "progresso": {"concluidos": concluidos, "total": len(itens)},
        "itens": itens,
        "proximo_passo": next((nome for nome, ok in itens.items() if not ok), None),
    }


@router.post("/organizacoes/{organizacao_id}/acesso")
async def gerar_acesso_administrador(organizacao_id: int, session: SessionDep, ator: SuperAdminDep) -> dict:
    """Regenera o acesso do administrador de uma organização cadastrada."""
    org = await session.get(Organizacao, organizacao_id)
    if not org:
        raise HTTPException(404, "Organizacao nao encontrada")
    administrador = (
        (
            await session.execute(
                select(UsuarioOperacoes)
                .where(
                    UsuarioOperacoes.organizacao_id == organizacao_id,
                    UsuarioOperacoes.perfil == "administrador",
                    UsuarioOperacoes.ativo.is_(True),
                )
                .order_by(UsuarioOperacoes.id)
            )
        )
        .scalars()
        .first()
    )
    if not administrador:
        raise HTTPException(404, "Administrador ativo nao encontrado")
    senha = _senha_temporaria()
    administrador.senha_hash = hash_senha(senha)
    administrador.alterar_senha = True
    await session.execute(
        update(SessaoOperacoes)
        .where(
            SessaoOperacoes.usuario_id == administrador.id,
            SessaoOperacoes.revogada_em.is_(None),
        )
        .values(revogada_em=datetime.now(UTC), motivo_revogacao="acesso_regenerado_superadmin")
    )
    await _auditar(
        session,
        ator,
        "REGERAR_ACESSO",
        f"organizacao:{organizacao_id}",
        {"usuario_id": administrador.id},
    )
    await session.commit()
    return {
        "organizacao_id": organizacao_id,
        "usuario": administrador.usuario,
        "email": administrador.email,
        "senha_temporaria": senha,
        "login_path": "/login",
    }


@router.post("/organizacoes/{organizacao_id}/dominios", status_code=201)
async def adicionar_dominio(
    organizacao_id: int,
    dados: DominioInput,
    session: SessionDep,
    ator: SuperAdminDep,
) -> dict:
    if not await session.get(Organizacao, organizacao_id):
        raise HTTPException(404, "Organizacao nao encontrada")
    if (
        await session.execute(select(DominioOrganizacao.id).where(DominioOrganizacao.dominio == dados.dominio))
    ).scalar_one_or_none():
        raise HTTPException(409, "Dominio ja cadastrado")
    codigo = secrets.token_urlsafe(24)
    item = DominioOrganizacao(
        organizacao_id=organizacao_id,
        dominio=dados.dominio,
        # O domínio não passa a resolver tenants até a propriedade ser
        # comprovada pelo TXT; evitar associação acidental de host não verificado.
        ativo=False,
        codigo_verificacao=codigo,
    )
    session.add(item)
    await session.flush()
    await _auditar(session, ator, "CRIAR_DOMINIO", f"organizacao:{organizacao_id}", {"dominio": dados.dominio})
    await session.commit()
    return {
        "id": item.id,
        "dominio": dados.dominio,
        "status": "pendente_verificacao",
        "registro_txt": f"_ze-registra.{dados.dominio}",
        "valor_txt": f"ze-registra-verification={codigo}",
    }


@router.post("/organizacoes/{organizacao_id}/credenciais", status_code=201)
async def criar_credencial(
    organizacao_id: int,
    dados: CredencialInput,
    session: SessionDep,
    ator: SuperAdminDep,
) -> dict:
    if not await session.get(Organizacao, organizacao_id):
        raise HTTPException(404, "Organizacao nao encontrada")
    token = f"zr_live_{secrets.token_urlsafe(36)}"
    credencial = CredencialIntegracao(
        organizacao_id=organizacao_id,
        nome=dados.nome,
        token_prefixo=token[:16],
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        criado_por=ator.email,
    )
    session.add(credencial)
    await _auditar(session, ator, "CRIAR_CREDENCIAL", f"organizacao:{organizacao_id}", {"nome": dados.nome})
    await session.commit()
    return {"token": token, "aviso": "Copie agora; o token nao sera exibido novamente."}


@router.get("/organizacoes/{organizacao_id}/credenciais")
async def listar_credenciais(
    organizacao_id: int,
    session: SessionDep,
    _: SuperAdminDep,
) -> list[dict]:
    itens = (
        await session.execute(
            select(CredencialIntegracao)
            .where(CredencialIntegracao.organizacao_id == organizacao_id)
            .order_by(CredencialIntegracao.criado_em.desc())
        )
    ).scalars()
    return [
        {
            "id": x.id,
            "nome": x.nome,
            "prefixo": x.token_prefixo,
            "ativo": x.ativo,
            "ultimo_uso_em": x.ultimo_uso_em,
            "expira_em": x.expira_em,
            "revoked_at": x.revoked_at,
        }
        for x in itens
    ]


@router.delete("/organizacoes/{organizacao_id}/credenciais/{credencial_id}")
async def revogar_credencial(
    organizacao_id: int,
    credencial_id: int,
    session: SessionDep,
    ator: SuperAdminDep,
) -> dict:
    item = (
        await session.execute(
            select(CredencialIntegracao).where(
                CredencialIntegracao.id == credencial_id,
                CredencialIntegracao.organizacao_id == organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Credencial não encontrada")
    item.ativo = False
    item.revoked_at = datetime.now(UTC)
    await _auditar(session, ator, "REVOGAR_CHAVE", f"credencial:{item.id}", {})
    await session.commit()
    return {"status": "revogada"}


@router.post("/organizacoes/{organizacao_id}/convites", status_code=201)
async def criar_convite(
    organizacao_id: int,
    dados: ConviteInput,
    session: SessionDep,
    ator: SuperAdminDep,
) -> dict:
    if not await session.get(Organizacao, organizacao_id):
        raise HTTPException(404, "Organização não encontrada")
    token = secrets.token_urlsafe(48)
    convite = ConviteOrganizacao(
        organizacao_id=organizacao_id,
        email=dados.email.strip().lower(),
        perfil=dados.perfil,
        permissoes=dados.permissoes,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expira_em=datetime.now(UTC) + timedelta(days=7),
        criado_por=ator.email,
    )
    session.add(convite)
    await _auditar(session, ator, "CRIAR_CONVITE", f"organizacao:{organizacao_id}", {"email": convite.email})
    await session.commit()
    return {"status": "criado", "token_teste_local": token, "expira_em": convite.expira_em}


def _consultar_txt(nome: str) -> list[str]:
    import dns.resolver

    return [str(registro).strip('"') for registro in dns.resolver.resolve(nome, "TXT")]


@router.post("/organizacoes/{organizacao_id}/dominios/{dominio_id}/verificar")
async def verificar_dominio(
    organizacao_id: int,
    dominio_id: int,
    session: SessionDep,
    ator: SuperAdminDep,
) -> dict:
    item = (
        await session.execute(
            select(DominioOrganizacao).where(
                DominioOrganizacao.id == dominio_id,
                DominioOrganizacao.organizacao_id == organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Domínio não encontrado")
    esperado = f"ze-registra-verification={item.codigo_verificacao}"
    try:
        valores = await asyncio.to_thread(_consultar_txt, f"_ze-registra.{item.dominio}")
    except Exception as exc:
        raise HTTPException(422, f"Registro TXT ainda não encontrado: {type(exc).__name__}") from exc
    if esperado not in valores:
        raise HTTPException(422, "Registro TXT não corresponde ao código esperado")
    item.verificado_em = datetime.now(UTC)
    item.ativo = True
    await _auditar(session, ator, "VERIFICAR_DOMINIO", f"dominio:{item.id}", {})
    await session.commit()
    return {"status": "verificado", "verificado_em": item.verificado_em}


@router.post("/organizacoes/{organizacao_id}/cobranca-sandbox")
async def simular_cobranca(
    organizacao_id: int,
    dados: CobrancaSandboxInput,
    session: SessionDep,
    ator: SuperAdminDep,
) -> dict:
    org = await session.get(Organizacao, organizacao_id)
    if not org:
        raise HTTPException(404, "Organização não encontrada")
    agora = datetime.now(UTC)
    status_por_evento = {
        "trial_iniciado": ("trial", "trial"),
        "pagamento_aprovado": ("ativa", "ativa"),
        "pagamento_falhou": ("suspensa", "inadimplente"),
        "cancelamento": ("cancelada", "cancelada"),
    }
    org.status, org.assinatura_status = status_por_evento[dados.evento]
    org.trial_ate = agora + timedelta(days=dados.dias_trial) if dados.evento == "trial_iniciado" else None
    evento = EventoCobrancaSandbox(
        organizacao_id=org.id,
        tipo=dados.evento,
        status=org.assinatura_status,
        referencia=f"sandbox_{secrets.token_hex(12)}",
        detalhes={"dias_trial": dados.dias_trial},
    )
    session.add(evento)
    await _auditar(session, ator, "COBRANCA_TESTE", f"organizacao:{org.id}", {"evento": dados.evento})
    await session.commit()
    return {
        "status": org.status,
        "assinatura_status": org.assinatura_status,
        "trial_ate": org.trial_ate,
    }


class CheckoutSaasInput(BaseModel):
    intervalo: str = Field(pattern=r"^(mensal|anual)$")


def _validar_assinatura_stripe(corpo: bytes, assinatura: str, segredo: str, agora: int) -> bool:
    partes = {}
    for item in assinatura.split(","):
        chave, separador, valor = item.partition("=")
        if separador:
            partes.setdefault(chave, []).append(valor)
    try:
        timestamp = int(partes["t"][0])
    except (KeyError, ValueError, IndexError):
        return False
    if abs(agora - timestamp) > 300:
        return False
    signed = str(timestamp).encode() + b"." + corpo
    esperado = hmac.new(segredo.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(esperado, valor) for valor in partes.get("v1", []))


@router.post("/organizacoes/{organizacao_id}/checkout")
async def criar_checkout_saas(
    organizacao_id: int,
    dados: CheckoutSaasInput,
    session: SessionDep,
    ator: SuperAdminDep,
) -> dict:
    """Gera link Stripe Checkout para contratação assistida pela equipe."""
    from app.settings import get_settings

    cfg = get_settings()
    if not cfg.stripe_saas_enabled or not cfg.stripe_saas_secret_key:
        raise HTTPException(503, "Cobrança Stripe do SaaS não está configurada")
    org = (
        await session.execute(select(Organizacao).options(selectinload(Organizacao.plano)).where(Organizacao.id == organizacao_id))
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(404, "Organização não encontrada")
    price_id = org.plano.stripe_price_mensal_id if dados.intervalo == "mensal" else org.plano.stripe_price_anual_id
    if not price_id:
        raise HTTPException(422, f"O plano não possui preço Stripe {dados.intervalo} configurado")
    campos = {
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": cfg.stripe_saas_success_url,
        "cancel_url": cfg.stripe_saas_cancel_url,
        "client_reference_id": str(org.id),
        "metadata[organization_id]": str(org.id),
        "metadata[billing_interval]": dados.intervalo,
        "subscription_data[metadata][organization_id]": str(org.id),
        "subscription_data[metadata][billing_interval]": dados.intervalo,
    }
    if org.billing_customer_id:
        campos["customer"] = org.billing_customer_id
    else:
        campos["customer_email"] = org.email_contato or ""
    campos = {k: v for k, v in campos.items() if v}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resposta = await client.post(
                "https://api.stripe.com/v1/checkout/sessions",
                data=campos,
                auth=(cfg.stripe_saas_secret_key, ""),
                headers={"Idempotency-Key": f"zeregistra-org-{org.id}-{dados.intervalo}-{int(datetime.now(UTC).timestamp() // 3600)}"},
            )
        resposta.raise_for_status()
        checkout = resposta.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Não foi possível criar a sessão de pagamento no Stripe") from exc
    if not checkout.get("url") or not checkout.get("id"):
        raise HTTPException(502, "Stripe não retornou uma sessão Checkout válida")
    org.billing_provider = "stripe"
    org.billing_intervalo = dados.intervalo
    org.assinatura_status = "checkout_pendente"
    await _auditar(session, ator, "CRIAR_CHECKOUT", f"organizacao:{org.id}", {"intervalo": dados.intervalo})
    await session.commit()
    return {"checkout_url": checkout["url"], "session_id": checkout["id"]}


@router.post("/webhooks/stripe", include_in_schema=False)
async def webhook_assinatura_stripe(
    request: Request,
    session: SessionDep,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict:
    """Webhook sem sessão; valida HMAC Stripe e deduplica por event id."""
    from app.settings import get_settings

    cfg = get_settings()
    if not cfg.stripe_saas_enabled or not cfg.stripe_saas_webhook_secret:
        raise HTTPException(503, "Webhook Stripe do SaaS não está configurado")
    corpo = await request.body()
    if not stripe_signature or not _validar_assinatura_stripe(
        corpo, stripe_signature, cfg.stripe_saas_webhook_secret, int(datetime.now(UTC).timestamp())
    ):
        raise HTTPException(400, "Assinatura Stripe inválida")
    # This exact route is authenticated by Stripe's HMAC signature instead
    # of an operator session; set the DB RLS context only after verification.
    session.info["superadmin"] = True
    try:
        evento = json.loads(corpo)
        event_id, tipo = str(evento["id"]), str(evento["type"])
        objeto = evento["data"]["object"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, "Evento Stripe inválido") from exc
    if await session.get(EventoAssinaturaStripe, event_id):
        return {"status": "duplicado"}
    item = EventoAssinaturaStripe(id=event_id, tipo=tipo)
    session.add(item)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return {"status": "duplicado"}
    metadata = objeto.get("metadata") or {}
    org_id = metadata.get("organization_id") or objeto.get("client_reference_id")
    org = None
    if org_id:
        org = (
            await session.execute(select(Organizacao).where(Organizacao.id == int(org_id)).with_for_update())
        ).scalar_one_or_none()
    if org is None and objeto.get("customer"):
        org = (
            await session.execute(
                select(Organizacao).where(Organizacao.billing_customer_id == objeto["customer"]).with_for_update()
            )
        ).scalar_one_or_none()
    if org is None:
        # Retain no raw payload/PII; acknowledge unrelated events to avoid retries.
        await session.commit()
        return {"status": "ignorado"}
    customer_id = objeto.get("customer")
    if customer_id:
        org.billing_customer_id = customer_id
    subscription_id = objeto.get("subscription") if tipo.startswith("checkout.session.") else objeto.get("id")
    if tipo in {"checkout.session.completed", "customer.subscription.created", "customer.subscription.updated"}:
        if subscription_id and tipo.startswith("checkout.session."):
            org.billing_subscription_id = subscription_id
            org.assinatura_status = "ativa" if objeto.get("payment_status") == "paid" else "checkout_concluido"
            org.billing_intervalo = metadata.get("billing_interval") or org.billing_intervalo
            if objeto.get("payment_status") == "paid":
                org.status = "ativa"
        elif tipo.startswith("customer.subscription."):
            org.billing_subscription_id = subscription_id
            status_remoto = objeto.get("status", "unknown")
            org.assinatura_status = status_remoto[:30]
            if status_remoto in {"active", "trialing"}:
                org.status = "ativa"
            # past_due/canceled/unpaid do not automatically suspend the tenant;
            # suspension policy/grace period requires an explicit product decision.
            org.billing_intervalo = metadata.get("billing_interval") or org.billing_intervalo
    elif tipo == "invoice.paid":
        org.assinatura_status = "ativa"
        org.status = "ativa"
    elif tipo == "invoice.payment_failed":
        org.assinatura_status = "past_due"
    elif tipo == "customer.subscription.deleted":
        org.assinatura_status = "canceled"
    await session.commit()
    return {"status": "processado"}


@router.post("/assinaturas/verificar")
async def verificar_assinaturas(session: SessionDep, ator: SuperAdminDep) -> dict:
    agora = datetime.now(UTC)
    expiradas = list(
        (
            await session.execute(
                select(Organizacao).where(
                    Organizacao.status == "trial",
                    Organizacao.trial_ate < agora,
                    Organizacao.suspender_automaticamente.is_(True),
                )
            )
        ).scalars()
    )
    for org in expiradas:
        org.status = "suspensa"
        org.assinatura_status = "trial_expirado"
        session.add(
            AlertaSistema(
                organizacao_id=org.id,
                severidade="aviso",
                codigo="TRIAL_EXPIRADO",
                mensagem="Período de teste expirou; organização suspensa automaticamente.",
            )
        )
    await _auditar(session, ator, "VERIFICAR_PLANOS", "assinaturas", {"suspensas": len(expiradas)})
    await session.commit()
    return {"suspensas": len(expiradas)}


@router.get("/uso")
async def relatorio_uso(session: SessionDep, _: SuperAdminDep) -> list[dict]:
    orgs = list((await session.execute(select(Organizacao).order_by(Organizacao.nome))).scalars())
    saida = []
    for org in orgs:
        usuarios = (
            await session.execute(
                select(func.count()).select_from(UsuarioOperacoes).where(UsuarioOperacoes.organizacao_id == org.id)
            )
        ).scalar_one()
        leads = (
            await session.execute(select(func.count()).select_from(Lead).where(Lead.organizacao_id == org.id))
        ).scalar_one()
        pesquisas = (
            await session.execute(
                select(func.count()).select_from(PesquisaMarca).where(PesquisaMarca.organizacao_id == org.id)
            )
        ).scalar_one()
        saida.append(
            {
                "organizacao_id": org.id,
                "nome": org.nome,
                "usuarios": usuarios,
                "leads": leads,
                "pesquisas": pesquisas,
                "status": org.status,
            }
        )
    return saida


@router.get("/alertas")
async def listar_alertas(session: SessionDep, _: SuperAdminDep) -> list[dict]:
    itens = (
        await session.execute(
            select(AlertaSistema)
            .where(AlertaSistema.resolvido_em.is_(None))
            .order_by(AlertaSistema.criado_em.desc())
            .limit(100)
        )
    ).scalars()
    return [
        {
            "id": x.id,
            "organizacao_id": x.organizacao_id,
            "severidade": x.severidade,
            "codigo": x.codigo,
            "mensagem": x.mensagem,
            "criado_em": x.criado_em,
        }
        for x in itens
    ]
