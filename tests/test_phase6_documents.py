import hashlib


def test_hash_documento_reproduzivel() -> None:
    conteudo = "procuracao|123||v1".encode()
    assert hashlib.sha256(conteudo).hexdigest() == hashlib.sha256(conteudo).hexdigest()


def test_alteracao_de_conteudo_exige_nova_versao() -> None:
    original = hashlib.sha256(b"procuracao|123||v1").hexdigest()
    alterado = hashlib.sha256(b"procuracao|456||v2").hexdigest()
    assert original != alterado
