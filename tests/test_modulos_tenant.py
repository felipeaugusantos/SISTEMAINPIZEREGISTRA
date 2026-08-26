from app.auth import MODULO_POR_PERMISSAO, UsuarioAutenticado, normalizar_modulos_plano
from app.permissions import CHAVES_PERMISSAO, permissoes_do_perfil


def test_modulos_operacionais_possuem_permissoes_independentes() -> None:
    assert {"crm.view", "crm.manage"} <= CHAVES_PERMISSAO
    assert {"crm.view", "crm.manage"} <= permissoes_do_perfil("comercial")
    assert MODULO_POR_PERMISSAO["crm"] == "crm"
    assert MODULO_POR_PERMISSAO["portfolio"] == "processos_monitorados"
    assert MODULO_POR_PERMISSAO["legal"] == "operacao_juridica"


def test_usuario_padrao_contem_modulos_operacionais() -> None:
    modulos = UsuarioAutenticado.__dataclass_fields__["modulos_plano"].default
    assert {"crm", "processos_monitorados", "operacao_juridica"} <= modulos


def test_aliases_legados_sao_normalizados_no_contexto_da_sessao() -> None:
    assert normalizar_modulos_plano(["leads", "portfolio", "legal"]) == {
        "leads",
        "processos_monitorados",
        "operacao_juridica",
    }
