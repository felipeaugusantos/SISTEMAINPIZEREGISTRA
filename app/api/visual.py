import hashlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao, hash_ip
from app.database import get_session
from app.models import EventoAuditoria
from app.proxy import cliente_ip
from app.ratelimit import RateLimiter
from app.trademarks.visual import assinatura_visual

logger = logging.getLogger("ze_registra.visual")

router = APIRouter(prefix="/v1/admin/figurativa", tags=["busca visual"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
OperadorDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]
# Achado da Fase 14.3 (auditoria fina da busca figurativa, 23/09/2026):
# upload + processamento de imagem (PIL) é a operação mais cara em CPU/IO
# do módulo, mas não tinha nenhum limitador de taxa -- diferente de todo
# outro endpoint de upload do sistema (app.api.portal_cliente, app.api.leads).
_limitar_validar_imagem = RateLimiter(limite=20, janela_segundos=60, escopo="figurativa-validar-imagem")


@router.post("/validar-imagem")
async def validar_imagem(
    arquivo: Annotated[UploadFile, File()],
    request: Request,
    session: SessionDep,
    operador: OperadorDep,
    limite_mb: Annotated[int, Query(ge=1, le=10)] = 5,
) -> dict:
    """Valida a imagem e devolve sua assinatura visual reproduzível.

    A comparação com um acervo será ativada na próxima entrega; por enquanto
    este endpoint evita aceitar arquivos inválidos e devolve o OCR e a
    assinatura, sem sugerir uma decisão jurídica automática nem uma pontuação
    de similaridade (ver achado da Fase 14.2 abaixo).
    """
    _limitar_validar_imagem.aplicar(f"operador:{operador.id}")
    # Achado da Fase 14.3: o arquivo inteiro era lido em memória antes de
    # checar o tamanho -- um cliente podia mandar um arquivo bem maior que
    # o limite e ele seria totalmente carregado antes de ser rejeitado.
    # arquivo.size (Starlette) reflete o que já foi gravado no spool
    # durante o parse do multipart, permitindo rejeitar sem ler tudo antes;
    # a checagem por len(conteudo) abaixo continua como defesa em
    # profundidade, mesmo padrão de app.api.leads.enviar_arquivo_documento_lead.
    if arquivo.size and arquivo.size > limite_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="A imagem excede o limite permitido.")
    conteudo = await arquivo.read()
    if len(conteudo) > limite_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="A imagem excede o limite permitido.")
    if not conteudo:
        raise HTTPException(status_code=422, detail="Envie uma imagem para análise.")
    try:
        assinatura = assinatura_visual(conteudo)
    except Exception as exc:
        # Achado da Fase 14.3: "except Exception" cru mascarava qualquer
        # erro interno (inclusive bug de código, não só imagem inválida)
        # como "arquivo inválido" 422, sem nenhum log -- passa a registrar
        # o erro real pra observabilidade, mantendo a mensagem genérica
        # pro cliente (não expõe detalhe interno na resposta).
        logger.exception("Falha ao calcular assinatura visual do upload em /validar-imagem")
        raise HTTPException(status_code=422, detail="O arquivo enviado não é uma imagem válida.") from exc
    ocr: dict[str, object] = {
        "status": "indisponivel",
        "texto": "",
        "motivo": "OCR opcional não instalado no ambiente.",
    }
    try:
        from io import BytesIO

        import pytesseract
        from PIL import Image

        texto = pytesseract.image_to_string(Image.open(BytesIO(conteudo))).strip()
        ocr = {"status": "concluido", "texto": texto, "confianca": None}
    except Exception:
        pass
    # Achado da Fase 14.2 (auditoria fina da busca figurativa, 23/09/2026):
    # o "score visual" antigo passava similaridade=1.0 fixo pro fator de
    # maior peso (55%) mesmo sem nenhuma comparação real com acervo --
    # qualquer upload válido saía com nota alta que não significava nada.
    # E a resposta de OCR era descartada e substituída por um texto
    # estático de "pendente" mesmo quando o OCR já tinha rodado de verdade
    # (bloco acima). Removido o score decorativo; devolvido o OCR real.
    assinatura_hex = "".join(map(str, assinatura))
    # Achado baixo da Fase 14.1 (auditoria fina da busca figurativa,
    # 23/09/2026): nenhum upload de imagem deixava rastro -- não dava pra
    # saber quem validou qual arquivo nem quando. Mesmo padrão de
    # EventoAuditoria já usado em app/api/figurativa.py.
    # Achado P2 do Codex no PR #126: nome/tipo/tamanho não identificam a
    # imagem de fato -- dois uploads distintos com o mesmo nome (ou sem
    # nome) ficavam indistinguíveis no rastro. O hash do conteúdo do
    # arquivo é um identificador estável e independente do nome informado
    # pelo cliente.
    hash_conteudo = hashlib.sha256(conteudo).hexdigest()
    session.add(
        EventoAuditoria(
            organizacao_id=operador.organizacao_id,
            actor_id=operador.id,
            ator=operador.ator,
            acao="validar_imagem",
            recurso="busca_figurativa",
            resource_type="validacao_visual",
            resource_id=hash_conteudo,
            sucesso=True,
            status_http=200,
            ip_hash=hash_ip(cliente_ip(request)),
            detalhes={
                "arquivo": arquivo.filename,
                "mime_type": arquivo.content_type,
                "tamanho_bytes": len(conteudo),
                "hash_conteudo": hash_conteudo,
                "assinatura_visual": assinatura_hex,
            },
        )
    )
    await session.commit()
    return {
        "arquivo": arquivo.filename,
        "mime_type": arquivo.content_type,
        "pixels": 256,
        "assinatura_visual": assinatura_hex,
        "ocr": ocr,
        "score_visual": {
            "disponivel": False,
            "motivo": "A comparação com um acervo de imagens ainda não foi implementada -- "
            "nenhuma pontuação de similaridade real é calculada por este endpoint.",
        },
        "aviso": "Esta validação confirma que o arquivo é uma imagem íntegra e gera sua "
        "assinatura visual reproduzível; não há decisão automática de anterioridade.",
    }
