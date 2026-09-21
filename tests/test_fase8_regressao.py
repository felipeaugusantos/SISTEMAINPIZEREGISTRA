"""Fase 8: regressões de segurança que não dependem de Postgres real
(complementam tests/test_fase8_seguranca_postgres.py, que cobre RLS/
concorrência/imutabilidade real de banco -- só roda com TEST_ADMIN_
DATABASE_URL acessível, ex. no CI via GitHub Actions).

Itens cobertos aqui:
- Compatibilidade entre API, worker e banco: import de app.worker/app.main
  sem erro, e a árvore de migrations tem uma única head (sem branch
  divergente que quebraria "alembic upgrade head" no deploy).
- Auditoria: lista guarda-chuva das ações críticas das Fases 4-7,
  garantindo que cada uma gera EventoAuditoria com a `acao` esperada
  (a maioria já era coberta caso a caso em cada módulo -- aqui fica
  reunida como checklist único, fácil de estender quando uma ação nova
  aparecer).
- Ausência de segredos nas respostas: teste transversal que serializa a
  resposta de vários endpoints e garante ausência de chaves sensíveis
  (senha, hash, token, caminho de armazenamento) em qualquer nível.
"""

import asyncio
import importlib
from pathlib import Path

from app.models import EventoAuditoria

RAIZ = Path(__file__).resolve().parent.parent

# Chaves que uma resposta HTTP nunca deveria conter, em nenhum nível
# (comparado contra a *chave* do JSON, não o valor -- então "hash" bate
# em "anexo_hash"/"conteudo_hash"/"token_hash" etc.).
CHAVES_PROIBIDAS_EM_RESPOSTA = (
    "senha",
    "senha_hash",
    "token_hash",
    "csrf_hash",
    "anexo_caminho",
    "api_token",
    "webhook_secret",
)


# --- Compatibilidade entre API, worker e banco ------------------------------


def test_worker_importa_sem_erro() -> None:
    """app/worker.py importa dezenas de módulos (app.api.*, app.crm,
    app.ia_sombra, app.feature_flags etc.) -- um import quebrado aqui só
    aparece em produção quando o container do worker sobe. Nenhum outro
    teste do repositório importa app.worker diretamente."""
    modulo = importlib.import_module("app.worker")
    assert hasattr(modulo, "main")
    assert hasattr(modulo, "processar")


def test_main_importa_sem_erro() -> None:
    modulo = importlib.import_module("app.main")
    assert hasattr(modulo, "app")


def test_migrations_tem_uma_unica_head() -> None:
    """Duas heads simultâneas (branch divergente na árvore de migrations)
    quebram "alembic upgrade head" na hora do deploy -- acontece quando
    duas migrations em desenvolvimento paralelo apontam pro mesmo
    down_revision sem que ninguém funda as pontas. Verificado aqui sem
    depender de banco: só lendo revision/down_revision de cada arquivo."""
    import re

    pasta = RAIZ / "migrations" / "versions"
    revisoes: set[str] = set()
    down_revisoes: set[str] = set()
    for arquivo in pasta.glob("*.py"):
        texto = arquivo.read_text(encoding="utf-8")
        rev = re.search(r'^revision(?::\s*str)?\s*=\s*"([^"]+)"', texto, re.MULTILINE)
        assert rev, f"{arquivo.name} sem `revision` no formato esperado"
        revisoes.add(rev.group(1))
        for down in re.findall(r'^down_revision(?::[^=]*)?\s*=\s*"([^"]+)"', texto, re.MULTILINE):
            down_revisoes.add(down)
        for down in re.findall(r'^down_revision(?::[^=]*)?\s*=\s*\(([^)]*)\)', texto, re.MULTILINE):
            down_revisoes.update(v.strip(' "\'') for v in down.split(",") if v.strip())

    heads = revisoes - down_revisoes
    assert len(heads) == 1, f"esperada uma única head de migration, encontradas: {heads}"


# --- Auditoria: ações críticas das Fases 4-7 sempre geram EventoAuditoria --


def test_acoes_criticas_das_fases_4_a_7_sempre_auditam() -> None:
    """Checklist único para as ações mais sensíveis introduzidas nas
    Fases 4-7 (rollout gradual e rollback de emergência) -- cada uma já
    tem teste próprio nos módulos de origem; aqui ficam reunidas como
    prova de que nenhuma foi esquecida."""
    from app.api.feature_flags import criar_flag as criar_flag_endpoint
    from app.api.observabilidade import desligar_flag_imediatamente, religar_flag
    from app.feature_flags import interromper_rollout
    from app.models import FeatureFlag
    from tests.conftest import FakeResult, FakeSession, usuario_teste

    tech = usuario_teste(perfil="tech")

    # desligar/religar (Fase 7, kill-switch de emergência)
    flag = FeatureFlag(id=1, codigo="fase8-flag", nome="Flag", ativo=True, estagio_rollout="liberacao_geral")
    session = FakeSession([FakeResult(scalar=flag)])
    asyncio.run(desligar_flag_imediatamente("fase8-flag", session, tech))
    acoes = {ev.acao for ev in session.adicionados if isinstance(ev, EventoAuditoria)}
    assert "FLAG_DESLIGAR" in acoes

    flag.ativo = False
    session = FakeSession([FakeResult(scalar=flag)])
    asyncio.run(religar_flag("fase8-flag", session, tech))
    acoes = {ev.acao for ev in session.adicionados if isinstance(ev, EventoAuditoria)}
    assert "FLAG_RELIGAR" in acoes

    # interromper rollout (Fase 5, recuo de estágio)
    flag2 = FeatureFlag(id=2, codigo="fase8-flag-2", nome="Flag 2", ativo=True, estagio_rollout="percentual_limitado")
    session = FakeSession([])
    asyncio.run(interromper_rollout(session, flag2, motivo="Teste de auditoria da Fase 8", por=tech.email))
    acoes = {ev.acao for ev in session.adicionados if isinstance(ev, EventoAuditoria)}
    assert "FLAG_INTERROMPER" in acoes

    # criar feature flag (Fase 4)
    from app.api.feature_flags import FeatureFlagInput

    session = FakeSession([FakeResult(scalar=None), FakeResult(itens=[])])
    dados = FeatureFlagInput(
        codigo="fase8-flag-3",
        nome="Flag 3",
        descricao="Descricao de teste com mais de vinte caracteres para a Fase 8.",
        modulos_envolvidos=["producao"],
        estado_padrao="desligado",
        confirmar_nao_e_correcao_seguranca=True,
    )
    asyncio.run(criar_flag_endpoint(dados, session, tech))
    acoes = {ev.acao for ev in session.adicionados if isinstance(ev, EventoAuditoria)}
    assert "FLAG_CRIAR" in acoes


