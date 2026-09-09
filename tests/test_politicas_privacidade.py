from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.politicas_privacidade import (
    PoliticaRascunhoInput,
    PublicarPoliticaInput,
    calcular_hash_politica,
    criar_rascunho,
    editar_rascunho,
    listar_politicas,
    politica_publica_vigente,
    publicar_politica,
)
from app.auth import UsuarioAutenticado
from app.models import EventoAuditoria, Lead, Organizacao, PoliticaPrivacidade
from tests.conftest import FakeResult, FakeSession


def _usuario(organizacao_id: int = 7) -> UsuarioAutenticado:
    return UsuarioAutenticado(
        id=3,
        nome="Admin",
        usuario="admin",
        email="admin@teste.local",
        perfil="administrador",
        permissoes=frozenset(),
        alterar_senha=False,
        sessao_id=1,
        csrf_hash="",
        organizacao_id=organizacao_id,
    )


def _politica(
    politica_id: int,
    versao: str,
    status: str,
    *,
    novo_consentimento: bool = False,
) -> PoliticaPrivacidade:
    agora = datetime.now(UTC)
    return PoliticaPrivacidade(
        id=politica_id,
        organizacao_id=7,
        versao=versao,
        status=status,
        conteudo="Texto público da política de privacidade. " * 4,
        documento_referencia=None,
        sha256=calcular_hash_politica("Texto público da política de privacidade. " * 4, None),
        criado_em=agora,
        publicado_em=agora if status != "rascunho" else None,
        vigencia_em=agora if status != "rascunho" else None,
        criado_por_id=3,
        criado_por="admin@teste.local",
        aprovado_por_id=3 if status != "rascunho" else None,
        aprovado_por="admin@teste.local" if status != "rascunho" else None,
        motivo_alteracao="Atualização necessária do aviso de privacidade.",
        requer_novo_consentimento=novo_consentimento,
    )


def _entrada(versao: str = "2.0", *, novo_consentimento: bool = False) -> PoliticaRascunhoInput:
    return PoliticaRascunhoInput(
        versao=versao,
        conteudo="Conteúdo revisado e completo da política de privacidade. " * 3,
        motivo_alteracao="Adequação documentada do tratamento de dados pessoais.",
        requer_novo_consentimento=novo_consentimento,
    )


def test_documento_exige_conteudo_ou_referencia_exclusivos() -> None:
    with pytest.raises(ValidationError):
        PoliticaRascunhoInput(
            versao="2.0",
            motivo_alteracao="Motivo suficientemente detalhado para a alteração.",
        )
    with pytest.raises(ValidationError):
        PoliticaRascunhoInput(
            versao="2.0",
            conteudo="Conteúdo válido da política de privacidade. " * 3,
            documento_referencia="/privacidade.pdf",
            motivo_alteracao="Motivo suficientemente detalhado para a alteração.",
        )


@pytest.mark.asyncio
async def test_criar_rascunho_calcula_hash_e_isola_organizacao() -> None:
    session = FakeSession(resultados=[FakeResult(scalar=None)])

    resposta = await criar_rascunho(_entrada(), session, _usuario())

    item = next(obj for obj in session.adicionados if isinstance(obj, PoliticaPrivacidade))
    assert item.organizacao_id == 7
    assert item.status == "rascunho"
    assert item.sha256 == calcular_hash_politica(item.conteudo, None)
    assert resposta["id"] == item.id
    assert session.commits == 1


@pytest.mark.asyncio
async def test_politica_publicada_nao_pode_ser_editada() -> None:
    publicada = _politica(10, "1.0", "publicada")
    session = FakeSession(resultados=[FakeResult(scalar=publicada)])

    with pytest.raises(HTTPException) as erro:
        await editar_rascunho(10, _entrada(), session, _usuario())

    assert erro.value.status_code == 409
    assert publicada.versao == "1.0"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_editar_rascunho_recalcula_hash() -> None:
    rascunho = _politica(10, "2.0", "rascunho")
    hash_anterior = rascunho.sha256
    session = FakeSession(resultados=[FakeResult(scalar=rascunho)])

    resposta = await editar_rascunho(10, _entrada("2.1"), session, _usuario())

    assert resposta["versao"] == "2.1"
    assert rascunho.sha256 != hash_anterior
    assert rascunho.sha256 == calcular_hash_politica(rascunho.conteudo, None)
    assert session.commits == 1


