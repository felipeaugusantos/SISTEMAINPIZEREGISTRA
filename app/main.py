import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

from app.api.admin import router as admin_router
from app.api.analises import router as analises_router
from app.api.aprendizado import router as aprendizado_router
from app.api.assistente_ia import router as assistente_ia_router
from app.api.atualizacoes import router as atualizacoes_router
from app.api.auth_routes import router as auth_router
from app.api.busca_admin import router as busca_admin_router
from app.api.carteira import router as carteira_router
from app.api.conciliacao import router as conciliacao_router
from app.api.confiabilidade import public_router as tenant_router
from app.api.confiabilidade import router as confiabilidade_router
from app.api.consulta import router as consulta_router
from app.api.contratacoes import router as contratacoes_router
from app.api.crm_admin import router as crm_router
from app.api.email_leads_config import router as email_leads_config_router
from app.api.escritorio import router as escritorio_router
from app.api.exclusoes import router as exclusoes_router
from app.api.fase2 import router as fase2_router
from app.api.fase3 import router as fase3_router
from app.api.feature_flags import router as feature_flags_router
from app.api.figurativa import router as figurativa_router
from app.api.financeiro import exigir_acesso_log_financeiro
from app.api.financeiro import router as financeiro_router
from app.api.horas import router as horas_router
from app.api.juridico import router as juridico_router
from app.api.leads import router as leads_router
from app.api.leads_guias import router as leads_guias_router
from app.api.leads_propostas import router as leads_propostas_router
from app.api.nfse import router as nfse_router
from app.api.observabilidade import router as observabilidade_router
from app.api.pagamentos import router_admin as pagamentos_admin_router
from app.api.pagamentos import router_webhook as pagamentos_webhook_router
from app.api.painel import router as painel_router
from app.api.pesquisas import router as pesquisas_router
from app.api.politicas_privacidade import public_router as politicas_privacidade_public_router
from app.api.politicas_privacidade import router as politicas_privacidade_router
from app.api.portal_cliente import router as portal_cliente_router
from app.api.portfolio_pi import portal_router as portfolio_pi_portal_router
from app.api.portfolio_pi import router as portfolio_pi_router
from app.api.privacidade import router as privacidade_router
from app.api.processos import router as processos_router
from app.api.producao import router as producao_router
from app.api.propostas_config import router as propostas_config_router
from app.api.prospeccao import router as prospeccao_router
from app.api.prospeccao import router_campanhas as prospeccao_campanhas_router
from app.api.rpi_admin import router as rpi_admin_router
from app.api.rpi_consulta import router as rpi_consulta_router
from app.api.saas import exigir_superadmin
from app.api.saas import router as saas_router
from app.api.scripts_atendimento_config import router as scripts_atendimento_config_router
from app.api.social_auth import router as social_auth_router
from app.api.usuarios import router as usuarios_router
from app.api.versoes_sistema import router as versoes_sistema_router
from app.api.vigilancia import router as vigilancia_router
from app.api.visual import router as visual_router
from app.auth import exigir_permissao, exigir_qualquer_permissao, obter_usuario_atual
from app.database import get_session
from app.models import EventoOperacional, RpiImportacao, RpiSyncEstado, RpiSyncExecucao
from app.observability import observar_requisicao
from app.queueing import status_fila
from app.rpi.health import avaliar_saude_rpi
from app.schemas import RpiHealthResponse
from app.settings import get_settings

settings = get_settings()
web_dir = Path(__file__).resolve().parent / "web"


async def exigir_chave_health(x_health_key: str | None = Header(default=None)) -> None:
    """Protege métricas e diagnósticos detalhados em produção."""
    if settings.app_env.lower() != "production":
        return
    if not settings.health_api_key:
        raise HTTPException(status_code=503, detail="Health check protegido nao configurado")
    if (
        not settings.health_api_key
        or not x_health_key
        or not secrets.compare_digest(x_health_key, settings.health_api_key)
    ):
        raise HTTPException(status_code=401, detail="Chave de health check invalida")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API de pesquisa indicativa de marcas publicadas pelo INPI Brasil.",
    docs_url=None if settings.app_env.lower() == "production" else "/docs",
    redoc_url=None if settings.app_env.lower() == "production" else "/redoc",
    openapi_url=None if settings.app_env.lower() == "production" else "/openapi.json",
)
if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        _provider = TracerProvider(resource=Resource.create({"service.name": "ze-registra-api"}))
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(_provider)
        FastAPIInstrumentor.instrument_app(app)
    except ImportError:
        pass
