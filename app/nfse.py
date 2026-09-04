"""Adaptador de emissão de NFS-e (Nota Fiscal de Serviço eletrônica) --
achado FASE7-6 da auditoria (04/09/2026): não existia nenhuma emissão de
nota fiscal, só ReciboFinanceiro (recibo interno, sem valor fiscal).

NFS-e no Brasil não tem um webservice nacional único -- cada município
tem o seu (muitos seguem o padrão ABRASF, mas não todos), cada um exige
certificado digital da prefeitura ou um provedor terceirizado (NFe.io,
Focus NFe, eNotas etc). Por adaptador, sem acoplar a um provedor --
trocar de município/provedor no futuro é escrever uma classe nova aqui.
AdaptadorSandbox nunca emite nota fiscal real em nenhum ambiente.
"""

import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class NotaFiscalEmitida:
    numero: str
    codigo_verificacao: str
    emitida_em: datetime
    url_pdf: str | None = None


class AdaptadorNFSeIndisponivelError(Exception):
    pass


class AdaptadorNFSe(ABC):
    nome: str

    @abstractmethod
    async def emitir(
        self,
        *,
        valor: Decimal,
        descricao_servico: str,
        tomador_documento: str,
        tomador_nome: str,
        competencia: date,
    ) -> NotaFiscalEmitida: ...

    @abstractmethod
    async def cancelar(self, numero: str) -> None: ...


class AdaptadorNFSeSandbox(AdaptadorNFSe):
    """Implementação de referência -- nunca fala com a prefeitura nem com
    um provedor real. Número e código de verificação são valores de
    exemplo (não consultáveis em nenhum portal municipal de verdade)."""

    nome = "sandbox"

    async def emitir(
        self,
        *,
        valor: Decimal,
        descricao_servico: str,
        tomador_documento: str,
        tomador_nome: str,
        competencia: date,
    ) -> NotaFiscalEmitida:
        return NotaFiscalEmitida(
            numero=f"SANDBOX-{secrets.token_hex(6).upper()}",
            codigo_verificacao=secrets.token_hex(8).upper(),
            emitida_em=datetime.now(UTC),
        )

    async def cancelar(self, numero: str) -> None:
        return None


def obter_adaptador_nfse(nome: str) -> AdaptadorNFSe:
    if nome == "sandbox":
        return AdaptadorNFSeSandbox()
    raise AdaptadorNFSeIndisponivelError(
        f'Adaptador de NFS-e "{nome}" não está configurado. Só "sandbox" está '
        "disponível até um provedor/município real ser integrado -- ver app/nfse.py."
    )
