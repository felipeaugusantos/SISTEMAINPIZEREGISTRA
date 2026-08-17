from datetime import UTC, datetime

from app.models import ColidenciaVigilancia, PreferenciaVigilancia
from app.vigilancia import calcular_score_risco, validar_comunicacao


def test_score_vigilancia_explicavel_e_deterministico():
    primeiro = calcular_score_risco("Minha Marca", "Minha Marca Ltda", {"35"}, {"01"}, {"35"}, {"01"})
    segundo = calcular_score_risco("Minha Marca", "Minha Marca Ltda", {"35"}, {"01"}, {"35"}, {"01"})
    assert primeiro == segundo
    assert primeiro[0] > 0
    assert primeiro[1]["classes_nice"] == ["35"]
    assert primeiro[1]["codigos_viena"] == ["01"]


def test_comunicacao_exige_regra_e_aprovacao():
    preferencia = PreferenciaVigilancia(ativo=True, canais=["portal"])
    item = ColidenciaVigilancia(status="pendente", evidencias={"regra": "vigilancia"}, justificativa="evidencia")
    permitido, motivo = validar_comunicacao(preferencia=preferencia, colidencia=item, canal="portal")
    assert not permitido and "aprovacao" in motivo
    item.status = "aprovado"
    item.aprovado_por = 1
    item.aprovado_em = datetime.now(UTC)
    permitido, motivo = validar_comunicacao(preferencia=preferencia, colidencia=item, canal="portal")
    assert permitido and motivo == "ok"


def test_falso_positivo_bloqueia_comunicacao():
    preferencia = PreferenciaVigilancia(ativo=True, canais=["portal"])
    item = ColidenciaVigilancia(status="aprovado", aprovado_por=1, aprovado_em=datetime.now(UTC),
                                evidencias={"regra": "vigilancia"}, justificativa="revisado", falso_positivo=True)
    permitido, motivo = validar_comunicacao(preferencia=preferencia, colidencia=item, canal="portal")
    assert not permitido and "falso positivo" in motivo
