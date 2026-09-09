from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.versoes_sistema import (
    ArquivarVersaoInput,
    EvidenciaTesteInput,
    PublicarVersaoInput,
    VersaoRascunhoInput,
    arquivar_versao,
    calcular_hash_versao,
    criar_versao,
    editar_versao,
    lembrar_atualizacoes_pendentes,
    listar_confirmacoes_versao,
    listar_versoes,
    publicar_versao,
    relatorio_pendencias,
)
from app.auth import UsuarioAutenticado
from app.models import AlertaSistema, EventoAuditoria, InteracaoVersaoSistema, VersaoSistema
from tests.conftest import FakeResult, FakeSession


def _superadmin() -> UsuarioAutenticado:
    return UsuarioAutenticado(
        id=1,
        nome="Superadmin",
        usuario="root",
        email="admin@teste.local",
        perfil="tech",
        permissoes=frozenset({"production.view", "production.manage"}),
        alterar_senha=False,
        sessao_id=1,
        csrf_hash="",
        superadmin=True,
    )


def _dados(*, versao: str = "1.0.71-2026-09-09", implantada: bool = True) -> VersaoRascunhoInput:
    return VersaoRascunhoInput(
        versao=versao,
        titulo="Cadastro técnico de versões",
        problema_identificado="As alterações implantadas não possuíam um histórico técnico centralizado.",
        solucao_aplicada="Foi criado um registro versionado, auditável e protegido contra alterações posteriores.",
        impacto_usuario="A equipe passa a consultar as mudanças da plataforma em um único local.",
        documentacao_url="https://app.zeregistra.com.br/sobre",
        permite_adiar=True,
        tipo_atualizacao="funcionalidade",
        modulos_afetados=["producao", "plataforma", "producao"],
        implantada_em=datetime.now(UTC) if implantada else None,
        commit_sha="a" * 40,
        migration_revision="j21t6v2q953",
        evidencias_testes=[
            EvidenciaTesteInput(
                nome="Suíte de regressão",
                resultado="aprovado",
                resumo="Testes automatizados do cadastro técnico aprovados.",
                quantidade=12,
                executado_em=datetime.now(UTC),
            )
        ],
        riscos_conhecidos=["A interface administrativa será entregue somente na próxima fase."],
        instrucoes="Consultar o endpoint administrativo com a permissão production.view.",
        plano_rollback="Executar o downgrade da migration antes de retornar para a imagem anterior.",
    )


def _versao(status: str = "rascunho", *, implantada: bool = True) -> VersaoSistema:
    dados = _dados(implantada=implantada)
    agora = datetime.now(UTC)
    return VersaoSistema(
        id=10,
        versao=dados.versao,
        titulo=dados.titulo,
        problema_identificado=dados.problema_identificado,
        solucao_aplicada=dados.solucao_aplicada,
        impacto_usuario=dados.impacto_usuario,
        documentacao_url=str(dados.documentacao_url),
        permite_adiar=dados.permite_adiar,
        tipo_atualizacao=dados.tipo_atualizacao,
        modulos_afetados=dados.modulos_afetados,
        implantada_em=dados.implantada_em,
        commit_sha=dados.commit_sha,
        migration_revision=dados.migration_revision,
        evidencias_testes=[item.model_dump(mode="json") for item in dados.evidencias_testes],
        riscos_conhecidos=dados.riscos_conhecidos,
        instrucoes=dados.instrucoes,
        plano_rollback=dados.plano_rollback,
        conteudo_hash=calcular_hash_versao(dados),
        status=status,
        criado_por_id=1,
        criado_por="admin@teste.local",
        criado_em=agora,
        atualizado_em=agora,
        publicado_por_id=1 if status != "rascunho" else None,
        publicado_por="admin@teste.local" if status != "rascunho" else None,
        publicado_em=agora if status != "rascunho" else None,
        arquivado_por_id=1 if status == "arquivada" else None,
        arquivado_por="admin@teste.local" if status == "arquivada" else None,
        arquivado_em=agora if status == "arquivada" else None,
        arquivamento_motivo=(
            "Versão retirada da consulta principal após o encerramento do ciclo." if status == "arquivada" else None
        ),
    )


def test_entrada_rejeita_status_e_segredos() -> None:
    payload = _dados().model_dump()
    payload["status"] = "publicada"
    with pytest.raises(ValidationError):
        VersaoRascunhoInput.model_validate(payload)

    payload = _dados().model_dump()
    payload["instrucoes"] = "Configure TOKEN=valor-que-nao-pode-ser-armazenado neste ambiente."
    with pytest.raises(ValidationError) as erro:
        VersaoRascunhoInput.model_validate(payload)
    assert "Não inclua credenciais" in str(erro.value)


