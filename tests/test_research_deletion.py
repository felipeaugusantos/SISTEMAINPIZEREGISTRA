from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import FakeResult, FakeSession, usuario_teste
from fastapi import HTTPException
from starlette.requests import Request

from app.api.exclusoes import (
    ConfirmarExclusaoInput,
    DecidirExclusaoInput,
    DecidirLoteInput,
    SolicitarExclusaoInput,
    decidir_solicitacao,
    decidir_solicitacoes_em_lote,
    excluir_pesquisa,
    listar_solicitacoes,
    solicitar_exclusao,
)
from app.auth import hash_senha
from app.models import (
    EventoAuditoria,
    PesquisaMarca,
    SolicitacaoExclusaoPesquisa,
    UsuarioOperacoes,
)


def requisicao() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/pesquisas/teste",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def pesquisa() -> PesquisaMarca:
    item = PesquisaMarca(
        id="12345678-1234-1234-1234-123456789abc",
        organizacao_id=1,
        lead_id=7,
        marca="MARCA TESTE",
        atividade=None,
        tipo_pesquisa="completa",
    )
    item.criado_em = datetime.now(UTC)
    return item


class SessaoExclusao(FakeSession):
    def __init__(self, resultados: list[FakeResult], usuario_db: UsuarioOperacoes | None = None):
        super().__init__(resultados)
        self.usuario_db = usuario_db
        self.excluidos: list[object] = []

    async def get(self, modelo: object, *_args: object, **_kwargs: object) -> object | None:
        if modelo is UsuarioOperacoes:
            return self.usuario_db
        return None

    async def delete(self, obj: object) -> None:
        self.excluidos.append(obj)


def usuario_banco(senha: str = "Senha-segura-123") -> UsuarioOperacoes:
    usuario = UsuarioOperacoes(
        organizacao_id=1,
        nome="Administrador",
        usuario="admin-teste-exclusao",
        email="admin-exclusao@teste.local",
        perfil="administrador",
        senha_hash=hash_senha(senha),
        ativo=True,
    )
    usuario.id = 1
    return usuario


@pytest.mark.asyncio
async def test_operador_solicita_exclusao_sem_apagar_pesquisa() -> None:
    item = pesquisa()
    session = SessaoExclusao([FakeResult(scalar=item), FakeResult(scalar=None)])

    resposta = await solicitar_exclusao(
        item.id,
        SolicitarExclusaoInput(motivo="Pesquisa criada por engano"),
        requisicao(),
        session,
        usuario_teste(perfil="comercial", permissoes={"leads.view"}),
        None,
    )

    assert resposta["status"] == "pendente"
    assert session.excluidos == []
    assert any(isinstance(obj, SolicitacaoExclusaoPesquisa) for obj in session.adicionados)
    assert any(isinstance(obj, EventoAuditoria) for obj in session.adicionados)


@pytest.mark.asyncio
async def test_administrador_precisa_confirmar_senha_correta() -> None:
    item = pesquisa()
    session = SessaoExclusao([FakeResult(scalar=item), FakeResult(scalar=None)], usuario_banco())

    with pytest.raises(HTTPException) as erro:
        await excluir_pesquisa(
            item.id,
            ConfirmarExclusaoInput(senha="senha-incorreta"),
            requisicao(),
            session,
            usuario_teste(),
            None,
        )

    assert erro.value.status_code == 403
    assert session.excluidos == []
    assert any(isinstance(obj, EventoAuditoria) and not obj.sucesso for obj in session.adicionados)


@pytest.mark.asyncio
async def test_administrador_exclui_com_senha_e_preserva_auditoria() -> None:
    item = pesquisa()
    session = SessaoExclusao([FakeResult(scalar=item), FakeResult(scalar=None)], usuario_banco())

    resposta = await excluir_pesquisa(
        item.id,
        ConfirmarExclusaoInput(senha="Senha-segura-123", motivo="Duplicidade confirmada"),
        requisicao(),
        session,
        usuario_teste(),
        None,
    )

    assert resposta.status_code == 204
    assert session.excluidos == [item]
    registro = next(obj for obj in session.adicionados if isinstance(obj, SolicitacaoExclusaoPesquisa))
    assert registro.status == "executada"