@pytest.mark.asyncio
async def test_publicacao_exige_confirmacao_explicita() -> None:
    session = FakeSession()

    with pytest.raises(HTTPException) as erro:
        await publicar_politica(
            10,
            PublicarPoliticaInput(confirmar_publicacao=False),
            session,
            _usuario(),
        )

    assert erro.value.status_code == 422
    assert session.commits == 0


@pytest.mark.asyncio
async def test_publicacao_nao_aceita_vigencia_futura() -> None:
    rascunho = _politica(10, "2.0", "rascunho")
    session = FakeSession(resultados=[FakeResult(scalar=rascunho)])

    with pytest.raises(HTTPException) as erro:
        await publicar_politica(
            10,
            PublicarPoliticaInput(
                confirmar_publicacao=True,
                vigencia_em=datetime.now(UTC) + timedelta(days=1),
            ),
            session,
            _usuario(),
        )

    assert erro.value.status_code == 422
    assert rascunho.status == "rascunho"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_publicacao_revoga_anterior_audita_e_preserva_consentimento() -> None:
    anterior = _politica(9, "1.0", "publicada")
    rascunho = _politica(10, "2.0", "rascunho", novo_consentimento=True)
    org = Organizacao(id=7, nome="Tenant", slug="tenant", politica_privacidade_versao="1.0")
    consentimento_antigo = Lead(
        id=55,
        organizacao_id=7,
        nome="Titular",
        email="titular@example.test",
        telefone="16999999999",
        marca="Marca",
        origem="site",
        aceite_privacidade=True,
        consentimento_versao_termo="1.0",
    )
    session = FakeSession(
        resultados=[FakeResult(scalar=rascunho), FakeResult(scalar=anterior)],
        objetos_get=[org],
    )

    resposta = await publicar_politica(
        10,
        PublicarPoliticaInput(confirmar_publicacao=True),
        session,
        _usuario(),
    )

    assert resposta["status"] == "publicada"
    assert anterior.status == "revogada"
    assert rascunho.status == "publicada"
    assert rascunho.aprovado_por_id == 3
    assert org.politica_privacidade_versao == "2.0"
    assert consentimento_antigo.consentimento_versao_termo == "1.0"
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "PUBLICAR_POLITICA"
    assert evento.organizacao_id == 7
    assert evento.detalhes["versao_revogada"] == "1.0"


@pytest.mark.asyncio
async def test_publicacao_exige_politica_existente_no_tenant() -> None:
    session = FakeSession(resultados=[FakeResult(scalar=None)])

    with pytest.raises(HTTPException) as erro:
        await publicar_politica(
            99,
            PublicarPoliticaInput(confirmar_publicacao=True),
            session,
            _usuario(),
        )

    assert erro.value.status_code == 404
    assert session.commits == 0


@pytest.mark.asyncio
async def test_listagem_informa_aceites_pendentes_sem_altera_los() -> None:
    vigente = _politica(10, "2.0", "publicada", novo_consentimento=True)
    historica = _politica(9, "1.0", "revogada")
    session = FakeSession(
        resultados=[FakeResult(itens=[vigente, historica]), FakeResult(scalar=4)]
    )

    resposta = await listar_politicas(session, _usuario())

    assert resposta["vigente"]["versao"] == "2.0"
    assert resposta["consentimentos_pendentes"] == 4
    assert "permanecem vinculados ao aceite original" in resposta["regra_novo_consentimento"]


@pytest.mark.asyncio
async def test_endpoint_publico_expoe_somente_documento_vigente() -> None:
    vigente = _politica(10, "2.0", "publicada", novo_consentimento=True)
    session = FakeSession(resultados=[FakeResult(scalar=vigente)])

    resposta = await politica_publica_vigente(SimpleNamespace(id=7), session)

    assert set(resposta) == {
        "versao",
        "conteudo",
        "documento_referencia",
        "sha256",
        "publicado_em",
        "vigencia_em",
        "requer_novo_consentimento",
    }
    assert resposta["versao"] == "2.0"
    assert "criado_por" not in resposta
    assert "motivo_alteracao" not in resposta


def test_migration_preserva_consentimentos_e_possui_rollback() -> None:
    caminho = Path("migrations/versions/i20s5u1p842_politica_privacidade_versionada.py")
    texto = caminho.read_text(encoding="utf-8")

    assert "UPDATE leads" not in texto.upper()
    assert 'versao = linha["versao"]' in texto
    assert 'linha["versao"].strip()' not in texto
    assert "fk_leads_consentimento_politica" in texto
    assert "trg_proteger_politica_privacidade" in texto
    assert 'op.drop_constraint("fk_leads_consentimento_politica"' in texto
    assert 'op.drop_table("politicas_privacidade")' in texto
