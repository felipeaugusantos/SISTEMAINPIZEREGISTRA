"""Scripts de atendimento comercial (item 1 da lista de melhorias de
produto, 15/09/2026): modelo de 1º atendimento + opção de adicionar mais
modelos manualmente. Reaproveita o padrão de app/api/email_leads_config.py
e app/api/propostas_config.py (texto em Organizacao.branding), mas como
lista em vez de um único texto por organização.
"""

import asyncio

from app.api.scripts_atendimento_config import (
    ITENS_PADRAO,
    ScriptAtendimentoInput,
    criar_script,
    editar_script,
    excluir_script,
    listar_scripts,
)
from app.models import Organizacao
from tests.conftest import FakeSession, usuario_teste


def _org(branding: dict | None = None) -> Organizacao:
    return Organizacao(id=1, nome="Zé Registra", slug="ze-registra", branding=branding or {})


def test_listar_scripts_sem_configuracao_devolve_padrao() -> None:
    session = FakeSession(objetos_get=[_org()])

    resultado = asyncio.run(listar_scripts(session, usuario_teste()))

    assert resultado["itens"] == ITENS_PADRAO
    assert resultado["itens"][0]["id"] == "primeiro-atendimento"


def test_criar_script_adiciona_a_lista_sem_apagar_o_padrao() -> None:
    org = _org()
    session = FakeSession(objetos_get=[org])
    dados = ScriptAtendimentoInput(titulo="Follow-up 7 dias", corpo="Olá {{lead.nome}}, passando para saber...")

    resultado = asyncio.run(criar_script(dados, session, usuario_teste()))

    assert len(resultado["itens"]) == 2
    assert resultado["itens"][0]["id"] == "primeiro-atendimento"
    assert resultado["itens"][1]["titulo"] == "Follow-up 7 dias"
    assert org.branding["scripts_atendimento"] == resultado["itens"]
    assert session.commits == 1


def test_editar_script_atualiza_titulo_e_corpo() -> None:
    org = _org({"scripts_atendimento": [{"id": "abc123", "titulo": "Antigo", "corpo": "Texto antigo"}]})
    session = FakeSession(objetos_get=[org])
    dados = ScriptAtendimentoInput(titulo="Novo título", corpo="Texto novo, bem mais completo que o anterior.")

    resultado = asyncio.run(editar_script("abc123", dados, session, usuario_teste()))

    assert resultado["itens"] == [{"id": "abc123", "titulo": "Novo título", "corpo": "Texto novo, bem mais completo que o anterior."}]


def test_excluir_script_remove_da_lista() -> None:
    org = _org(
        {
            "scripts_atendimento": [
                {"id": "abc123", "titulo": "Um", "corpo": "Texto um, com conteúdo suficiente."},
                {"id": "def456", "titulo": "Dois", "corpo": "Texto dois, com conteúdo suficiente."},
            ]
        }
    )
    session = FakeSession(objetos_get=[org])

    resultado = asyncio.run(excluir_script("abc123", session, usuario_teste()))

    assert [item["id"] for item in resultado["itens"]] == ["def456"]


def test_script_de_saida_de_rede_bloqueia_destinos_internos_sem_derrubar_o_dns() -> None:
    # Reforço do A09: o script não pode rodar em CI, então confere o essencial do conteúdo.
    from pathlib import Path

    script = Path("docker/saida-rede-docker.sh").read_text(encoding="utf-8")
    assert "169.254.0.0/16" in script
    for rede in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
        assert rede in script
    assert "DOCKER-USER" in script
    # DNS do host e tráfego entre os containers do projeto vêm ANTES dos bloqueios
    assert script.index("--dport 53") < script.index('for destino in $BLOQUEADOS')
    assert script.index('-d "$sub" -j RETURN') < script.index('for destino in $BLOQUEADOS')
    # nunca derruba serviços (regra de produção: nada de compose down)
    assert "compose down" not in script and "docker compose" not in script.replace("docker compose up", "")


def test_script_de_saida_de_rede_cobre_o_proprio_host_e_recusa_backend_nftables() -> None:
    # Revisão do Codex (PR #195): tráfego para o host entra por INPUT; backend nftables ignora DOCKER-USER.
    from pathlib import Path

    script = Path("docker/saida-rede-docker.sh").read_text(encoding="utf-8")
    assert 'iptables -I INPUT 1 -s "$sub" -j "$CADEIA_HOST"' in script
    assert "LIBERAR_HOST_PORTAS" in script
    assert 'iptables -S FORWARD | grep -q -- "-j DOCKER-USER"' in script
    # a verificação do backend acontece antes de qualquer alteração
    assert script.index('grep -q -- "-j DOCKER-USER"') < script.index('iptables -N "$CADEIA_HOST"')


def test_script_de_saida_de_rede_nao_tem_barra_n_literal_fora_de_texto() -> None:
    # Um "\n" literal depois da continuação de linha virou argumento extra e gerou um aviso falso no status.
    from pathlib import Path

    script = Path("docker/saida-rede-docker.sh").read_text(encoding="utf-8")
    barra_n = chr(92) + "n"
    assert barra_n not in script