if settings.admin_force_https:
    app.add_middleware(HTTPSRedirectMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Integration-Key",
        "X-Report-Token",
        "X-CSRF-Token",
        "X-Health-Key",
    ],
)
app.middleware("http")(observar_requisicao)
app.include_router(processos_router)
app.include_router(leads_router)
app.include_router(leads_guias_router)
app.include_router(leads_propostas_router)
app.include_router(prospeccao_router)
app.include_router(prospeccao_campanhas_router)
app.include_router(privacidade_router)
app.include_router(pesquisas_router)
app.include_router(fase2_router)
app.include_router(fase3_router)
app.include_router(admin_router)
app.include_router(painel_router)
app.include_router(portal_cliente_router)
app.include_router(portfolio_pi_router)
app.include_router(portfolio_pi_portal_router)
app.include_router(observabilidade_router)
app.include_router(versoes_sistema_router)
app.include_router(atualizacoes_router)
app.include_router(feature_flags_router)
app.include_router(analises_router)
app.include_router(producao_router)
app.include_router(propostas_config_router)
app.include_router(email_leads_config_router)
app.include_router(scripts_atendimento_config_router)
app.include_router(assistente_ia_router)
app.include_router(rpi_admin_router)
app.include_router(rpi_consulta_router)
app.include_router(aprendizado_router)
app.include_router(busca_admin_router)
app.include_router(carteira_router)
app.include_router(juridico_router)
app.include_router(horas_router)
app.include_router(consulta_router)
app.include_router(figurativa_router)
app.include_router(visual_router)
app.include_router(vigilancia_router)
app.include_router(crm_router)
app.include_router(exclusoes_router)
app.include_router(escritorio_router)
app.include_router(financeiro_router)
app.include_router(auth_router)
app.include_router(social_auth_router)
app.include_router(usuarios_router)
app.include_router(saas_router)
app.include_router(confiabilidade_router)
app.include_router(politicas_privacidade_router)
app.include_router(contratacoes_router)
app.include_router(tenant_router)
app.include_router(politicas_privacidade_public_router)
app.include_router(observabilidade_router)
app.include_router(pagamentos_admin_router)
app.include_router(pagamentos_webhook_router)
app.include_router(conciliacao_router)
app.include_router(nfse_router)
app.mount("/static", StaticFiles(directory=web_dir / "static"), name="static")


@app.get("/", include_in_schema=False)
async def pagina_inicial() -> FileResponse:
    return FileResponse(web_dir / "index.html")


@app.get("/buscar-gratuita", include_in_schema=False)
async def pagina_busca_gratuita() -> FileResponse:
    return FileResponse(web_dir / "buscar-gratuita.html")


@app.get("/processos/{numero}", include_in_schema=False)
async def pagina_detalhe_processo(numero: str) -> FileResponse:
    return FileResponse(web_dir / "processo.html")


@app.get("/relatorios/{pesquisa_id}", include_in_schema=False)
async def pagina_relatorio(pesquisa_id: str) -> FileResponse:
    return FileResponse(web_dir / "relatorio.html")


@app.get("/login", include_in_schema=False)
async def pagina_login() -> FileResponse:
    return FileResponse(web_dir / "login.html")


@app.get("/alterar-senha", include_in_schema=False)
async def pagina_alterar_senha() -> FileResponse:
    return FileResponse(web_dir / "alterar-senha.html")


@app.get("/esqueci-senha", include_in_schema=False)
async def pagina_esqueci_senha() -> FileResponse:
    return FileResponse(web_dir / "esqueci-senha.html")


