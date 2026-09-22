import asyncio
import io

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import Workbook
from starlette.requests import Request

import app.api.carteira as modulo_carteira
from app.api.carteira import COLUNAS_EMPRESA, COLUNAS_NUMERO, COLUNAS_OBS, _numero_processo, importar_carteira
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.importacao_planilha import LINHAS_MAXIMAS_IMPORTACAO
from app.importacao_planilha import chave_coluna as _chave_coluna
from app.importacao_planilha import ler_planilha as _ler_planilha
from app.importacao_planilha import valor_coluna as _valor
from app.main import app
from app.models import Processo, TipoProcesso
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste


def test_numero_processo_tolera_cabecalhos_variados() -> None:
    def primeira(conteudo: str) -> dict:
        return _ler_planilha(conteudo.encode("utf-8"), "c.csv")[0]

    assert _numero_processo(primeira("Número do Processo;Empresa\n900123456;X\n")) == "900123456"
    assert _numero_processo(primeira("Nº;Cliente\n909111222;Y\n")) == "909111222"
    assert _numero_processo(primeira("Nº do Processo,Empresa\n900333444,Z\n")) == "900333444"
    assert _numero_processo(primeira("PROCESSO\tTITULAR\n900555666\tW\n")) == "900555666"


def test_numero_processo_ausente_retorna_none() -> None:
    registro = _ler_planilha(b"Marca;Titular\nCAFE;Fulano\n", "c.csv")[0]
    assert _numero_processo(registro) is None


def test_chave_coluna_remove_acento_e_maiuscula() -> None:
    assert _chave_coluna("Número") == "numero"
    assert _chave_coluna("Observações") == "observacoes"
    assert _chave_coluna(" Razão Social ") == "razaosocial"


def test_ler_csv_detecta_ponto_e_virgula_e_ignora_linhas_vazias() -> None:
    conteudo = "Numero;Empresa;Procurador\n900123456;Padaria X;Dr. Silva\n; ;\n909876543;Loja Y;\n"
    registros = _ler_planilha(conteudo.encode("utf-8"), "carteira.csv")
    assert len(registros) == 2
    assert _valor(registros[0], COLUNAS_NUMERO) == "900123456"
    assert _valor(registros[0], COLUNAS_EMPRESA) == "Padaria X"
    assert _valor(registros[1], COLUNAS_EMPRESA) == "Loja Y"


def test_ler_csv_com_virgula() -> None:
    registros = _ler_planilha(b"numero,observacoes\n900,teste\n", "x.csv")
    assert _valor(registros[0], COLUNAS_OBS) == "teste"


def test_ler_xlsx_com_cabecalho_acentuado_e_celula_numerica() -> None:
    wb = Workbook()
    planilha = wb.active
    planilha.append(["Número", "Empresa", "Observações"])
    planilha.append(["900111222", "Cafeteria Z", "marca principal"])
    planilha.append([909333444, "Bar W", None])
    buffer = io.BytesIO()
    wb.save(buffer)
    registros = _ler_planilha(buffer.getvalue(), "carteira.xlsx")
    assert len(registros) == 2
    assert _valor(registros[0], COLUNAS_NUMERO) == "900111222"
    assert _valor(registros[1], COLUNAS_NUMERO) == "909333444"
    assert _valor(registros[1], COLUNAS_EMPRESA) == "Bar W"


def test_planilha_so_com_cabecalho_retorna_vazio() -> None:
    assert _ler_planilha(b"numero,empresa\n", "x.csv") == []


def test_planilha_acima_do_limite_de_linhas_e_rejeitada() -> None:
    # Achado baixo da auditoria da carteira (Fase 10, 22/09/2026): só havia
    # limite de tamanho de arquivo (5MB), não de linhas -- um CSV compacto
    # pode ter centenas de milhares de linhas processadas num único request
    # síncrono, sem paginação.
    conteudo = "numero\n" + "\n".join(f"90012345{i}" for i in range(LINHAS_MAXIMAS_IMPORTACAO + 1))
    with pytest.raises(HTTPException) as exc_info:
        _ler_planilha(conteudo.encode("utf-8"), "carteira.csv")
    assert exc_info.value.status_code == 413


def test_planilha_no_limite_de_linhas_e_aceita() -> None:
    conteudo = "numero\n" + "\n".join(f"90012345{i}" for i in range(LINHAS_MAXIMAS_IMPORTACAO))
    registros = _ler_planilha(conteudo.encode("utf-8"), "carteira.csv")
    assert len(registros) == LINHAS_MAXIMAS_IMPORTACAO


def test_valor_ignora_colunas_desconhecidas() -> None:
    registro = {"numero": "900", "coluna_estranha": "lixo"}
    assert _valor(registro, COLUNAS_EMPRESA) is None


class _ArquivoFake:
    """Dublê mínimo de UploadFile -- só o que importar_carteira lê de fato
    (read() assíncrono, filename)."""

    def __init__(self, conteudo: bytes, filename: str = "carteira.csv") -> None:
        self.filename = filename
        self._conteudo = conteudo

    async def read(self, _tamanho: int | None = None) -> bytes:
        return self._conteudo


# --- Achado médio da auditoria da carteira (Fase 10, 22/09/2026):
# verificar_conflito_interesse não rodava na importação de planilha, e o
# "checa depois insere" não tratava corrida concorrente. ---


def test_importar_carteira_aplica_checagem_de_conflito_de_interesse(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sem_virus(_conteudo: bytes) -> None:
        return None

    monkeypatch.setattr(modulo_carteira, "escanear_upload_ou_rejeitar", _sem_virus)

    processo = Processo(
        id=301, numero="900123456", numero_normalizado="900123456", tipo=TipoProcesso.MARCA, titulo="Marca X"
    )
    session = FakeSession(
        [
            FakeResult(itens=[processo]),  # Processo.numero_normalizado.in_(...)
            FakeResult(itens=[]),  # ja_monitorados
            FakeResult(itens=[(301, "Marca Concorrente Ltda")]),  # titulares dos processos vinculados nesta importação
            FakeResult(itens=[("Marca Concorrente Ltda", 77, "Outro Cliente", 301)]),  # conflito: titulares
            FakeResult(itens=[]),  # conflito: empresas
        ]
    )
    usuario = usuario_teste()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/carteira/importar",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )

    resultado = asyncio.run(
        importar_carteira(
            request, session, usuario, _ArquivoFake(b"numero\n900123456\n"), responsavel_id=None
        )
    )

    assert resultado["vinculados"] == 1
    assert resultado["alertas_conflito_interesse"] == [
        {
            "tipo": "titular_outro_cliente",
            "nome_encontrado": "Marca Concorrente Ltda",
            "empresa_id": 77,
            "empresa_nome": "Outro Cliente",
            "processo_id": 301,
        }
    ]


def test_importar_carteira_rejeita_arquivo_infectado(monkeypatch: pytest.MonkeyPatch) -> None:
    # Achado da varredura ampla do sistema (18/09/2026): este endpoint
    # aceitava CSV/XLSX sem nenhuma varredura antivírus, diferente dos
    # demais pontos de upload do sistema (portal do cliente, central de
    # atualizações).
    async def _rejeitar(_conteudo: bytes) -> None:
        raise HTTPException(status_code=422, detail="Arquivo rejeitado: malware detectado.")

    monkeypatch.setattr(modulo_carteira, "escanear_upload_ou_rejeitar", _rejeitar)

    async def _sessao() -> FakeSession:
        yield FakeSession()

    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/carteira/importar",
            files={"arquivo": ("carteira.csv", b"numero;empresa\n900123456;X\n", "text/csv")},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 422
