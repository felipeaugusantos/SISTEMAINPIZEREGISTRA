"""Assistente interno de CRM: chat para a equipe consultar dados de leads em
linguagem natural (decisão do usuário, 11/09/2026).

Diferente da IA em sombra (app/ia_sombra.py, que só sugere em segundo plano,
nunca conversa), este é um chat interativo -- mas com o mesmo espírito de
segurança adaptado ao formato: só ENXERGA dados (nenhuma ferramenta de
escrita foi implementada), sempre restrito à organização do usuário logado
(cada tool recebe organizacao_id do usuário autenticado, nunca de um
parâmetro vindo do modelo), e usa a mesma chave/modelo/pacing de cota do
Gemini já configurados para a IA em sombra.

Acesso (achado do usuário, 11/09/2026): o widget flutuante fica disponível
para QUALQUER usuário autenticado, não só quem tem leads.view -- mas isso
não abre uma brecha de permissão: cada ferramenta abaixo checa
usuario.pode("leads.view") por conta própria e devolve uma recusa em texto
em vez de consultar o banco quando falta a permissão, exatamente como o
resto do sistema já faz (ex.: mascaramento de PII em _lead_resumo).

Fluxo por pergunta (sem persistir histórico bruto do Gemini -- só o texto
visível entra na conversa seguinte, ver PerguntaInput.historico): o modelo
pode pedir para chamar uma das ferramentas abaixo (contents com
functionCall); o backend executa a consulta real no banco e devolve o
resultado (functionResponse); o modelo então formula a resposta final em
português. Até MAX_RODADAS_TOOL idas e vindas por pergunta, para nunca
travar em loop se o modelo insistir em chamar ferramentas.
"""

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.leads import _filtros_lead
from app.auth import UsuarioAtualDep, UsuarioAutenticado, exigir_csrf
from app.database import get_session
from app.ia_sombra import _modelos_gemini_em_ordem, _respeitar_intervalo_minimo_gemini
from app.models import Lead, StatusLead
from app.settings import get_settings

logger = logging.getLogger("ze_registra.assistente_ia")
router = APIRouter(prefix="/v1/admin/assistente", tags=["assistente ia"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
MSG_SEM_PERMISSAO_LEADS = "Você não tem permissão para consultar dados de leads neste sistema."

MAX_RODADAS_TOOL = 3
MAX_MENSAGENS_HISTORICO = 12
MAX_TAMANHO_PERGUNTA = 500
LIMITE_PADRAO_LEADS = 10
LIMITE_MAXIMO_LEADS = 20

PROMPT_SISTEMA = (
    "Você é o Zezinho das Marcas, o assistente interno do Zé Registra, um sistema de "
    "gestão para escritórios de registro de marcas. Responde em português, de forma "
    "direta e objetiva, a perguntas da equipe sobre os leads e o funil comercial da "
    "própria organização. Use sempre as ferramentas disponíveis para consultar dados "
    "reais -- nunca invente números, nomes ou status. Se a pergunta não puder ser "
    "respondida com as ferramentas disponíveis, diga isso claramente em vez de "
    "adivinhar. Você só consulta dados, nunca altera nada no sistema."
)


def _declaracoes_tools() -> list[dict]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": "contar_leads_por_status",
                    "description": "Conta quantos leads (não arquivados) a organização tem, agrupados por status.",
                    "parameters": {"type": "OBJECT", "properties": {}},
                },
                {
                    "name": "buscar_leads",
                    "description": (
                        "Busca leads por nome, empresa, marca, e-mail ou telefone, opcionalmente "
                        "filtrando por status. Devolve no máximo os campos básicos de cada lead."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "busca": {"type": "STRING", "description": "Termo livre: nome, empresa, marca, etc."},
                            "status": {
                                "type": "STRING",
                                "description": "Filtra por status exato.",
                                "enum": [s.value for s in StatusLead],
                            },
                            "limite": {"type": "INTEGER", "description": "Máximo de resultados (padrão 10, teto 20)."},
                        },
                    },
                },
                {
                    "name": "leads_prioritarios",
                    "description": (
                        "Lista leads que precisam de atenção: atrasados (próxima ação vencida), sem "
                        "responsável atribuído, ou sem nenhuma próxima ação definida."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "criterio": {
                                "type": "STRING",
                                "enum": ["atrasadas", "sem_responsavel", "sem_proxima_acao"],
                            },
                            "limite": {"type": "INTEGER", "description": "Máximo de resultados (padrão 10, teto 20)."},
                        },
                        "required": ["criterio"],
                    },
                },
            ]
        }
    ]


