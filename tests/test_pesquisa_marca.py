from app.schemas import PesquisaMarcaCreate
from app.search import extrair_radicais, gerar_variacoes, identificar_criterios


def payload() -> dict[str, object]:
    return {
        "nome": "Maria Silva",
        "empresa": "ACME Ltda",
        "email_corporativo": "maria@acme.com.br",
        "telefone": "11999999999",
        "marca": "CAVALINHO FEROZ",
        "atividade": "Venda de roupas e acessórios pela internet",
        "aceite_privacidade": True,
    }


def test_extrai_radicais_recomendados() -> None:
    # Convencao oficial do BuscaWeb do INPI para "CAVALINHO FEROZ" (ver ajuda de
    # pesquisa por radical do proprio INPI).
    assert extrair_radicais("CAVALINHO FEROZ") == ["CAVALIN", "FERO"]


def test_gera_variacoes_ortograficas_e_foneticas_recomendadas() -> None:
    variacoes = gerar_variacoes("CAVALINHO FEROZ")

    assert {"CAVALO", "KAVAL", "PHERO", "FHERO"} <= set(variacoes)


def test_explica_por_que_ocorrencia_foi_encontrada() -> None:
    assert "Nome idêntico" in identificar_criterios("CAVALINHO FEROZ", "CAVALINHO FEROZ")
    assert "Variação ortográfica ou fonética" in identificar_criterios(
        "KAVAL PHERO",
        "CAVALINHO FEROZ",
    )


def test_aceita_email_com_dominio_da_empresa() -> None:
    dados = PesquisaMarcaCreate.model_validate(payload())
    assert dados.email_corporativo == "maria@acme.com.br"


def test_aceita_email_pessoal() -> None:
    dados = payload()
    dados["email_corporativo"] = "maria@gmail.com"
    pesquisa = PesquisaMarcaCreate.model_validate(dados)
    assert pesquisa.email_corporativo == "maria@gmail.com"


def test_atividade_do_negocio_e_obrigatoria() -> None:
    dados = payload()
    del dados["atividade"]

    try:
        PesquisaMarcaCreate.model_validate(dados)
    except ValueError:
        return
    raise AssertionError("A atividade deveria ser obrigatória")
