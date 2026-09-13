import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.atualizacoes import (
    AdiarAvisoInput,
    AtualizarStatusProblemaInput,
    ConfirmarLeituraInput,
    ReportarProblemaInput,
    adiar_aviso,
    atualizacao_publica,
    atualizar_status_problema,
    baixar_anexo_problema,
    confirmar_leitura,
    gerar_relatorio_pdf,
    listar_atualizacoes,
    listar_problemas,
    reportar_problema,
)
from app.models import EventoAuditoria, InteracaoVersaoSistema, Organizacao, ProblemaVersaoSistema, VersaoSistema
from tests.conftest import FakeResult, FakeSession, usuario_teste


def _usuario(org: int = 7):
    base = usuario_teste("operador", {"dashboard.view"})
    return base.__class__(**{**base.__dict__, "organizacao_id": org})


def _tech(org: int = 1):
    base = usuario_teste("tech", {"production.view"})
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


@pytest.mark.asyncio
async def test_relatorio_pdf_gera_documento_e_audita() -> None:
    item = _versao()
    organizacao = Organizacao(id=7, nome="Cliente Teste", slug="cliente-teste", plano_id=1)
    session = FakeSession(resultados=[FakeResult(itens=[item])], objetos_get=[organizacao])

    resposta = await gerar_relatorio_pdf(session, _usuario(), de=None, ate=None)

    assert resposta.media_type == "application/pdf"
    assert resposta.body.startswith(b"%PDF")
    assert "central-de-atualizacoes.pdf" in resposta.headers["content-disposition"]
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "EXPORTAR_RELATORIO_ATUALIZACOES"
    assert evento.organizacao_id == 7
    assert session.commits == 1


@pytest.mark.asyncio
async def test_relatorio_pdf_sem_atualizacoes_nao_falha() -> None:
    organizacao = Organizacao(id=7, nome="Cliente Teste", slug="cliente-teste", plano_id=1)
    session = FakeSession(resultados=[FakeResult(itens=[])], objetos_get=[organizacao])

    resposta = await gerar_relatorio_pdf(session, _usuario(), de=None, ate=None)

    assert resposta.body.startswith(b"%PDF")


def test_interface_nao_contem_campos_tecnicos_restritos() -> None:
    texto = (
        Path("app/web/admin-atualizacoes.html").read_text(encoding="utf-8")
        + Path("app/web/static/admin-atualizacoes.js").read_text(encoding="utf-8")
    )
    for campo in ("commit_sha", "migration_revision", "plano_rollback", "conteudo_hash", "publicado_por"):
        assert campo not in texto


# --- Auditoria de problemas relatados (achado do usuário): "Reportar
# problema" gravava no banco, mas não existia nenhuma tela nem endpoint
# pra ver esses relatos. Restrito a Tech (production.view). ---


def _problema(**kwargs: object) -> ProblemaVersaoSistema:
    base = dict(
        id=1,
        versao_sistema_id=12,
        organizacao_id=7,
        usuario_id=3,
        categoria="erro",
        modulo="producao",
        descricao="Ao abrir os detalhes, o conteúdo esperado não foi apresentado.",
        etapas_reproduzir=None,
        resultado_esperado=None,
        resultado_encontrado=None,
        gravidade="media",
        anexo_nome=None,
        anexo_caminho=None,
        anexo_content_type=None,
        anexo_tamanho=None,
        anexo_hash=None,
        status="aberto",
        criado_em=datetime.now(UTC),
    )
    base.update(kwargs)
    return ProblemaVersaoSistema(**base)


