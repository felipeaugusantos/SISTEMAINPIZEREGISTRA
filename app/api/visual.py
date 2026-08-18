from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.trademarks.visual import assinatura_visual
from app.trademarks.visual_ranking import calcular_score_visual

router = APIRouter(prefix="/v1/admin/figurativa", tags=["busca visual"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
OperadorDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]


@router.post("/validar-imagem")
async def validar_imagem(
    arquivo: Annotated[UploadFile, File()],
    _session: SessionDep,
    _operador: OperadorDep,
    limite_mb: Annotated[int, Query(ge=1, le=10)] = 5,
) -> dict:
    """Valida a imagem e devolve sua assinatura visual reproduzível.

    A comparação com um acervo será ativada na próxima entrega; por enquanto
    este endpoint evita aceitar arquivos inválidos e estabelece o contrato do
    score visual, sem sugerir uma decisão jurídica automática.
    """
    conteudo = await arquivo.read()
    if len(conteudo) > limite_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="A imagem excede o limite permitido.")
    if not conteudo:
        raise HTTPException(status_code=422, detail="Envie uma imagem para análise.")
    try:
        assinatura = assinatura_visual(conteudo)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="O arquivo enviado não é uma imagem válida.") from exc
    ocr: dict[str, object] = {"status": "indisponivel", "texto": "", "motivo": "OCR opcional não instalado no ambiente."}
    try:
        from io import BytesIO

        import pytesseract
        from PIL import Image
        texto = pytesseract.image_to_string(Image.open(BytesIO(conteudo))).strip()
        ocr = {"status": "concluido", "texto": texto, "confianca": None}
    except Exception:
        pass
    score = calcular_score_visual(1.0, 1.0 if ocr.get("texto") else 0.0)
    return {
        "arquivo": arquivo.filename,
        "mime_type": arquivo.content_type,
        "pixels": 256,
        "assinatura_visual": "".join(map(str, assinatura)),
        "ocr": {"status": "pendente", "motivo": "OCR será executado na etapa de processamento textual."},
        "score_combinado": score.as_dict(),
        "score_status": "experimental",
        "aviso": "A similaridade visual é um indicador técnico e requer validação humana.",
    }