@app.get("/configurar-mfa", include_in_schema=False)
async def pagina_configurar_mfa() -> FileResponse:
    return FileResponse(web_dir / "configurar-mfa.html")


@app.get("/redefinir-senha", include_in_schema=False)
async def pagina_redefinir_senha() -> FileResponse:
    return FileResponse(web_dir / "redefinir-senha.html")


@app.get("/privacidade/excluir-meus-dados", include_in_schema=False)
async def pagina_solicitar_exclusao() -> FileResponse:
    return FileResponse(web_dir / "privacidade-solicitar-exclusao.html")


@app.get("/privacidade/confirmar-exclusao", include_in_schema=False)
async def pagina_confirmar_exclusao() -> FileResponse:
    return FileResponse(web_dir / "privacidade-confirmar-exclusao.html")


@app.get("/admin/leads", include_in_schema=False, dependencies=[Depends(exigir_permissao("leads.view"))])
async def painel_leads() -> FileResponse:
    return FileResponse(web_dir / "admin-leads.html")


@app.get("/admin", include_in_schema=False, dependencies=[Depends(exigir_permissao("dashboard.view"))])
async def painel_administrativo() -> FileResponse:
    return FileResponse(web_dir / "admin.html")


@app.get(
    "/admin/relatorios",
    include_in_schema=False,
    dependencies=[
        Depends(
            exigir_qualquer_permissao(
                "leads.view", "crm.view", "finance.view", "legal.view", "portfolio.view"
            )
        )
    ],
)
async def painel_relatorios() -> FileResponse:
    """Catálogo de relatórios; cada relatório continua protegido pelo módulo de origem."""
    return FileResponse(web_dir / "admin-relatorios.html")


@app.get(
    "/admin/atualizacoes",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("dashboard.view"))],
)
async def painel_atualizacoes() -> FileResponse:
    return FileResponse(web_dir / "admin-atualizacoes.html")


@app.get("/admin/feature-flags", include_in_schema=False, dependencies=[Depends(exigir_superadmin)])
async def painel_feature_flags() -> FileResponse:
    return FileResponse(web_dir / "admin-feature-flags.html")


@app.get("/admin/atualizacoes/cadastrar", include_in_schema=False, dependencies=[Depends(exigir_superadmin)])
async def painel_atualizacoes_cadastro() -> FileResponse:
    return FileResponse(web_dir / "admin-atualizacoes-cadastro.html")


@app.get(
    "/admin/configuracao/clicksign",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("production.view"))],
)
async def painel_configuracao_clicksign() -> FileResponse:
    return FileResponse(web_dir / "admin-clicksign.html")


@app.get(
    "/admin/configuracao/onboarding",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("dashboard.view"))],
)
async def painel_configuracao_onboarding() -> FileResponse:
    return FileResponse(web_dir / "admin-onboarding.html")


@app.get(
    "/admin/consulta",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("leads.view"))],
)
async def painel_consulta() -> FileResponse:
    return FileResponse(web_dir / "admin-consulta.html")


@app.get(
    "/admin/consulta/{pesquisa_id}",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("leads.view"))],
)
async def painel_consulta_relatorio(pesquisa_id: str) -> FileResponse:
    return FileResponse(web_dir / "admin-consulta.html")


@app.get(
    "/admin/figurativa",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("leads.view"))],
)
async def painel_figurativa() -> FileResponse:
    return FileResponse(web_dir / "admin-figurativa.html")


@app.get(
    "/admin/pesquisas",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("leads.view"))],
)
async def painel_pesquisas() -> FileResponse:
    return FileResponse(web_dir / "admin-leads.html")


@app.get(
    "/admin/processos-monitorados",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("portfolio.view"))],
)
async def painel_processos_monitorados() -> FileResponse:
    return FileResponse(web_dir / "admin-carteira.html")


@app.get(
    "/admin/prospeccao",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("prospeccao.view"))],
)
async def painel_prospeccao() -> FileResponse:
    return FileResponse(web_dir / "admin-prospeccao.html")


@app.get(
    "/admin/operacao-juridica",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("legal.view"))],
)
async def painel_operacao_juridica() -> FileResponse:
    return FileResponse(web_dir / "admin-juridico.html")


