from types import SimpleNamespace

from app.security_ext import codigo_totp, proteger_segredo, revelar_segredo, validar_totp


def test_segredo_protegido_nao_fica_em_texto_claro() -> None:
    protegido = proteger_segredo("SEGREDO-TOTP")
    assert protegido != "SEGREDO-TOTP"
    assert revelar_segredo(protegido) == "SEGREDO-TOTP"


def test_totp_aceita_janela_de_relogio() -> None:
    segredo = "JBSWY3DPEHPK3PXP"
    codigo = codigo_totp(segredo, instante=1_700_000_000)
    assert validar_totp(segredo, codigo, instante=1_700_000_000)
    assert validar_totp(segredo, codigo, instante=1_700_000_030)
    assert not validar_totp(segredo, "000000", instante=1_700_000_000)


def test_rotacao_mantem_leitura_da_chave_anterior(monkeypatch) -> None:
    chave_antiga = "chave-antiga-com-pelo-menos-trinta-e-dois-caracteres"
    chave_nova = "chave-nova-com-pelo-menos-trinta-e-dois-caracteres"
    antiga = SimpleNamespace(
        security_master_key=chave_antiga,
        security_master_key_version=1,
        security_master_key_previous="",
        security_master_key_previous_version=0,
    )
    monkeypatch.setattr("app.security_ext.get_settings", lambda: antiga)
    protegido_v1 = proteger_segredo("SEGREDO-TOTP")
    legado_sem_versao = protegido_v1.split(":", 1)[1]

    nova = SimpleNamespace(
        security_master_key=chave_nova,
        security_master_key_version=2,
        security_master_key_previous=chave_antiga,
        security_master_key_previous_version=1,
    )
    monkeypatch.setattr("app.security_ext.get_settings", lambda: nova)

    assert protegido_v1.startswith("v1:")
    assert revelar_segredo(protegido_v1) == "SEGREDO-TOTP"
    assert revelar_segredo(legado_sem_versao) == "SEGREDO-TOTP"
    assert proteger_segredo("NOVO").startswith("v2:")
