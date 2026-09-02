import asyncio

from app.imap_polling import verificar_respostas_email
from tests.conftest import FakeResult, FakeSession

# --- Fase 9 do plano Leads/CRM (03/09/2026), achado L6: detecção de resposta
# via IMAP fica inerte enquanto settings.imap_enabled=False (padrão). ---


def test_verificar_respostas_desligado_por_padrao_nao_toca_nada() -> None:
    session = FakeSession()
    resultado = asyncio.run(verificar_respostas_email(session))
    assert resultado == {"verificado": False, "motivo": "imap_desabilitado"}


def test_verificar_respostas_habilitado_pausa_leads_correspondentes() -> None:
    import app.imap_polling as modulo
    from app.settings import get_settings

    settings = get_settings()
    original_imap_enabled = settings.imap_enabled
    original_buscar = modulo._buscar_remetentes_nao_lidos
    original_pausar = modulo.pausar_envios_pendentes_do_lead

    settings.imap_enabled = True
    modulo._buscar_remetentes_nao_lidos = lambda *_a, **_k: ["fulano@example.com"]

    chamadas: list[tuple[int, int]] = []

    async def _pausar_fake(_session, organizacao_id, lead_id):
        chamadas.append((organizacao_id, lead_id))
        return 2

    modulo.pausar_envios_pendentes_do_lead = _pausar_fake

    try:
        session = FakeSession([FakeResult(itens=[(9, 1)])])
        resultado = asyncio.run(verificar_respostas_email(session))
    finally:
        settings.imap_enabled = original_imap_enabled
        modulo._buscar_remetentes_nao_lidos = original_buscar
        modulo.pausar_envios_pendentes_do_lead = original_pausar

    assert resultado == {"verificado": True, "remetentes": 1, "pausados": 2}
    assert chamadas == [(1, 9)]


def test_verificar_respostas_falha_de_conexao_nao_quebra() -> None:
    import app.imap_polling as modulo
    from app.settings import get_settings

    settings = get_settings()
    original_imap_enabled = settings.imap_enabled
    original_buscar = modulo._buscar_remetentes_nao_lidos

    def _explode(*_a: object, **_k: object) -> list[str]:
        raise OSError("conexao recusada")

    settings.imap_enabled = True
    modulo._buscar_remetentes_nao_lidos = _explode
    try:
        session = FakeSession()
        resultado = asyncio.run(verificar_respostas_email(session))
    finally:
        settings.imap_enabled = original_imap_enabled
        modulo._buscar_remetentes_nao_lidos = original_buscar

    assert resultado["verificado"] is False
    assert "erro" in resultado
