"""Painel de TV: Zé Registra e EZRA ERP lado a lado, só números agregados.

Pedido do dono (17/11/2026): um acompanhamento único dos dois sistemas numa TV
de monitoramento. A TV não tem login interativo (a sessão normal expira em 8 h
e em 60 min parada), então o acesso é por uma chave de leitura própria:

- a chave só lê ESTE endpoint (não abre nenhuma outra rota nem sessão);
- só o hash SHA-256 fica configurado (TV_MONITORAMENTO_TOKEN_SHA256); vazio =
  painel desligado, e trocar o hash revoga a chave anterior;
- a resposta é agregada: contagens, saúde, série de 7 dias e top 3 erros por
  operação. Sem nome de organização, usuário, e-mail, IP nem texto de erro.
  Única exceção, aprovada pelo dono em 17/11/2026: a lista de clientes do ERP
  sem uso em 7 dias leva o nome e há quantos dias (sem e-mail nem documento);
- limite de taxa por IP e Cache-Control no-store.

O resumo do EZRA ERP vem do servidor daquele sistema (chamada servidor a
servidor, sem CORS e sem credencial no navegador). Se o ERP cair, o cartão dele
mostra o último valor bom com a hora e "indisponível desde"; o lado do Zé
Registra continua normal.
"""

import asyncio
import hashlib
import logging
import os
import secrets
import shutil
from datetime import UTC, datetime, time, timedelta
from time import monotonic
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import EventoAuditoria, EventoOperacional
from app.ratelimit import RateLimiter
from app.saude_backups import ler_snapshot
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant

logger = logging.getLogger("ze_registra.tv_monitoramento")