def test_entrada_normaliza_modulos_e_exige_commit_completo() -> None:
    dados = _dados()
    assert dados.modulos_afetados == ["producao", "plataforma"]

    payload = dados.model_dump()
    payload["commit_sha"] = "abc1234"
    with pytest.raises(ValidationError):
        VersaoRascunhoInput.model_validate(payload)

    payload = dados.model_dump()
    payload["documentacao_url"] = "http://documentacao.insegura.local/versao"
    with pytest.raises(ValidationError):
        VersaoRascunhoInput.model_validate(payload)

    payload = dados.model_dump()
    payload["evidencias_testes"] = []
    with pytest.raises(ValidationError):
        VersaoRascunhoInput.model_validate(payload)


@pytest.mark.asyncio
async def test_criar_rascunho_calcula_hash_e_audita_sem_textos_extensos() -> None:
    session = FakeSession(resultados=[FakeResult(scalar=None)])
    dados = _dados()

    resposta = await criar_versao(dados, session, _superadmin())

    item = next(obj for obj in session.adicionados if isinstance(obj, VersaoSistema))
    assert item.status == "rascunho"
    assert item.conteudo_hash == calcular_hash_versao(dados)
    assert resposta["conteudo_hash"] == item.conteudo_hash
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "CRIAR_RELEASE"
    assert evento.organizacao_id is None
    assert set(evento.detalhes) == {"versao", "tipo", "hash"}
    assert session.commits == 1


@pytest.mark.asyncio
async def test_versao_publicada_nao_pode_ser_editada() -> None:
    publicada = _versao("publicada")
    session = FakeSession(objetos_get=[publicada])

    with pytest.raises(HTTPException) as erro:
        await editar_versao(10, _dados(versao="1.0.72-2026-09-09"), session, _superadmin())

    assert erro.value.status_code == 409
    assert publicada.versao == "1.0.71-2026-09-09"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_edicao_de_rascunho_recalcula_hash_e_audita() -> None:
    rascunho = _versao()
    session = FakeSession(resultados=[FakeResult(scalar=None)], objetos_get=[rascunho])
    dados = _dados(versao="1.0.72-2026-09-09")

    resposta = await editar_versao(10, dados, session, _superadmin())

    assert resposta["versao"] == "1.0.72-2026-09-09"
    assert resposta["conteudo_hash"] == calcular_hash_versao(dados)
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "EDITAR_RELEASE"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_publicacao_exige_confirmacao_e_data_de_implantacao() -> None:
    session = FakeSession()
    with pytest.raises(HTTPException) as erro:
        await publicar_versao(
            10,
            PublicarVersaoInput(confirmar_publicacao=False),
            session,
            _superadmin(),
        )
    assert erro.value.status_code == 422

    sem_data = _versao(implantada=False)
    session = FakeSession(objetos_get=[sem_data])
    with pytest.raises(HTTPException) as erro:
        await publicar_versao(
            10,
            PublicarVersaoInput(confirmar_publicacao=True),
            session,
            _superadmin(),
        )
    assert erro.value.status_code == 422
    assert sem_data.status == "rascunho"

    sem_aprovacao = _versao()
    sem_aprovacao.evidencias_testes[0]["resultado"] = "falhou"
    session = FakeSession(objetos_get=[sem_aprovacao])
    with pytest.raises(HTTPException) as erro:
        await publicar_versao(
            10,
            PublicarVersaoInput(confirmar_publicacao=True),
            session,
            _superadmin(),
        )
    assert erro.value.status_code == 422
    assert sem_aprovacao.status == "rascunho"


@pytest.mark.asyncio
async def test_publicacao_torna_versao_imutavel_e_e_auditada() -> None:
    rascunho = _versao()
    session = FakeSession(objetos_get=[rascunho])

    resposta = await publicar_versao(
        10,
        PublicarVersaoInput(confirmar_publicacao=True),
        session,
        _superadmin(),
    )

    assert resposta["status"] == "publicada"
    assert rascunho.publicado_por_id == 1
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "PUBLICAR_RELEASE"
    assert evento.detalhes["commit"] == "a" * 40
    assert session.commits == 1


@pytest.mark.asyncio
async def test_arquivamento_preserva_conteudo_e_e_auditado() -> None:
    publicada = _versao("publicada")
    hash_original = publicada.conteudo_hash
    session = FakeSession(objetos_get=[publicada])

    resposta = await arquivar_versao(
        10,
        ArquivarVersaoInput(
            confirmar_arquivamento=True,
            motivo="Versão antiga removida da consulta principal após revisão técnica.",
        ),
        session,
        _superadmin(),
    )

    assert resposta["status"] == "arquivada"
    assert publicada.conteudo_hash == hash_original
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "ARQUIVAR_RELEASE"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_listagem_e_paginada_e_preserva_historico() -> None:
    publicada = _versao("publicada")
    arquivada = _versao("arquivada")
    arquivada.id = 9
    arquivada.versao = "1.0.70-2026-09-09"
    session = FakeSession(
        resultados=[FakeResult(scalar=2), FakeResult(itens=[publicada, arquivada])]
    )

    resposta = await listar_versoes(session, _superadmin(), limite=20, deslocamento=0)

    assert resposta["total"] == 2
    assert [item["status"] for item in resposta["itens"]] == ["publicada", "arquivada"]
    assert resposta["limite"] == 20