def _lead_resumo(lead: Lead, usuario: UsuarioAutenticado) -> dict:
    item = {
        "id": lead.id,
        "nome": lead.nome,
        "empresa": lead.empresa,
        "marca": lead.marca,
        "status": lead.status.value,
        "responsavel_id": lead.responsavel_id,
        "proxima_acao_em": lead.proxima_acao_em.isoformat() if lead.proxima_acao_em else None,
        "criado_em": lead.criado_em.isoformat() if lead.criado_em else None,
    }
    # Mesma regra de mascaramento de PII já usada no resto do CRM
    # (app.api.leads._lead_response) -- o assistente não contorna essa regra.
    if usuario.pode("leads.pii.view"):
        item["email"] = lead.email
        item["telefone"] = lead.telefone
    return item


async def _tool_contar_leads_por_status(session: AsyncSession, usuario: UsuarioAutenticado, _args: dict) -> dict:
    if not usuario.pode("leads.view"):
        return {"erro": MSG_SEM_PERMISSAO_LEADS}
    linhas = (
        await session.execute(
            select(Lead.status, func.count())
            .where(Lead.organizacao_id == usuario.organizacao_id, Lead.arquivado_em.is_(None))
            .group_by(Lead.status)
        )
    ).all()
    return {"contagem_por_status": {status.value: quantidade for status, quantidade in linhas}}


async def _tool_buscar_leads(session: AsyncSession, usuario: UsuarioAutenticado, args: dict) -> dict:
    if not usuario.pode("leads.view"):
        return {"erro": MSG_SEM_PERMISSAO_LEADS}
    limite = max(1, min(int(args.get("limite") or LIMITE_PADRAO_LEADS), LIMITE_MAXIMO_LEADS))
    status_enum = None
    if args.get("status"):
        try:
            status_enum = StatusLead(args["status"])
        except ValueError:
            pass
    filtros = _filtros_lead(usuario, args.get("busca"), status_enum, None, None, None, None, None, False, None)
    leads = (
        (await session.execute(select(Lead).where(*filtros).order_by(Lead.criado_em.desc()).limit(limite)))
        .scalars()
        .all()
    )
    return {"total_encontrado": len(leads), "leads": [_lead_resumo(lead, usuario) for lead in leads]}


async def _tool_leads_prioritarios(session: AsyncSession, usuario: UsuarioAutenticado, args: dict) -> dict:
    if not usuario.pode("leads.view"):
        return {"erro": MSG_SEM_PERMISSAO_LEADS}
    limite = max(1, min(int(args.get("limite") or LIMITE_PADRAO_LEADS), LIMITE_MAXIMO_LEADS))
    criterio = args.get("criterio")
    if criterio not in {"atrasadas", "sem_responsavel", "sem_proxima_acao"}:
        return {"erro": "criterio invalido, use atrasadas, sem_responsavel ou sem_proxima_acao"}
    filtros = _filtros_lead(usuario, None, None, None, None, None, None, None, False, criterio)
    leads = (
        (
            await session.execute(
                select(Lead).where(*filtros).order_by(Lead.proxima_acao_em.asc().nulls_first()).limit(limite)
            )
        )
        .scalars()
        .all()
    )
    return {"criterio": criterio, "total_encontrado": len(leads), "leads": [_lead_resumo(lead, usuario) for lead in leads]}


FERRAMENTAS = {
    "contar_leads_por_status": _tool_contar_leads_por_status,
    "buscar_leads": _tool_buscar_leads,
    "leads_prioritarios": _tool_leads_prioritarios,
}


async def _chamar_gemini_bruto(contents: list[dict], *, http_client: httpx.AsyncClient) -> dict:
    """Fallback automático (achado do usuário, 11/09/2026): se o modelo
    principal devolver 429 (cota por minuto estourada) ou 503 (modelo
    sobrecarregado do lado do Google -- achado do usuário, 14/09/2026, visto
    ao vivo em produção repetidas vezes no "flash" cheio), tenta na mesma
    chamada o modelo fallback mais leve (settings.gemini_modelo_fallback)
    antes de desistir -- só para esses dois casos transitórios, qualquer
    outro erro propaga direto. Mesma lógica de app.ia_sombra.chamar_gemini,
    reaproveitada aqui."""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY nao configurada")
    corpo = {
        "contents": contents,
        "tools": _declaracoes_tools(),
        "systemInstruction": {"parts": [{"text": PROMPT_SISTEMA}]},
    }
    modelos = _modelos_gemini_em_ordem(settings)
    resposta = None
    for indice, modelo in enumerate(modelos):
        await _respeitar_intervalo_minimo_gemini(settings.gemini_intervalo_minimo_segundos)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        resposta = await http_client.post(url, json=corpo, headers={"x-goog-api-key": settings.gemini_api_key})
        if resposta.status_code in (429, 503) and indice < len(modelos) - 1:
            continue
        break
    resposta.raise_for_status()
    return resposta.json()


