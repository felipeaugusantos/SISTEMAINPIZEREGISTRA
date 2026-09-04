import base64
import hashlib
import re
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.saas import exigir_superadmin
from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.badepi.despachos_codigos import DESCRICOES_DESPACHO, codigo_numerico
from app.crm import obter_ou_criar_empresa, registrar_evento_operacional
from app.database import get_session
from app.emailing import enviar_alerta_prazo_juridico
from app.models import (
    DocumentoEntregaJuridico,
    EmpresaCRM,
    EventoAuditoria,
    EventoJuridico,
    ItemChecklistPrazo,
    Movimentacao,
    MovimentacaoAvaliadaJuridico,
    NotificacaoJuridica,
    PoliticaJuridica,
    PrazoJuridico,
    Processo,
    ProcessoMonitorado,
    RegraJuridicaVersionada,
    Titular,
    UsuarioOperacoes,
    processo_titulares,
)
from app.proxy import cliente_ip
from app.storage import StorageError, save_bytes

FERIADOS_NACIONAIS_FIXOS: tuple[tuple[int, int, str], ...] = (
    (1, 1, "Confraternização Universal"),
    (4, 21, "Tiradentes"),
    (5, 1, "Dia do Trabalho"),
    (9, 7, "Independência do Brasil"),
    (10, 12, "Nossa Senhora Aparecida"),  # Lei nº 6.802/1980
    (11, 2, "Finados"),
    (11, 15, "Proclamação da República"),
    (11, 20, "Dia Nacional de Zumbi e da Consciência Negra"),  # Lei nº 14.759/2023, a partir de 2024
    (12, 25, "Natal"),
)


def _pascoa(ano: int) -> date:
    """Data da Páscoa (algoritmo do calendário gregoriano — Gauss/Meeus)."""
    a = ano % 19
    b = ano // 100
    c = ano % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    n = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * n) // 451
    mes = (h + n - 7 * m + 114) // 31
    dia = ((h + n - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def _feriados_nacionais(ano: int) -> set[date]:
    """Feriados nacionais fixos + Sexta-feira Santa (móvel).

    NÃO inclui pontos facultativos (discricionários, fora da redação "sábado,
    domingo ou feriado" da Portaria/INPI/PR nº 08/2022, art. 6º, § 2º) nem
    feriados estaduais/municipais (que exigem petição comprobatória própria,
    conforme Parecer nº 00016/2021/CGPI/PFE-INPI/PGF/AGU — não prorrogam
    automaticamente). Fonte da lista fixa: Lei nº 662/1949 c/ Lei nº
    6.802/1980 e Lei nº 14.759/2023.
    """
    feriados = {
        date(ano, mes, dia)
        for mes, dia, _nome in FERIADOS_NACIONAIS_FIXOS
        if not (mes == 11 and dia == 20 and ano < 2024)
    }
    feriados.add(_pascoa(ano) - timedelta(days=2))
    return feriados


def _eh_dia_util(dia: date) -> bool:
    return dia.weekday() < 5 and dia not in _feriados_nacionais(dia.year)


def _proximo_dia_util(dia: date) -> date:
    """Prorroga para o primeiro dia útil seguinte quando ``dia`` cair em
    sábado, domingo ou feriado nacional — Portaria/INPI/PR nº 08/2022, art.
    6º, § 2º: "prorroga-se automaticamente para o primeiro dia útil o prazo
    que vença no sábado, domingo ou feriado"."""
    while not _eh_dia_util(dia):
        dia += timedelta(days=1)
    return dia


router = APIRouter(prefix="/v1/admin/juridico", tags=["operacao juridica"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ViewDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("legal.view"))]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("legal.manage"))]
SuperAdminDep = Annotated[UsuarioAutenticado, Depends(exigir_superadmin)]

TIPOS_PRAZO = {
    "publicacao_rpi": "Publicação RPI",
    "manifestacao": "Manifestação",
    "exigencia": "Cumprimento de exigência",
    "oposicao": "Oposição",
    "recurso": "Recurso",
    "pagamento": "Pagamento ou retribuição",
    "deferimento": "Deferimento",
    "concessao": "Concessão",
    "renovacao": "Renovação",
    "decenio": "Decênio",
    "vencimento_interno": "Vencimento interno",
    "outro": "Outro",
}
STATUS_ATIVOS = {"aguardando_confirmacao", "pendente", "em_andamento"}
PADRAO_PRAZO = re.compile(
    # "prazo de 60 (sessenta) dias", "Prazo para cumprimento - 30 (Trinta) dias
    # corridos": o número aparece perto da palavra "prazo", antes de "dias",
    # sem cruzar o fim da frase.
    r"prazo\b[^.\n]{0,40}?(\d{1,3})\s*(?:\([^)]*\)\s*)?dias?",
    re.IGNORECASE,
)
# Prazo administrativo padrão (dias corridos) aplicado a todos os despachos de
# DESPACHOS_PRAZO quando o texto da publicação não soletra o número de dias.
# Base: LPI (Lei 9.279/96); o prazo administrativo de marca é de 60 dias na
# quase totalidade dos casos. Valor default usado quando não há linha
# aplicável em RegraJuridicaVersionada (codigo="prazo_administrativo_padrao_
# dias") -- ver _historico_regra/_valor_vigente, achado 5.5 da auditoria
# (Fase 3, 02/09/2026).
PRAZO_ADMINISTRATIVO_PADRAO_DIAS = 60

# Padrões de texto -> (tipo de prazo, ação), na ordem em que devem ser
# testados (o primeiro que casar vence). O número de dias vem de
# PRAZO_ADMINISTRATIVO_PADRAO_DIAS (ou da regra vigente, se houver), não é
# mais fixo por entrada -- todas usavam o mesmo valor (60). Despachos
# terminais (concessão, arquivamento, extinção, recurso julgado) não constam
# de propósito e não geram prazo.
DESPACHOS_PRAZO: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"instaura[çc][ãa]o de processo de nulidade", re.IGNORECASE),
        "manifestacao",
        "Manifestação em processo de nulidade",
    ),
    (
        re.compile(r"notifica[çc][ãa]o de oposi[çc][ãa]o", re.IGNORECASE),
        "oposicao",
        "Manifestação sobre oposição",
    ),
    (re.compile(r"para oposi[çc][ãa]o", re.IGNORECASE), "oposicao", "Janela de oposição"),
    (
        re.compile(r"notifica[çc][ãa]o de recurso", re.IGNORECASE),
        "recurso",
        "Contrarrazões de recurso",
    ),
    (
        re.compile(r"indeferimento do pedido", re.IGNORECASE),
        "recurso",
        "Recurso contra indeferimento",
    ),
    (re.compile(r"exig[êe]ncia", re.IGNORECASE), "exigencia", "Cumprimento de exigência"),
    (
        re.compile(r"deferimento do pedido", re.IGNORECASE),
        "pagamento",
        "Pagamento da taxa de concessão",
    ),
)

# Achado da auditoria (01/09/2026): a regra usava a data de DEPÓSITO, mas a
# regra oficial do INPI isenta pela data de DEFERIMENTO — critério errado,
# gerava cobrança indevida para o caso mais comum (depósito antigo, deferido
# recentemente). Fonte oficial: FAQ do INPI, item 7 —
# gov.br/inpi/pt-br/inpi-data/precificacao-dos-servicos/PerguntaseRespostas:
# "Todos os pedidos de marca com deferimento publicado a partir de 22/06/2025
# poderão ter a emissão automática do Primeiro decênio de vigência de
# registro de marca e expedição de certificado de registro, sem necessidade
# de pagamento, mesmo se depositados antes da entrada em vigor da nova
# tabela." O corte operacional é a RPI nº 2842 (24/06/2025) — primeira RPI
# publicada a partir de 22/06/2025 (domingo, sem RPI). O valor zero em si
# (serviços 372/373/3012) é da Portaria/INPI/PR nº 10/2025, vigente desde
# 20/09/2025 — mas o critério de isenção retroage ao deferimento, não ao
# depósito, e não é "pago no depósito": é gratuito e automático (mesmo FAQ,
# item 10, resposta explícita "Não").
MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO = date(2025, 6, 24)


def _dispensa_concessao(
    tipo: str, data_deferimento: date, marco: date = MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO
) -> bool:
    """Pedidos de marca com deferimento publicado a partir da RPI nº 2842
    (24/06/2025) são isentos do pagamento do primeiro decênio/certificado de
    registro (serviços 372/373/3012), mesmo se depositados antes da entrada
    em vigor da nova tabela do INPI — ver fonte no comentário de
    ``MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO``.

    ``marco`` é o valor default, mas pode ser sobrescrito pela regra vigente
    em ``RegraJuridicaVersionada`` (codigo="marco_isencao_taxa_concessao").
    """
    return tipo == "pagamento" and data_deferimento >= marco

DESPACHOS_TERMINAIS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(
            r"arquivamento definitivo.*falta de pagamento da concess[aã]o",
            re.IGNORECASE,
        ),
        "cancelado",
        "Não pago — pedido arquivado por falta de pagamento da concessão",
    ),
    (
        re.compile(r"arquivamento definitivo", re.IGNORECASE),
        "cancelado",
        "Pedido arquivado definitivamente pelo INPI",
    ),
    (
        re.compile(r"extin[cç][aã]o (?:do pedido|do registro|da marca)", re.IGNORECASE),
        "cancelado",
        "Processo extinto pelo INPI",
    ),
    (
        re.compile(r"concess[aã]o de registro", re.IGNORECASE),
        "concluido",
        "Registro concedido pelo INPI",
    ),
    (
        re.compile(
            r"recurso.{0,100}(?:n[aã]o provido|provido|decis[aã]o mantida)",
            re.IGNORECASE,
        ),
        "concluido",
        "Recurso decidido pelo INPI",
    ),
)


def _codigos_por_regra_de_texto(
    regras: tuple[tuple[re.Pattern[str], str, str], ...], catalogo: dict[str, str]
) -> dict[str, tuple[str, str]]:
    """Deriva um mapa código→classificação aplicando as mesmas regras de texto
    (DESPACHOS_PRAZO/DESPACHOS_TERMINAIS) às descrições oficiais do catálogo de
    códigos de despacho (``app.badepi.despachos_codigos``, fonte: INPI —
    "Tabela de Códigos de Despachos - Marcas").

    Achado 5.7 da auditoria (02/09/2026), Fase 5: ``codigo_despacho`` (campo
    estruturado da RPI) nunca era usado para classificar, só a descrição em
    texto livre via regex — menos confiável quando a redação varia. Derivar o
    mapa por código A PARTIR das mesmas regras de texto, em vez de curar cada
    código manualmente, garante que a classificação por código nunca cubra
    mais nem menos casos do que a classificação por texto já cobre hoje.
    """
    mapa: dict[str, tuple[str, str]] = {}
    for codigo, descricao in catalogo.items():
        for padrao, a, b in regras:
            if padrao.search(descricao):
                mapa[codigo] = (a, b)
                break
    return mapa


