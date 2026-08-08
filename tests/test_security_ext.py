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
