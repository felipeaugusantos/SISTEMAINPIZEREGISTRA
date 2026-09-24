"""Busca ao vivo, por CNPJ/CPF, de titularidade de marca no site público do
INPI (pePI, https://busca.inpi.gov.br/pePI/jsp/marcas/Pesquisa_titular.jsp).

Achado do usuário (08/09/2026): diferente da nossa base local (sincronizada
semanalmente via RPI, só tem nome do titular -- ver app.prospeccao_triagem),
o próprio pePI aceita CNPJ/CPF como chave de busca direta, o que é mais
preciso que comparar nome normalizado.

IMPORTANTE -- confirmado na prática nesta sessão: o pePI é um sistema legado
(servlet Java sem API pública) e devolveu 502/timeout em várias tentativas
manuais antes de uma responder com sucesso. Este módulo é estritamente
best-effort: timeout curto, uma única tentativa, qualquer erro (rede, HTTP,
HTML em formato inesperado) devolve None em silêncio. Quem chama trata None
como "sem sinal ao vivo", nunca como "não tem marca" -- o fallback é sempre
o match por nome na base local, que continua rodando independentemente.

Fluxo mapeado por inspeção manual do formulário real (POST):
  GET  /pePI/servlet/LoginController?action=login   -- abre sessão anônima
  POST /pePI/servlet/MarcasServletController         -- Action=searchNome,
       tipoPesquisa=BY_CNPJ_NOME, cpf_cgc_numINPI=<cnpj>
A resposta é uma lista de variações de nome do titular cadastradas sob esse
CNPJ (ex.: "AMBEV S.A", "AMBEV S.A.", "AMBEV S/A."), cada uma um resultado
de marca(s). Para o propósito daqui (só precisamos saber SE existe alguma
marca registrada, não listar todas), a contagem de registros e o primeiro
nome de titular já bastam.
"""

import asyncio
import re
import time
from collections import OrderedDict, deque

import httpx

from app.settings import get_settings

BASE_URL = "https://busca.inpi.gov.br/pePI"

_PADRAO_TOTAL_REGISTROS = re.compile(r"Foram encontrados\s*<b>\s*(\d+)\s*</b>\s*registros")
_PADRAO_PRIMEIRO_TITULAR = re.compile(
    r'tipoPesquisa=BY_CNPJ_NOME&(?:amp;)?pos=0"[^>]*>\s*([^<]+?)\s*</a>'
)

# Cache local ao processo do worker. A consulta ao vivo é executada pelo
# worker (instância única no desenho atual); o rate limit dos endpoints HTTP,
# por sua vez, usa Redis e é compartilhado entre réplicas da API.
_cache: OrderedDict[str, tuple[float, str | None]] = OrderedDict()
_chamadas_externas: deque[float] = deque()
_consultas_em_andamento: dict[str, asyncio.Future[str | None]] = {}
_lock = asyncio.Lock()


def limpar_estado_titularidade_inpi() -> None:
    """Limpa cache e contadores. Destinado aos testes, não ao fluxo normal."""
    _cache.clear()
    _chamadas_externas.clear()
    _consultas_em_andamento.clear()


def _resultado_em_cache(cnpj: str, agora: float) -> tuple[bool, str | None]:
    entrada = _cache.get(cnpj)
    if entrada is None:
        return False, None
    expira_em, resultado = entrada
    if expira_em <= agora:
        _cache.pop(cnpj, None)
        return False, None
    _cache.move_to_end(cnpj)
    return True, resultado


def _guardar_cache(cnpj: str, resultado: str | None, ttl: int, maximo: int, agora: float) -> None:
    if ttl <= 0 or maximo <= 0:
        return
    _cache[cnpj] = (agora + ttl, resultado)
    _cache.move_to_end(cnpj)
    while len(_cache) > maximo:
        _cache.popitem(last=False)


