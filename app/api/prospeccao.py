"""Radar de Prospecção -- Fase 1 (estrutura de Prospect).

docs/arquitetura-radar-prospeccao-2026-09-03.md: só CRUD, importação manual
e conversão em Lead. Coleta automática (Fase 2), enriquecimento (Fase 3),
triagem de marca (Fase 4) e score (Fase 5) entram depois, cada um com sua
própria migração -- esta fase não antecipa colunas que nenhum código ainda
preenche.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.juridico import FUSO_BRASIL
from app.api.leads import _garantir_proxima_acao_padrao
from app.api.pesquisas import detectar_pesquisa_duplicada
from app.api.saas import SuperAdminDep
from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.crm import registrar_consentimento_prospeccao_comercial
from app.database import get_session
from app.ia_sombra import enfileirar_qualificacao_ia_se_ativa
from app.importacao_planilha import TAMANHO_MAXIMO_IMPORTACAO, ler_planilha, valor_coluna
from app.models import (
    CampanhaProspeccao,
    EmpresaCRM,
    EventoAuditoria,
    HistoricoStatusProspect,
    ImportacaoCnpjRfb,
    Lead,
    PesquisaMarca,
    PoliticaProspeccao,
    Prospect,
    ProspectEnriquecimento,
    ProspectFonte,
    ProspectTriagem,
    StatusLead,
    StatusProspect,
    SupressaoProspeccao,
)
from app.prospeccao_triagem import DISCLAIMER_TRIAGEM, extrair_marca_candidata
from app.proxy import cliente_ip
from app.queueing import enfileirar
from app.schemas import (
    CampanhaProspeccaoCreate,
    CampanhaProspeccaoListResponse,
    CampanhaProspeccaoResponse,
    DuplicatasProspectResponse,
    GrupoDuplicataProspect,
    ImportacaoCnpjRfbResponse,
    ImportacaoCnpjRfbTrigger,
    MesclarProspectRequest,
    PoliticaProspeccaoResponse,
    PoliticaProspeccaoUpdate,
    ProspectCreate,
    ProspectListResponse,
    ProspectResponse,
    ProspectStatusUpdate,
    SupressaoProspeccaoCreate,
    SupressaoProspeccaoResponse,
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
    "opt_out_lgpd",
    # Achado de 05/09/2026: triagem de marca com classificação "ja_e_titular"
    # (app/prospeccao_triagem.py) habilita descarte direto na tela.
    "ja_possui_marca_registrada",
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


async def _esta_suprimido(session: AsyncSession, organizacao_id: int, cnpj: str | None, email: str) -> bool:
    """Achado FASE5-5 da auditoria (04/09/2026): opt-out de prospecção --
    quem está na lista de supressão nunca vira Prospect de novo nessa
    organização, mesmo reaparecendo numa nova importação/campanha."""
    condicoes = []
    if cnpj:
        condicoes.append(SupressaoProspeccao.cnpj == cnpj)
    if email:
        condicoes.append(SupressaoProspeccao.email == email)
    if not condicoes:
        return False
    resultado = await session.execute(
        select(SupressaoProspeccao.id)
        .where(SupressaoProspeccao.organizacao_id == organizacao_id, or_(*condicoes))
        .limit(1)
    )
    return resultado.scalar_one_or_none() is not None


async def _criar_prospect(
    session: AsyncSession,
    organizacao_id: int,
    dados: ProspectCreate,
    por: str,
    *,
    fonte_id: int | None = None,
    campanha_id: int | None = None,
) -> tuple[Prospect | None, str]:
    """Cria um Prospect aplicando as duas camadas de dedup e a lista de
    supressão (opt-out). Devolve (prospect, resultado), resultado em
    {"criado", "duplicado", "suprimido"} -- prospect é None quando suprimido."""
    telefone_digitos = _digitos(dados.telefone)
    email = (dados.email or "").lower()
    if await _esta_suprimido(session, organizacao_id, dados.cnpj, email):
        return None, "suprimido"
    existente = await _prospect_duplicado(session, organizacao_id, dados.cnpj, telefone_digitos, email)
    if existente is not None:
        return existente, "duplicado"

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
    return prospect, "criado"


@router.get("", response_model=ProspectListResponse)
async def listar_prospects(
    session: SessionDep,
    usuario: ProspeccaoViewDep,
    busca: Annotated[str | None, Query(max_length=200)] = None,
    status_prospect: Annotated[str | None, Query(alias="status")] = None,
    uf: Annotated[list[str] | None, Query(max_length=2)] = None,
    cidade: Annotated[list[str] | None, Query(max_length=120)] = None,
    cnae_principal: Annotated[str | None, Query(max_length=10)] = None,
    responsavel_id: Annotated[int | None, Query(ge=1)] = None,
    campanha_id: Annotated[int | None, Query(ge=1)] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
) -> ProspectListResponse:
    # Achado do usuário: filtro de estado/cidade era de valor único --
    # agora aceita vários (?uf=SP&uf=RJ), a fonte de dados (cache nacional
    # do CNPJ/RFB) já cobre qualquer UF, era só o filtro que limitava.
    filtros = [Prospect.organizacao_id == usuario.organizacao_id]
    if busca:
        termo = f"%{busca.strip()}%"
        condicoes_busca = [Prospect.razao_social.ilike(termo), Prospect.nome_fantasia.ilike(termo), Prospect.cnpj.ilike(termo)]
        # Achado da validação do Radar de Prospecção (17/09/2026): Prospect.cnpj
        # é sempre gravado só com dígitos (ProspectCreate._validar_cnpj em
        # app/schemas.py, único ponto de entrada nos três fluxos de criação --
        # manual, importação e coleta de campanha). Buscar com CNPJ formatado
        # ("12.345.678/0001-90") nunca batia com o ilike acima, que comparava o
        # texto digitado literalmente. Compara também a versão só com dígitos.
        digitos_busca = re.sub(r"\D", "", busca)
        if digitos_busca and digitos_busca != busca.strip():
            condicoes_busca.append(Prospect.cnpj.ilike(f"%{digitos_busca}%"))
        filtros.append(or_(*condicoes_busca))
    if status_prospect:
        filtros.append(Prospect.status == status_prospect)
    ufs = [item.strip().upper() for item in uf if item.strip()] if uf else []
    if ufs:
        filtros.append(Prospect.uf.in_(ufs))
    cidades = [item.strip() for item in cidade if item.strip()] if cidade else []
    if cidades:
        filtros.append(or_(*[Prospect.cidade.ilike(f"%{item}%") for item in cidades]))
    if cnae_principal:
        filtros.append(Prospect.cnae_principal == cnae_principal)
    if responsavel_id:
        filtros.append(Prospect.responsavel_id == responsavel_id)
    if campanha_id:
        # Achado do usuário (13/09/2026): sem esse filtro, a lista sempre
        # trazia todos os prospects da organização, sem como isolar só os
        # gerados por uma campanha específica.
        filtros.append(Prospect.campanha_id == campanha_id)

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


# --- Fase 1 do roadmap pos-auditoria do CRM (06/09/2026): central de
# duplicidades e mesclagem assistida -- Prospect.duplicado_de_id e
# StatusProspect.DUPLICADO existiam desde a Fase 1 do Radar (03/09/2026) mas
# nenhum codigo os escrevia (achado da auditoria completa do CRM,
# publicada em 06/09/2026). Escopo desta entrega: so Prospect -- Lead ja
# tem sua propria dedup na criacao (ver _prospect_duplicado acima e
# app/api/leads.py), mas nao tinha tela de revisao/merge para casos que a
# dedup automatica na entrada nao pegou (ex.: telefone cadastrado depois).

STATUS_ATIVOS_PARA_DUPLICATA = (StatusProspect.NOVO.value, StatusProspect.APROVADO.value)


async def _duplicatas_por(
    session: AsyncSession, organizacao_id: int, expressao, criterio: str
) -> list[GrupoDuplicataProspect]:
    """Agrupa prospects ainda acionaveis (novo/aprovado) que compartilham o
    mesmo valor normalizado de `expressao` -- so relevante quando 2+
    prospects caem na mesma chave. Agrupamento feito em Python (nao HAVING
    no SQL) porque `expressao` pode ser uma coluna computada (ex.: digitos
    do telefone), mais simples de reconciliar com os objetos ORM assim."""
    linhas = (
        await session.execute(
            select(expressao.label("chave"), Prospect)
            .where(
                Prospect.organizacao_id == organizacao_id,
                Prospect.status.in_(STATUS_ATIVOS_PARA_DUPLICATA),
                expressao.isnot(None),
                expressao != "",
            )
            .order_by(expressao, Prospect.criado_em.asc())
        )
    ).all()
    agrupado: dict[str, list[Prospect]] = {}
    for chave, prospect in linhas:
        agrupado.setdefault(chave, []).append(prospect)
    return [
        GrupoDuplicataProspect(criterio=criterio, valor=chave, itens=[_prospect_response(p) for p in itens])
        for chave, itens in agrupado.items()
        if len(itens) > 1
    ]


@router.get("/duplicatas", response_model=DuplicatasProspectResponse)
async def listar_duplicatas_prospect(session: SessionDep, usuario: ProspeccaoViewDep) -> DuplicatasProspectResponse:
    org = usuario.organizacao_id
    grupos = [
        *(await _duplicatas_por(session, org, Prospect.cnpj, "cnpj")),
        *(await _duplicatas_por(session, org, func.lower(Prospect.email), "email")),
        *(await _duplicatas_por(session, org, func.regexp_replace(Prospect.telefone, r"\D", "", "g"), "telefone")),
    ]
    return DuplicatasProspectResponse(grupos=grupos)


# Campos preenchidos no sobrevivente só quando estiverem vazios -- a fusão
# nunca sobrescreve um dado que o operador já confirmou no primário, só
# completa o que falta a partir do duplicado (mesclagem assistida, não
# substituição cega).
CAMPOS_PREENCHIVEIS_NA_MESCLA = (
    "cnpj",
    "nome_fantasia",
    "cnae_principal",
    "porte",
    "situacao_cadastral",
    "data_abertura",
    "uf",
    "cidade",
    "endereco",
    "telefone",
    "email",
    "site",
)


@router.post("/{primario_id}/mesclar", response_model=ProspectResponse)
async def mesclar_prospect(
    primario_id: int, dados: MesclarProspectRequest, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> ProspectResponse:
    if primario_id == dados.duplicado_id:
        raise HTTPException(422, "Não é possível mesclar um prospect com ele mesmo.")
    primario = await _buscar_prospect(session, primario_id, usuario.organizacao_id)
    duplicado = await _buscar_prospect(session, dados.duplicado_id, usuario.organizacao_id)
    if duplicado.status == StatusProspect.CONVERTIDO_LEAD.value:
        raise HTTPException(422, "Este prospect já foi convertido em lead -- não pode ser mesclado como duplicado.")
    if duplicado.status == StatusProspect.DUPLICADO.value:
        raise HTTPException(422, "Este prospect já foi mesclado anteriormente.")

    campos_preenchidos = []
    for campo in CAMPOS_PREENCHIVEIS_NA_MESCLA:
        if getattr(primario, campo) in (None, "") and getattr(duplicado, campo) not in (None, ""):
            setattr(primario, campo, getattr(duplicado, campo))
            campos_preenchidos.append(campo)

    for modelo in (ProspectTriagem, ProspectEnriquecimento, HistoricoStatusProspect):
        await session.execute(update(modelo).where(modelo.prospect_id == duplicado.id).values(prospect_id=primario.id))

    duplicado.duplicado_de_id = primario.id
    duplicado.status = StatusProspect.DUPLICADO.value
    session.add(
        HistoricoStatusProspect(
            organizacao_id=usuario.organizacao_id,
            prospect_id=duplicado.id,
            status=StatusProspect.DUPLICADO.value,
            por=usuario.nome or "sistema",
        )
    )
    _auditar(
        session,
        request,
        usuario,
        "mesclar_prospect",
        f"prospect:{primario.id}",
        {"duplicado_id": duplicado.id, "campos_preenchidos": campos_preenchidos},
    )
    await session.commit()
    await session.refresh(primario)
    return _prospect_response(primario)


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
    prospect, resultado = await _criar_prospect(session, usuario.organizacao_id, dados, usuario.nome or "sistema")
    if prospect is None:
        raise HTTPException(422, "Este CNPJ/e-mail está na lista de supressão de prospecção (opt-out).")
    _auditar(
        session,
        request,
        usuario,
        "criar_prospect",
        f"prospect:{prospect.id}",
        {"resultado": resultado, "razao_social": dados.razao_social},
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
    suprimidos = 0
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
        _, resultado_item = await _criar_prospect(session, usuario.organizacao_id, dados, usuario.nome or "sistema")
        if resultado_item == "criado":
            criados += 1
        elif resultado_item == "suprimido":
            suprimidos += 1
        else:
            duplicados += 1

    resultado = {
        "total_linhas": len(registros),
        "criados": criados,
        "duplicados": duplicados,
        "invalidos": invalidos,
        "suprimidos": suprimidos,
    }
    _auditar(session, request, usuario, "importar_prospects", f"prospects:importacao:{arquivo.filename}", resultado)
    await session.commit()
    return resultado


@router.post("/{prospect_id}/converter-lead", status_code=status.HTTP_201_CREATED)
async def converter_prospect_em_lead(
    prospect_id: int, request: Request, session: SessionDep, usuario: ProspeccaoConvertDep
) -> dict:
    # Achado FASE5-9 da auditoria (04/09/2026): permitia converter direto de
    # "novo" -- ou seja, sem NUNCA ter passado por aprovar_prospect (manual)
    # nem pela aprovação automática auditada em app/worker.py (que exige
    # politica.aprovacao_automatica_ativa explicitamente ligada). Corrigido:
    # só converte quem já está "aprovado", por um dos dois caminhos.
    prospect = await _buscar_prospect(session, prospect_id, usuario.organizacao_id)
    if prospect.status != StatusProspect.APROVADO.value:
        raise HTTPException(
            422,
            f"Prospect precisa estar aprovado antes de virar lead (status atual: {prospect.status}). "
            "Aprove manualmente ou habilite a aprovação automática por score na política de prospecção.",
        )
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
        registrar_consentimento_prospeccao_comercial(lead, usuario.id)
        session.add(lead)
        await _garantir_proxima_acao_padrao(session, lead)
        criado_novo = True

    await session.flush()

    # Achado do usuário (08/09/2026): leads vindos da prospecção nunca têm
    # nenhuma PesquisaMarca (a triagem automática de marca -- app.
    # prospeccao_triagem -- é uma entidade separada, indicativa, que nunca
    # virou pesquisa formal). Isso fazia o lead desaparecer da tela de Leads
    # (visão "Por pesquisa"), e exigiria pesquisar manualmente, cliente por
    # cliente, pra ele aparecer. Reaproveita a triagem mais recente do
    # prospect quando existe (marca já pesquisada, sem rodar o motor de
    # busca de novo); quando o prospect nunca foi triado (achado numa
    # conversão real: nem todo prospect passa por "Triar marca" antes de
    # aprovar/converter), usa a mesma extração de marca candidata da
    # triagem (nome fantasia, ou razão social sem sufixos societários) --
    # a pesquisa nasce igual, só quem calcula os resultados é a Central de
    # Análise ao abrir. Estado DRAFT, mesma revisão humana obrigatória de
    # qualquer pesquisa antes de virar relatório; só evita a digitação
    # manual, não pula nenhuma etapa de análise.
    triagem_mais_recente = (
        await session.execute(
            select(ProspectTriagem)
            .where(ProspectTriagem.prospect_id == prospect.id)
            .order_by(ProspectTriagem.criado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    marca_candidata = (
        triagem_mais_recente.marca_pesquisada
        if triagem_mais_recente is not None
        else extrair_marca_candidata(prospect.razao_social, prospect.nome_fantasia)
    )
    if marca_candidata and len(marca_candidata) >= 2:
        original_id = await detectar_pesquisa_duplicada(
            session, usuario.organizacao_id, lead.id, marca_candidata, classe_nice=None
        )
        if original_id is None:
            session.add(
                PesquisaMarca(
                    organizacao_id=usuario.organizacao_id,
                    lead_id=lead.id,
                    marca=marca_candidata[:200],
                    tipo_pesquisa="completa",
                    analysis_notes="Pesquisa criada automaticamente ao converter o prospect em lead "
                    "(Radar de Prospecção) -- ainda pendente de revisão humana, como qualquer outra "
                    "pesquisa.",
                )
            )

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
    if criado_novo:
        # IA em sombra (captação de leads): qualificação gerada uma única
        # vez, só para lead genuinamente novo -- reconciliar com um lead já
        # existente não é uma nova captação.
        await enfileirar_qualificacao_ia_se_ativa(session, lead)
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


@router_campanhas.delete("/campanhas/{campanha_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir_campanha(
    campanha_id: int, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> None:
    """Exclui a campanha. Os prospects já gerados por ela não são apagados --
    só perdem o vínculo (fk_prospects_campanha_id é ON DELETE SET NULL),
    continuam no radar normalmente. Bloqueado enquanto a coleta está em
    andamento (status "ativa") para não apagar a campanha embaixo de um job
    que ainda vai tentar atualizá-la."""
    campanha = (
        await session.execute(
            select(CampanhaProspeccao).where(
                CampanhaProspeccao.id == campanha_id, CampanhaProspeccao.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if campanha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campanha não encontrada")
    if campanha.status == "ativa":
        raise HTTPException(422, "Não é possível excluir uma campanha com coleta em andamento.")
    _auditar(session, request, usuario, "excluir_campanha_prospeccao", f"campanha:{campanha.id}", {"nome": campanha.nome})
    await session.delete(campanha)
    await session.commit()


@router_campanhas.post("/campanhas/{campanha_id}/cancelar")
async def cancelar_campanha(
    campanha_id: int, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> CampanhaProspeccaoResponse:
    """Interrompe manualmente uma campanha em coleta.

    Achado do usuário (13/09/2026): o job de coleta (app/worker.py) só
    tirava a campanha do status "ativa" ao terminar o laço com sucesso --
    se o worker travasse, morresse ou lançasse uma exceção não tratada no
    meio da coleta, a campanha ficava "ativa" para sempre, e o botão
    "Excluir" (bloqueado nesse status de propósito, para não apagar uma
    campanha embaixo de um job que ainda vai tentar atualizá-la) não tinha
    como ser destravado. Esta rota dá essa saída manual; o worker também
    passou a se auto-recuperar de exceções (ver processar() em
    app/worker.py), mas isso não cobre um processo morto sem chance de
    rodar o `except`."""
    campanha = (
        await session.execute(
            select(CampanhaProspeccao).where(
                CampanhaProspeccao.id == campanha_id, CampanhaProspeccao.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if campanha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campanha não encontrada")
    if campanha.status != "ativa":
        raise HTTPException(422, "Esta campanha não está em coleta no momento.")
    campanha.status = "pausada"
    campanha.encerrada_em = datetime.now(UTC)
    _auditar(session, request, usuario, "cancelar_campanha_prospeccao", f"campanha:{campanha.id}", {"nome": campanha.nome})
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
    if campanha.status == "ativa":
        raise HTTPException(422, "Campanha já está sendo coletada.")
    if campanha.status not in ("rascunho", "pausada", "concluida"):
        raise HTTPException(422, f"Campanha não pode ser coletada no status atual: {campanha.status}.")

    # Uma campanha concluída pode ser executada novamente depois que o cache da
    # RFB for atualizado ou seus filtros forem corrigidos. A data da conclusão
    # anterior diferencia o novo job do primeiro disparo do mesmo dia, enquanto
    # o status "ativa" acima impede dois disparos concorrentes.
    versao_execucao = campanha.encerrada_em.isoformat() if campanha.encerrada_em else datetime.now(UTC).date().isoformat()

    job = await enfileirar(
        "prospeccao.coletar_campanha",
        {"campanha_id": campanha.id, "organizacao_id": usuario.organizacao_id},
        idempotency_key=f"{campanha.id}:{versao_execucao}",
    )
    campanha.status = "ativa"
    campanha.encerrada_em = None
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


# --- Fase 5 do Radar de Prospecção (03/09/2026) -- score e aprovação -------


@router.post("/{prospect_id}/calcular-score", status_code=status.HTTP_202_ACCEPTED)
async def calcular_score_prospect_endpoint(
    prospect_id: int, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> dict:
    prospect = await _buscar_prospect(session, prospect_id, usuario.organizacao_id)
    job = await enfileirar(
        "prospeccao.calcular_score",
        {"prospect_id": prospect.id, "organizacao_id": usuario.organizacao_id},
        idempotency_key=f"{prospect.id}:score:{datetime.now(UTC).date().isoformat()}",
    )
    _auditar(session, request, usuario, "calcular_score_prospect", f"prospect:{prospect.id}", {"job_id": job.get("id")})
    await session.commit()
    return {"job_id": job.get("id"), "duplicado": job.get("duplicado", False)}


@router.post("/{prospect_id}/aprovar", response_model=ProspectResponse)
async def aprovar_prospect(
    prospect_id: int, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> ProspectResponse:
    prospect = await _buscar_prospect(session, prospect_id, usuario.organizacao_id)
    if prospect.status != StatusProspect.NOVO.value:
        raise HTTPException(422, f"Prospect já foi processado (status atual: {prospect.status}).")
    prospect.status = StatusProspect.APROVADO.value
    session.add(
        HistoricoStatusProspect(
            organizacao_id=usuario.organizacao_id,
            prospect_id=prospect.id,
            status=StatusProspect.APROVADO.value,
            por=usuario.nome or "sistema",
        )
    )
    _auditar(session, request, usuario, "aprovar_prospect", f"prospect:{prospect.id}", {})
    await session.commit()
    await session.refresh(prospect)
    return _prospect_response(prospect)


async def obter_politica_prospeccao(session: AsyncSession, organizacao_id: int) -> PoliticaProspeccao:
    politica = (
        await session.execute(select(PoliticaProspeccao).where(PoliticaProspeccao.organizacao_id == organizacao_id))
    ).scalar_one_or_none()
    return politica or PoliticaProspeccao(organizacao_id=organizacao_id, aprovacao_automatica_ativa=False)


@router_campanhas.get("/politica", response_model=PoliticaProspeccaoResponse)
async def consultar_politica_prospeccao(
    session: SessionDep, usuario: ProspeccaoViewDep
) -> PoliticaProspeccaoResponse:
    politica = await obter_politica_prospeccao(session, usuario.organizacao_id)
    return PoliticaProspeccaoResponse(
        aprovacao_automatica_ativa=politica.aprovacao_automatica_ativa,
        score_minimo_aprovacao=politica.score_minimo_aprovacao,
        atualizado_por=politica.atualizado_por,
        atualizado_em=politica.atualizado_em or datetime.now(UTC),
    )


@router_campanhas.put("/politica", response_model=PoliticaProspeccaoResponse)
async def editar_politica_prospeccao(
    dados: PoliticaProspeccaoUpdate, session: SessionDep, usuario: ProspeccaoManageDep
) -> PoliticaProspeccaoResponse:
    politica = (
        await session.execute(
            select(PoliticaProspeccao).where(PoliticaProspeccao.organizacao_id == usuario.organizacao_id)
        )
    ).scalar_one_or_none()
    if politica is None:
        politica = PoliticaProspeccao(organizacao_id=usuario.organizacao_id)
        session.add(politica)
    politica.aprovacao_automatica_ativa = dados.aprovacao_automatica_ativa
    politica.score_minimo_aprovacao = dados.score_minimo_aprovacao
    politica.atualizado_por = usuario.ator
    await session.commit()
    await session.refresh(politica)
    return PoliticaProspeccaoResponse(
        aprovacao_automatica_ativa=politica.aprovacao_automatica_ativa,
        score_minimo_aprovacao=politica.score_minimo_aprovacao,
        atualizado_por=politica.atualizado_por,
        atualizado_em=politica.atualizado_em,
    )


@router_campanhas.get("/dashboard")
async def dashboard_prospeccao(
    session: SessionDep, usuario: ProspeccaoViewDep, dias: Annotated[int, Query(ge=7, le=180)] = 30
) -> dict:
    org = usuario.organizacao_id
    funil = dict(
        (
            await session.execute(
                select(Prospect.status, func.count()).where(Prospect.organizacao_id == org).group_by(Prospect.status)
            )
        ).all()
    )

    desde = datetime.now(UTC) - timedelta(days=dias)
    # Achado D1 da auditoria de 06/09/2026 (mesmo padrao de app.api.juridico,
    # achado JUR-1): trunca no fuso de Brasilia, nao no fuso da sessao (UTC).
    data_prospect_brasil = func.date(func.timezone("America/Sao_Paulo", Prospect.criado_em))
    criados_por_dia = dict(
        (
            await session.execute(
                select(data_prospect_brasil, func.count())
                .where(Prospect.organizacao_id == org, Prospect.criado_em >= desde)
                .group_by(data_prospect_brasil)
            )
        ).all()
    )
    hoje = datetime.now(UTC).astimezone(FUSO_BRASIL).date()
    serie = [
        {"data": (hoje - timedelta(days=offset)).isoformat(), "prospects_criados": int(criados_por_dia.get(hoje - timedelta(days=offset), 0))}
        for offset in range(dias, -1, -1)
    ]

    return {"funil": funil, "dias": dias, "serie": serie}


# --- Importação do cache nacional de empresas (Dados Abertos do CNPJ) ------
#
# Restrito a superadmin: alimenta cache_estabelecimentos_rfb, que não é por
# tenant -- afeta a plataforma inteira, não uma organização só. Roda pelo
# worker (não preso à sessão HTTP/SSH de quem disparou -- uma importação
# rodando via SSH direto já morreu no meio do download em 03/09/2026 quando
# a conexão caiu).


@router_campanhas.post(
    "/importar-cnpj-rfb", status_code=status.HTTP_202_ACCEPTED, response_model=ImportacaoCnpjRfbResponse
)
async def disparar_importacao_cnpj_rfb(
    dados: ImportacaoCnpjRfbTrigger, request: Request, session: SessionDep, usuario: SuperAdminDep
) -> ImportacaoCnpjRfbResponse:
    em_andamento = (
        await session.execute(select(ImportacaoCnpjRfb).where(ImportacaoCnpjRfb.status == "executando").limit(1))
    ).scalar_one_or_none()
    if em_andamento is not None:
        raise HTTPException(422, "Já existe uma importação em andamento -- aguarde terminar antes de disparar outra.")

    execucao = ImportacaoCnpjRfb(
        status="executando",
        periodo=dados.periodo,
        solicitado_por=usuario.nome or "sistema",
        total_processados=0,
        total_validos=0,
        solicitado_em=datetime.now(UTC),
    )
    session.add(execucao)
    await session.flush()
    job = await enfileirar(
        "prospeccao.importar_cnpj_rfb",
        {"execucao_id": execucao.id, "periodo": dados.periodo, "limite_linhas": dados.limite_linhas},
    )
    _auditar(
        session, request, usuario, "importar_cnpj_rfb", f"importacao_cnpj_rfb:{execucao.id}", {"job_id": job.get("id")}
    )
    await session.commit()
    await session.refresh(execucao)
    return ImportacaoCnpjRfbResponse.model_validate(execucao)


@router_campanhas.get("/importar-cnpj-rfb", response_model=list[ImportacaoCnpjRfbResponse])
async def listar_importacoes_cnpj_rfb(
    session: SessionDep, usuario: ProspeccaoViewDep, limite: Annotated[int, Query(ge=1, le=50)] = 10
) -> list[ImportacaoCnpjRfbResponse]:
    execucoes = (
        (
            await session.execute(
                select(ImportacaoCnpjRfb).order_by(ImportacaoCnpjRfb.solicitado_em.desc()).limit(limite)
            )
        )
        .scalars()
        .all()
    )
    return [ImportacaoCnpjRfbResponse.model_validate(item) for item in execucoes]


# --- Fase 5 do Radar de Prospecção (04/09/2026) -- opt-out de prospecção ---
#
# Achado FASE5-5 da auditoria (04/09/2026): não existia nenhum mecanismo para
# alguém pedir pra não ser mais contatado por prospecção comercial (só Lead
# tinha isso, via app/api/privacidade.py). Quem entra aqui nunca mais vira
# Prospect nessa organização (checado em _esta_suprimido, chamado por
# _criar_prospect -- único ponto de criação, usado por criar_prospect,
# importar_prospects e o job prospeccao.coletar_campanha). Prospects já
# existentes que casarem são rejeitados e têm os dados de contato apagados
# na hora, igual à anonimização de Lead.


def _anonimizar_prospect(prospect: Prospect) -> None:
    prospect.telefone = None
    prospect.email = None
    prospect.site = None
    prospect.endereco = None


@router_campanhas.post(
    "/supressoes", status_code=status.HTTP_201_CREATED, response_model=SupressaoProspeccaoResponse
)
async def criar_supressao_prospeccao(
    dados: SupressaoProspeccaoCreate, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> SupressaoProspeccaoResponse:
    supressao = SupressaoProspeccao(
        organizacao_id=usuario.organizacao_id,
        cnpj=dados.cnpj,
        email=dados.email,
        motivo=dados.motivo,
        criado_por=usuario.nome or "sistema",
    )
    session.add(supressao)
    await session.flush()

    condicoes = []
    if dados.cnpj:
        condicoes.append(Prospect.cnpj == dados.cnpj)
    if dados.email:
        condicoes.append(func.lower(Prospect.email) == dados.email)
    afetados = 0
    if condicoes:
        prospects = (
            (
                await session.execute(
                    select(Prospect).where(Prospect.organizacao_id == usuario.organizacao_id, or_(*condicoes))
                )
            )
            .scalars()
            .all()
        )
        for prospect in prospects:
            if prospect.status not in (StatusProspect.CONVERTIDO_LEAD.value,):
                prospect.status = StatusProspect.REJEITADO.value
                prospect.motivo_descarte = "opt_out_lgpd"
            _anonimizar_prospect(prospect)
            session.add(
                HistoricoStatusProspect(
                    organizacao_id=usuario.organizacao_id,
                    prospect_id=prospect.id,
                    status=prospect.status,
                    por=f"opt_out:{usuario.nome or 'sistema'}",
                )
            )
            afetados += 1

    _auditar(
        session,
        request,
        usuario,
        "criar_supressao",
        f"supressao_prospeccao:{supressao.id}",
        {"prospects_afetados": afetados},
    )
    await session.commit()
    await session.refresh(supressao)
    return SupressaoProspeccaoResponse.model_validate(supressao)


@router_campanhas.get("/supressoes", response_model=list[SupressaoProspeccaoResponse])
async def listar_supressoes_prospeccao(
    session: SessionDep, usuario: ProspeccaoViewDep, limite: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[SupressaoProspeccaoResponse]:
    itens = (
        (
            await session.execute(
                select(SupressaoProspeccao)
                .where(SupressaoProspeccao.organizacao_id == usuario.organizacao_id)
                .order_by(SupressaoProspeccao.criado_em.desc())
                .limit(limite)
            )
        )
        .scalars()
        .all()
    )
    return [SupressaoProspeccaoResponse.model_validate(item) for item in itens]


@router_campanhas.delete("/supressoes/{supressao_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_supressao_prospeccao(
    supressao_id: int, request: Request, session: SessionDep, usuario: ProspeccaoManageDep
) -> None:
    supressao = (
        await session.execute(
            select(SupressaoProspeccao).where(
                SupressaoProspeccao.id == supressao_id, SupressaoProspeccao.organizacao_id == usuario.organizacao_id
            )
        )
    ).scalar_one_or_none()
    if supressao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supressão não encontrada")
    await session.delete(supressao)
    _auditar(session, request, usuario, "remover_supressao", f"supressao_prospeccao:{supressao_id}", {})
    await session.commit()