CODIGOS_DESPACHO_PRAZO = _codigos_por_regra_de_texto(DESPACHOS_PRAZO, DESCRICOES_DESPACHO)
CODIGOS_DESPACHO_TERMINAL = _codigos_por_regra_de_texto(DESPACHOS_TERMINAIS, DESCRICOES_DESPACHO)


def _classificar_despacho(
    descricao: str | None,
    codigo_despacho: str | None = None,
    dias_padrao: int = PRAZO_ADMINISTRATIVO_PADRAO_DIAS,
) -> tuple[int, str, str] | None:
    """Deriva (dias, tipo, ação) de uma movimentação de RPI de marca.

    O número soletrado no texto ("prazo de N dias") tem prioridade sobre
    qualquer classificação. Na ausência dele: se ``codigo_despacho`` bate com
    um código conhecido (``CODIGOS_DESPACHO_PRAZO``), usa essa classificação —
    mais confiável que o texto, que varia de redação. Sem código reconhecido,
    cai para o regex sobre ``descricao`` (comportamento anterior). Retorna
    ``None`` para despachos terminais ou sem prazo processual mapeado.
    """
    texto = descricao or ""
    match = PADRAO_PRAZO.search(texto)
    dias_texto = int(match.group(1)) if match else None
    if dias_texto is not None and not 1 <= dias_texto <= 365:
        dias_texto = None
    numero = codigo_numerico(codigo_despacho)
    if numero is not None and numero in CODIGOS_DESPACHO_PRAZO:
        tipo, acao = CODIGOS_DESPACHO_PRAZO[numero]
        return (dias_texto or dias_padrao), tipo, acao
    for padrao, tipo, acao in DESPACHOS_PRAZO:
        if padrao.search(texto):
            return (dias_texto or dias_padrao), tipo, acao
    if dias_texto is not None:
        return dias_texto, "outro", "Prazo indicado no texto da publicação"
    return None


async def _historico_regra(session: AsyncSession, codigo: str) -> list[RegraJuridicaVersionada]:
    resultado = await session.execute(
        select(RegraJuridicaVersionada)
        .where(RegraJuridicaVersionada.codigo == codigo)
        .order_by(RegraJuridicaVersionada.vigencia_inicio)
    )
    return list(resultado.scalars().all())


def _valor_vigente(historico: list[RegraJuridicaVersionada], data_referencia: date, valor_padrao: object) -> object:
    """Escolhe, dentre o histórico de um código, o valor vigente em
    ``data_referencia`` (intervalo [vigencia_inicio, vigencia_fim) —
    vigencia_fim nulo = "vigente até hoje"). Sem linha aplicável, devolve
    ``valor_padrao`` (o hardcoded no código) -- garante que uma tabela vazia
    não muda nenhum comportamento existente.
    """
    for regra in historico:
        if regra.vigencia_inicio <= data_referencia and (
            regra.vigencia_fim is None or data_referencia < regra.vigencia_fim
        ):
            return regra.valor.get("valor", valor_padrao)
    return valor_padrao


class RegraJuridicaInput(BaseModel):
    codigo: Literal["marco_isencao_taxa_concessao", "prazo_administrativo_padrao_dias"]
    valor: int | date
    vigencia_inicio: date
    fonte_legal: str = Field(min_length=10, max_length=2000)
    observacoes: str | None = Field(default=None, max_length=2000)

    @field_validator("valor")
    @classmethod
    def _validar_valor(cls, valor: int | date, info: ValidationInfo) -> int | date:
        codigo = info.data.get("codigo")
        if codigo == "prazo_administrativo_padrao_dias" and (not isinstance(valor, int) or not 1 <= valor <= 365):
            raise ValueError("Para prazo_administrativo_padrao_dias, valor deve ser um inteiro entre 1 e 365 (dias)")
        if codigo == "marco_isencao_taxa_concessao" and not isinstance(valor, date):
            raise ValueError("Para marco_isencao_taxa_concessao, valor deve ser uma data (AAAA-MM-DD)")
        return valor


def _serializar_regra(regra: RegraJuridicaVersionada) -> dict:
    return {
        "id": regra.id,
        "codigo": regra.codigo,
        "valor": regra.valor.get("valor"),
        "vigencia_inicio": regra.vigencia_inicio,
        "vigencia_fim": regra.vigencia_fim,
        "fonte_legal": regra.fonte_legal,
        "observacoes": regra.observacoes,
        "criado_por": regra.criado_por,
        "criado_em": regra.criado_em,
    }


@router.get("/regras")
async def consultar_regras_juridicas(
    session: SessionDep, usuario: ViewDep, codigo: str | None = Query(default=None)
) -> list[dict]:
    consulta = select(RegraJuridicaVersionada).order_by(
        RegraJuridicaVersionada.codigo, RegraJuridicaVersionada.vigencia_inicio.desc()
    )
    if codigo:
        consulta = consulta.where(RegraJuridicaVersionada.codigo == codigo)
    regras = (await session.execute(consulta)).scalars().all()
    return [_serializar_regra(regra) for regra in regras]


@router.post("/regras", status_code=status.HTTP_201_CREATED)
async def criar_regra_juridica(
    dados: RegraJuridicaInput, request: Request, session: SessionDep, usuario: SuperAdminDep
) -> dict:
    """Registra uma nova vigência para um parâmetro jurídico. Exclusivo do
    superadministrador da plataforma: o parâmetro é global (vale para todas
    as organizações), não uma configuração por tenant. Fecha automaticamente
    a vigência aberta anterior do mesmo código, se houver — nunca sobrescreve
    ou apaga histórico (append-only)."""
    aberta = (
        await session.execute(
            select(RegraJuridicaVersionada).where(
                RegraJuridicaVersionada.codigo == dados.codigo,
                RegraJuridicaVersionada.vigencia_fim.is_(None),
            )
        )
    ).scalar_one_or_none()
    if aberta is not None and aberta.vigencia_inicio >= dados.vigencia_inicio:
        raise HTTPException(
            422, "A nova vigência deve começar depois do início da vigência atualmente aberta para este código"
        )
    if aberta is not None:
        aberta.vigencia_fim = dados.vigencia_inicio
    valor_serializado = dados.valor.isoformat() if isinstance(dados.valor, date) else dados.valor
    regra = RegraJuridicaVersionada(
        codigo=dados.codigo,
        valor={"valor": valor_serializado},
        vigencia_inicio=dados.vigencia_inicio,
        fonte_legal=dados.fonte_legal.strip(),
        observacoes=dados.observacoes.strip() if dados.observacoes else None,
        criado_por=usuario.ator,
    )
    session.add(regra)
    await session.flush()
    _auditar(
        session,
        request,
        usuario,
        "criar_regra_juridica",
        f"regra_juridica:{dados.codigo}",
        {"vigencia_inicio": dados.vigencia_inicio.isoformat(), "valor": valor_serializado},
    )
    await session.commit()
    return _serializar_regra(regra)


def _classificar_despacho_terminal(
    descricao: str | None, codigo_despacho: str | None = None
) -> tuple[str, str] | None:
    numero = codigo_numerico(codigo_despacho)
    if numero is not None and numero in CODIGOS_DESPACHO_TERMINAL:
        return CODIGOS_DESPACHO_TERMINAL[numero]
    texto = descricao or ""
    for padrao, status_final, motivo in DESPACHOS_TERMINAIS:
        if padrao.search(texto):
            return status_final, motivo
    return None


def _filtro_despacho_terminal():
    descricao = Movimentacao.descricao
    return or_(
        descricao.ilike("%arquivamento definitivo%"),
        descricao.ilike("%concessão de registro%"),
        descricao.ilike("%extinção do pedido%"),
        descricao.ilike("%extinção do registro%"),
        descricao.ilike("%extinção da marca%"),
        descricao.ilike("%recurso%não provido%"),
        descricao.ilike("%recurso%provido%"),
        descricao.ilike("%recurso%decisão mantida%"),
    )


def _movimentacao_posterior(terminal: Movimentacao, origem: Movimentacao) -> bool:
    if terminal.data_rpi is None or origem.data_rpi is None:
        return False
    return (terminal.data_rpi, terminal.id or 0) > (origem.data_rpi, origem.id or 0)


FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")


def calcular_vencimento(data_base: date, dias: int, contagem: str) -> datetime:
    """Retorna o instante UTC correspondente ao final do dia (23:59:59) em
    Brasília, não 23:59:59 UTC.

    Achado JUR-1 da auditoria (04/09/2026): a versão anterior rotulava
    23:59:59 diretamente como UTC (`tzinfo=UTC`), quando `atual` é uma data
    civil brasileira (calculada com a tabela de feriados nacionais e a
    Portaria INPI/PR nº 08/2022). Isso adiantava o vencimento em 3 horas
    (Brasília = UTC-3): das 21h às 23h59 de Brasília no dia do vencimento,
    o prazo já aparecia como vencido."""
    atual = data_base
    if contagem == "uteis":
        restantes = dias
        while restantes:
            atual += timedelta(days=1)
            if _eh_dia_util(atual):
                restantes -= 1
    else:
        atual += timedelta(days=dias)
        # Portaria/INPI/PR nº 08/2022, art. 6º, § 2º: prorroga automaticamente
        # para o primeiro dia útil o prazo corrido que vença em sábado,
        # domingo ou feriado nacional.
        atual = _proximo_dia_util(atual)
    return datetime.combine(atual, time(23, 59, 59), tzinfo=FUSO_BRASIL).astimezone(UTC)


class PrazoInput(BaseModel):
    processo_monitorado_id: int = Field(ge=1)
    titulo: str = Field(min_length=3, max_length=180)
    descricao: str | None = Field(default=None, max_length=4000)
    tipo: str = "manifestacao"
    data_base: date
    dias_prazo: int = Field(ge=0, le=3650)
    contagem: Literal["corridos", "uteis"] = "corridos"
    responsavel_id: int | None = Field(default=None, ge=1)
    escalonar_para_id: int | None = Field(default=None, ge=1)
    prioridade: Literal["baixa", "media", "alta", "critica"] = "media"
    antecedencia_dias: int = Field(default=7, ge=0, le=365)
    escalonar_dias_antes: int = Field(default=2, ge=0, le=365)

    @field_validator("tipo")
    @classmethod
    def validar_tipo(cls, value: str) -> str:
        if value not in TIPOS_PRAZO:
            raise ValueError("Tipo de prazo inválido")
        return value


