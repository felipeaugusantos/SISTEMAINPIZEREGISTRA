from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.atualizacoes import (
    AdiarAvisoInput,
    ConfirmarLeituraInput,
    ReportarProblemaInput,
    adiar_aviso,
    atualizacao_publica,
    confirmar_leitura,
    listar_atualizacoes,
    reportar_problema,
)
from app.models import EventoAuditoria, InteracaoVersaoSistema, ProblemaVersaoSistema, VersaoSistema
from tests.conftest import FakeResult, FakeSession, usuario_teste


def _usuario(org: int = 7):
    base = usuario_teste("operador", {"dashboard.view"})
    return base.__class__(**{**base.__dict__, "organizacao_id": org})


def _versao(*, tipo: str = "correcao", permite_adiar: bool = True) -> VersaoSistema:
    agora = datetime.now(UTC)
    return VersaoSistema(
        id=12,
        versao="1.0.72-2026-09-09",
        titulo="Central de atualizações",
        problema_identificado="Os operadores não tinham uma visão clara das mudanças implantadas.",
        solucao_aplicada="Foi criada uma central com informações funcionais e ações por usuário.",
        impacto_usuario="As novidades agora podem ser entendidas e confirmadas dentro do painel.",
        documentacao_url="https://app.zeregistra.com.br/sobre",
        permite_adiar=permite_adiar,
        tipo_atualizacao=tipo,
        modulos_afetados=["plataforma", "producao"],
        implantada_em=agora,
        commit_sha="b" * 40,
        migration_revision="k22u7w3r064",
        evidencias_testes=[
            {"nome": "Segredo técnico", "resultado": "aprovado", "resumo": "Detalhe interno", "quantidade": 20},
            {"nome": "Outro teste", "resultado": "ignorado", "resumo": "Ambiente indisponível"},
        ],
        riscos_conhecidos=["Risco técnico interno"],
        instrucoes="Instruções internas não devem aparecer.",
        plano_rollback="Plano técnico não deve aparecer.",
        conteudo_hash="c" * 64,
        status="publicada",
        criado_por="superadmin@interno.local",
        criado_em=agora,
        atualizado_em=agora,
        publicado_por="superadmin@interno.local",
        publicado_em=agora,
    )


def test_projecao_publica_expoe_so_informacao_funcional() -> None:
    resposta = atualizacao_publica(_versao(), None)

    assert resposta["evidencias"] == {"aprovadas": 1, "falharam": 0, "ignoradas": 1, "total": 2}
    assert resposta["leitura_obrigatoria"] is True
    assert resposta["pode_adiar"] is True
    proibidos = {
        "commit_sha", "migration_revision", "riscos_conhecidos", "instrucoes", "plano_rollback",
        "conteudo_hash", "criado_por", "publicado_por", "evidencias_testes",
    }
    assert proibidos.isdisjoint(resposta)
    assert "Segredo técnico" not in str(resposta)


@pytest.mark.asyncio
async def test_listagem_personaliza_interacao_sem_expor_dados_tecnicos() -> None:
    item = _versao()
    interacao = InteracaoVersaoSistema(
        versao_sistema_id=item.id,
        organizacao_id=7,
        usuario_id=1,
        confirmado_em=datetime.now(UTC),
    )
    session = FakeSession(resultados=[FakeResult(itens=[(item, interacao)])])

    resposta = await listar_atualizacoes(session, _usuario())

    assert resposta["versao_implantada"] == "development"
    assert resposta["novidades"][0]["estado"]["confirmada_em"] is not None
    assert "commit_sha" not in resposta["novidades"][0]


@pytest.mark.asyncio
async def test_confirmacao_e_idempotente_e_auditada_por_tenant() -> None:
    item = _versao()
    session = FakeSession(objetos_get=[item], resultados=[FakeResult(scalar=None)])

    resposta = await confirmar_leitura(12, ConfirmarLeituraInput(confirmar=True), session, _usuario())

    assert resposta["confirmado_em"] is not None
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.organizacao_id == 7
    assert evento.acao == "LER_ATUALIZACAO"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_correcao_critica_nunca_pode_ser_adiada() -> None:
    item = _versao(tipo="critica", permite_adiar=True)
    session = FakeSession(objetos_get=[item])

    with pytest.raises(HTTPException) as erro:
        await adiar_aviso(12, AdiarAvisoInput(dias=7), session, _usuario())

    assert erro.value.status_code == 409
    assert session.commits == 0


@pytest.mark.asyncio
async def test_relato_valida_modulo_e_nao_audita_descricao() -> None:
    item = _versao()
    session = FakeSession(objetos_get=[item])
    dados = ReportarProblemaInput(
        categoria="erro",
        modulo="producao",
        descricao="Ao abrir os detalhes, o conteúdo esperado não foi apresentado.",
    )

    resposta = await reportar_problema(12, dados, session, _usuario())

    assert resposta["status"] == "aberto"
    problema = next(obj for obj in session.adicionados if isinstance(obj, ProblemaVersaoSistema))
    assert problema.organizacao_id == 7
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert "descricao" not in evento.detalhes


def test_relato_rejeita_credencial_e_campos_extras() -> None:
    with pytest.raises(ValidationError):
        ReportarProblemaInput(
            categoria="erro",
            modulo="producao",
            descricao="O sistema mostrou token=segredo-que-nao-deve-ser-persistido na página.",
        )
    with pytest.raises(ValidationError):
        ReportarProblemaInput.model_validate(
            {"categoria": "erro", "descricao": "Descrição válida para o relato do operador.", "commit": "abc"}
        )


def test_migration_isola_interacoes_e_relato_por_organizacao() -> None:
    texto = Path("migrations/versions/k22u7w3r064_central_atualizacoes.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "j21t6v2q953"' in texto
    assert "uq_interacao_versao_usuario" in texto
    assert "app.organizacao_id" in texto
    assert "FORCE ROW LEVEL SECURITY" in texto
    assert 'op.drop_table("problemas_versoes_sistema")' in texto


def test_interface_nao_contem_campos_tecnicos_restritos() -> None:
    texto = (
        Path("app/web/admin-atualizacoes.html").read_text(encoding="utf-8")
        + Path("app/web/static/admin-atualizacoes.js").read_text(encoding="utf-8")
    )
    for campo in ("commit_sha", "migration_revision", "plano_rollback", "conteudo_hash", "publicado_por"):
        assert campo not in texto
