"""Primitivas determinísticas para a primeira versão da busca visual."""

from io import BytesIO

from PIL import Image, ImageOps


def assinatura_visual(conteudo: bytes) -> tuple[int, ...]:
    """Calcula um hash perceptual simples (aHash) sem depender de ML."""
    imagem = Image.open(BytesIO(conteudo)).convert("L")
    imagem = ImageOps.fit(imagem, (16, 16), method=Image.Resampling.LANCZOS)
    pixels = list(imagem.getdata())
    media = sum(pixels) / len(pixels)
    return tuple(1 if pixel >= media else 0 for pixel in pixels)


def similaridade_visual(esquerda: tuple[int, ...], direita: tuple[int, ...]) -> float:
    if not esquerda or len(esquerda) != len(direita):
        return 0.0
    iguais = sum(a == b for a, b in zip(esquerda, direita, strict=True))
    return round(iguais / len(esquerda), 4)
