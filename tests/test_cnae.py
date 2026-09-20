from app.cnae import descricao_cnae

# --- Achado do usuário (20/09/2026): a tela de Prospecção mostra o código
# do CNAE, mas a equipe de atendimento não sabe o que cada número
# significa. app/data/cnae.json traz a tabela oficial de subclasses do
# CNAE 2.0 (código -> descrição), só para exibição. ---


def test_descricao_cnae_reconhece_codigo_conhecido() -> None:
    assert descricao_cnae("4711302") == "COMÉRCIO VAREJISTA DE MERCADORIAS EM GERAL, COM PREDOMINÂNCIA DE PRODUTOS ALIMENTÍCIOS - SUPERMERCADOS"


def test_descricao_cnae_ignora_pontuacao_no_codigo() -> None:
    # Formato "4711-3/02" (com pontuação) e "4711302" (só dígitos) devem
    # resolver para a mesma descrição -- o sistema não normaliza o campo
    # ao gravar, então a busca precisa tolerar os dois formatos.
    assert descricao_cnae("4711-3/02") == descricao_cnae("4711302")


def test_descricao_cnae_codigo_desconhecido_retorna_none() -> None:
    assert descricao_cnae("0000000") is None


def test_descricao_cnae_vazio_retorna_none() -> None:
    assert descricao_cnae(None) is None
    assert descricao_cnae("") is None
