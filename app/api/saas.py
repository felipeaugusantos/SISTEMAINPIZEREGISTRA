import asyncio
import hashlib
import re
import secrets
import string
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, update
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


class PlanoInput(BaseModel):
    nome: str = Field(min_length=2, max_length=100)
    codigo: str = Field(pattern=r"^[a-z0-9-]{2,50}$")
    descricao: str | None = Field(default=None, max_length=500)
    modulos: list[str] = []
    limites: dict[str, int] = {}


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


class PrivacidadeInput(BaseModel):
    retencao_dados_dias: int = Field(ge=30, le=3650)
    politica_privacidade_versao: str = Field(min_length=1, max_length=30)


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
        status="ativa",
        assinatura_status="manual",
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
        ativo=True,
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