def test_migration_protege_historico_e_possui_rollback() -> None:
    caminho = Path("migrations/versions/j21t6v2q953_versoes_sistema.py")
    texto = caminho.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "i20s5u1p842"' in texto
    assert "trg_proteger_versao_sistema" in texto
    assert "versoes_sistema_read" in texto
    assert "versoes_sistema_insert" in texto
    assert "current_setting('app.superadmin'" in texto
    assert "DROP TRIGGER IF EXISTS trg_proteger_versao_sistema" in texto
    assert 'op.drop_table("versoes_sistema")' in texto


def test_ci_usa_postgres_com_pgvector() -> None:
    texto = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "image: pgvector/pgvector:pg16" in texto
    assert "ancestor=postgres:16-alpine" not in texto


# --- Fase 3: continuação da Fase 2 (central de atualizações) -- contagem
# de usuários pendentes de confirmação e lembrete periódico. Reaproveita
# VersaoSistema/InteracaoVersaoSistema já existentes, sem tabela nova. ---


def _versao_publicada(*, tipo_atualizacao: str = "funcionalidade", versao_id: int = 10) -> VersaoSistema:
    item = _versao("publicada")
    item.id = versao_id
    item.tipo_atualizacao = tipo_atualizacao
    return item


@pytest.mark.asyncio
async def test_relatorio_pendencias_calcula_por_versao_publicada() -> None:
    versao = _versao_publicada(tipo_atualizacao="critica")
    session = FakeSession(
        resultados=[
            FakeResult(itens=[versao]),  # versoes publicadas
            FakeResult(scalar=8),  # total_usuarios
            FakeResult(scalar=3),  # confirmados
        ]
    )

    resposta = await relatorio_pendencias(session, _superadmin())

    assert resposta["itens"] == [
        {
            "id": versao.id,
            "versao": versao.versao,
            "titulo": versao.titulo,
            "critico": True,
            "total_usuarios": 8,
            "pendentes": 5,
        }
    ]


@pytest.mark.asyncio
async def test_listar_confirmacoes_versao_devolve_pendentes_e_lista() -> None:
    versao = _versao_publicada()
    interacao = InteracaoVersaoSistema(
        versao_sistema_id=versao.id, organizacao_id=1, usuario_id=7, confirmado_em=datetime.now(UTC)
    )
    session = FakeSession(
        resultados=[
            FakeResult(scalar=10),  # total_usuarios
            FakeResult(scalar=1),  # confirmados
            FakeResult(itens=[interacao]),  # confirmacoes
        ],
        objetos_get=[versao],
    )

    resposta = await listar_confirmacoes_versao(versao.id, session, _superadmin())

    assert resposta["pendentes"] == 9
    assert resposta["confirmacoes"] == [{"usuario_id": 7, "confirmado_em": interacao.confirmado_em}]


@pytest.mark.asyncio
async def test_lembrar_atualizacoes_pendentes_critica_com_pendencia_gera_alerta() -> None:
    versao = _versao_publicada(tipo_atualizacao="critica")
    session = FakeSession(
        resultados=[
            FakeResult(itens=[versao]),  # versoes criticas/correcao publicadas
            FakeResult(scalar=8),  # total_usuarios
            FakeResult(scalar=3),  # confirmados
            FakeResult(scalar=None),  # registrar_alerta_plataforma: alerta existente
        ]
    )

    total = await lembrar_atualizacoes_pendentes(session)

    assert total == 1
    alertas = [item for item in session.adicionados if isinstance(item, AlertaSistema)]
    assert len(alertas) == 1
    assert alertas[0].codigo == f"ATUALIZACAO_PENDENTE_{versao.id}"
    assert alertas[0].severidade == "critico"
    assert alertas[0].detalhes["pendentes"] == 5


@pytest.mark.asyncio
async def test_lembrar_atualizacoes_pendentes_resolve_quando_todos_confirmam() -> None:
    versao = _versao_publicada(tipo_atualizacao="correcao")
    session = FakeSession(
        resultados=[
            FakeResult(itens=[versao]),  # versoes criticas/correcao publicadas
            FakeResult(scalar=5),  # total_usuarios
            FakeResult(scalar=5),  # confirmados
            FakeResult(itens=[]),  # resolver_alerta_plataforma: alertas abertos
        ]
    )

    total = await lembrar_atualizacoes_pendentes(session)

    assert total == 0
    assert session.adicionados == []