@pytest.mark.asyncio
async def test_exclusao_direta_conclui_solicitacao_ja_pendente() -> None:
    item = pesquisa()
    pendente = SolicitacaoExclusaoPesquisa(
        id=8,
        organizacao_id=1,
        pesquisa_id=item.id,
        pesquisa_referencia=item.id,
        marca=item.marca,
        lead_id=item.lead_id,
        solicitado_por_id=2,
        solicitado_por="operador@teste.local",
        motivo="Pesquisa duplicada",
        status="pendente",
    )
    session = SessaoExclusao([FakeResult(scalar=item), FakeResult(scalar=pendente)], usuario_banco())

    await excluir_pesquisa(
        item.id,
        ConfirmarExclusaoInput(senha="Senha-segura-123"),
        requisicao(),
        session,
        usuario_teste(),
        None,
    )

    assert pendente.status == "executada"
    assert pendente.decidido_por == "admin@teste.local"
    assert not any(isinstance(obj, SolicitacaoExclusaoPesquisa) for obj in session.adicionados)


@pytest.mark.asyncio
async def test_administrador_aprova_solicitacao_com_senha() -> None:
    item = pesquisa()
    solicitacao = SolicitacaoExclusaoPesquisa(
        id=9,
        organizacao_id=1,
        pesquisa_id=item.id,
        pesquisa_referencia=item.id,
        marca=item.marca,
        lead_id=item.lead_id,
        solicitado_por_id=2,
        solicitado_por="operador@teste.local",
        motivo="Cadastro incorreto",
        status="pendente",
    )
    solicitacao.criado_em = datetime.now(UTC)
    session = SessaoExclusao([FakeResult(scalar=solicitacao), FakeResult(scalar=item)], usuario_banco())

    resposta = await decidir_solicitacao(
        solicitacao.id,
        DecidirExclusaoInput(decisao="aprovar", senha="Senha-segura-123"),
        requisicao(),
        session,
        usuario_teste(),
        None,
    )

    assert resposta["status"] == "executada"
    assert session.excluidos == [item]
    assert solicitacao.decidido_por == "admin@teste.local"


def solicitacao_pendente(numero: int, item: PesquisaMarca) -> SolicitacaoExclusaoPesquisa:
    solicitacao = SolicitacaoExclusaoPesquisa(
        id=numero,
        organizacao_id=1,
        pesquisa_id=item.id,
        pesquisa_referencia=item.id,
        marca=item.marca,
        lead_id=item.lead_id,
        solicitado_por_id=2,
        solicitado_por="operador@teste.local",
        motivo="Cadastro incorreto",
        status="pendente",
    )
    solicitacao.criado_em = datetime.now(UTC)
    return solicitacao


@pytest.mark.asyncio
async def test_aprovacao_em_lote_exclui_todas_as_pesquisas_selecionadas() -> None:
    item_a, item_b = pesquisa(), pesquisa()
    item_b.id = "99999999-1234-1234-1234-123456789abc"
    sol_a, sol_b = solicitacao_pendente(1, item_a), solicitacao_pendente(2, item_b)
    session = SessaoExclusao(
        [FakeResult(itens=[sol_a, sol_b]), FakeResult(scalar=item_a), FakeResult(scalar=item_b)],
        usuario_banco(),
    )

    resposta = await decidir_solicitacoes_em_lote(
        DecidirLoteInput(ids=[1, 2], decisao="aprovar", senha="Senha-segura-123"),
        requisicao(),
        session,
        usuario_teste(),
        None,
    )

    assert resposta == {"decisao": "aprovar", "processadas": 2, "ja_removidas": 0, "ignoradas": 0}
    assert session.excluidos == [item_a, item_b]
    assert sol_a.status == sol_b.status == "executada"


@pytest.mark.asyncio
async def test_aprovacao_em_lote_com_senha_errada_nao_exclui_nada() -> None:
    item = pesquisa()
    sol = solicitacao_pendente(1, item)
    session = SessaoExclusao([FakeResult(itens=[sol])], usuario_banco())

    with pytest.raises(HTTPException) as erro:
        await decidir_solicitacoes_em_lote(
            DecidirLoteInput(ids=[1], decisao="aprovar", senha="errada"),
            requisicao(),
            session,
            usuario_teste(),
            None,
        )

    assert erro.value.status_code == 403
    assert session.excluidos == []
    assert sol.status == "pendente"