@pytest.mark.asyncio
async def test_listar_problemas_junta_versao_organizacao_e_usuario() -> None:
    problema = _problema()
    session = FakeSession(
        resultados=[FakeResult(itens=[(problema, "1.0.72-2026-09-09", "Central de atualizações", "Cliente Teste", "Ana Operadora")])]
    )

    resposta = await listar_problemas(session, _tech(), status_filtro=None)

    assert resposta["itens"] == [
        {
            "id": 1,
            "versao_sistema_id": 12,
            "versao": "1.0.72-2026-09-09",
            "versao_titulo": "Central de atualizações",
            "organizacao_nome": "Cliente Teste",
            "usuario_nome": "Ana Operadora",
            "categoria": "erro",
            "modulo": "producao",
            "descricao": problema.descricao,
            "etapas_reproduzir": None,
            "resultado_esperado": None,
            "resultado_encontrado": None,
            "gravidade": "media",
            "anexo": None,
            "status": "aberto",
            "criado_em": problema.criado_em,
        }
    ]


@pytest.mark.asyncio
async def test_listar_problemas_rejeita_status_invalido() -> None:
    session = FakeSession([])

    with pytest.raises(HTTPException) as erro:
        await listar_problemas(session, _tech(), status_filtro="cancelado")

    assert erro.value.status_code == 422
    assert session.executados == []


@pytest.mark.asyncio
async def test_atualizar_status_problema_audita_e_comita() -> None:
    problema = _problema(status="aberto")
    session = FakeSession(objetos_get=[problema])

    resposta = await atualizar_status_problema(1, AtualizarStatusProblemaInput(status="resolvido"), session, _tech())

    assert resposta == {"id": 1, "status": "resolvido"}
    assert problema.status == "resolvido"
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "STATUS_PROBLEMA"
    assert evento.detalhes == {"status_anterior": "aberto", "status_novo": "resolvido"}
    assert session.commits == 1


@pytest.mark.asyncio
async def test_atualizar_status_problema_inexistente_devolve_404() -> None:
    session = FakeSession(objetos_get=[None])

    with pytest.raises(HTTPException) as erro:
        await atualizar_status_problema(999, AtualizarStatusProblemaInput(status="resolvido"), session, _tech())

    assert erro.value.status_code == 404


# --- Fase 6: reporte estruturado (etapas/resultado/gravidade/anexo) ---------
# Critério de aceite: todo problema fica vinculado à versão (versao_sistema_id,
# já obrigatório desde a Fase 3) e pode ser acompanhado até a resolução
# (status, já rastreado desde a Fase 3 -- ver testes acima).


@pytest.mark.asyncio
async def test_relato_gravidade_padrao_e_media() -> None:
    item = _versao()
    session = FakeSession(objetos_get=[item])
    dados = ReportarProblemaInput(
        categoria="erro", descricao="Ao salvar o formulário, a tela ficou em branco sem mensagem de erro."
    )

    await reportar_problema(12, dados, session, _usuario())

    problema = next(obj for obj in session.adicionados if isinstance(obj, ProblemaVersaoSistema))
    assert problema.gravidade == "media"


@pytest.mark.asyncio
async def test_relato_com_etapas_e_resultado_persiste_tudo() -> None:
    item = _versao()
    session = FakeSession(objetos_get=[item])
    dados = ReportarProblemaInput(
        categoria="regressao",
        descricao="O botão de exportar parou de funcionar depois da última atualização.",
        etapas_reproduzir="1. Abrir relatório\n2. Clicar em exportar",
        resultado_esperado="Um arquivo CSV deveria ser baixado.",
        resultado_encontrado="Nada acontece, sem erro visível.",
        gravidade="alta",
    )

    await reportar_problema(12, dados, session, _usuario())

    problema = next(obj for obj in session.adicionados if isinstance(obj, ProblemaVersaoSistema))
    assert problema.etapas_reproduzir == "1. Abrir relatório\n2. Clicar em exportar"
    assert problema.resultado_esperado == "Um arquivo CSV deveria ser baixado."
    assert problema.resultado_encontrado == "Nada acontece, sem erro visível."
    assert problema.gravidade == "alta"
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.detalhes["gravidade"] == "alta"
    assert evento.detalhes["com_anexo"] is False