@app.get(
    "/admin/crm",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("crm.view"))],
)
async def painel_crm() -> FileResponse:
    return FileResponse(web_dir / "admin-crm.html")


@app.get(
    "/admin/financeiro",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_financeiro() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro.html")


@app.get(
    "/admin/financeiro/contas-a-pagar",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_contas_a_pagar() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro.html")


@app.get(
    "/admin/financeiro/contas-a-receber",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_contas_a_receber() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro.html")


@app.get(
    "/admin/financeiro/formas-pagamento",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_formas_pagamento() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro-formas.html")


@app.get(
    "/admin/financeiro/categorias",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_categorias_financeiras() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro-categorias.html")


@app.get(
    "/admin/financeiro/retribuicoes",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_retribuicoes() -> FileResponse:
    return FileResponse(web_dir / "admin-retribuicoes.html")


@app.get(
    "/admin/financeiro/plano-contas",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_plano_contas() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro-contabil.html")


@app.get(
    "/admin/financeiro/lucratividade",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_lucratividade() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro-lucratividade.html")


@app.get(
    "/admin/financeiro/comissoes",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_comissoes() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro-comissoes.html")


@app.get(
    "/admin/financeiro/conciliacao",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_conciliacao() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro-conciliacao.html")


@app.get(
    "/admin/financeiro/nfse",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_nfse() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro-nfse.html")


@app.get(
    "/admin/configuracao/regras-automaticas",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("leads.view"))],
)
async def painel_regras_automaticas() -> FileResponse:
    return FileResponse(web_dir / "admin-regras-automaticas.html")


@app.get(
    "/admin/configuracao/modelo-propostas",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("leads.view"))],
)
async def painel_modelo_propostas() -> FileResponse:
    return FileResponse(web_dir / "admin-modelo-propostas.html")


@app.get(
    "/admin/configuracao/modelo-email-leads",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("leads.view"))],
)
async def painel_modelo_email_leads() -> FileResponse:
    return FileResponse(web_dir / "admin-modelo-email-leads.html")


@app.get(
    "/admin/configuracao/script-atendimento",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("leads.view"))],
)
async def painel_script_atendimento() -> FileResponse:
    return FileResponse(web_dir / "admin-script-atendimento.html")


@app.get(
    "/admin/assistente",
    include_in_schema=False,
    # Disponível para qualquer usuário autenticado (achado do usuário,
    # 11/09/2026) -- o widget flutuante também é global; a restrição real
    # de dados fica dentro de cada ferramenta do assistente (leads.view).
    dependencies=[Depends(obter_usuario_atual)],
)
async def painel_assistente_ia() -> FileResponse:
    return FileResponse(web_dir / "admin-assistente.html")


@app.get(
    "/admin/configuracao/consulta-rpi",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("rpi.view"))],
)
async def painel_consulta_rpi() -> FileResponse:
    return FileResponse(web_dir / "admin-consulta-rpi.html")


@app.get(
    "/admin/producao/log-financeiro",
    include_in_schema=False,
    dependencies=[Depends(exigir_acesso_log_financeiro)],
)
async def painel_log_financeiro() -> FileResponse:
    return FileResponse(web_dir / "admin-financeiro-log.html")


@app.get(
    "/admin/analises/{pesquisa_id}",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("leads.view"))],
)
async def central_analise_marca(pesquisa_id: str) -> FileResponse:
    return FileResponse(web_dir / "admin-analise.html")


@app.get(
    "/admin/fase2",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("validation.view"))],
)
async def painel_fase2() -> FileResponse:
    return FileResponse(web_dir / "admin-fase2.html")


@app.get(
    "/admin/validacao",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("validation.view"))],
)
async def painel_validacao() -> FileResponse:
    return FileResponse(web_dir / "admin-fase2.html")


@app.get("/admin/fase3", include_in_schema=False, dependencies=[Depends(exigir_permissao("risk.view"))])
async def painel_fase3() -> FileResponse:
    return FileResponse(web_dir / "admin-fase3.html")