@pytest.mark.asyncio
async def test_rejeicao_em_lote_nao_exige_senha_e_preserva_pesquisas() -> None:
    item = pesquisa()
    sol = solicitacao_pendente(1, item)
    session = SessaoExclusao([FakeResult(itens=[sol])], usuario_banco())

    resposta = await decidir_solicitacoes_em_lote(
        DecidirLoteInput(ids=[1, 5], decisao="rejeitar", observacao="Manter"),
        requisicao(),
        session,
        usuario_teste(),
        None,
    )

    assert resposta == {"decisao": "rejeitar", "processadas": 1, "ja_removidas": 0, "ignoradas": 1}
    assert session.excluidos == []
    assert sol.status == "rejeitada"


def test_exclusao_da_pesquisa_nao_exclui_o_lead() -> None:
    fk = next(iter(PesquisaMarca.__table__.c.lead_id.foreign_keys))

    assert fk.ondelete == "SET NULL"


def test_telas_expoem_exclusao_protegida_e_fila_administrativa() -> None:
    consulta = Path("app/web/admin-consulta.html").read_text(encoding="utf-8")
    leads = Path("app/web/admin-leads.html").read_text(encoding="utf-8")
    script = Path("app/web/static/admin-leads.js").read_text(encoding="utf-8")
    dialog = Path("app/web/static/lead-dialog.js").read_text(encoding="utf-8")

    assert 'id="delete-research"' in consulta
    assert 'autocomplete="current-password"' in consulta
    assert 'id="deletion-requests"' in leads
    assert "deletion-requests-section" in leads
    assert "deletion-requests-empty" in script
    assert "Aprovar e excluir" in script
    assert "solicitar-exclusao" in dialog
    assert 'id="deletion-select-all"' in leads
    assert "decidir-lote" in dialog


@pytest.mark.asyncio
async def test_aprovacao_em_lote_encerra_solicitacao_cuja_pesquisa_ja_foi_removida() -> None:
    item = pesquisa()
    orfa = solicitacao_pendente(1, item)
    orfa.pesquisa_id = None
    session = SessaoExclusao([FakeResult(itens=[orfa])], usuario_banco())

    resposta = await decidir_solicitacoes_em_lote(
        DecidirLoteInput(ids=[1], decisao="aprovar", senha="Senha-segura-123"),
        requisicao(),
        session,
        usuario_teste(),
        None,
    )

    assert resposta == {"decisao": "aprovar", "processadas": 1, "ja_removidas": 1, "ignoradas": 0}
    assert orfa.status == "executada"
    assert session.excluidos == []


@pytest.mark.asyncio
async def test_listagem_informa_total_real_alem_das_itens_exibidos() -> None:
    item = pesquisa()
    session = SessaoExclusao([FakeResult(scalar=250), FakeResult(itens=[solicitacao_pendente(1, item)])])

    resposta = await listar_solicitacoes(session, usuario_teste(), "pendente", 100, 0)

    assert resposta["total"] == 250
    assert resposta["exibidas"] == 1


@pytest.mark.asyncio
async def test_listagem_aceita_busca_e_historico_sem_filtro_de_status() -> None:
    item = pesquisa()
    decidida = solicitacao_pendente(3, item)
    decidida.status = "executada"
    session = SessaoExclusao([FakeResult(scalar=1), FakeResult(itens=[decidida])])

    resposta = await listar_solicitacoes(session, usuario_teste(), "", 100, 0, "marca_teste%")

    assert resposta["total"] == 1
    assert resposta["itens"][0]["status"] == "executada"


def test_tela_expoe_filtros_historico_e_previa_do_lote() -> None:
    leads = Path("app/web/admin-leads.html").read_text(encoding="utf-8")
    script = Path("app/web/static/admin-leads.js").read_text(encoding="utf-8")
    dialog = Path("app/web/static/lead-dialog.js").read_text(encoding="utf-8")

    assert 'id="deletion-filter-status"' in leads
    assert 'id="deletion-filter-busca"' in leads
    assert 'id="deletion-load-more"' in leads
    assert 'id="research-delete-preview-list"' in leads
    assert "decidido_por" in script
    assert "research-delete-preview-list" in dialog