def test_anexo_rejeita_tipo_de_arquivo_nao_permitido() -> None:
    with pytest.raises(ValidationError):
        ReportarProblemaInput(
            categoria="erro",
            descricao="Descrição válida para o relato do operador sobre o erro encontrado.",
            anexo={"nome": "script.exe", "content_type": "application/x-msdownload", "conteudo_base64": "eA=="},
        )


@pytest.mark.asyncio
async def test_relato_com_anexo_calcula_hash_e_persiste_via_storage() -> None:
    import app.api.atualizacoes as atualizacoes_modulo

    caminhos_salvos: list[tuple[str, bytes]] = []

    def _save_bytes_fake(key: str, content: bytes) -> str:
        caminhos_salvos.append((key, content))
        return f"data/uploads/{key}"

    original = atualizacoes_modulo.save_bytes
    atualizacoes_modulo.save_bytes = _save_bytes_fake
    try:
        item = _versao()
        session = FakeSession(objetos_get=[item])
        conteudo = b"print de tela em bytes fake"
        dados = ReportarProblemaInput(
            categoria="erro",
            descricao="A tela de resultados mostrou um valor incorreto no total.",
            anexo={
                "nome": "print-erro.png",
                "content_type": "image/png",
                "conteudo_base64": base64.b64encode(conteudo).decode(),
            },
        )

        resposta = await reportar_problema(12, dados, session, _usuario())
    finally:
        atualizacoes_modulo.save_bytes = original

    assert resposta["status"] == "aberto"
    assert len(caminhos_salvos) == 1
    problema = next(obj for obj in session.adicionados if isinstance(obj, ProblemaVersaoSistema))
    assert problema.anexo_nome == "print-erro.png"
    assert problema.anexo_hash == hashlib.sha256(conteudo).hexdigest()
    assert problema.anexo_tamanho == len(conteudo)
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.detalhes["com_anexo"] is True


@pytest.mark.asyncio
async def test_relato_com_anexo_excedendo_tamanho_maximo_rejeita() -> None:
    item = _versao()
    session = FakeSession(objetos_get=[item])
    conteudo_grande = b"x" * (9 * 1024 * 1024)
    dados = ReportarProblemaInput(
        categoria="erro",
        descricao="Anexo grande demais para testar o limite de tamanho do upload.",
        anexo={
            "nome": "grande.png",
            "content_type": "image/png",
            "conteudo_base64": base64.b64encode(conteudo_grande).decode(),
        },
    )

    with pytest.raises(HTTPException) as erro:
        await reportar_problema(12, dados, session, _usuario())

    assert erro.value.status_code == 413


@pytest.mark.asyncio
async def test_listar_problemas_expoe_metadados_do_anexo_sem_o_conteudo() -> None:
    problema = _problema(
        anexo_nome="print.png",
        anexo_caminho="data/uploads/problemas-versao/7/1/hash-print.png",
        anexo_content_type="image/png",
        anexo_tamanho=2048,
    )
    session = FakeSession(
        resultados=[FakeResult(itens=[(problema, "1.0.72-2026-09-09", "Central de atualizações", "Cliente Teste", "Ana Operadora")])]
    )

    resposta = await listar_problemas(session, _tech(), status_filtro=None)

    assert resposta["itens"][0]["anexo"] == {"nome": "print.png", "content_type": "image/png", "tamanho": 2048}
    assert "anexo_caminho" not in str(resposta)


@pytest.mark.asyncio
async def test_baixar_anexo_problema_sem_anexo_devolve_404() -> None:
    problema = _problema()
    session = FakeSession(objetos_get=[problema])

    with pytest.raises(HTTPException) as erro:
        await baixar_anexo_problema(1, session, _tech())

    assert erro.value.status_code == 404

    assert erro.value.status_code == 404
    assert session.commits == 0