router = APIRouter(prefix="/v1/monitoramento", tags=["tv-monitoramento"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

FUSO_NEGOCIO = ZoneInfo("America/Sao_Paulo")
_limitar = RateLimiter(limite=60, janela_segundos=60, escopo="tv-monitoramento")

CACHE_ERP_SEGUNDOS = 30.0
TIMEOUT_ERP_SEGUNDOS = 4.0

_CAMPOS_INTEIROS_ERP = (
    "erros_abertos",
    "erros_novos_1h",
    "erros_novos_24h",
    "usuarios_ativos_24h",
    "acoes_24h",
    "clientes_ativos_24h",
    "clientes_sem_uso_7d",
)


def exigir_chave_tv(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Chave de leitura da TV. Painel desligado ou chave errada: sem detalhe."""
    _limitar(request)
    esperado = get_settings().tv_monitoramento_token_sha256.strip().lower()
    if not esperado:
        raise HTTPException(status_code=404, detail="Não encontrado")
    prefixo = "Bearer "
    recebido = (authorization or "")[len(prefixo) :] if (authorization or "").startswith(prefixo) else ""
    calculado = hashlib.sha256(recebido.encode("utf-8")).hexdigest()
    if not recebido or not secrets.compare_digest(calculado, esperado):
        raise HTTPException(status_code=401, detail="Chave inválida")


ChaveTvDep = Annotated[None, Depends(exigir_chave_tv)]


def saude_por_erros(erros_5xx_ultima_hora: int) -> str:
    """0 erros de servidor na última hora = verde; 1 a 9 = amarelo; 10 ou mais = vermelho.

    Só 5xx conta: respostas 4xx (senha errada, limite de taxa, validação) são
    ruído esperado e não indicam que o sistema está quebrado.
    """
    if erros_5xx_ultima_hora >= 10:
        return "vermelho"
    if erros_5xx_ultima_hora >= 1:
        return "amarelo"
    return "verde"


def _dias_da_serie(dias: int) -> tuple[datetime, list]:
    hoje = datetime.now(UTC).astimezone(FUSO_NEGOCIO).date()
    primeiro = hoje - timedelta(days=dias - 1)
    desde = datetime.combine(primeiro, time.min, tzinfo=FUSO_NEGOCIO)
    return desde, [primeiro + timedelta(deslocamento) for deslocamento in range(dias)]


def _dia(coluna):  # noqa: ANN001, ANN202 -- expressão SQLAlchemy
    return func.date(func.timezone("America/Sao_Paulo", coluna))


async def _resumo_central(session: AsyncSession) -> dict[str, Any]:
    agora = datetime.now(UTC)
    ha_24h = agora - timedelta(hours=24)
    ha_1h = agora - timedelta(hours=1)
    desde_serie, dias_lista = _dias_da_serie(7)

    requisicoes, erros = (
        await session.execute(
            select(
                func.count(),
                func.sum(case((EventoOperacional.sucesso.is_(False), 1), else_=0)),
            ).where(EventoOperacional.criado_em >= ha_24h)
        )
    ).one()
    erros_5xx = (
        await session.execute(
            select(func.count()).where(
                EventoOperacional.criado_em >= ha_1h,
                EventoOperacional.status_http >= 500,
            )
        )
    ).scalar_one()
    acoes, usuarios, organizacoes = (
        await session.execute(
            select(
                func.count(),
                func.count(func.distinct(EventoAuditoria.actor_id)),
                func.count(func.distinct(EventoAuditoria.organizacao_id)),
            ).where(EventoAuditoria.criado_em >= ha_24h)
        )
    ).one()

    quantidade = func.count()
    top = (
        await session.execute(
            select(EventoOperacional.operacao, EventoOperacional.status_http, quantidade)
            .where(EventoOperacional.criado_em >= ha_24h, EventoOperacional.sucesso.is_(False))
            .group_by(EventoOperacional.operacao, EventoOperacional.status_http)
            .order_by(quantidade.desc())
            .limit(3)
        )
    ).all()

    dia_aud = _dia(EventoAuditoria.criado_em)
    acoes_por_dia = {
        dia: int(total or 0)
        for dia, total in (
            await session.execute(
                select(dia_aud, func.count()).where(EventoAuditoria.criado_em >= desde_serie).group_by(dia_aud)
            )
        ).all()
    }
    dia_op = _dia(EventoOperacional.criado_em)
    erros_por_dia = {
        dia: int(total or 0)
        for dia, total in (
            await session.execute(
                select(dia_op, func.sum(case((EventoOperacional.sucesso.is_(False), 1), else_=0)))
                .where(EventoOperacional.criado_em >= desde_serie)
                .group_by(dia_op)
            )
        ).all()
    }

    requisicoes = int(requisicoes or 0)
    erros = int(erros or 0)
    erros_5xx = int(erros_5xx or 0)
    return {
        "agregado": True,
        "gerado_em": agora,
        "saude": saude_por_erros(erros_5xx),
        "erros_5xx_1h": erros_5xx,
        "requisicoes_24h": requisicoes,
        "erros_24h": erros,
        "taxa_erros_24h": erros / requisicoes if requisicoes else 0,
        "acoes_24h": int(acoes or 0),
        "usuarios_ativos_24h": int(usuarios or 0),
        "organizacoes_ativas_24h": int(organizacoes or 0),
        "serie": [
            {"dia": dia.isoformat(), "acoes": acoes_por_dia.get(dia, 0), "erros": erros_por_dia.get(dia, 0)}
            for dia in dias_lista
        ],
        "top_erros": [
            {"operacao": str(operacao)[:80], "status_http": int(status), "quantidade": int(total or 0)}
            for operacao, status, total in top
        ],
    }


def _inteiro(valor: Any) -> int:
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return 0
    return max(numero, 0)


def _inteiro_ou_nulo(valor: Any) -> int | None:
    if valor is None:
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero >= 0 else None


def _data_iso(valor: Any) -> str | None:
    """Data/hora em ISO ou None: o que não for data válida nunca chega à TV."""
    if not isinstance(valor, str) or not valor:
        return None
    try:
        datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return valor[:40]


def normalizar_backup_erp(bruto: Any, agora: datetime | None = None) -> dict[str, Any]:
    """Backup noturno por cliente do ERP, só contagens e datas (sem nome de cliente nem caminho de arquivo).

    Saúde: verde se todos os clientes ativos têm backup das últimas 26 h; amarelo se algum está atrasado ou ainda
    sem backup; vermelho se algum falhou ou o backup mais antigo passou de 50 h; cinza se o ERP não mandou o campo."""
    if not isinstance(bruto, dict):
        return {"disponivel": False, "saude": "cinza"}
    agora = agora or datetime.now(UTC)
    ativos = _inteiro(bruto.get("clientes_ativos"))
    resumo: dict[str, Any] = {
        "disponivel": True,
        "clientes_ativos": ativos,
        "em_dia": _inteiro(bruto.get("em_dia")),
        "atrasados": _inteiro(bruto.get("atrasados")),
        "sem_backup": _inteiro(bruto.get("sem_backup")),
        "com_erro": _inteiro(bruto.get("com_erro")),
        "ultimo_em": _data_iso(bruto.get("ultimo_em")),
        "mais_antigo_em": _data_iso(bruto.get("mais_antigo_em")),
    }
    muito_velho = False
    if resumo["mais_antigo_em"]:
        antigo = datetime.fromisoformat(resumo["mais_antigo_em"].replace("Z", "+00:00"))
        if antigo.tzinfo is None:
            antigo = antigo.replace(tzinfo=UTC)
        muito_velho = (agora - antigo).total_seconds() > BACKUP_LIMITE_CRITICO_HORAS * 3600
    if ativos == 0:
        resumo["saude"] = "cinza"
    elif resumo["com_erro"] > 0 or muito_velho:
        resumo["saude"] = "vermelho"
    elif resumo["atrasados"] > 0 or resumo["sem_backup"] > 0 or resumo["em_dia"] < ativos:
        resumo["saude"] = "amarelo"
    else:
        resumo["saude"] = "verde"
    return resumo


def normalizar_erp(bruto: Any) -> dict[str, Any]:
    """Copia só os campos conhecidos da resposta do ERP.

    Se o outro sistema passar a devolver mais coisa (e-mail, documento, texto
    de erro), isso nunca chega à TV. A única informação nominal é a lista de
    clientes sem uso (nome + dias), aprovada pelo dono em 17/11/2026.
    """
    dados = bruto if isinstance(bruto, dict) else {}
    erros_1h = _inteiro(dados.get("erros_novos_1h"))
    saude = "vermelho" if erros_1h >= 10 else "amarelo" if erros_1h >= 1 else "verde"
    resumo: dict[str, Any] = {campo: _inteiro(dados.get(campo)) for campo in _CAMPOS_INTEIROS_ERP}
    resumo["agregado"] = True
    resumo["saude"] = saude
    resumo["gerado_em"] = str(dados.get("gerado_em") or "")[:40]
    resumo["serie"] = [
        {
            "dia": str(item.get("dia") or "")[:10],
            "acoes": _inteiro(item.get("acoes")),
            "erros": _inteiro(item.get("erros_novos")),
        }
        for item in (dados.get("serie") or [])[:31]
        if isinstance(item, dict)
    ]
    resumo["top_erros"] = [
        {
            "rota": str(item.get("rota") or "")[:80],
            "origem": str(item.get("origem") or "")[:20],
            "quantidade": _inteiro(item.get("ocorrencias")),
        }
        for item in (dados.get("top_erros") or [])[:3]
        if isinstance(item, dict)
    ]
    lista_sem_uso: list[dict[str, Any]] = []
    bruto_sem_uso = dados.get("clientes_sem_uso")
    if isinstance(bruto_sem_uso, list):
        for item in bruto_sem_uso[:20]:
            if not isinstance(item, dict):
                continue
            nome = str(item.get("nome") or "").strip()[:60]
            if nome:
                lista_sem_uso.append({"nome": nome, "dias_sem_uso": _inteiro_ou_nulo(item.get("dias_sem_uso"))})
    resumo["clientes_sem_uso"] = lista_sem_uso
    resumo["backup"] = normalizar_backup_erp(dados.get("backup"))
    return resumo


class _EstadoErp:
    """Último resultado bom e quando o ERP passou a falhar (memória do processo)."""

    def __init__(self) -> None:
        self.dados: dict[str, Any] | None = None
        self.obtido_em: datetime | None = None
        self.consultado_em: float = 0.0
        self.falhando_desde: datetime | None = None
        self.lock = asyncio.Lock()


_erp = _EstadoErp()


def _resposta_erp(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "dados": _erp.dados,
        "atualizado_em": _erp.obtido_em,
        "indisponivel_desde": _erp.falhando_desde,
    }


async def resumo_erp() -> dict[str, Any]:
    config = get_settings()
    if not config.erp_tv_url or not config.erp_tv_token:
        return {"status": "nao_configurado", "dados": None, "atualizado_em": None, "indisponivel_desde": None}
    async with _erp.lock:
        if monotonic() - _erp.consultado_em < CACHE_ERP_SEGUNDOS and _erp.consultado_em:
            return _resposta_erp("ok" if _erp.falhando_desde is None else "indisponivel")
        _erp.consultado_em = monotonic()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_ERP_SEGUNDOS) as cliente:
                resposta = await cliente.get(
                    config.erp_tv_url,
                    headers={"Authorization": f"Bearer {config.erp_tv_token}", "Accept": "application/json"},
                )
            resposta.raise_for_status()
            _erp.dados = normalizar_erp(resposta.json())
            _erp.obtido_em = datetime.now(UTC)
            _erp.falhando_desde = None
            return _resposta_erp("ok")
        except Exception as erro:  # noqa: BLE001 -- qualquer falha do ERP vira "indisponível", nunca derruba o painel
            if _erp.falhando_desde is None:
                _erp.falhando_desde = datetime.now(UTC)
            logger.warning("Resumo do ERP indisponível: %s", type(erro).__name__)
            return _resposta_erp("indisponivel")


BACKUP_LIMITE_ATENCAO_HORAS = 26.0
BACKUP_LIMITE_CRITICO_HORAS = 50.0


def _saude_backup(etapa: Any, agora: datetime) -> dict[str, Any]:
    """Última execução e saúde de uma etapa da rotina de backup (só data/hora e cor, sem caminho nem detalhe).

    Verde até 26 h, amarelo até 50 h, vermelho acima disso ou quando a última execução falhou."""
    if not isinstance(etapa, dict) or not etapa.get("ultima_em"):
        return {"ultima_em": None, "saude": "cinza"}
    try:
        ultima = datetime.fromisoformat(str(etapa["ultima_em"]).replace("Z", "+00:00"))
    except ValueError:
        return {"ultima_em": None, "saude": "cinza"}
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=UTC)
    horas = (agora - ultima).total_seconds() / 3600
    if etapa.get("status") == "critico" and "falhou" in str(etapa.get("detalhe", "")).lower():
        saude = "vermelho"
    elif horas > BACKUP_LIMITE_CRITICO_HORAS:
        saude = "vermelho"
    elif horas > BACKUP_LIMITE_ATENCAO_HORAS:
        saude = "amarelo"
    else:
        saude = "verde"
    return {"ultima_em": ultima.isoformat(), "saude": saude}


async def resumo_backup() -> dict[str, Any]:
    """Último backup local do banco e última cópia externa, lidos do instantâneo que o worker publica no Redis."""
    agora = datetime.now(UTC)
    try:
        snapshot = await asyncio.wait_for(ler_snapshot(), timeout=2.0)
    except Exception:  # noqa: BLE001 -- Redis fora do ar não pode derrubar a TV
        logger.warning("TV: leitura do estado dos backups falhou", exc_info=True)
        snapshot = None
    if not isinstance(snapshot, dict):
        sem_dado = {"ultima_em": None, "saude": "cinza"}
        return {"disponivel": False, "banco_local": sem_dado, "copia_externa": dict(sem_dado)}
    etapas = {e.get("chave"): e for e in snapshot.get("etapas", []) if isinstance(e, dict)}
    return {
        "disponivel": True,
        "banco_local": _saude_backup(etapas.get("dump"), agora),
        "copia_externa": _saude_backup(etapas.get("offsite"), agora),
    }


SISTEMA_LIMITE_ATENCAO = 80.0
SISTEMA_LIMITE_CRITICO = 90.0
_DISCO_CAMINHO = "/app/data"


def _saude_percentual(percentual: float) -> str:
    if percentual >= SISTEMA_LIMITE_CRITICO:
        return "vermelho"
    if percentual >= SISTEMA_LIMITE_ATENCAO:
        return "amarelo"
    return "verde"


def _leitura_cpu() -> dict[str, Any]:
    """CPU sustentada: carga média dos últimos 5 minutos dividida pelos núcleos da VPS (o container vê o /proc do host)."""
    try:
        with open("/proc/loadavg", encoding="ascii") as arquivo:
            carga_5min = float(arquivo.read().split()[1])
    except (OSError, ValueError, IndexError):
        return {"percentual": None, "saude": "cinza"}
    nucleos = os.cpu_count() or 1
    percentual = carga_5min / nucleos * 100
    return {"percentual": round(percentual), "saude": _saude_percentual(percentual)}


def _leitura_memoria() -> dict[str, Any]:
    try:
        valores: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as arquivo:
            for linha in arquivo:
                chave, _, resto = linha.partition(":")
                if chave in ("MemTotal", "MemAvailable"):
                    valores[chave] = int(resto.split()[0])
        total, livre = valores["MemTotal"], valores["MemAvailable"]
    except (OSError, ValueError, KeyError, IndexError):
        return {"percentual": None, "saude": "cinza"}
    if total <= 0:
        return {"percentual": None, "saude": "cinza"}
    percentual = (total - livre) / total * 100
    return {"percentual": round(percentual), "saude": _saude_percentual(percentual)}


def _leitura_disco() -> dict[str, Any]:
    try:
        uso = shutil.disk_usage(_DISCO_CAMINHO)
    except OSError:
        return {"percentual": None, "livre_gb": None, "saude": "cinza"}
    if uso.total <= 0:
        return {"percentual": None, "livre_gb": None, "saude": "cinza"}
    percentual = uso.used / uso.total * 100
    return {
        "percentual": round(percentual),
        "livre_gb": round(uso.free / 1024**3),
        "saude": _saude_percentual(percentual),
    }


def resumo_sistema() -> dict[str, Any]:
    """CPU, memória e disco da VPS: só percentuais e GB livres (sem caminhos, processos nem nomes)."""
    return {"cpu": _leitura_cpu(), "memoria": _leitura_memoria(), "disco": _leitura_disco()}


@router.get("/tv")
async def painel_tv(session: SessionDep, response: Response, _chave: ChaveTvDep) -> dict[str, Any]:
    """Resumo agregado dos dois sistemas para a TV (somente leitura)."""
    response.headers["Cache-Control"] = "no-store"
    # Leitura agregada da plataforma inteira, liberada só depois da chave da TV (mesmo recurso do webhook
    # do WhatsApp e do portal): a auditoria tem RLS por organização e esta consulta cruza todas.
    await aplicar_contexto_tenant(session, get_settings().default_organization_id, superadmin=True)
    central, erp, backup = await asyncio.gather(_resumo_central(session), resumo_erp(), resumo_backup())
    return {"gerado_em": datetime.now(UTC), "zeregistra": central, "erp": erp, "backup": backup, "sistema": resumo_sistema()}