async def buscar_titularidade_inpi_ao_vivo(
    cnpj: str | None, *, cliente: httpx.AsyncClient | None = None
) -> str | None:
    """Devolve o nome do primeiro titular encontrado no pePI para esse CNPJ,
    ou None se não achou nada, se a flag estiver desligada, ou se qualquer
    coisa der errado (nunca levanta exceção).

    `cliente` é injetável só para teste (mesmo padrão de
    app.verificacao_site.verificar_site) -- em produção sempre é None e um
    AsyncClient novo é criado e fechado aqui."""
    settings = get_settings()
    if not settings.prospeccao_titularidade_inpi_ao_vivo_enabled:
        return None

    cnpj_limpo = re.sub(r"\D", "", cnpj or "")
    if len(cnpj_limpo) != 14:
        return None

    # Uma resposta None também é cacheada: tanto "zero resultados" quanto uma
    # falha do legado precisam evitar tempestade de retries. Falhas usam TTL
    # curto; respostas válidas (inclusive zero) usam o TTL normal.
    agora = time.monotonic()
    dono_consulta = False
    async with _lock:
        encontrado, resultado_cache = _resultado_em_cache(cnpj_limpo, agora)
        if encontrado:
            return resultado_cache

        consulta_existente = _consultas_em_andamento.get(cnpj_limpo)
        if consulta_existente is None:
            janela = 60.0
            while _chamadas_externas and agora - _chamadas_externas[0] >= janela:
                _chamadas_externas.popleft()
            maximo_chamadas = max(1, settings.prospeccao_titularidade_inpi_max_chamadas_minuto)
            if len(_chamadas_externas) >= maximo_chamadas:
                return None
            _chamadas_externas.append(agora)
            consulta_existente = asyncio.get_running_loop().create_future()
            _consultas_em_andamento[cnpj_limpo] = consulta_existente
            dono_consulta = True

    if not dono_consulta:
        return await asyncio.shield(consulta_existente)

    timeout = httpx.Timeout(settings.prospeccao_titularidade_inpi_timeout_segundos, connect=5.0)
    client = cliente or httpx.AsyncClient(base_url=BASE_URL, timeout=timeout, follow_redirects=True)
    resultado: str | None = None
    consulta_concluida = False
    consulta_cancelada = False
    try:
        await client.get("/servlet/LoginController", params={"action": "login"})
        resposta = await client.post(
            "/servlet/MarcasServletController",
            data={
                "cpf_cgc_numINPI": cnpj_limpo,
                "nomeTitular": "",
                "registerPerPage": "20",
                "botao": " pesquisar » ",
                "Action": "searchNome",
                "precisao": "aproximacao",
                "tipoPesquisa": "BY_CNPJ_NOME",
            },
        )
        resposta.raise_for_status()
        html = resposta.text
        consulta_concluida = True

        total_match = _PADRAO_TOTAL_REGISTROS.search(html)
        if total_match and int(total_match.group(1)) > 0:
            titular_match = _PADRAO_PRIMEIRO_TITULAR.search(html)
            if titular_match:
                resultado = titular_match.group(1).strip() or None
    except asyncio.CancelledError:
        consulta_cancelada = True
        raise
    except Exception:
        # O legado já apresentou timeout, 502 e HTML inesperado. Qualquer
        # falha mantém o contrato best-effort e deixa a base local responder.
        resultado = None
    finally:
        if cliente is None:
            try:
                await client.aclose()
            except Exception:
                consulta_concluida = False

        ttl = (
            settings.prospeccao_titularidade_inpi_cache_segundos
            if consulta_concluida
            else settings.prospeccao_titularidade_inpi_cache_falha_segundos
        )
        async with _lock:
            if not consulta_cancelada:
                _guardar_cache(
                    cnpj_limpo,
                    resultado,
                    ttl=max(0, ttl),
                    maximo=max(0, settings.prospeccao_titularidade_inpi_cache_maximo),
                    agora=time.monotonic(),
                )
            _consultas_em_andamento.pop(cnpj_limpo, None)
            if not consulta_existente.done():
                consulta_existente.set_result(resultado)
    return resultado
