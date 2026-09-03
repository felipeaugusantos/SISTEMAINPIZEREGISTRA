"""Radar de Prospecção -- Fase 1 (estrutura de Prospect).

docs/arquitetura-radar-prospeccao-2026-09-03.md: só CRUD, importação manual
e conversão em Lead. Coleta automática (Fase 2), enriquecimento (Fase 3),
triagem de marca (Fase 4) e score (Fase 5) entram depois, cada um com sua
própria migração -- esta fase não antecipa colunas que nenhum código ainda
preenche.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.leads import _garantir_proxima_acao_padrao
from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.crm import registrar_consentimento_operador
from app.database import get_session
from app.importacao_planilha import TAMANHO_MAXIMO_IMPORTACAO, ler_planilha, valor_coluna
from app.models import (
    CampanhaProspeccao,
    EmpresaCRM,
    EventoAuditoria,
    HistoricoStatusProspect,
    Lead,
    Prospect,
    ProspectEnriquecimento,
    ProspectFonte,
    ProspectTriagem,
    StatusLead,
    StatusProspect,
)
from app.prospeccao_triagem import DISCLAIMER_TRIAGEM
from app.proxy import cliente_ip
from app.queueing import enfileirar
from app.schemas import (
    CampanhaProspeccaoCreate,
    CampanhaProspeccaoListResponse,
    CampanhaProspeccaoResponse,
    ProspectCreate,
    ProspectListResponse,
    ProspectResponse,
    ProspectStatusUpdate,
)

router = APIRouter(prefix="/v1/admin/prospects", tags=["prospeccao"])
router_campanhas = APIRouter(prefix="/v1/admin/prospeccao", tags=["prospeccao"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProspeccaoViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("prospeccao.view"))]
ProspeccaoManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("prospeccao.manage"))]
ProspeccaoConvertDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("prospeccao.convert"))]

MOTIVOS_DESCARTE_PROSPECT: tuple[str, ...] = (
    "ja_e_cliente",
    "fora_do_perfil",
    "sem_contato_valido",
    "cnae_incompativel",
    "outro",
)


def _auditar(
    session: AsyncSession,
    request: Request,
    usuario: UsuarioAutenticado,
    acao: str,
    recurso: str,
    detalhes: dict,
) -> None:
    session.add(
        EventoAuditoria(
            organizacao_id=usuario.organizacao_id,
            actor_id=usuario.id,
            ator=usuario.ator,
            acao=acao[:20],
            recurso=recurso[:180],
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes=detalhes,
        )
    )


def _prospect_response(prospect: Prospect) -> ProspectResponse:
    dados = ProspectResponse.model_validate(prospect)
    dados.responsavel_nome = getattr(getattr(prospect, "responsavel", None), "nome", None)
    return dados


def _digitos(valor: str | None) -> str:
    return "".join(c for c in (valor or "") if c.isdigit())


async def _buscar_prospect(session: AsyncSession, prospect_id: int, organizacao_id: int) -> Prospect:
    prospect = (
        await session.execute(
            select(Prospect).where(Prospect.id == prospect_id, Prospect.organizacao_id == organizacao_id)
        )
    ).scalar_one_or_none()
    if prospect is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prospect não encontrado")
    return prospect


async def _prospect_duplicado(
    session: AsyncSession, organizacao_id: int, cnpj: str | None, telefone_digitos: str, email: str
) -> Prospect | None:
    """Camada 1 de dedup (seção B9): só contra prospects ainda "novo" -- os
    já resolvidos (rejeitado/duplicado/convertido) não bloqueiam reentrada."""
    condicoes = []
    if cnpj:
        condicoes.append(Prospect.cnpj == cnpj)
    if email:
        condicoes.append(func.lower(Prospect.email) == email)
    if telefone_digitos:
        condicoes.append(func.regexp_replace(Prospect.telefone, r"\D", "", "g") == telefone_digitos)
    if not condicoes:
        return None
    return (
        await session.execute(
            select(Prospect)
            .where(Prospect.organizacao_id == organizacao_id, Prospect.status == StatusProspect.NOVO, or_(*condicoes))
            .limit(1)
        )
    ).scalar_one_or_none()


async def _correspondencia_crm(
    session: AsyncSession, organizacao_id: int, cnpj: str | None, telefone_digitos: str, email: str
) -> tuple[int | None, int | None]:
    """Camada 2 de dedup: casa contra Lead/EmpresaCRM já existentes, para o
    prospect nascer sinalizado em vez de reabordar quem já é lead/cliente."""
    lead_id: int | None = None
    empresa_crm_id: int | None = None
    condicoes_lead = []
    if email:
        condicoes_lead.append(func.lower(Lead.email) == email)
    if telefone_digitos:
        condicoes_lead.append(func.regexp_replace(Lead.telefone, r"\D", "", "g") == telefone_digitos)
    if cnpj:
        condicoes_lead.append(func.regexp_replace(Lead.documento, r"\D", "", "g") == cnpj)
    if condicoes_lead:
        lead_id = (
            await session.execute(
                select(Lead.id)
                .where(Lead.organizacao_id == organizacao_id, Lead.arquivado_em.is_(None), or_(*condicoes_lead))
                .limit(1)
            )
        ).scalar_one_or_none()
    if cnpj:
        empresa_crm_id = (
            await session.execute(
                select(EmpresaCRM.id)
                .where(
                    EmpresaCRM.organizacao_id == organizacao_id,
                    func.regexp_replace(EmpresaCRM.documento, r"\D", "", "g") == cnpj,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return lead_id, empresa_crm_id


async def _criar_prospect(
    session: AsyncSession,
    organizacao_id: int,
    dados: ProspectCreate,
    por: str,
    *,
    fonte_id: int | None = None,
    campanha_id: int | None = None,
) -> tuple[Prospect, bool]:
    """Cria um Prospect aplicando as duas camadas de dedup. Devolve
    (prospect, criado) -- criado=False quando reaproveitou um já existente."""
    telefone_digitos = _digitos(dados.telefone)
    email = (dados.email or "").lower()
    existente = await _prospect_duplicado(session, organizacao_id, dados.cnpj, telefone_digitos, email)
    if existente is not None:
        return existente, False

    lead_id, empresa_crm_id = await _correspondencia_crm(session, organizacao_id, dados.cnpj, telefone_digitos, email)
    prospect = Prospect(
        organizacao_id=organizacao_id,
        razao_social=dados.razao_social,
        nome_fantasia=dados.nome_fantasia,
        cnpj=dados.cnpj,
        cnae_principal=dados.cnae_principal,
        cnaes_secundarios=dados.cnaes_secundarios,
        porte=dados.porte,
        situacao_cadastral=dados.situacao_cadastral,
        data_abertura=dados.data_abertura,
        uf=dados.uf,
        cidade=dados.cidade,
        telefone=dados.telefone,
        email=dados.email,
        site=dados.site,
        status=StatusProspect.NOVO.value,
        empresa_crm_id=empresa_crm_id,
        fonte_id=fonte_id,
        campanha_id=campanha_id,
    )
    session.add(prospect)
    await session.flush()
    session.add(
        HistoricoStatusProspect(
            organizacao_id=organizacao_id, prospect_id=prospect.id, status=StatusProspect.NOVO.value, por=por
        )
    )
    return prospect, True


@router.get("", response_model=ProspectListResponse)
async def listar_prospects(
    session: SessionDep,
    usuario: ProspeccaoViewDep,
    busca: Annotated[str | None, Query(max_length=200)] = None,
    status_prospect: Annotated[str | None, Query(alias="status")] = None,
    uf: Annotated[str | None, Query(max_length=2)] = None,
    cidade: Annotated[str | None, Query(max_length=120)] = None,
    cnae_principal: Annotated[str | None, Query(max_length=10)] = None,
    responsavel_id: Annotated[int | None, Query(ge=1)] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
) -> ProspectListResponse:
    filtros = [Prospect.organizacao_id == usuario.organizacao_id]
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(or_(Prospect.razao_social.ilike(termo), Prospect.nome_fantasia.ilike(termo), Prospect.cnpj.ilike(termo)))
    if status_prospect:
        filtros.append(Prospect.status == status_prospect)
    if uf:
        filtros.append(Prospect.uf == uf.upper())
    if cidade:
        filtros.append(Prospect.cidade.ilike(f"%{cidade.strip()}%"))
    if cnae_principal:
        filtros.append(Prospect.cnae_principal == cnae_principal)
    if responsavel_id:
        filtros.append(Prospect.responsavel_id == responsavel_id)

    total = (await session.execute(select(func.count()).select_from(Prospect).where(*filtros))).scalar_one()
    itens = (
        (
            await session.execute(
                select(Prospect)
                .where(*filtros)
                .order_by(Prospect.criado_em.desc(), Prospect.id.desc())
                .limit(limite)
                .offset(deslocamento)
            )
        )
        .scalars()
        .all()
    )
    return ProspectListResponse(
        total=total, limite=limite, deslocamento=deslocamento, itens=[_prospect_response(item) for item in itens]
    )


@router.get("/{prospect_id}", response_model=ProspectResponse)
async def detalhar_prospect(prospect_id: int, session: SessionDep, usuario: ProspeccaoViewDep) -> ProspectResponse:
    prospect = await _buscar_prospect(session, prospect_id, usuario.organizacao_id)
    return _prospect_response(prospect)


@router.get("/{prospect_id}/timeline")
async def timeline_prospect(prospect_id: int, session: SessionDep, usuario: ProspeccaoViewDep) -> dict:
    await _buscar_prospect(session, prospect_id, usuario.organizacao_id)
    eventos = (
        (
            await session.execute(
                select(HistoricoStatusProspect)
                .where(
                    HistoricoStatusProspect.organizacao_id == usuario.organizacao_id,
                    HistoricoStatusProspect.prospect_id == prospect_id,
                )
                .order_by(HistoricoStatusProspect.entrou_em.asc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [
            {"status": evento.status, "entrou_em": evento.entrou_em, "por": evento.por} for evento in eventos
        ]
    }


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProspectResponse)
async def criar_prospect(
    dados: ProspectCreate, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> ProspectResponse:
    prospect, criado = await _criar_prospect(session, usuario.organizacao_id, dados, usuario.nome or "sistema")
    _auditar(
        session,
        request,
        usuario,
        "criar_prospect",
        f"prospect:{prospect.id}",
        {"criado": criado, "razao_social": dados.razao_social},
    )
    await session.commit()
    await session.refresh(prospect)
    return _prospect_response(prospect)


@router.patch("/{prospect_id}", response_model=ProspectResponse)
async def atualizar_status_prospect(
    prospect_id: int, dados: ProspectStatusUpdate, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> ProspectResponse:
    prospect = await _buscar_prospect(session, prospect_id, usuario.organizacao_id)
    if prospect.status != StatusProspect.NOVO.value:
        raise HTTPException(422, f"Prospect já foi processado (status atual: {prospect.status}).")
    if dados.motivo_descarte not in MOTIVOS_DESCARTE_PROSPECT:
        raise HTTPException(422, f"Motivo de descarte inválido. Use um de: {', '.join(MOTIVOS_DESCARTE_PROSPECT)}.")
    prospect.status = StatusProspect.REJEITADO.value
    prospect.motivo_descarte = dados.motivo_descarte
    session.add(
        HistoricoStatusProspect(
            organizacao_id=usuario.organizacao_id,
            prospect_id=prospect.id,
            status=StatusProspect.REJEITADO.value,
            por=usuario.nome or "sistema",
        )
    )
    _auditar(
        session,
        request,
        usuario,
        "rejeitar_prospect",
        f"prospect:{prospect.id}",
        {"motivo_descarte": dados.motivo_descarte},
    )
    await session.commit()
    await session.refresh(prospect)
    return _prospect_response(prospect)


COLUNAS_RAZAO_SOCIAL = ("razaosocial", "razao", "nome", "empresa")
COLUNAS_NOME_FANTASIA = ("fantasia", "apelido")
COLUNAS_CNPJ = ("cnpj",)
COLUNAS_CNAE = ("cnae",)
COLUNAS_PORTE = ("porte",)
COLUNAS_UF = ("uf", "estado")
COLUNAS_CIDADE = ("cidade", "municipio")
COLUNAS_TELEFONE_PROSPECT = ("telefone", "fone", "celular", "whatsapp")
COLUNAS_EMAIL_PROSPECT = ("email",)
COLUNAS_SITE = ("site", "website")


@router.post("/importar", status_code=status.HTTP_201_CREATED)
async def importar_prospects(
    request: Request, session: SessionDep, usuario: ProspeccaoManageDep, arquivo: Annotated[UploadFile, File()]
) -> dict:
    """Importação manual (CSV/XLSX) de prospects -- fonte externa "importacao_manual".

    Colunas reconhecidas (cabeçalho, sem acento/maiúsculas): razaosocial
    (obrigatória), fantasia, cnpj, cnae, porte, uf, cidade, telefone, email, site.
    """
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(400, "Arquivo vazio.")
    if len(conteudo) > TAMANHO_MAXIMO_IMPORTACAO:
        raise HTTPException(413, "Arquivo muito grande (máximo 5 MB).")
    registros = ler_planilha(conteudo, arquivo.filename or "")
    if not registros:
        raise HTTPException(400, "Planilha vazia ou sem cabeçalho reconhecível. Inclua uma coluna 'razaosocial'.")

    criados = 0
    duplicados = 0
    invalidos = 0
    for registro in registros:
        razao_social = valor_coluna(registro, COLUNAS_RAZAO_SOCIAL)
        if not razao_social:
            invalidos += 1
            continue
        try:
            dados = ProspectCreate(
                razao_social=razao_social,
                nome_fantasia=valor_coluna(registro, COLUNAS_NOME_FANTASIA),
                cnpj=valor_coluna(registro, COLUNAS_CNPJ),
                cnae_principal=valor_coluna(registro, COLUNAS_CNAE),
                porte=valor_coluna(registro, COLUNAS_PORTE),
                uf=valor_coluna(registro, COLUNAS_UF),
                cidade=valor_coluna(registro, COLUNAS_CIDADE),
                telefone=valor_coluna(registro, COLUNAS_TELEFONE_PROSPECT),
                email=valor_coluna(registro, COLUNAS_EMAIL_PROSPECT),
                site=valor_coluna(registro, COLUNAS_SITE),
            )
        except ValueError:
            invalidos += 1
            continue
        _, criado = await _criar_prospect(session, usuario.organizacao_id, dados, usuario.nome or "sistema")
        if criado:
            criados += 1
        else:
            duplicados += 1

    resultado = {"total_linhas": len(registros), "criados": criados, "duplicados": duplicados, "invalidos": invalidos}
    _auditar(session, request, usuario, "importar_prospects", f"prospects:importacao:{arquivo.filename}", resultado)
    await session.commit()
    return resultado


@router.post("/{prospect_id}/converter-lead", status_code=status.HTTP_201_CREATED)
async def converter_prospect_em_lead(
    prospect_id: int, request: Request, session: SessionDep, usuario: ProspeccaoConvertDep
) -> dict:
    prospect = await _buscar_prospect(session, prospect_id, usuario.organizacao_id)
    if prospect.status != StatusProspect.NOVO.value:
        raise HTTPException(422, f"Prospect já foi processado (status atual: {prospect.status}).")
    if not prospect.email and not prospect.telefone:
        raise HTTPException(422, "Prospect sem e-mail ou telefone não pode virar lead.")

    email = (prospect.email or "").lower()
    telefone_digitos = _digitos(prospect.telefone)
    condicoes = []
    if email:
        condicoes.append(func.lower(Lead.email) == email)
    if telefone_digitos:
        condicoes.append(func.regexp_replace(Lead.telefone, r"\D", "", "g") == telefone_digitos)
    existente = (
        await session.execute(
            select(Lead)
            .where(Lead.organizacao_id == usuario.organizacao_id, Lead.arquivado_em.is_(None), or_(*condicoes))
            .limit(1)
        )
    ).scalar_one_or_none()

    if existente is not None:
        lead = existente
        criado_novo = False
    else:
        lead = Lead(
            organizacao_id=usuario.organizacao_id,
            nome=prospect.razao_social,
            email=prospect.email or "",
            telefone=prospect.telefone or "",
            empresa=prospect.nome_fantasia or prospect.razao_social,
            documento=prospect.cnpj,
            marca="",
            origem="prospeccao",
            aceite_privacidade=True,
            status=StatusLead.NOVO,
            responsavel_id=prospect.responsavel_id,
        )
        registrar_consentimento_operador(lead, usuario.id)
        session.add(lead)
        await _garantir_proxima_acao_padrao(session, lead)
        criado_novo = True

    await session.flush()
    prospect.lead_id = lead.id
    prospect.status = StatusProspect.CONVERTIDO_LEAD.value
    session.add(
        HistoricoStatusProspect(
            organizacao_id=usuario.organizacao_id,
            prospect_id=prospect.id,
            status=StatusProspect.CONVERTIDO_LEAD.value,
            por=usuario.nome or "sistema",
        )
    )
    _auditar(
        session,
        request,
        usuario,
        "converter_prospect",
        f"prospect:{prospect.id}",
        {"lead_id": lead.id, "criado_novo": criado_novo},
    )
    await session.commit()
    return {"lead_id": lead.id, "criado_novo": criado_novo}


# --- Fase 2 do Radar de Prospecção (03/09/2026) -- fontes e campanhas ------
#
# A fonte "cnae_publico" (Dados Abertos do CNPJ/RFB) não precisa ser criada
# manualmente pelo usuário -- é a única fonte automática que existe por
# enquanto, então nasce sozinha na primeira campanha da organização.


async def obter_ou_criar_fonte_cnae_publico(session: AsyncSession, organizacao_id: int) -> ProspectFonte:
    fonte = (
        await session.execute(
            select(ProspectFonte).where(
                ProspectFonte.organizacao_id == organizacao_id, ProspectFonte.tipo == "cnae_publico"
            )
        )
    ).scalar_one_or_none()
    if fonte is None:
        fonte = ProspectFonte(
            organizacao_id=organizacao_id, tipo="cnae_publico", nome="Dados Abertos do CNPJ (Receita Federal)"
        )
        session.add(fonte)
        await session.flush()
    return fonte


async def _contagem_prospects_por_campanha(session: AsyncSession, organizacao_id: int, campanha_ids: list[int]) -> dict[int, int]:
    if not campanha_ids:
        return {}
    linhas = await session.execute(
        select(Prospect.campanha_id, func.count())
        .where(Prospect.organizacao_id == organizacao_id, Prospect.campanha_id.in_(campanha_ids))
        .group_by(Prospect.campanha_id)
    )
    return dict(linhas.all())


@router_campanhas.get("/campanhas", response_model=CampanhaProspeccaoListResponse)
async def listar_campanhas(session: SessionDep, usuario: ProspeccaoViewDep) -> CampanhaProspeccaoListResponse:
    total = (
        await session.execute(
            select(func.count()).select_from(CampanhaProspeccao).where(CampanhaProspeccao.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one()
    campanhas = (
        (
            await session.execute(
                select(CampanhaProspeccao)
                .where(CampanhaProspeccao.organizacao_id == usuario.organizacao_id)
                .order_by(CampanhaProspeccao.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    contagens = await _contagem_prospects_por_campanha(session, usuario.organizacao_id, [c.id for c in campanhas])
    itens = []
    for campanha in campanhas:
        dados = CampanhaProspeccaoResponse.model_validate(campanha)
        dados.prospects_gerados = contagens.get(campanha.id, 0)
        itens.append(dados)
    return CampanhaProspeccaoListResponse(total=total, itens=itens)


@router_campanhas.get("/campanhas/{campanha_id}", response_model=CampanhaProspeccaoResponse)
async def detalhar_campanha(campanha_id: int, session: SessionDep, usuario: ProspeccaoViewDep) -> CampanhaProspeccaoResponse:
    campanha = (
        await session.execute(
            select(CampanhaProspeccao).where(
                CampanhaProspeccao.id == campanha_id, CampanhaProspeccao.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if campanha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campanha não encontrada")
    contagens = await _contagem_prospects_por_campanha(session, usuario.organizacao_id, [campanha.id])
    dados = CampanhaProspeccaoResponse.model_validate(campanha)
    dados.prospects_gerados = contagens.get(campanha.id, 0)
    return dados


@router_campanhas.post("/campanhas", status_code=status.HTTP_201_CREATED, response_model=CampanhaProspeccaoResponse)
async def criar_campanha(
    dados: CampanhaProspeccaoCreate, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> CampanhaProspeccaoResponse:
    campanha = CampanhaProspeccao(
        organizacao_id=usuario.organizacao_id,
        nome=dados.nome,
        descricao=dados.descricao,
        criterios_busca=dados.criterios_busca.model_dump(mode="json", exclude_none=True),
        status="rascunho",
        meta_prospects=dados.meta_prospects,
        criado_por=usuario.nome or "sistema",
    )
    session.add(campanha)
    _auditar(session, request, usuario, "criar_campanha_prospeccao", "campanha:nova", {"nome": dados.nome})
    await session.commit()
    await session.refresh(campanha)
    return CampanhaProspeccaoResponse.model_validate(campanha)


@router_campanhas.post("/campanhas/{campanha_id}/coletar", status_code=status.HTTP_202_ACCEPTED)
async def coletar_campanha(
    campanha_id: int, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> dict:
    """Enfileira a coleta -- não roda na hora. A RFB não é uma API de consulta
    sob demanda: quem gera os prospects é o job (app/worker.py), consultando
    o cache local já pronto (ver migrations/.../98czgjqcsywi_...py)."""
    campanha = (
        await session.execute(
            select(CampanhaProspeccao).where(
                CampanhaProspeccao.id == campanha_id, CampanhaProspeccao.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if campanha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campanha não encontrada")
    if campanha.status not in ("rascunho", "ativa", "pausada"):
        raise HTTPException(422, f"Campanha já foi concluída (status atual: {campanha.status}).")

    job = await enfileirar(
        "prospeccao.coletar_campanha",
        {"campanha_id": campanha.id, "organizacao_id": usuario.organizacao_id},
        idempotency_key=f"{campanha.id}:{datetime.now(UTC).date().isoformat()}",
    )
    campanha.status = "ativa"
    _auditar(session, request, usuario, "coletar_campanha_prospeccao", f"campanha:{campanha.id}", {"job_id": job.get("id")})
    await session.commit()
    return {"job_id": job.get("id"), "duplicado": job.get("duplicado", False)}


# --- Fase 3 do Radar de Prospecção (03/09/2026) -- enriquecimento ----------
#
# Fonte escolhida pelo usuário: só verificar se o site do prospect responde,
# sem provedor pago (ver app/verificacao_site.py). Cache de JANELA_CACHE_HORAS
# evita reverificar o mesmo site em sequência.

PROVEDOR_VERIFICACAO_SITE = "verificacao_site"
JANELA_CACHE_ENRIQUECIMENTO_HORAS = 24


@router.post("/{prospect_id}/enriquecer", status_code=status.HTTP_202_ACCEPTED)
async def enriquecer_prospect(
    prospect_id: int, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> dict:
    prospect = await _buscar_prospect(session, prospect_id, usuario.organizacao_id)
    recente = (
        await session.execute(
            select(ProspectEnriquecimento)
            .where(
                ProspectEnriquecimento.prospect_id == prospect.id,
                ProspectEnriquecimento.provedor == PROVEDOR_VERIFICACAO_SITE,
                ProspectEnriquecimento.criado_em >= datetime.now(UTC) - timedelta(hours=JANELA_CACHE_ENRIQUECIMENTO_HORAS),
            )
            .order_by(ProspectEnriquecimento.criado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if recente is not None:
        return {"cache": True, "resultado": recente.payload}

    job = await enfileirar(
        "prospeccao.enriquecer_prospect",
        {"prospect_id": prospect.id, "organizacao_id": usuario.organizacao_id},
        idempotency_key=f"{prospect.id}:{PROVEDOR_VERIFICACAO_SITE}:{datetime.now(UTC).date().isoformat()}",
    )
    _auditar(session, request, usuario, "enriquecer_prospect", f"prospect:{prospect.id}", {"job_id": job.get("id")})
    await session.commit()
    return {"job_id": job.get("id"), "cache": False}


# --- Fase 4 do Radar de Prospecção (03/09/2026) -- triagem de marca --------
#
# SOMENTE indicativa (ver app/prospeccao_triagem.py e DISCLAIMER_TRIAGEM) --
# nunca afirma que uma marca está disponível para registro.


@router.post("/{prospect_id}/triar-marca", status_code=status.HTTP_202_ACCEPTED)
async def triar_marca_prospect_endpoint(
    prospect_id: int, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> dict:
    prospect = await _buscar_prospect(session, prospect_id, usuario.organizacao_id)
    job = await enfileirar(
        "prospeccao.triar_marca_prospect",
        {"prospect_id": prospect.id, "organizacao_id": usuario.organizacao_id},
        idempotency_key=f"{prospect.id}:triagem_marca:{datetime.now(UTC).date().isoformat()}",
    )
    _auditar(session, request, usuario, "triar_marca_prospect", f"prospect:{prospect.id}", {"job_id": job.get("id")})
    await session.commit()
    return {"job_id": job.get("id"), "duplicado": job.get("duplicado", False)}


@router.get("/{prospect_id}/triagens")
async def listar_triagens_prospect(prospect_id: int, session: SessionDep, usuario: ProspeccaoViewDep) -> dict:
    await _buscar_prospect(session, prospect_id, usuario.organizacao_id)
    triagens = (
        (
            await session.execute(
                select(ProspectTriagem)
                .where(
                    ProspectTriagem.organizacao_id == usuario.organizacao_id,
                    ProspectTriagem.prospect_id == prospect_id,
                )
                .order_by(ProspectTriagem.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "disclaimer": DISCLAIMER_TRIAGEM,
        "itens": [
            {
                "id": item.id,
                "marca_pesquisada": item.marca_pesquisada,
                "classificacao": item.classificacao,
                "justificativa": item.justificativa,
                "total_resultados": item.total_resultados,
                "criado_em": item.criado_em,
            }
            for item in triagens
        ],
    }
