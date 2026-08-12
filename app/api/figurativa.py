from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.trademarks.viena import buscar_anterioridades_viena

router = APIRouter(prefix="/v1/admin/figurativa", tags=["busca figurativa"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
OperadorDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]


@router.get("/anterioridades")
async def anterioridades_figurativas(
    session: SessionDep,
    operador: OperadorDep,
    codigos: Annotated[str, Query(min_length=1, max_length=500)],
    limite: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    """Anterioridades figurativas por Classificação de Viena (códigos separados por vírgula)."""
    lista = [item.strip() for item in codigos.replace(";", ",").split(",") if item.strip()]
    resultados = await buscar_anterioridades_viena(session, lista, limite)
    return {"codigos": lista, "total": len(resultados), "anterioridades": resultados}
