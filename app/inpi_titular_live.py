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

import re

import httpx

from app.settings import get_settings

BASE_URL = "https://busca.inpi.gov.br/pePI"

_PADRAO_TOTAL_REGISTROS = re.compile(r"Foram encontrados\s*<b>\s*(\d+)\s*</b>\s*registros")
_PADRAO_PRIMEIRO_TITULAR = re.compile(
    r'tipoPesquisa=BY_CNPJ_NOME&(?:amp;)?pos=0"[^>]*>\s*([^<]+?)\s*</a>'
)


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

    timeout = httpx.Timeout(settings.prospeccao_titularidade_inpi_timeout_segundos, connect=5.0)
    client = cliente or httpx.AsyncClient(base_url=BASE_URL, timeout=timeout, follow_redirects=True)
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
    except (httpx.HTTPError, httpx.TimeoutException):
        return None
    finally:
        if cliente is None:
            await client.aclose()

    total_match = _PADRAO_TOTAL_REGISTROS.search(html)
    if not total_match or int(total_match.group(1)) == 0:
        return None

    titular_match = _PADRAO_PRIMEIRO_TITULAR.search(html)
    if not titular_match:
        return None
    return titular_match.group(1).strip() or None