class PrazoUpdate(BaseModel):
    status: Literal["aguardando_confirmacao", "pendente", "em_andamento", "concluido", "cancelado"] | None = None
    responsavel_id: int | None = Field(default=None, ge=1)
    escalonar_para_id: int | None = Field(default=None, ge=1)
    prioridade: Literal["baixa", "media", "alta", "critica"] | None = None
    confirmar: bool = False
    confirmacao_observacoes: str | None = Field(default=None, min_length=3, max_length=2000)
    descricao_evento: str | None = Field(default=None, max_length=500)


class PoliticaJuridicaUpdate(BaseModel):
    exigir_evidencia_conclusao: bool = False
    exigir_segunda_pessoa_critico: bool = False


async def obter_politica_juridica(session: AsyncSession, organizacao_id: int) -> PoliticaJuridica:
    politica = (
        await session.execute(select(PoliticaJuridica).where(PoliticaJuridica.organizacao_id == organizacao_id))
    ).scalar_one_or_none()
    return politica or PoliticaJuridica(
        organizacao_id=organizacao_id,
        exigir_evidencia_conclusao=False,
        exigir_segunda_pessoa_critico=False,
    )


def _politica_juridica_dict(politica: PoliticaJuridica) -> dict:
    return {
        "exigir_evidencia_conclusao": politica.exigir_evidencia_conclusao,
        "exigir_segunda_pessoa_critico": politica.exigir_segunda_pessoa_critico,
    }


TAMANHO_MAXIMO_DOCUMENTO_ENTREGA = 15 * 1024 * 1024  # 15 MB decodificado


class EntregaInput(BaseModel):
    descricao: str = Field(min_length=3, max_length=500)
    protocolo: str | None = Field(default=None, max_length=120)
    documento: str | None = Field(default=None, max_length=500)
    documento_nome: str | None = Field(default=None, min_length=1, max_length=255)
    documento_base64: str | None = Field(default=None, min_length=1)
    documento_content_type: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _exigir_nome_com_base64(self) -> "EntregaInput":
        if self.documento_base64 and not self.documento_nome:
            raise ValueError("Informe documento_nome ao anexar documento_base64")
        return self


class ChecklistItemInput(BaseModel):
    descricao: str = Field(min_length=2, max_length=300)


class ChecklistItemUpdate(BaseModel):
    concluido: bool


# Checklist operacional padrão por tipo de prazo (LPI/prática de marcas).
CHECKLIST_PADRAO: dict[str, list[str]] = {
    "oposicao": [
        "Conferir prazo, marca e partes envolvidas",
        "Levantar fundamentos e anterioridades",
        "Elaborar a peça de oposição/manifestação",
        "Emitir e pagar a GRU",
        "Protocolar no INPI",
        "Arquivar o comprovante de protocolo",
    ],
    "recurso": [
        "Analisar o despacho de indeferimento",
        "Levantar os argumentos do recurso",
        "Elaborar as razões de recurso",
        "Emitir e pagar a GRU",
        "Protocolar o recurso no INPI",
        "Arquivar o comprovante",
    ],
    "exigencia": [
        "Ler o teor da exigência no parecer",
        "Reunir os documentos/correções exigidos",
        "Elaborar a petição de cumprimento",
        "Emitir e pagar a GRU (se aplicável)",
        "Protocolar o cumprimento no INPI",
        "Arquivar o comprovante",
    ],
    "pagamento": [
        "Emitir a GRU de concessão/retribuição",
        "Conferir o valor e o código de serviço",
        "Efetuar o pagamento dentro do prazo",
        "Protocolar o comprovante no INPI",
        "Arquivar o comprovante",
    ],
    "manifestacao": [
        "Conferir o objeto da manifestação",
        "Levantar subsídios e provas",
        "Elaborar a manifestação",
        "Protocolar no INPI",
        "Arquivar o comprovante",
    ],
}
CHECKLIST_GENERICO = [
    "Analisar o prazo e o processo",
    "Preparar a providência necessária",
    "Protocolar/registrar no INPI",
    "Arquivar o comprovante",
]


def _serializar_item_checklist(item: ItemChecklistPrazo) -> dict:
    return {
        "id": item.id,
        "descricao": item.descricao,
        "concluido": item.concluido,
        "ordem": item.ordem,
        "concluido_em": item.concluido_em,
        "concluido_por": item.concluido_por,
    }


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


def _evento(
    session: AsyncSession,
    usuario: UsuarioAutenticado,
    monitorado_id: int,
    tipo: str,
    descricao: str,
    prazo_id: int | None = None,
    detalhes: dict | None = None,
) -> None:
    session.add(
        EventoJuridico(
            organizacao_id=usuario.organizacao_id,
            prazo_id=prazo_id,
            processo_monitorado_id=monitorado_id,
            tipo=tipo,
            ator=usuario.ator,
            descricao=descricao[:500],
            detalhes=detalhes or {},
        )
    )
    registrar_evento_operacional(
        session,
        organizacao_id=usuario.organizacao_id,
        dominio="juridico",
        tipo=f"juridico.{tipo}",
        entidade_tipo="prazo" if prazo_id else "processo_monitorado",
        entidade_id=prazo_id or monitorado_id,
        ator=usuario.ator,
        ator_id=getattr(usuario, "id", None),
        payload={"processo_monitorado_id": monitorado_id, **(detalhes or {})},
    )


async def _usuario_valido(session: AsyncSession, organizacao_id: int, usuario_id: int | None) -> bool:
    if usuario_id is None:
        return True
    resultado = await session.execute(
        select(UsuarioOperacoes.id).where(
            UsuarioOperacoes.id == usuario_id,
            UsuarioOperacoes.organizacao_id == organizacao_id,
            UsuarioOperacoes.ativo.is_(True),
        )
    )
    return bool(resultado.scalar_one_or_none())


