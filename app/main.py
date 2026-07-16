from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.leads import exigir_admin
from app.api.leads import router as leads_router
from app.api.processos import router as processos_router
from app.database import get_session
from app.settings import get_settings

settings = get_settings()
web_dir = Path(__file__).resolve().parent / "web"

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="API de consulta de marcas e patentes do INPI Brasil.",
)
app.include_router(processos_router)
app.include_router(leads_router)
app.mount("/static", StaticFiles(directory=web_dir / "static"), name="static")


@app.get("/", include_in_schema=False)
async def pagina_inicial() -> FileResponse:
    return FileResponse(web_dir / "index.html")


@app.get("/processos/{numero}", include_in_schema=False)
async def pagina_detalhe_processo(numero: str) -> FileResponse:
    return FileResponse(web_dir / "processo.html")


@app.get("/admin/leads", include_in_schema=False, dependencies=[Depends(exigir_admin)])
async def painel_leads() -> FileResponse:
    return FileResponse(web_dir / "admin-leads.html")


@app.get("/privacidade", include_in_schema=False)
async def pagina_privacidade() -> FileResponse:
    return FileResponse(web_dir / "privacidade.html")


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
    return JSONResponse(
        content={"status": "ok", "environment": settings.app_env, "database": "ok"},
    )