@app.get("/admin/risco", include_in_schema=False, dependencies=[Depends(exigir_permissao("risk.view"))])
async def painel_risco() -> FileResponse:
    return FileResponse(web_dir / "admin-fase3.html")


@app.get(
    "/admin/producao",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("production.view"))],
)
async def painel_producao() -> FileResponse:
    return FileResponse(web_dir / "admin-producao.html")


@app.get(
    "/admin/aprendizado",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("learning.view"))],
)
async def painel_aprendizado() -> FileResponse:
    return FileResponse(web_dir / "admin-aprendizado.html")


@app.get(
    "/admin/usuarios",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("users.view"))],
)
async def painel_usuarios() -> FileResponse:
    return FileResponse(web_dir / "admin-usuarios.html")


@app.get(
    "/admin/notificacoes",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("dashboard.view"))],
)
async def painel_notificacoes() -> FileResponse:
    return FileResponse(web_dir / "admin-notificacoes.html")


@app.get("/admin/saas", include_in_schema=False, dependencies=[Depends(exigir_superadmin)])
async def painel_saas() -> FileResponse:
    return FileResponse(web_dir / "admin-saas.html")


@app.get(
    "/admin/confiabilidade",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("production.manage"))],
)
async def painel_confiabilidade() -> FileResponse:
    return FileResponse(web_dir / "admin-confiabilidade.html")


@app.get(
    "/admin/observabilidade",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("production.view"))],
)
async def painel_observabilidade() -> FileResponse:
    return FileResponse(web_dir / "admin-observabilidade.html")


@app.get("/privacidade", include_in_schema=False)
async def pagina_privacidade() -> FileResponse:
    return FileResponse(web_dir / "privacidade.html")


@app.get("/sobre", include_in_schema=False)
async def pagina_sobre() -> FileResponse:
    return FileResponse(web_dir / "sobre.html")


@app.get("/contato", include_in_schema=False)
async def pagina_contato() -> FileResponse:
    return FileResponse(web_dir / "contato.html")


@app.get("/health", tags=["infraestrutura"])
async def health(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "environment": settings.app_env, "database": "error"},
        )
    fila = await status_fila()
    status_geral = "ok" if fila["status"] == "ok" or not settings.redis_required else "degraded"
    conteudo = {"status": status_geral, "environment": settings.app_env, "database": "ok"}
    if fila["status"] == "ok" or settings.redis_required:
        conteudo["redis"] = fila
    return JSONResponse(
        status_code=200 if status_geral == "ok" else 503,
        content=conteudo,
    )


@app.get("/health/db", tags=["infraestrutura"], dependencies=[Depends(exigir_chave_health)])
async def health_db(session: Annotated[AsyncSession, Depends(get_session)]) -> JSONResponse:
    inicio = datetime.now(UTC)
    try:
        await session.execute(text("SELECT 1"))
        latencia = round((datetime.now(UTC) - inicio).total_seconds() * 1000, 2)
        return JSONResponse(status_code=200, content={"status": "ok", "latencia_ms": latencia})
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "indisponivel", "erro": type(exc).__name__},
        )


@app.get("/health/queue", tags=["infraestrutura"], dependencies=[Depends(exigir_chave_health)])
async def health_queue() -> JSONResponse:
    fila = await status_fila()
    obrigatoria = bool(settings.redis_required)
    return JSONResponse(
        status_code=200 if fila["status"] == "ok" or not obrigatoria else 503,
        content=fila,
    )


