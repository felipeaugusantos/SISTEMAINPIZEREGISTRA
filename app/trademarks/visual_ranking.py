from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScoreVisual:
    total: float
    fatores: tuple[dict, ...]
    versao: str = "visual-ranking-1.0"

    def as_dict(self) -> dict:
        return {"score": self.total, "versao": self.versao, "fatores": list(self.fatores), "revisao_humana": "obrigatoria"}


def calcular_score_visual(similaridade: float, ocr_similaridade: float = 0.0, vienna_afinidade: float = 0.0) -> ScoreVisual:
    valores = [("similaridade_visual", similaridade, 0.55), ("ocr", ocr_similaridade, 0.20), ("v viena", vienna_afinidade, 0.25)]
    fatores = tuple({"fator": nome, "valor": round(max(0.0, min(1.0, valor)), 4), "peso": peso, "contribuicao": round(max(0.0, min(1.0, valor)) * peso * 100, 2)} for nome, valor, peso in valores)
    total = round(sum(item["contribuicao"] for item in fatores), 2)
    return ScoreVisual(total, fatores)