async def perguntar(session: AsyncSession, usuario: UsuarioAutenticado, pergunta: str, historico: list[dict]) -> str:
    """Executa uma pergunta do assistente, com até MAX_RODADAS_TOOL idas e
    vindas de chamada de ferramenta. `historico` é só texto visível (pares
    pergunta/resposta anteriores desta conversa) -- o histórico bruto do
    Gemini (com thoughtSignature) nunca é persistido nem volta ao
    frontend, só existe dentro desta função, por pergunta."""
    contents: list[dict] = []
    for item in historico[-MAX_MENSAGENS_HISTORICO:]:
        role = "model" if item.get("role") == "model" else "user"
        contents.append({"role": role, "parts": [{"text": str(item.get("texto", ""))[:MAX_TAMANHO_PERGUNTA]}]})
    contents.append({"role": "user", "parts": [{"text": pergunta}]})

    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.gemini_timeout_segundos) as http_client:
        for _rodada in range(MAX_RODADAS_TOOL):
            dados = await _chamar_gemini_bruto(contents, http_client=http_client)
            candidatos = dados.get("candidates") or []
            if not candidatos:
                return "Não consegui gerar uma resposta agora. Tente reformular a pergunta."
            conteudo_modelo = candidatos[0].get("content", {})
            partes = conteudo_modelo.get("parts", [])
            chamadas_funcao = [p["functionCall"] for p in partes if "functionCall" in p]
            if not chamadas_funcao:
                textos = [p.get("text", "") for p in partes if "text" in p]
                return "".join(textos).strip() or "Não encontrei uma resposta para essa pergunta."

            contents.append(conteudo_modelo)
            partes_resposta = []
            for chamada in chamadas_funcao:
                nome = chamada.get("name", "")
                funcao = FERRAMENTAS.get(nome)
                resultado = (
                    await funcao(session, usuario, chamada.get("args") or {})
                    if funcao
                    else {"erro": f"ferramenta desconhecida: {nome}"}
                )
                parte_resposta = {"functionResponse": {"name": nome, "response": resultado}}
                if "id" in chamada:
                    parte_resposta["functionResponse"]["id"] = chamada["id"]
                partes_resposta.append(parte_resposta)
            contents.append({"role": "user", "parts": partes_resposta})

    return "Essa pergunta exigiu consultas demais -- tente ser mais específico."


class PerguntaInput(BaseModel):
    pergunta: str = Field(min_length=1, max_length=MAX_TAMANHO_PERGUNTA)
    # Sem limite de tamanho aqui de propósito -- perguntar() já recorta para
    # as últimas MAX_MENSAGENS_HISTORICO entradas antes de montar o prompt;
    # um histórico mais longo (ex.: reload de página com conversa antiga)
    # não deve virar 422 no frontend, só é ignorado além do recorte.
    historico: list[dict] = Field(default_factory=list)


@router.post("/perguntar")
async def perguntar_endpoint(
    dados: PerguntaInput, request: Request, session: SessionDep, usuario: UsuarioAtualDep
) -> dict:
    # Disponível para qualquer usuário autenticado (achado do usuário,
    # 11/09/2026) -- não há Depends(exigir_permissao(...)) aqui, então o
    # CSRF (normalmente checado por exigir_permissao) precisa ser validado
    # manualmente. A restrição de dados real está dentro de cada ferramenta
    # (usuario.pode("leads.view")), não no acesso ao endpoint.
    exigir_csrf(request, usuario)
    settings = get_settings()
    if not settings.assistente_crm_enabled:
        raise HTTPException(status_code=503, detail="Zezinho das Marcas está desativado nesta instalação")
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="Zezinho das Marcas não está configurado (chave ausente)")
    try:
        resposta = await perguntar(session, usuario, dados.pergunta, dados.historico)
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.warning("Zezinho das Marcas recebeu %s do Gemini", exc.response.status_code)
        if exc.response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="O Zezinho das Marcas atingiu o limite de uso da IA no momento. Aguarde um minuto e tente de novo.",
            ) from exc
        if exc.response.status_code == 503:
            # Achado do usuário, 14/09/2026: mesmo com o fallback de modelo
            # (ver _chamar_gemini_bruto), o Gemini às vezes devolve 503 nos
            # dois modelos em sequência -- sobrecarga momentânea do lado do
            # Google, não um problema de configuração daqui.
            raise HTTPException(
                status_code=503,
                detail="O Zezinho das Marcas está temporariamente sobrecarregado (instabilidade do provedor de IA). Tente novamente em instantes.",
            ) from exc
        raise HTTPException(status_code=502, detail="Não foi possível consultar o assistente agora") from exc
    except Exception as exc:
        logger.exception("Falha ao consultar o assistente de IA")
        raise HTTPException(status_code=502, detail="Não foi possível consultar o assistente agora") from exc
    return {"resposta": resposta}
