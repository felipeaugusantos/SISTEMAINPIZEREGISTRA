from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

from app.api.admin import router as admin_router
from app.api.analises import router as analises_router
from app.api.aprendizado import router as aprendizado_router
from app.api.auth_routes import router as auth_router
from app.api.carteira import router as carteira_router
from app.api.confiabilidade import public_router as tenant_router
from app.api.confiabilidade import router as confiabilidade_router
from app.api.consulta import router as consulta_router
from app.api.crm_admin import router as crm_router
from app.api.exclusoes import router as exclusoes_router
from app.api.fase2 import router as fase2_router
from app.api.fase3 import router as fase3_router
from app.api.figurativa import router as figurativa_router
from app.api.financeiro import exigir_acesso_log_financeiro
from app.api.financeiro import router as financeiro_router
from app.api.juridico import router as juridico_router
from app.api.leads import router as leads_router
from app.api.painel import router as painel_router
from app.api.pesquisas import router as pesquisas_router
from app.api.processos import router as processos_router
from app.api.producao import router as producao_router
from app.api.rpi_admin import router as rpi_admin_router
from app.api.saas import exigir_superadmin
from app.api.saas import router as saas_router
from app.api.social_auth import router as social_auth_router
from app.api.usuarios import router as usuarios_router
from app.auth import exigir_permissao
from app.database import get_session
from app.observability import observar_requisicao
from app.queueing import status_fila
from app.security import exigir_token_integracao
from app.settings import get_settings

settings = get_settings()
web_dir = Path(__file__).resolve().parent / "web"

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="API de pesquisa indicativa de marcas publicadas pelo INPI Brasil.",
    docs_url=None if settings.app_env.lower() == "production" else "/docs",
    redoc_url=None if settings.app_env.lower() == "production" else "/redoc",
    openapi_url=None if settings.app_env.lower() == "production" else "/openapi.json",
)
if settings.admin_force_https:
    app.add_middleware(HTTPSRedirectMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Integration-Key"],
)
app.middleware("http")(observar_requisicao)
app.include_router(processos_router)
app.include_router(leads_router)
app.include_router(
    pesquisas_router,
    dependencies=[Depends(exigir_token_integracao)],
)
app.include_router(fase2_router)
app.include_router(fase3_router)
app.include_router(admin_router)
app.include_router(painel_router)
app.include_router(analises_router)
app.include_router(producao_router)
app.include_router(rpi_admin_router)
app.include_router(aprendizado_router)
app.include_router(carteira_router)
app.include_router(juridico_router)
app.include_router(consulta_router)
app.include_router(figurativa_router)
app.include_router(crm_router)
app.include_router(exclusoes_router)
app.include_router(financeiro_router)
app.include_router(auth_router)
app.include_router(social_auth_router)
app.include_router(usuarios_router)
app.include_router(saas_router)
app.include_router(confiabilidade_router)
app.include_router(tenant_router)
app.mount("/static", StaticFiles(directory=web_dir / "static"), name="static")


@app.get("/", include_in_schema=False)
async def pagina_inicial() -> FileResponse:
    return FileResponse(web_dir / "index.html")


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


@app.get("/redefinir-senha", include_in_schema=False)
async def pagina_redefinir_senha() -> FileResponse:
    return FileResponse(web_dir / "redefinir-senha.html")


@app.get(
    "/admin/leads", include_in_schema=False, dependencies=[Depends(exigir_permissao("leads.view"))]
)
async def painel_leads() -> FileResponse:
    return FileResponse(web_dir / "admin-leads.html")


@app.get(
    "/admin", include_in_schema=False, dependencies=[Depends(exigir_permissao("dashboard.view"))]
)
async def painel_administrativo() -> FileResponse:
    return FileResponse(web_dir / "admin.html")


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
    "/admin/operacao-juridica",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("legal.view"))],
)
async def painel_operacao_juridica() -> FileResponse:
    return FileResponse(web_dir / "admin-juridico.html")


@app.get(
    "/admin/crm",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("leads.view"))],
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
    "/admin/financeiro/retribuicoes",
    include_in_schema=False,
    dependencies=[Depends(exigir_permissao("finance.view"))],
)
async def painel_retribuicoes() -> FileResponse:
    return FileResponse(web_dir / "admin-retribuicoes.html")


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


@app.get(
    "/admin/fase3", include_in_schema=False, dependencies=[Depends(exigir_permissao("risk.view"))]
)
async def painel_fase3() -> FileResponse:
    return FileResponse(web_dir / "admin-fase3.html")


@app.get(
    "/admin/risco", include_in_schema=False, dependencies=[Depends(exigir_permissao("risk.view"))]
)
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