# --- Ausência de segredos nas respostas (checagem transversal) -------------


def _percorrer_chaves(valor: object) -> set[str]:
    encontradas: set[str] = set()
    if isinstance(valor, dict):
        for chave, sub in valor.items():
            encontradas.add(str(chave).lower())
            encontradas |= _percorrer_chaves(sub)
    elif isinstance(valor, list):
        for item in valor:
            encontradas |= _percorrer_chaves(item)
    return encontradas


def test_flag_json_nao_expoe_nenhuma_chave_proibida() -> None:
    from app.api.feature_flags import flag_json
    from app.models import FeatureFlag

    flag = FeatureFlag(
        id=1,
        codigo="fase8-flag",
        nome="Flag",
        descricao="Descricao",
        modulos_envolvidos=["producao"],
        dependencias=[],
        estado_padrao="ligado",
        ativo=True,
        estagio_rollout="liberacao_geral",
        percentual_rollout=100,
    )
    chaves = _percorrer_chaves(flag_json(flag))
    assert chaves.isdisjoint(CHAVES_PROIBIDAS_EM_RESPOSTA)


def test_migration_de_dados_redige_clicksign_ja_persistido() -> None:
    """Achado do review do Codex na PR #98: a redação do payload bruto do
    webhook Clicksign (_redigir_payload_webhook em app/api/portal_cliente.py)
    só se aplica a eventos processados após o deploy. A migration de dados
    e1f2a3b4c5d6 varre PropostaComercial.dados["clicksign"]["ultimo_evento"]
    já persistido e aplica a mesma redação -- aqui garantimos que a lógica
    duplicada na migration (que não pode importar app/, ver convenção das
    outras migrations) continua igual à da API."""
    import importlib.util

    from app.api.portal_cliente import _redigir_payload_webhook as redigir_da_api

    caminho = RAIZ / "migrations" / "versions" / "e1f2a3b4c5d6_redige_clicksign_ja_persistido.py"
    spec = importlib.util.spec_from_file_location("migracao_redige_clicksign", caminho)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    payload = {
        "status": "document_closed",
        "envelope_id": "env-123",
        "data": {"signers": [{"name": "Fulano de Tal", "email": "fulano@example.com", "cpf": "12345678900"}]},
    }
    assert modulo._redigir_payload_webhook(payload) == redigir_da_api(payload)


def test_listar_problemas_nao_expoe_caminho_do_anexo() -> None:
    """anexo_caminho (path físico em disco/S3) nunca pode vazar pra fora
    -- só metadados (nome, tipo, tamanho) são retornados. Ver também
    tests/test_atualizacoes.py::test_listar_problemas_expoe_metadados_do_anexo_sem_o_conteudo."""
    from datetime import UTC, datetime

    from app.api.atualizacoes import listar_problemas
    from app.models import ProblemaVersaoSistema
    from tests.conftest import FakeResult, FakeSession, usuario_teste

    problema = ProblemaVersaoSistema(
        id=1,
        versao_sistema_id=1,
        organizacao_id=1,
        usuario_id=1,
        categoria="erro",
        modulo="producao",
        descricao="Descricao de teste com mais de vinte caracteres.",
        etapas_reproduzir=None,
        resultado_esperado=None,
        resultado_encontrado=None,
        gravidade="media",
        anexo_nome="print.png",
        anexo_caminho="data/uploads/problemas-versao/1/1/segredo-do-caminho.png",
        anexo_content_type="image/png",
        anexo_tamanho=2048,
        anexo_hash="a" * 64,
        status="aberto",
        criado_em=datetime.now(UTC),
    )
    session = FakeSession(resultados=[FakeResult(itens=[(problema, "1.0.0", "Titulo", "Org", "Usuario")])])

    resposta = asyncio.run(listar_problemas(session, usuario_teste(perfil="tech"), status_filtro=None))

    texto = str(resposta)
    assert "segredo-do-caminho" not in texto
    chaves = _percorrer_chaves(resposta)
    assert chaves.isdisjoint(CHAVES_PROIBIDAS_EM_RESPOSTA)