async def _obter_prazo(session: AsyncSession, usuario: UsuarioAutenticado, prazo_id: int) -> PrazoJuridico:
    prazo = (
        await session.execute(
            select(PrazoJuridico).where(
                PrazoJuridico.id == prazo_id,
                PrazoJuridico.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if prazo is None:
        raise HTTPException(404, "Prazo jurídico não encontrado")
    return prazo


def _dias_restantes(vencimento: datetime) -> int:
    """Dias corridos até o vencimento, contados pelo calendário civil de
    Brasília -- não pelo calendário UTC (achado JUR-1, ver calcular_vencimento).
    Comparar `.date()` em UTC adiantava o vencimento em até 3h todo fim de
    tarde/noite em Brasília."""
    if vencimento.tzinfo is None:
        vencimento = vencimento.replace(tzinfo=UTC)
    agora_brasil = datetime.now(UTC).astimezone(FUSO_BRASIL)
    vencimento_brasil = vencimento.astimezone(FUSO_BRASIL)
    return (vencimento_brasil.date() - agora_brasil.date()).days


def _serializar_prazo(row) -> dict:
    prazo, numero, marca, empresa, responsavel, escalacao = row
    restantes = _dias_restantes(prazo.vencimento_em)
    historico = prazo.status == "historico"
    return {
        "id": prazo.id,
        "processo_monitorado_id": prazo.processo_monitorado_id,
        "numero": numero,
        "marca": marca,
        "empresa": empresa,
        "titulo": prazo.titulo,
        "descricao": prazo.descricao,
        "tipo": prazo.tipo,
        "tipo_nome": TIPOS_PRAZO.get(prazo.tipo, prazo.tipo),
        "origem": prazo.origem,
        "data_base": prazo.data_base,
        "dias_prazo": prazo.dias_prazo,
        "contagem": prazo.contagem,
        "vencimento_em": prazo.vencimento_em,
        "dias_restantes": restantes,
        # Uma publicação antiga, descoberta pelo motor somente depois de o
        # prazo terminar, é referência histórica e não atraso operacional atual.
        "vencido": (restantes < 0 and prazo.status in STATUS_ATIVOS and prazo.confirmado),
        "alerta": (
            "atrasado"
            if restantes < 0 and prazo.status in STATUS_ATIVOS
            else "vence_hoje"
            if restantes == 0 and prazo.status in STATUS_ATIVOS
            else "proximo"
            if restantes <= (prazo.antecedencia_dias or 7) and prazo.status in STATUS_ATIVOS
            else None
        ),
        "historico": historico,
        "status": prazo.status,
        "prioridade": prazo.prioridade,
        "confirmado": prazo.confirmado,
        "confirmado_por": prazo.confirmado_por,
        "confirmado_em": prazo.confirmado_em,
        "confirmacao_origem": prazo.confirmacao_origem,
        "confirmacao_observacoes": prazo.confirmacao_observacoes,
        "responsavel_id": prazo.responsavel_id,
        "responsavel": responsavel,
        "escalonar_para_id": prazo.escalonar_para_id,
        "escalonar_para": escalacao,
        "escalonado_em": prazo.escalonado_em,
    }


@router.get("/referencias")
async def referencias(session: SessionDep, usuario: ViewDep) -> dict:
    pessoas = (
        await session.execute(
            select(UsuarioOperacoes.id, UsuarioOperacoes.nome)
            .where(
                UsuarioOperacoes.organizacao_id == usuario.organizacao_id,
                UsuarioOperacoes.ativo.is_(True),
            )
            .order_by(UsuarioOperacoes.nome)
        )
    ).all()
    processos = (
        await session.execute(
            select(ProcessoMonitorado.id, Processo.numero, Processo.titulo)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .where(
                ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
                ProcessoMonitorado.status == "ativo",
            )
            .order_by(Processo.titulo)
            .limit(1000)
        )
    ).all()
    return {
        "usuarios": [{"id": item.id, "nome": item.nome} for item in pessoas],
        "processos": [{"id": item.id, "nome": f"{item.numero} · {item.titulo or 'Sem título'}"} for item in processos],
        "tipos": [{"id": key, "nome": value} for key, value in TIPOS_PRAZO.items()],
        "acoes": {"gerenciar": usuario.pode("legal.manage")},
    }


@router.get("/agenda")
async def agenda_centralizada(
    session: SessionDep,
    usuario: ViewDep,
    visualizacao: Literal["lista", "calendario"] = "lista",
    responsavel_id: int | None = Query(default=None, ge=1),
    cliente_id: int | None = Query(default=None, ge=1),
    processo_monitorado_id: int | None = Query(default=None, ge=1),
    prioridade: Literal["baixa", "media", "alta", "critica"] | None = None,
    tipo: str | None = Query(default=None, max_length=40),
    inicio: date | None = None,
    fim: date | None = None,
    pagina: int = Query(default=1, ge=1),
    por_pagina: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Agenda única de prazos, com visão de lista ou calendário.

    O endpoint não expõe exclusão: encerramentos são mudanças de status auditadas.
    """
    filtros = [
        PrazoJuridico.organizacao_id == usuario.organizacao_id,
        PrazoJuridico.status != "duplicado",
    ]
    if responsavel_id:
        filtros.append(PrazoJuridico.responsavel_id == responsavel_id)
    if cliente_id:
        filtros.append(ProcessoMonitorado.empresa_id == cliente_id)
    if processo_monitorado_id:
        filtros.append(PrazoJuridico.processo_monitorado_id == processo_monitorado_id)
    if prioridade:
        filtros.append(PrazoJuridico.prioridade == prioridade)
    if tipo:
        filtros.append(PrazoJuridico.tipo == tipo)
    if inicio:
        filtros.append(PrazoJuridico.vencimento_em >= datetime.combine(inicio, time.min, UTC))
    if fim:
        filtros.append(PrazoJuridico.vencimento_em <= datetime.combine(fim, time.max, UTC))

    responsavel = UsuarioOperacoes.__table__.alias("agenda_responsavel")
    escalacao = UsuarioOperacoes.__table__.alias("agenda_escalacao")
    base = (
        select(
            PrazoJuridico,
            Processo.numero,
            Processo.titulo,
            EmpresaCRM.nome,
            responsavel.c.nome,
            escalacao.c.nome,
        )
        .join(ProcessoMonitorado, ProcessoMonitorado.id == PrazoJuridico.processo_monitorado_id)
        .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
        .outerjoin(EmpresaCRM, EmpresaCRM.id == ProcessoMonitorado.empresa_id)
        .outerjoin(responsavel, responsavel.c.id == PrazoJuridico.responsavel_id)
        .outerjoin(escalacao, escalacao.c.id == PrazoJuridico.escalonar_para_id)
        .where(*filtros)
        .order_by(PrazoJuridico.vencimento_em, PrazoJuridico.id)
    )
    total = int((await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one() or 0)
    linhas = (await session.execute(base.limit(por_pagina).offset((pagina - 1) * por_pagina))).all()
    itens = [_serializar_prazo(linha) for linha in linhas]
    calendario: dict[str, list[dict]] = {}
    for item in itens:
        chave = item["vencimento_em"].date().isoformat()
        calendario.setdefault(chave, []).append(
            {
                "id": item["id"],
                "titulo": item["titulo"],
                "tipo": item["tipo"],
                "prioridade": item["prioridade"],
                "alerta": item["alerta"],
            }
        )
    return {
        "visualizacao": visualizacao,
        "itens": itens,
        "calendario": calendario if visualizacao == "calendario" else {},
        "paginacao": {
            "pagina": pagina,
            "por_pagina": por_pagina,
            "total": total,
            "total_paginas": max(1, (total + por_pagina - 1) // por_pagina),
        },
        "regras": {"exclusao_permitida": False, "encerramento_exige_auditoria": True},
    }


@router.get("/painel")
async def painel(
    session: SessionDep,
    usuario: ViewDep,
    busca: str | None = Query(default=None, max_length=150),
    status_prazo: str | None = Query(default=None, max_length=30),
    responsavel_id: int | None = Query(default=None, ge=1),
    inicio: date | None = None,
    fim: date | None = None,
    limite: int = Query(default=10, ge=1, le=10),
    deslocamento: int = Query(default=0, ge=0),
    prioridade: str | None = Query(default=None, max_length=10),
    cliente_id: int | None = Query(default=None, ge=1),
    processo_monitorado_id: int | None = Query(default=None, ge=1),
    tipo: str | None = Query(default=None, max_length=40),
) -> dict:
    filtros = [PrazoJuridico.organizacao_id == usuario.organizacao_id]
    if status_prazo:
        filtros.append(PrazoJuridico.status == status_prazo)
    else:
        # Duplicidades técnicas ficam preservadas para auditoria, mas não
        # poluem a agenda operacional.
        filtros.append(PrazoJuridico.status != "duplicado")
    if responsavel_id:
        filtros.append(PrazoJuridico.responsavel_id == responsavel_id)
    if prioridade:
        filtros.append(PrazoJuridico.prioridade == prioridade)
    if cliente_id:
        filtros.append(ProcessoMonitorado.empresa_id == cliente_id)
    if processo_monitorado_id:
        filtros.append(PrazoJuridico.processo_monitorado_id == processo_monitorado_id)
    if tipo:
        filtros.append(PrazoJuridico.tipo == tipo)
    if inicio:
        filtros.append(PrazoJuridico.vencimento_em >= datetime.combine(inicio, time.min, UTC))
    if fim:
        filtros.append(PrazoJuridico.vencimento_em <= datetime.combine(fim, time.max, UTC))
    if busca:
        termo = f"%{busca.strip()}%"
        filtros.append(
            or_(
                Processo.numero.ilike(termo),
                Processo.titulo.ilike(termo),
                EmpresaCRM.nome.ilike(termo),
                PrazoJuridico.titulo.ilike(termo),
            )
        )
    responsavel = UsuarioOperacoes.__table__.alias("responsavel")
    escalacao = UsuarioOperacoes.__table__.alias("escalacao")
    # Achado JUR-1 da auditoria (04/09/2026): janelas "hoje"/"7 dias" devem
    # ser calculadas pelo calendário civil de Brasília, não UTC (ver
    # calcular_vencimento/_dias_restantes para o mesmo achado).
    hoje_brasil = datetime.now(UTC).astimezone(FUSO_BRASIL).date()
    hoje_inicio = datetime.combine(hoje_brasil, time.min, FUSO_BRASIL).astimezone(UTC)
    hoje_fim = datetime.combine(hoje_brasil, time.max, FUSO_BRASIL).astimezone(UTC)
    sete_dias_fim = datetime.combine(hoje_brasil + timedelta(days=7), time.max, FUSO_BRASIL).astimezone(UTC)
    metricas_row = (
        await session.execute(
            select(
                func.count(PrazoJuridico.id),
                func.count(PrazoJuridico.id).filter(
                    PrazoJuridico.vencimento_em < hoje_inicio,
                    PrazoJuridico.status.in_(STATUS_ATIVOS),
                ),
                func.count(PrazoJuridico.id).filter(
                    PrazoJuridico.vencimento_em >= hoje_inicio,
                    PrazoJuridico.vencimento_em <= hoje_fim,
                    PrazoJuridico.status.in_(STATUS_ATIVOS),
                ),
                func.count(PrazoJuridico.id).filter(
                    PrazoJuridico.vencimento_em >= hoje_inicio,
                    PrazoJuridico.vencimento_em <= sete_dias_fim,
                    PrazoJuridico.status.in_(STATUS_ATIVOS),
                ),
                func.count(PrazoJuridico.id).filter(PrazoJuridico.confirmado.is_(False)),
                func.count(func.distinct(ProcessoMonitorado.id)),
            )
            .select_from(PrazoJuridico)
            .join(
                ProcessoMonitorado,
                ProcessoMonitorado.id == PrazoJuridico.processo_monitorado_id,
            )
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .outerjoin(EmpresaCRM, EmpresaCRM.id == ProcessoMonitorado.empresa_id)
            .where(*filtros)
        )
    ).one()
    (
        total_prazos,
        vencidos,
        vence_hoje,
        proximos_7_dias,
        aguardando_confirmacao,
        total_clientes,
    ) = metricas_row
    ids_pagina = list(
        (
            await session.execute(
                select(ProcessoMonitorado.id)
                .select_from(PrazoJuridico)
                .join(
                    ProcessoMonitorado,
                    ProcessoMonitorado.id == PrazoJuridico.processo_monitorado_id,
                )
                .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
                .outerjoin(EmpresaCRM, EmpresaCRM.id == ProcessoMonitorado.empresa_id)
                .where(*filtros)
                .group_by(ProcessoMonitorado.id)
                .order_by(func.min(PrazoJuridico.vencimento_em), ProcessoMonitorado.id)
                .limit(limite)
                .offset(deslocamento)
            )
        )
        .scalars()
        .all()
    )
    query = (
        select(
            PrazoJuridico,
            Processo.numero,
            Processo.titulo,
            EmpresaCRM.nome,
            responsavel.c.nome,
            escalacao.c.nome,
        )
        .join(ProcessoMonitorado, ProcessoMonitorado.id == PrazoJuridico.processo_monitorado_id)
        .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
        .outerjoin(EmpresaCRM, EmpresaCRM.id == ProcessoMonitorado.empresa_id)
        .outerjoin(responsavel, responsavel.c.id == PrazoJuridico.responsavel_id)
        .outerjoin(escalacao, escalacao.c.id == PrazoJuridico.escalonar_para_id)
        .where(*filtros, ProcessoMonitorado.id.in_(ids_pagina))
        .order_by(PrazoJuridico.vencimento_em, PrazoJuridico.id)
    )
    itens = [_serializar_prazo(row) for row in (await session.execute(query)).all()]
    metricas = {
        "vencidos": int(vencidos or 0),
        "vence_hoje": int(vence_hoje or 0),
        "proximos_7_dias": int(proximos_7_dias or 0),
        "aguardando_confirmacao": int(aguardando_confirmacao or 0),
        "total": int(total_prazos or 0),
    }
    notificacoes = (
        await session.execute(
            select(NotificacaoJuridica, PrazoJuridico, Processo.numero)
            .join(PrazoJuridico, PrazoJuridico.id == NotificacaoJuridica.prazo_id)
            .join(ProcessoMonitorado, ProcessoMonitorado.id == PrazoJuridico.processo_monitorado_id)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .where(
                NotificacaoJuridica.organizacao_id == usuario.organizacao_id,
                NotificacaoJuridica.status != "arquivada",
                or_(
                    NotificacaoJuridica.destinatario_id.is_(None),
                    NotificacaoJuridica.destinatario_id == usuario.id,
                ),
            )
            .order_by(NotificacaoJuridica.criado_em.desc())
            .limit(50)
        )
    ).all()
    historico = (
        await session.execute(
            select(EventoJuridico, Processo.numero)
            .join(ProcessoMonitorado, ProcessoMonitorado.id == EventoJuridico.processo_monitorado_id)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .where(EventoJuridico.organizacao_id == usuario.organizacao_id)
            .order_by(EventoJuridico.criado_em.desc())
            .limit(100)
        )
    ).all()
    return {
        "metricas": metricas,
        "prazos": itens,
        "paginacao": {
            "total_clientes": int(total_clientes or 0),
            "limite": limite,
            "deslocamento": deslocamento,
            "pagina": (deslocamento // limite) + 1,
            "total_paginas": max(1, ((int(total_clientes or 0) + limite - 1) // limite)),
            "tem_anterior": deslocamento > 0,
            "tem_proxima": deslocamento + len(ids_pagina) < int(total_clientes or 0),
        },
        "notificacoes": [
            {
                "id": item.id,
                "prazo_id": item.prazo_id,
                "numero": numero,
                "tipo": item.tipo,
                "titulo": item.titulo,
                "mensagem": item.mensagem,
                "status": item.status,
                "criado_em": item.criado_em,
            }
            for item, _prazo, numero in notificacoes
        ],
        "historico": [
            {
                "id": evento.id,
                "prazo_id": evento.prazo_id,
                "numero": numero,
                "tipo": evento.tipo,
                "ator": evento.ator,
                "descricao": evento.descricao,
                "detalhes": evento.detalhes,
                "criado_em": evento.criado_em,
            }
            for evento, numero in historico
        ],
        "acoes": {"gerenciar": usuario.pode("legal.manage")},
    }


@router.get("/indicadores")
async def indicadores_juridicos(
    session: SessionDep,
    usuario: ViewDep,
    dias: int = Query(default=90, ge=1, le=365),
) -> dict:
    """Indicadores de gestão do módulo jurídico — achado 5.10 da auditoria
    (02/09/2026), Fase 10: o painel só tinha contadores operacionais do
    momento (vencidos, vence hoje, aguardando confirmação); nada media
    desempenho ao longo do tempo. Quatro indicadores, todos sobre dados já
    existentes (nenhuma coluna nova):

    - taxa_cumprimento: % de prazos concluídos dentro do prazo vs. em atraso.
    - tempo_medio_confirmacao_horas: quão rápido a equipe reage a um prazo
      novo (só confirmação humana via revisão — não a auto-confirmada na
      criação manual nem a dispensa/histórico automáticos do motor).
    - carga_por_responsavel: prazos ativos e atrasados, por responsável.
    - taxa_escalonamento: % de prazos elegíveis (com escalonar_para_id) que
      de fato precisaram ser escalados.
    """
    agora = datetime.now(UTC)
    desde = agora - timedelta(days=dias)

    cumprimento = (
        await session.execute(
            select(
                func.count(PrazoJuridico.id).filter(PrazoJuridico.concluido_em <= PrazoJuridico.vencimento_em),
                func.count(PrazoJuridico.id).filter(PrazoJuridico.concluido_em > PrazoJuridico.vencimento_em),
            ).where(
                PrazoJuridico.organizacao_id == usuario.organizacao_id,
                PrazoJuridico.status == "concluido",
                PrazoJuridico.concluido_em >= desde,
            )
        )
    ).one()
    no_prazo, atrasados_concluidos = (int(v or 0) for v in cumprimento)
    total_concluidos = no_prazo + atrasados_concluidos

    tempo_confirmacao = (
        await session.execute(
            select(func.avg(func.extract("epoch", PrazoJuridico.confirmado_em - PrazoJuridico.criado_em))).where(
                PrazoJuridico.organizacao_id == usuario.organizacao_id,
                PrazoJuridico.confirmacao_origem == "revisao_humana",
                PrazoJuridico.confirmado_em >= desde,
            )
        )
    ).scalar_one()

    carga_rows = (
        await session.execute(
            select(
                PrazoJuridico.responsavel_id,
                UsuarioOperacoes.nome,
                func.count(PrazoJuridico.id),
                func.count(PrazoJuridico.id).filter(PrazoJuridico.vencimento_em < agora),
            )
            .join(UsuarioOperacoes, UsuarioOperacoes.id == PrazoJuridico.responsavel_id)
            .where(
                PrazoJuridico.organizacao_id == usuario.organizacao_id,
                PrazoJuridico.status.in_(STATUS_ATIVOS),
                PrazoJuridico.responsavel_id.is_not(None),
            )
            .group_by(PrazoJuridico.responsavel_id, UsuarioOperacoes.nome)
            .order_by(func.count(PrazoJuridico.id).desc())
        )
    ).all()

    escalonamento = (
        await session.execute(
            select(
                func.count(PrazoJuridico.id),
                func.count(PrazoJuridico.id).filter(PrazoJuridico.escalonado_em.is_not(None)),
            ).where(
                PrazoJuridico.organizacao_id == usuario.organizacao_id,
                PrazoJuridico.escalonar_para_id.is_not(None),
                PrazoJuridico.criado_em >= desde,
            )
        )
    ).one()
    elegiveis, escalonados = (int(v or 0) for v in escalonamento)

    return {
        "periodo_dias": dias,
        "taxa_cumprimento": {
            "concluidos_no_prazo": no_prazo,
            "concluidos_atrasados": atrasados_concluidos,
            "total_concluidos": total_concluidos,
            "percentual_no_prazo": round(100 * no_prazo / total_concluidos, 1) if total_concluidos else None,
        },
        "tempo_medio_confirmacao_horas": (
            round(float(tempo_confirmacao) / 3600, 1) if tempo_confirmacao is not None else None
        ),
        "carga_por_responsavel": [
            {
                "responsavel_id": responsavel_id,
                "responsavel_nome": nome,
                "ativos": int(ativos or 0),
                "atrasados": int(atrasados or 0),
            }
            for responsavel_id, nome, ativos, atrasados in carga_rows
        ],
        "taxa_escalonamento": {
            "elegiveis": elegiveis,
            "escalonados": escalonados,
            "percentual": round(100 * escalonados / elegiveis, 1) if elegiveis else None,
        },
    }


@router.post("/prazos", status_code=status.HTTP_201_CREATED)
async def criar_prazo(dados: PrazoInput, request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    monitorado = (
        await session.execute(
            select(ProcessoMonitorado).where(
                ProcessoMonitorado.id == dados.processo_monitorado_id,
                ProcessoMonitorado.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if monitorado is None:
        raise HTTPException(404, "Processo monitorado não encontrado")
    for user_id in (dados.responsavel_id, dados.escalonar_para_id):
        if not await _usuario_valido(session, usuario.organizacao_id, user_id):
            raise HTTPException(404, "Responsável não encontrado")
    prazo = PrazoJuridico(
        organizacao_id=usuario.organizacao_id,
        processo_monitorado_id=dados.processo_monitorado_id,
        titulo=dados.titulo.strip(),
        descricao=dados.descricao.strip() if dados.descricao else None,
        tipo=dados.tipo,
        origem="manual",
        contagem=dados.contagem,
        data_base=dados.data_base,
        dias_prazo=dados.dias_prazo,
        vencimento_em=calcular_vencimento(dados.data_base, dados.dias_prazo, dados.contagem),
        status="pendente",
        prioridade=dados.prioridade,
        confirmado=True,
        confirmado_por_id=usuario.id,
        confirmado_por=usuario.ator,
        confirmado_em=datetime.now(UTC),
        confirmacao_origem="criacao_manual",
        confirmacao_observacoes="Prazo criado e confirmado manualmente pelo operador.",
        responsavel_id=dados.responsavel_id,
        escalonar_para_id=dados.escalonar_para_id,
        antecedencia_dias=dados.antecedencia_dias,
        escalonar_dias_antes=dados.escalonar_dias_antes,
        criado_por=usuario.ator,
    )
    session.add(prazo)
    await session.flush()
    _evento(
        session,
        usuario,
        monitorado.id,
        "prazo_criado",
        "Prazo jurídico criado",
        prazo.id,
        {"vencimento": prazo.vencimento_em.isoformat()},
    )
    _auditar(
        session,
        request,
        usuario,
        "criar_prazo",
        f"prazo:{prazo.id}",
        {"processo_monitorado_id": monitorado.id},
    )
    await session.commit()
    return {"id": prazo.id, "vencimento_em": prazo.vencimento_em, "status": prazo.status}


@router.patch("/prazos/{prazo_id}")
async def atualizar_prazo(
    prazo_id: int,
    dados: PrazoUpdate,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    prazo = await _obter_prazo(session, usuario, prazo_id)
    for user_id in (dados.responsavel_id, dados.escalonar_para_id):
        if user_id and not await _usuario_valido(session, usuario.organizacao_id, user_id):
            raise HTTPException(404, "Responsável não encontrado")
    anterior = prazo.status
    if dados.confirmar:
        responsavel_final = dados.responsavel_id or prazo.responsavel_id
        if responsavel_final is None:
            raise HTTPException(422, "Defina o responsável antes de confirmar o prazo")
        if not dados.confirmacao_observacoes:
            raise HTTPException(422, "Informe as observações da confirmação jurídica")
        prazo.confirmado = True
        prazo.status = "pendente"
        prazo.confirmado_por_id = usuario.id
        prazo.confirmado_por = usuario.ator
        prazo.confirmado_em = datetime.now(UTC)
        prazo.confirmacao_origem = "revisao_humana"
        prazo.confirmacao_observacoes = dados.confirmacao_observacoes.strip()
    if dados.status == "cancelado" and not (dados.descricao_evento and dados.descricao_evento.strip()):
        raise HTTPException(422, "Informe a justificativa do cancelamento")
    if dados.status == "concluido" and anterior in STATUS_ATIVOS:
        politica = await obter_politica_juridica(session, usuario.organizacao_id)
        if politica.exigir_evidencia_conclusao:
            entrega_existente = (
                await session.execute(
                    select(EventoJuridico.id)
                    .where(EventoJuridico.prazo_id == prazo.id, EventoJuridico.tipo == "entrega_registrada")
                    .limit(1)
                )
            ).scalar_one_or_none()
            if entrega_existente is None:
                raise HTTPException(
                    422, "Registre ao menos uma entrega (protocolo/documento) antes de concluir este prazo"
                )
        if (
            politica.exigir_segunda_pessoa_critico
            and prazo.prioridade == "critica"
            and prazo.confirmado_por_id is not None
            and prazo.confirmado_por_id == usuario.id
        ):
            raise HTTPException(
                422, "Prazo crítico exige confirmação por uma segunda pessoa antes da conclusão"
            )
    if dados.status:
        prazo.status = dados.status
        if dados.status == "concluido":
            prazo.concluido_em = datetime.now(UTC)
            prazo.concluido_por = usuario.ator
    if dados.responsavel_id is not None:
        prazo.responsavel_id = dados.responsavel_id
    if dados.escalonar_para_id is not None:
        prazo.escalonar_para_id = dados.escalonar_para_id
    if dados.prioridade:
        prazo.prioridade = dados.prioridade
    descricao = dados.descricao_evento or f"Prazo atualizado de {anterior} para {prazo.status}"
    _evento(
        session,
        usuario,
        prazo.processo_monitorado_id,
        "prazo_atualizado",
        descricao,
        prazo.id,
        {
            "status_anterior": anterior,
            "status_atual": prazo.status,
            "confirmado": prazo.confirmado,
            "confirmacao_origem": prazo.confirmacao_origem,
        },
    )
    _auditar(session, request, usuario, "atualizar_prazo", f"prazo:{prazo.id}", {"status": prazo.status})
    await session.commit()
    return {"id": prazo.id, "status": prazo.status, "confirmado": prazo.confirmado}


@router.get("/politica")
async def consultar_politica_juridica(session: SessionDep, usuario: ViewDep) -> dict:
    return _politica_juridica_dict(await obter_politica_juridica(session, usuario.organizacao_id))


@router.put("/politica")
async def editar_politica_juridica(
    dados: PoliticaJuridicaUpdate, session: SessionDep, usuario: ManageDep
) -> dict:
    politica = (
        await session.execute(select(PoliticaJuridica).where(PoliticaJuridica.organizacao_id == usuario.organizacao_id))
    ).scalar_one_or_none()
    if politica is None:
        politica = PoliticaJuridica(organizacao_id=usuario.organizacao_id)
        session.add(politica)
    for campo, valor in dados.model_dump().items():
        setattr(politica, campo, valor)
    politica.atualizado_por = usuario.ator
    await session.commit()
    return _politica_juridica_dict(politica)


def _slug_documento(valor: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9._-]", "_", valor))[:180] or "documento"


@router.post("/prazos/{prazo_id}/entregas", status_code=status.HTTP_201_CREATED)
async def registrar_entrega(
    prazo_id: int,
    dados: EntregaInput,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    prazo = await _obter_prazo(session, usuario, prazo_id)
    detalhes = {"protocolo": dados.protocolo, "documento": dados.documento}
    documento_id = None
    if dados.documento_base64:
        try:
            conteudo = base64.b64decode(dados.documento_base64, validate=True)
        except Exception as exc:
            raise HTTPException(422, "documento_base64 inválido") from exc
        if len(conteudo) > TAMANHO_MAXIMO_DOCUMENTO_ENTREGA:
            raise HTTPException(
                422, f"Documento excede o tamanho máximo de {TAMANHO_MAXIMO_DOCUMENTO_ENTREGA // (1024 * 1024)} MB"
            )
        digest = hashlib.sha256(conteudo).hexdigest()
        try:
            caminho = save_bytes(
                f"juridico/{usuario.organizacao_id}/{prazo.id}/{digest}-{_slug_documento(dados.documento_nome)}",
                conteudo,
            )
        except StorageError as exc:
            raise HTTPException(503, str(exc)) from exc
        documento = DocumentoEntregaJuridico(
            organizacao_id=usuario.organizacao_id,
            prazo_id=prazo.id,
            nome=dados.documento_nome,
            hash_documento=digest,
            caminho=caminho,
            content_type=dados.documento_content_type,
            criado_por=usuario.ator,
        )
        session.add(documento)
        await session.flush()
        documento_id = documento.id
        detalhes["documento_anexo"] = {"id": documento.id, "nome": dados.documento_nome, "hash": digest}
    _evento(
        session,
        usuario,
        prazo.processo_monitorado_id,
        "entrega_registrada",
        dados.descricao,
        prazo.id,
        detalhes,
    )
    _auditar(session, request, usuario, "registrar_entrega", f"prazo:{prazo.id}", detalhes)
    await session.commit()
    return {"registrado": True, "documento_id": documento_id}


@router.get("/prazos/{prazo_id}/documentos")
async def listar_documentos_entrega(prazo_id: int, session: SessionDep, usuario: ViewDep) -> dict:
    await _obter_prazo(session, usuario, prazo_id)
    documentos = (
        (
            await session.execute(
                select(DocumentoEntregaJuridico)
                .where(
                    DocumentoEntregaJuridico.prazo_id == prazo_id,
                    DocumentoEntregaJuridico.organizacao_id == usuario.organizacao_id,
                )
                .order_by(DocumentoEntregaJuridico.criado_em.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "documentos": [
            {
                "id": documento.id,
                "nome": documento.nome,
                "hash": documento.hash_documento,
                "content_type": documento.content_type,
                "criado_por": documento.criado_por,
                "criado_em": documento.criado_em,
            }
            for documento in documentos
        ]
    }


class VincularClienteInput(BaseModel):
    empresa_nome: str | None = Field(default=None, min_length=2, max_length=200)

    @field_validator("empresa_nome", mode="before")
    @classmethod
    def _limpar_nome(cls, valor: object) -> str | None:
        if valor is None:
            return None
        limpo = re.sub(r"\s+", " ", str(valor)).strip()
        return limpo or None


@router.post("/prazos/{prazo_id}/vincular-cliente")
async def vincular_cliente(
    prazo_id: int,
    dados: VincularClienteInput,
    request: Request,
    session: SessionDep,
    usuario: ManageDep,
) -> dict:
    """Vincula (ou cria) o cliente no CRM ao processo monitorado do prazo. Usa o
    nome informado ou, na ausência, o titular do processo."""
    prazo = await _obter_prazo(session, usuario, prazo_id)
    monitorado = await session.get(ProcessoMonitorado, prazo.processo_monitorado_id)
    if monitorado is None:
        raise HTTPException(404, "Processo monitorado não encontrado")
    nome = dados.empresa_nome
    if not nome:
        nome = (
            await session.execute(
                select(Titular.nome)
                .join(processo_titulares, processo_titulares.c.titular_id == Titular.id)
                .where(processo_titulares.c.processo_id == monitorado.processo_id)
                .order_by(Titular.nome)
                .limit(1)
            )
        ).scalar_one_or_none()
    empresa = await obter_ou_criar_empresa(session, usuario.organizacao_id, nome)
    if empresa is None:
        raise HTTPException(400, "Informe o nome do cliente (o processo não tem titular cadastrado).")
    monitorado.empresa_id = empresa.id
    monitorado.atualizado_em = datetime.now(UTC)
    _auditar(
        session,
        request,
        usuario,
        "vincular_cliente_crm",
        f"prazo:{prazo.id}",
        {"empresa": empresa.nome},
    )
    await session.commit()
    return {"empresa": empresa.nome}


async def _obter_item_checklist(session: AsyncSession, usuario: UsuarioAutenticado, item_id: int) -> ItemChecklistPrazo:
    item = (
        await session.execute(
            select(ItemChecklistPrazo).where(
                ItemChecklistPrazo.id == item_id,
                ItemChecklistPrazo.organizacao_id == usuario.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Item de checklist não encontrado")
    return item


async def _listar_checklist(session: AsyncSession, usuario: UsuarioAutenticado, prazo_id: int) -> dict:
    itens = (
        (
            await session.execute(
                select(ItemChecklistPrazo)
                .where(
                    ItemChecklistPrazo.prazo_id == prazo_id,
                    ItemChecklistPrazo.organizacao_id == usuario.organizacao_id,
                )
                .order_by(ItemChecklistPrazo.ordem, ItemChecklistPrazo.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        "itens": [_serializar_item_checklist(item) for item in itens],
        "total": len(itens),
        "concluidos": sum(1 for item in itens if item.concluido),
    }


@router.get("/checklists/resumo")
async def resumo_checklists(session: SessionDep, usuario: ViewDep) -> dict:
    """Progresso de checklist por prazo (para os cartões do Kanban)."""
    linhas = (
        await session.execute(
            select(
                ItemChecklistPrazo.prazo_id,
                func.count(),
                func.count().filter(ItemChecklistPrazo.concluido),
            )
            .where(ItemChecklistPrazo.organizacao_id == usuario.organizacao_id)
            .group_by(ItemChecklistPrazo.prazo_id)
        )
    ).all()
    return {str(prazo_id): {"total": total, "concluidos": feitos} for prazo_id, total, feitos in linhas}


@router.get("/prazos/{prazo_id}/checklist")
async def obter_checklist(prazo_id: int, session: SessionDep, usuario: ViewDep) -> dict:
    await _obter_prazo(session, usuario, prazo_id)
    return await _listar_checklist(session, usuario, prazo_id)


@router.post("/prazos/{prazo_id}/checklist", status_code=status.HTTP_201_CREATED)
async def adicionar_item_checklist(
    prazo_id: int, dados: ChecklistItemInput, session: SessionDep, usuario: ManageDep
) -> dict:
    await _obter_prazo(session, usuario, prazo_id)
    ordem = (
        await session.execute(
            select(func.coalesce(func.max(ItemChecklistPrazo.ordem), 0)).where(ItemChecklistPrazo.prazo_id == prazo_id)
        )
    ).scalar_one()
    session.add(
        ItemChecklistPrazo(
            organizacao_id=usuario.organizacao_id,
            prazo_id=prazo_id,
            descricao=dados.descricao,
            ordem=ordem + 1,
        )
    )
    await session.commit()
    return await _listar_checklist(session, usuario, prazo_id)


@router.post("/prazos/{prazo_id}/checklist/padrao", status_code=status.HTTP_201_CREATED)
async def aplicar_checklist_padrao(prazo_id: int, session: SessionDep, usuario: ManageDep) -> dict:
    prazo = await _obter_prazo(session, usuario, prazo_id)
    existentes = (
        await session.execute(
            select(func.count()).select_from(ItemChecklistPrazo).where(ItemChecklistPrazo.prazo_id == prazo_id)
        )
    ).scalar_one()
    if existentes:
        raise HTTPException(409, "Este prazo já possui itens de checklist.")
    modelo = CHECKLIST_PADRAO.get(prazo.tipo, CHECKLIST_GENERICO)
    for ordem, descricao in enumerate(modelo, start=1):
        session.add(
            ItemChecklistPrazo(
                organizacao_id=usuario.organizacao_id,
                prazo_id=prazo_id,
                descricao=descricao,
                ordem=ordem,
            )
        )
    _evento(
        session,
        usuario,
        prazo.processo_monitorado_id,
        "checklist_padrao",
        f"Checklist padrão aplicado ({len(modelo)} itens)",
        prazo.id,
    )
    await session.commit()
    return await _listar_checklist(session, usuario, prazo_id)


@router.patch("/checklist/{item_id}")
async def atualizar_item_checklist(
    item_id: int, dados: ChecklistItemUpdate, session: SessionDep, usuario: ManageDep
) -> dict:
    item = await _obter_item_checklist(session, usuario, item_id)
    item.concluido = dados.concluido
    item.concluido_em = datetime.now(UTC) if dados.concluido else None
    item.concluido_por = usuario.ator if dados.concluido else None
    await session.commit()
    return _serializar_item_checklist(item)


@router.delete("/checklist/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_item_checklist(item_id: int, session: SessionDep, usuario: ManageDep) -> None:
    item = await _obter_item_checklist(session, usuario, item_id)
    await session.delete(item)
    await session.commit()


@router.patch("/notificacoes/{notificacao_id}")
async def ler_notificacao(
    notificacao_id: int,
    request: Request,
    session: SessionDep,
    usuario: ViewDep,
) -> dict:
    item = (
        await session.execute(
            select(NotificacaoJuridica).where(
                NotificacaoJuridica.id == notificacao_id,
                NotificacaoJuridica.organizacao_id == usuario.organizacao_id,
                or_(
                    NotificacaoJuridica.destinatario_id.is_(None),
                    NotificacaoJuridica.destinatario_id == usuario.id,
                ),
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Notificação não encontrada")
    item.status = "lida"
    item.lida_em = datetime.now(UTC)
    item.lida_por = usuario.ator
    prazo = await _obter_prazo(session, usuario, item.prazo_id)
    _evento(
        session,
        usuario,
        prazo.processo_monitorado_id,
        "notificacao_lida",
        f"Notificação lida: {item.titulo}",
        prazo.id,
        {"notificacao_id": item.id},
    )
    _auditar(session, request, usuario, "ler_notificacao", f"notificacao:{item.id}", {})
    await session.commit()
    return {"id": item.id, "status": item.status, "lida_em": item.lida_em}


async def _emails_usuarios(session: AsyncSession, organizacao_id: int, usuario_ids: set[int]) -> dict[int, str]:
    if not usuario_ids:
        return {}
    linhas = (
        await session.execute(
            select(UsuarioOperacoes.id, UsuarioOperacoes.email).where(
                UsuarioOperacoes.organizacao_id == organizacao_id,
                UsuarioOperacoes.id.in_(usuario_ids),
            )
        )
    ).all()
    return dict(linhas)


async def _notificar(
    session: AsyncSession,
    prazo: PrazoJuridico,
    tipo: str,
    destinatario_id: int | None,
    titulo: str,
    mensagem: str,
    email_destinatario: str | None = None,
) -> bool:
    chave = f"juridico:{prazo.organizacao_id}:{prazo.id}:{tipo}:{destinatario_id or 0}"
    existe = (
        await session.execute(select(NotificacaoJuridica.id).where(NotificacaoJuridica.chave == chave))
    ).scalar_one_or_none()
    if existe:
        return False
    session.add(
        NotificacaoJuridica(
            organizacao_id=prazo.organizacao_id,
            prazo_id=prazo.id,
            destinatario_id=destinatario_id,
            chave=chave,
            tipo=tipo,
            titulo=titulo,
            mensagem=mensagem,
            status="nova",
        )
    )
    # Achado 5.9 da auditoria (Fase 8): antes só o registro acima existia --
    # ninguém era avisado de fato fora do painel. E-mail é reforço, não
    # substitui a central de notificações (falha de envio não desfaz o
    # registro nem interrompe o motor -- ver enviar_alerta_prazo_juridico).
    if email_destinatario:
        await enviar_alerta_prazo_juridico(email_destinatario, titulo, mensagem)
    return True


async def _terminais_dos_processos(session: AsyncSession, processo_ids: set[int]) -> dict[int, Movimentacao]:
    if not processo_ids:
        return {}
    movimentacoes = (
        (
            await session.execute(
                select(Movimentacao)
                .where(
                    Movimentacao.processo_id.in_(processo_ids),
                    Movimentacao.data_rpi.is_not(None),
                    _filtro_despacho_terminal(),
                )
                .order_by(
                    Movimentacao.processo_id,
                    Movimentacao.data_rpi.desc(),
                    Movimentacao.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    terminais: dict[int, Movimentacao] = {}
    for movimentacao in movimentacoes:
        if _classificar_despacho_terminal(movimentacao.descricao, movimentacao.codigo_despacho) is not None:
            terminais.setdefault(movimentacao.processo_id, movimentacao)
    return terminais


async def _reconciliar_prazos_terminais(
    session: AsyncSession,
    organizacao_id: int,
    ator: str,
) -> tuple[int, dict[int, Movimentacao]]:
    """Encerra tarefas automáticas invalidadas por um despacho posterior da RPI."""
    pendencias = (
        await session.execute(
            select(PrazoJuridico, Movimentacao)
            .join(Movimentacao, Movimentacao.id == PrazoJuridico.movimentacao_origem_id)
            .where(
                PrazoJuridico.organizacao_id == organizacao_id,
                PrazoJuridico.origem == "motor_rpi",
                PrazoJuridico.status.in_(STATUS_ATIVOS),
            )
        )
    ).all()
    processo_ids = {origem.processo_id for _prazo, origem in pendencias}
    terminais = await _terminais_dos_processos(session, processo_ids)
    agora = datetime.now(UTC)
    reconciliados: list[PrazoJuridico] = []
    motor_usuario = SimpleNamespace(organizacao_id=organizacao_id, ator=ator)
    for prazo, origem in pendencias:
        terminal = terminais.get(origem.processo_id)
        if terminal is None or not _movimentacao_posterior(terminal, origem):
            continue
        classificacao = _classificar_despacho_terminal(terminal.descricao, terminal.codigo_despacho)
        if classificacao is None:
            continue
        status_final, motivo = classificacao
        prazo.status = status_final
        prazo.confirmado = True
        prazo.concluido_em = agora
        prazo.concluido_por = ator
        reconciliados.append(prazo)
        _evento(
            session,
            motor_usuario,
            prazo.processo_monitorado_id,
            "prazo_reconciliado",
            f"{motivo} (RPI {terminal.numero_rpi})",
            prazo.id,
            {
                "movimentacao_origem_id": origem.id,
                "movimentacao_terminal_id": terminal.id,
                "rpi_terminal": terminal.numero_rpi,
                "status_final": status_final,
            },
        )
    if reconciliados:
        notificacoes = (
            (
                await session.execute(
                    select(NotificacaoJuridica).where(
                        NotificacaoJuridica.organizacao_id == organizacao_id,
                        NotificacaoJuridica.prazo_id.in_([prazo.id for prazo in reconciliados]),
                        NotificacaoJuridica.status != "arquivada",
                    )
                )
            )
            .scalars()
            .all()
        )
        for notificacao in notificacoes:
            notificacao.status = "arquivada"
            notificacao.lida_em = agora
            notificacao.lida_por = ator
    return len(reconciliados), terminais


def _chave_publicacao(prazo: PrazoJuridico, origem: Movimentacao) -> tuple:
    """Identifica uma publicação mesmo quando a importação duplicou a linha."""
    return (
        prazo.processo_monitorado_id,
        prazo.tipo,
        origem.data_rpi,
        origem.numero_rpi,
        (origem.codigo_despacho or "").strip().upper(),
    )


async def _reconciliar_prazos_historicos(
    session: AsyncSession,
    organizacao_id: int,
    ator: str,
) -> tuple[int, int]:
    """Arquiva sugestões que já estavam vencidas quando foram descobertas.

    O prazo legal continua registrado, mas deixa de ser contado como pendência
    vencida da equipe. Publicações repetidas da mesma RPI são preservadas como
    duplicadas para manter a trilha de auditoria.
    """
    linhas = (
        await session.execute(
            select(PrazoJuridico, Movimentacao)
            .join(Movimentacao, Movimentacao.id == PrazoJuridico.movimentacao_origem_id)
            .where(
                PrazoJuridico.organizacao_id == organizacao_id,
                PrazoJuridico.origem == "motor_rpi",
                PrazoJuridico.status.in_((*STATUS_ATIVOS, "historico")),
            )
            .order_by(PrazoJuridico.id)
        )
    ).all()
    agora = datetime.now(UTC)
    motor_usuario = SimpleNamespace(organizacao_id=organizacao_id, ator=ator)
    vistos: dict[tuple, PrazoJuridico] = {}
    historicos: list[PrazoJuridico] = []
    duplicados: list[PrazoJuridico] = []
    for prazo, origem in linhas:
        chave = _chave_publicacao(prazo, origem)
        if chave in vistos:
            prazo.status = "duplicado"
            prazo.confirmado = True
            prazo.concluido_em = agora
            prazo.concluido_por = ator
            duplicados.append(prazo)
            _evento(
                session,
                motor_usuario,
                prazo.processo_monitorado_id,
                "prazo_duplicado",
                "Publicação repetida da mesma RPI arquivada pelo motor",
                prazo.id,
                {"prazo_canonico_id": vistos[chave].id, "rpi": origem.numero_rpi},
            )
            continue
        vistos[chave] = prazo
        criado_em = prazo.criado_em
        if criado_em is not None and criado_em.tzinfo is None:
            criado_em = criado_em.replace(tzinfo=UTC)
        vencimento = prazo.vencimento_em
        if vencimento.tzinfo is None:
            vencimento = vencimento.replace(tzinfo=UTC)
        descoberto_depois = criado_em is None or criado_em > vencimento
        if prazo.status in STATUS_ATIVOS and not prazo.confirmado and vencimento < agora and descoberto_depois:
            prazo.status = "historico"
            prazo.prioridade = "baixa"
            prazo.confirmado = True
            prazo.concluido_em = agora
            prazo.concluido_por = ator
            historicos.append(prazo)
            _evento(
                session,
                motor_usuario,
                prazo.processo_monitorado_id,
                "prazo_historico",
                "Prazo já encerrado quando a publicação foi importada; mantido como referência",
                prazo.id,
                {"rpi": origem.numero_rpi, "vencimento_em": vencimento.isoformat()},
            )
    encerrados = historicos + duplicados
    if encerrados:
        notificacoes = (
            (
                await session.execute(
                    select(NotificacaoJuridica).where(
                        NotificacaoJuridica.organizacao_id == organizacao_id,
                        NotificacaoJuridica.prazo_id.in_([item.id for item in encerrados]),
                        NotificacaoJuridica.status != "arquivada",
                    )
                )
            )
            .scalars()
            .all()
        )
        for notificacao in notificacoes:
            notificacao.status = "arquivada"
            notificacao.lida_em = agora
            notificacao.lida_por = ator
    return len(historicos), len(duplicados)


async def executar_motor_organizacao(session: AsyncSession, organizacao_id: int, ator: str = "motor-juridico") -> dict:
    """Materializa alertas e sugestões; usado pela API e pela rotina horária."""
    motor_usuario = SimpleNamespace(organizacao_id=organizacao_id, ator=ator)
    agora = datetime.now(UTC)
    reconciliados, _terminais_reconciliados = await _reconciliar_prazos_terminais(session, organizacao_id, ator)
    historicos, duplicados = await _reconciliar_prazos_historicos(session, organizacao_id, ator)
    prazos = (
        (
            await session.execute(
                select(PrazoJuridico).where(
                    PrazoJuridico.organizacao_id == organizacao_id,
                    PrazoJuridico.status.in_(STATUS_ATIVOS),
                    PrazoJuridico.confirmado.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    destinatario_ids = {p.responsavel_id for p in prazos if p.responsavel_id} | {
        p.escalonar_para_id for p in prazos if p.escalonar_para_id
    }
    emails_destinatarios = await _emails_usuarios(session, organizacao_id, destinatario_ids)
    notificacoes = 0
    escalados = 0
    for prazo in prazos:
        dias = _dias_restantes(prazo.vencimento_em)
        if dias < 0:
            prazo.prioridade = "critica"
            notificacoes += await _notificar(
                session,
                prazo,
                "vencido",
                prazo.responsavel_id,
                "Prazo jurídico vencido",
                f"{prazo.titulo} venceu há {abs(dias)} dia(s).",
                emails_destinatarios.get(prazo.responsavel_id),
            )
        elif dias <= prazo.antecedencia_dias:
            notificacoes += await _notificar(
                session,
                prazo,
                f"antecedencia_{dias}",
                prazo.responsavel_id,
                "Prazo jurídico próximo",
                f"{prazo.titulo} vence em {dias} dia(s).",
                emails_destinatarios.get(prazo.responsavel_id),
            )
        if dias <= prazo.escalonar_dias_antes and prazo.escalonar_para_id and prazo.escalonado_em is None:
            prazo.escalonado_em = agora
            prazo.prioridade = "critica" if dias <= 0 else "alta"
            escalados += 1
            notificacoes += await _notificar(
                session,
                prazo,
                "escalonado",
                prazo.escalonar_para_id,
                "Prazo escalonado",
                f"{prazo.titulo} requer acompanhamento imediato.",
                emails_destinatarios.get(prazo.escalonar_para_id),
            )
            _evento(
                session,
                motor_usuario,
                prazo.processo_monitorado_id,
                "prazo_escalonado",
                "Prazo escalonado automaticamente",
                prazo.id,
                {"dias_restantes": dias},
            )

    candidatos = (
        await session.execute(
            select(ProcessoMonitorado, Movimentacao)
            .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
            .join(Movimentacao, Movimentacao.processo_id == Processo.id)
            .outerjoin(
                PrazoJuridico,
                (PrazoJuridico.movimentacao_origem_id == Movimentacao.id)
                & (PrazoJuridico.organizacao_id == organizacao_id),
            )
            .outerjoin(
                MovimentacaoAvaliadaJuridico,
                (MovimentacaoAvaliadaJuridico.movimentacao_id == Movimentacao.id)
                & (MovimentacaoAvaliadaJuridico.organizacao_id == organizacao_id),
            )
            .where(
                ProcessoMonitorado.organizacao_id == organizacao_id,
                ProcessoMonitorado.status == "ativo",
                PrazoJuridico.id.is_(None),
                MovimentacaoAvaliadaJuridico.id.is_(None),
            )
            # Mais antigas primeiro: são as mais urgentes (prazo mais perto de
            # vencer ou já vencido). Achado 5.6 da auditoria (Fase 4): antes
            # ordenava por mais recente, deixando pendências antigas nunca
            # avaliadas quando o backlog passava de 2000 candidatos.
            .order_by(Movimentacao.data_rpi.asc())
            .limit(2000)
        )
    ).all()
    terminais_candidatos = await _terminais_dos_processos(
        session, {monitorado.processo_id for monitorado, _movimentacao in candidatos}
    )
    chaves_existentes = {
        _chave_publicacao(prazo, origem)
        for prazo, origem in (
            await session.execute(
                select(PrazoJuridico, Movimentacao)
                .join(Movimentacao, Movimentacao.id == PrazoJuridico.movimentacao_origem_id)
                .where(
                    PrazoJuridico.organizacao_id == organizacao_id,
                    PrazoJuridico.origem == "motor_rpi",
                )
            )
        ).all()
    }
    historico_prazo_padrao = await _historico_regra(session, "prazo_administrativo_padrao_dias")
    historico_marco_isencao = await _historico_regra(session, "marco_isencao_taxa_concessao")
    sugeridos = 0
    dispensados = 0
    avaliados_sem_prazo = 0

    def _marcar_avaliada(motivo: str) -> None:
        nonlocal avaliados_sem_prazo
        session.add(
            MovimentacaoAvaliadaJuridico(
                organizacao_id=organizacao_id, movimentacao_id=movimentacao.id, motivo=motivo
            )
        )
        avaliados_sem_prazo += 1

    for monitorado, movimentacao in candidatos:
        if movimentacao.data_rpi is None:
            _marcar_avaliada("sem_data_rpi")
            continue
        terminal = terminais_candidatos.get(monitorado.processo_id)
        if terminal is not None and _movimentacao_posterior(terminal, movimentacao):
            _marcar_avaliada("despacho_posterior_a_terminal")
            continue
        dias_padrao = _valor_vigente(
            historico_prazo_padrao, movimentacao.data_rpi, PRAZO_ADMINISTRATIVO_PADRAO_DIAS
        )
        classificacao = _classificar_despacho(
            movimentacao.descricao, codigo_despacho=movimentacao.codigo_despacho, dias_padrao=dias_padrao
        )
        if classificacao is None:
            _marcar_avaliada("sem_prazo_mapeado")
            continue
        dias, tipo, acao = classificacao
        chave_publicacao = (
            monitorado.id,
            tipo,
            movimentacao.data_rpi,
            movimentacao.numero_rpi,
            (movimentacao.codigo_despacho or "").strip().upper(),
        )
        if chave_publicacao in chaves_existentes:
            _marcar_avaliada("publicacao_duplicada")
            continue
        chaves_existentes.add(chave_publicacao)
        # (a) Deferimentos a partir da RPI nº 2842 (24/06/2025) são isentos do
        # pagamento de concessão, mesmo se o depósito for anterior — não gera
        # prazo acionável. (b) Registra um aviso informativo (prazo
        # dispensado, sem ação) para o operador entender. Ver fonte no
        # comentário de MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO.
        marco_isencao = _valor_vigente(
            historico_marco_isencao, movimentacao.data_rpi, MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO
        )
        if isinstance(marco_isencao, str):
            marco_isencao = date.fromisoformat(marco_isencao)
        dispensa_concessao = _dispensa_concessao(tipo, movimentacao.data_rpi, marco=marco_isencao)
        if dispensa_concessao:
            titulo = "Concessão sem taxa — isenta pela regra do INPI (deferimento ≥ 24/06/2025)"
            descricao = (
                "O primeiro decênio de vigência e a expedição do certificado são "
                "gratuitos e automáticos para pedidos com deferimento publicado a "
                "partir da RPI nº 2842 (24/06/2025), mesmo se depositados antes da "
                "nova tabela de retribuições do INPI (Portaria/INPI/PR nº 10/2025, "
                "vigente desde 20/09/2025) — fonte: FAQ oficial do INPI, item 7. "
                "Nenhum pagamento de concessão é devido — nenhuma ação necessária."
            )
        else:
            titulo = f"Revisar: {acao} (RPI {movimentacao.numero_rpi})"
            descricao = movimentacao.descricao or ""
        vencimento = calcular_vencimento(movimentacao.data_rpi, dias, "corridos")
        referencia_historica = not dispensa_concessao and vencimento < agora
        if referencia_historica:
            titulo = f"Histórico: {acao} (RPI {movimentacao.numero_rpi})"
            descricao = (
                f"{descricao}\n\nPublicação importada após o encerramento do prazo. "
                "Mantida como referência histórica; não representa pendência "
                "operacional atual. Confirme a tramitação no BuscaWeb e na RPI."
            ).strip()
        prazo = PrazoJuridico(
            organizacao_id=organizacao_id,
            processo_monitorado_id=monitorado.id,
            movimentacao_origem_id=movimentacao.id,
            responsavel_id=monitorado.responsavel_id,
            titulo=titulo[:180],
            descricao=descricao[:4000],
            tipo=tipo,
            origem="motor_rpi",
            contagem="corridos",
            data_base=movimentacao.data_rpi,
            dias_prazo=dias,
            vencimento_em=vencimento,
            status=(
                "dispensado"
                if dispensa_concessao
                else "historico"
                if referencia_historica
                else "aguardando_confirmacao"
            ),
            prioridade="baixa" if dispensa_concessao or referencia_historica else "alta",
            confirmado=dispensa_concessao or referencia_historica,
            concluido_em=agora if referencia_historica else None,
            concluido_por=ator if referencia_historica else None,
            criado_por="motor-juridico",
        )
        session.add(prazo)
        await session.flush()
        _evento(
            session,
            motor_usuario,
            monitorado.id,
            "prazo_dispensado"
            if dispensa_concessao
            else "prazo_historico"
            if referencia_historica
            else "prazo_sugerido",
            descricao
            if dispensa_concessao or referencia_historica
            else "Prazo sugerido pelo motor a partir do despacho da RPI; requer confirmação humana",
            prazo.id,
            {"movimentacao_id": movimentacao.id, "dias_prazo": dias, "tipo": tipo},
        )
        if dispensa_concessao:
            dispensados += 1
        elif referencia_historica:
            historicos += 1
        else:
            sugeridos += 1
    return {
        "notificacoes_criadas": notificacoes,
        "escalados": escalados,
        "prazos_sugeridos": sugeridos,
        "prazos_dispensados": dispensados,
        "prazos_reconciliados": reconciliados,
        "prazos_historicos": historicos,
        "prazos_duplicados": duplicados,
        "avaliados_sem_prazo": avaliados_sem_prazo,
        "backlog_no_limite": len(candidatos) == 2000,
    }


@router.post("/motor/executar")
async def executar_motor(request: Request, session: SessionDep, usuario: ManageDep) -> dict:
    resultado = await executar_motor_organizacao(session, usuario.organizacao_id, usuario.ator)
    _auditar(
        session,
        request,
        usuario,
        "executar_motor",
        "juridico:motor",
        resultado,
    )
    await session.commit()
    return resultado
