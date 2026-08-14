from app.auditing import criar_evento_auditoria, mascarar_dados_auditoria


def test_mascaramento_remove_segredos_em_qualquer_nivel() -> None:
    dados = mascarar_dados_auditoria(
        {
            "usuario": "operador",
            "senha": "nao-pode-aparecer",
            "oauth": {"client_secret": "segredo", "provedor": "google"},
            "itens": [{"token_hash": "hash"}],
        }
    )

    assert dados == {
        "usuario": "operador",
        "senha": "[REDACTED]",
        "oauth": {"client_secret": "[REDACTED]", "provedor": "google"},
        "itens": [{"token_hash": "[REDACTED]"}],
    }


def test_evento_ampliado_mantem_before_after_mascarados() -> None:
    evento = criar_evento_auditoria(
        organizacao_id=7,
        actor_id=11,
        ator="admin@example.test",
        acao="ALTERAR",
        recurso="usuario:22",
        resource_type="usuario",
        resource_id=22,
        sucesso=True,
        status_http=200,
        before_state={"email": "antes@example.test", "senha_hash": "antiga"},
        after_state={"email": "depois@example.test", "senha_hash": "nova"},
    )

    assert evento.actor_id == 11
    assert evento.resource_type == "usuario"
    assert evento.resource_id == "22"
    assert evento.before_state["senha_hash"] == "[REDACTED]"
    assert evento.after_state["senha_hash"] == "[REDACTED]"