@app.get("/metrics", tags=["infraestrutura"], dependencies=[Depends(exigir_chave_health)])
async def metrics(session: Annotated[AsyncSession, Depends(get_session)]) -> PlainTextResponse:
    """Métricas Prometheus simples, sem dados sensíveis e compatíveis com scraping."""
    total, erros, duracao = (
        await session.execute(
            select(
                func.count(EventoOperacional.id),
                func.sum(case((EventoOperacional.sucesso.is_(False), 1), else_=0)),
                func.avg(EventoOperacional.duracao_ms),
            )
        )
    ).one()
    fila = await status_fila()
    linhas = [
        "# HELP ze_registra_http_requests_total Requisições operacionais registradas.",
        "# TYPE ze_registra_http_requests_total counter",
        f"ze_registra_http_requests_total {int(total or 0)}",
        "# HELP ze_registra_http_errors_total Erros operacionais registrados.",
        "# TYPE ze_registra_http_errors_total counter",
        f"ze_registra_http_errors_total {int(erros or 0)}",
        "# HELP ze_registra_http_duration_ms_avg Duração média das operações em milissegundos.",
        "# TYPE ze_registra_http_duration_ms_avg gauge",
        f"ze_registra_http_duration_ms_avg {float(duracao or 0):.2f}",
    ]
    if fila["status"] == "ok":
        linhas.extend(
            [
                "# TYPE ze_registra_queue_pending gauge",
                f"ze_registra_queue_pending {int(fila.get('pendentes') or 0)}",
                "# TYPE ze_registra_queue_failed gauge",
                f"ze_registra_queue_failed {int(fila.get('falhas') or 0)}",
                "# TYPE ze_registra_queue_processing gauge",
                f"ze_registra_queue_processing {int(fila.get('processando') or 0)}",
            ]
        )
    return PlainTextResponse("\n".join(linhas) + "\n", media_type="text/plain; version=0.0.4")


@app.get(
    "/health/rpi",
    response_model=RpiHealthResponse,
    tags=["infraestrutura"],
    dependencies=[Depends(exigir_chave_health)],
)
async def health_rpi(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    estado = await session.get(RpiSyncEstado, 1)
    ultima = (
        await session.execute(
            select(RpiImportacao)
            .where(RpiImportacao.tipo == "marca")
            .order_by(RpiImportacao.numero_rpi.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    ultima_execucao = (
        await session.execute(
            select(RpiSyncExecucao)
            .where(RpiSyncExecucao.finalizado_em.is_not(None))
            .order_by(RpiSyncExecucao.finalizado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    quantidade_erros = int(
        await session.scalar(
            select(func.count()).select_from(RpiSyncExecucao).where(RpiSyncExecucao.status == "falhou")
        )
        or 0
    )
    ultima_sincronizacao = (estado.ultima_verificacao_em if estado and estado.ultima_verificacao_em else None) or (
        ultima.importado_em if ultima else None
    )
    status_rpi, idade_horas, motivos = avaliar_saude_rpi(
        status_sync=estado.status if estado else None,
        ultima_rpi_oficial=estado.ultima_rpi_oficial if estado else None,
        ultima_rpi_importada=ultima.numero_rpi if ultima else None,
        ultima_sincronizacao=ultima_sincronizacao,
        status_integridade=ultima.status_integridade if ultima else None,
        limite_atraso_horas=settings.rpi_stale_hours,
        agora=datetime.now(UTC),
    )
    duracao = None
    if ultima_execucao and ultima_execucao.iniciado_em and ultima_execucao.finalizado_em:
        duracao = max(
            0.0,
            (ultima_execucao.finalizado_em - ultima_execucao.iniciado_em).total_seconds(),
        )
    payload = RpiHealthResponse(
        status=status_rpi,
        ultima_rpi_disponivel=estado.ultima_rpi_oficial if estado else None,
        ultima_rpi_importada=ultima.numero_rpi if ultima else None,
        ultima_sincronizacao=ultima_sincronizacao,
        idade_dados_horas=round(idade_horas, 2) if idade_horas is not None else None,
        registros_ultima_importacao=ultima.registros_processados if ultima else None,
        duracao_ultima_sincronizacao_segundos=round(duracao, 2) if duracao is not None else None,
        status_integridade=ultima.status_integridade if ultima else None,
        anomalias=ultima.anomalias if ultima else [],
        quantidade_erros=quantidade_erros,
        ultimo_erro=estado.ultimo_erro if estado else None,
        motivos=motivos,
    )
    return JSONResponse(
        status_code=200 if status_rpi in {"ok", "processando"} else 503,
        content=payload.model_dump(mode="json"),
    )
