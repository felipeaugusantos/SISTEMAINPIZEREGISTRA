import pytest
from sqlalchemy import create_engine, select, text

from app.api.leads import _filtros_lead
from app.models import Lead, StatusLead
from tests.conftest import usuario_teste


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE leads (
            id INTEGER PRIMARY KEY, organizacao_id INTEGER, arquivado_em TEXT,
            nome TEXT, email TEXT, telefone TEXT, documento TEXT, empresa TEXT,
            marca TEXT, atividade TEXT, processo_numero TEXT, status TEXT,
            origem TEXT, responsavel_id INTEGER, criado_em TEXT, aceite_marketing BOOLEAN
        )"""))
        conn.execute(text("""CREATE TABLE pesquisas_marca (
            id TEXT PRIMARY KEY, lead_id INTEGER, organizacao_id INTEGER, marca TEXT, atividade TEXT
        )"""))
        conn.execute(text("""CREATE TABLE avaliacoes_risco_marca (
            id INTEGER PRIMARY KEY, pesquisa_id TEXT, nivel TEXT
        )"""))
        for id_, org, archived in [(1, 1, None), (2, 1, None), (3, 2, None), (4, 1, "2026-01-01")]:
            conn.execute(text("""INSERT INTO leads (id, organizacao_id, arquivado_em, nome, status)
                VALUES (:id, :org, :archived, :nome, 'novo')"""),
                {"id": id_, "org": org, "archived": archived, "nome": f"Contato {id_}"})
            conn.execute(text("""INSERT INTO pesquisas_marca VALUES (:id, :lead, :org, 'Marca', :activity)"""),
                {"id": str(id_), "lead": id_, "org": org, "activity": "Panificação" if id_ != 2 else "Comércio"})
            conn.execute(text("INSERT INTO avaliacoes_risco_marca VALUES (:id, :research, :risk)"),
                {"id": id_, "research": str(id_), "risk": "alto" if id_ != 2 else "baixo"})
    yield engine
    engine.dispose()


def ids(engine, **overrides):
    args = {"usuario": usuario_teste(), "busca": None, "status_lead": None, "origem": None,
            "responsavel_id": None, "data_inicio": None, "data_fim": None, "marketing": None,
            "arquivados": False}
    args.update(overrides)
    with engine.connect() as conn:
        return list(conn.execute(select(Lead.id).where(*_filtros_lead(**args))).scalars())


def test_busca_atividade_historica_e_risco_combinados_respeitam_tenant(database):
    assert ids(database, busca="Panificação", risco="alto", status_lead=StatusLead.NOVO) == [1]
    assert ids(database, risco="baixo") == [2]
    assert ids(database, busca="Comércio", risco="alto") == []


def test_risco_e_busca_respeitam_arquivamento(database):
    assert ids(database, busca="Panificação", risco="alto", arquivados=True) == [4]


def test_busca_nao_trata_curingas_como_todos_os_registros(database):
    assert ids(database, busca="%") == []
    assert ids(database, busca="_") == []
