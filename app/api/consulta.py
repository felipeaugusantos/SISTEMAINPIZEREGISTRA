from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pesquisas import detectar_pesquisa_duplicada, gerar_resumo_pesquisa
from app.auth import UsuarioAutenticado, exigir_permissao
from app.crm import buscar_lead_ativo_por_email, obter_ou_criar_empresa, registrar_consentimento_operador
from app.database import get_session
from app.models import (
    Contato,
    ExplicacaoAnaliseMarca,
    Lead,
    PesquisaMarca,
    SolicitacaoExclusaoPesquisa,
    StatusLead,
    TipoProcesso,
)
from app.schemas import PesquisaMarcaCriada, ResumoPublicoMarcaResponse
from app.trademarks.nice import CLASSES_NICE

router = APIRouter(prefix="/v1/admin/consulta", tags=["consulta interna"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
OperadorDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.view"))]


class MarcaConsultaInput(BaseModel):
    marca: str = Field(min_length=2, max_length=200)
    atividade: str | None = Field(default=None, max_length=500)
    # Achado da auditoria completa do CRM (06/09/2026, item 4): classe_nice
    # existia no modelo e já era usada pelo motor de busca/risco
    # (gerar_resumo_pesquisa -> buscar_marcas), mas nenhum fluxo comercial
    # jamais capturava um valor real -- sempre None. Lista vazia mantém o
    # comportamento de sempre (uma única pesquisa sem recorte, buscando em
    # todas as classes); uma ou mais classes cria uma PesquisaMarca PARA
    # CADA classe, todas ligadas ao mesmo lead -- decisão do usuário de
    # tratar "várias classes" como "várias pesquisas da mesma oportunidade"
    # em vez de redesenhar o motor de busca/risco (pensado para 1 classe
    # por vez) para aceitar uma lista.
    classes_nice: list[str] = Field(default_factory=list, max_length=45)

    @field_validator("marca")
    @classmethod
    def limpar_marca(cls, valor: str) -> str:
        limpa = valor.strip()
        if len(limpa) < 2:
            raise ValueError("A marca deve ter ao menos 2 caracteres")
        return limpa

    @field_validator("atividade")
    @classmethod
    def limpar_atividade(cls, valor: str | None) -> str | None:
        return (valor or "").strip() or None

    @field_validator("classes_nice")
    @classmethod
    def validar_classes_nice(cls, valores: list[str]) -> list[str]:
        limpas = list(dict.fromkeys(valor.strip() for valor in valores if valor.strip()))
        invalidas = [valor for valor in limpas if valor not in CLASSES_NICE]
        if invalidas:
            raise ValueError(f"Classe Nice inválida: {', '.join(invalidas)}")
        return limpas


class ConsultaOperadorInput(BaseModel):
    # Campos legados mantidos para clientes existentes da API. A interface nova
    # envia ``marcas``; quando ela estiver vazia, marca/atividade/classes_nice
    # continuam produzindo exatamente a consulta única anterior.
    marca: str | None = Field(default=None, min_length=2, max_length=200)
    atividade: str | None = Field(default=None, max_length=500)
    classes_nice: list[str] = Field(default_factory=list, max_length=45)
    marcas: list[MarcaConsultaInput] = Field(default_factory=list, min_length=0, max_length=20)
    # Nome e e-mail sao obrigatorios: toda consulta interna vira lead, para o
    # comercial poder dar sequencia (ver app.api.consulta.criar_consulta).
    nome: str = Field(min_length=2, max_length=150)
    empresa: str | None = Field(default=None, max_length=200)
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    telefone: str = Field(default="", max_length=30)

    @field_validator("marca")
    @classmethod
    def limpar_marca_opcional(cls, valor: str | None) -> str | None:
        return valor.strip() if valor is not None else None

    @field_validator("nome")
    @classmethod
    def limpar_nome(cls, valor: str) -> str:
        return valor.strip()

    @field_validator("atividade")
    @classmethod
    def limpar_atividade(cls, valor: str | None) -> str | None:
        return (valor or "").strip() or None

    @field_validator("classes_nice")
    @classmethod
    def validar_classes_nice(cls, valores: list[str]) -> list[str]:
        limpas = list(dict.fromkeys(valor.strip() for valor in valores if valor.strip()))
        invalidas = [valor for valor in limpas if valor not in CLASSES_NICE]
        if invalidas:
            raise ValueError(f"Classe Nice inválida: {', '.join(invalidas)}")
        return limpas

    @model_validator(mode="after")
    def exigir_ao_menos_uma_marca(self) -> "ConsultaOperadorInput":
        if not self.marcas and not self.marca:
            raise ValueError("Informe ao menos uma marca")
        itens = self.itens_marca()
        total_pesquisas = sum(max(1, len(item.classes_nice)) for item in itens)
        if total_pesquisas > 50:
            raise ValueError("A consulta aceita no máximo 50 combinações de marca e classe")
        combinacoes: set[tuple[str, str | None]] = set()
        for item in itens:
            for classe in item.classes_nice or [None]:
                chave = (item.marca.casefold(), classe)
                if chave in combinacoes:
                    raise ValueError("Não repita a mesma combinação de marca e classe")
                combinacoes.add(chave)
        return self

    def itens_marca(self) -> list[MarcaConsultaInput]:
        if self.marcas:
            return self.marcas
        return [
            MarcaConsultaInput(
                marca=self.marca or "",
                atividade=self.atividade,
                classes_nice=self.classes_nice,
            )
        ]


@router.get("/classes-nice")
async def listar_classes_nice(operador: OperadorDep) -> list[dict]:
    return [{"codigo": codigo, "titulo": titulo} for codigo, (titulo, _palavras_chave) in CLASSES_NICE.items()]


class ConsultaMultiplaCriada(BaseModel):
    lead_id: int | None = None
    itens: list[PesquisaMarcaCriada]


@router.post("", response_model=ConsultaMultiplaCriada, status_code=status.HTTP_201_CREATED)
async def criar_consulta(
    dados: ConsultaOperadorInput, session: SessionDep, operador: OperadorDep
) -> ConsultaMultiplaCriada:
    marcas = dados.itens_marca()
    primeira_marca = marcas[0]
    empresa = await obter_ou_criar_empresa(session, operador.organizacao_id, dados.empresa)
    email = dados.email.strip().lower()
    lead = await buscar_lead_ativo_por_email(session, operador.organizacao_id, email)
    if lead is not None and lead.contato_id is not None:
        with session.no_autoflush:
            contato_valido = (
                await session.execute(
                    select(Contato.id).where(
                        Contato.id == lead.contato_id,
                        Contato.organizacao_id == operador.organizacao_id,
                        Contato.empresa_id == (empresa.id if empresa else None),
                    )
                )
            ).scalar_one_or_none()
        if contato_valido is None:
            lead.contato_id = None
    if lead is None:
        lead = Lead(
            organizacao_id=operador.organizacao_id,
            empresa_id=empresa.id if empresa else None,
            nome=dados.nome,
            empresa=empresa.nome if empresa else None,
            email=email,
            telefone=dados.telefone.strip(),
            marca=primeira_marca.marca,
            atividade=primeira_marca.atividade,
            origem="operador",
            tipo_interesse=TipoProcesso.MARCA,
            aceite_privacidade=True,
            aceite_marketing=False,
            responsavel_id=operador.id,
            status=StatusLead.NOVO,
        )
        registrar_consentimento_operador(lead, operador.id)
        session.add(lead)
        await session.flush()
    elif lead is not None:
        lead.nome = dados.nome.strip() or lead.nome
        lead.telefone = dados.telefone.strip() or lead.telefone
        if empresa is not None:
            lead.empresa_id = empresa.id
            lead.empresa = empresa.nome
        lead.marca = primeira_marca.marca
        if primeira_marca.atividade is not None:
            lead.atividade = primeira_marca.atividade
        lead.responsavel_id = lead.responsavel_id or operador.id

    # Lista vazia = uma única pesquisa sem recorte de classe (comportamento
    # de sempre); cada classe informada vira sua própria PesquisaMarca,
    # todas no mesmo lead -- a "oportunidade" continua sendo o lead, não
    # precisa de nenhuma entidade de agrupamento nova.
    pesquisas: list[PesquisaMarca] = []
    for item_marca in marcas:
        classes_para_pesquisar: list[str | None] = list(item_marca.classes_nice) or [None]
        for classe_nice in classes_para_pesquisar:
            original = await detectar_pesquisa_duplicada(
                session,
                operador.organizacao_id,
                lead.id if lead else None,
                item_marca.marca,
                classe_nice=classe_nice,
            )
            pesquisa = PesquisaMarca(
                organizacao_id=operador.organizacao_id,
                lead_id=lead.id if lead else None,
                empresa_id=empresa.id if empresa else (lead.empresa_id if lead else None),
                marca=item_marca.marca,
                atividade=item_marca.atividade,
                tipo_pesquisa="completa",
                classe_nice=classe_nice,
                duplicada=original is not None,
                pesquisa_original_id=original,
            )
            session.add(pesquisa)
            pesquisas.append(pesquisa)
    await session.commit()
    itens: list[PesquisaMarcaCriada] = []
    for pesquisa in pesquisas:
        await session.refresh(pesquisa)
        itens.append(
            PesquisaMarcaCriada(
                id=pesquisa.id,
                relatorio_url=f"/admin/consulta/{pesquisa.id}",
                lead_id=lead.id if lead else None,
                duplicada=pesquisa.duplicada,
                pesquisa_original_id=pesquisa.pesquisa_original_id,
                marca=pesquisa.marca,
                classe_nice=pesquisa.classe_nice,
            )
        )
    return ConsultaMultiplaCriada(lead_id=lead.id if lead else None, itens=itens)


@router.get("/{pesquisa_id}/contexto")
async def contexto_consulta(pesquisa_id: str, session: SessionDep, operador: OperadorDep) -> dict:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca).where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == operador.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Consulta nao encontrada")
    exclusao_status = (
        await session.execute(
            select(SolicitacaoExclusaoPesquisa.status)
            .where(
                SolicitacaoExclusaoPesquisa.pesquisa_id == pesquisa.id,
                SolicitacaoExclusaoPesquisa.status == "pendente",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "pesquisa_id": pesquisa.id,
        "lead_id": pesquisa.lead_id,
        "empresa_id": pesquisa.empresa_id,
        "duplicada": pesquisa.duplicada,
        "pesquisa_original_id": pesquisa.pesquisa_original_id,
        "exclusao_status": exclusao_status,
        "pode_excluir": operador.pode("leads.delete"),
    }


@router.get("/{pesquisa_id}/relatorio", response_model=ResumoPublicoMarcaResponse)
async def relatorio_consulta(
    pesquisa_id: str, session: SessionDep, operador: OperadorDep
) -> ResumoPublicoMarcaResponse:
    pesquisa = (
        await session.execute(
            select(PesquisaMarca)
            .where(
                PesquisaMarca.id == pesquisa_id,
                PesquisaMarca.organizacao_id == operador.organizacao_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if pesquisa is None:
        raise HTTPException(status_code=404, detail="Consulta nao encontrada")
    return await gerar_resumo_pesquisa(session, pesquisa)


def _explicacao_ia_dict(explicacao: ExplicacaoAnaliseMarca) -> dict:
    return {
        "id": explicacao.id,
        "pesquisa_id": explicacao.pesquisa_id,
        "gerado_em": explicacao.gerado_em,
        "modelo": explicacao.modelo,
        "explicacao": explicacao.explicacao,
        "status": explicacao.status,
        "revisado_por": explicacao.revisado_por,
        "revisado_em": explicacao.revisado_em,
        "erro": explicacao.erro,
    }


@router.get("/{pesquisa_id}/explicacao-ia")
async def obter_explicacao_ia(pesquisa_id: str, session: SessionDep, operador: OperadorDep) -> dict:
    """Explicação em linguagem simples do risco já calculado (IA em sombra)
    -- nunca recalcula nem substitui o resultado técnico, só traduz. Só
    aparece na tela interna do analista, nunca no relatório público."""
    explicacao = (
        await session.execute(
            select(ExplicacaoAnaliseMarca)
            .where(
                ExplicacaoAnaliseMarca.pesquisa_id == pesquisa_id,
                ExplicacaoAnaliseMarca.organizacao_id == operador.organizacao_id,
            )
            .order_by(ExplicacaoAnaliseMarca.gerado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if explicacao is None:
        raise HTTPException(status_code=404, detail="Nenhuma explicação de IA gerada para esta consulta ainda")
    return _explicacao_ia_dict(explicacao)


class RevisaoExplicacaoIAInput(BaseModel):
    status: Literal["aprovada", "descartada"]


@router.post("/{pesquisa_id}/explicacao-ia/{explicacao_id}/revisar")
async def revisar_explicacao_ia(
    pesquisa_id: str, explicacao_id: int, dados: RevisaoExplicacaoIAInput, session: SessionDep, operador: OperadorDep
) -> dict:
    explicacao = (
        await session.execute(
            select(ExplicacaoAnaliseMarca).where(
                ExplicacaoAnaliseMarca.id == explicacao_id,
                ExplicacaoAnaliseMarca.pesquisa_id == pesquisa_id,
                ExplicacaoAnaliseMarca.organizacao_id == operador.organizacao_id,
            )
        )
    ).scalar_one_or_none()
    if explicacao is None:
        raise HTTPException(status_code=404, detail="Explicação não encontrada")
    explicacao.status = dados.status
    explicacao.revisado_por = operador.ator
    explicacao.revisado_em = datetime.now(UTC)
    await session.commit()
    return _explicacao_ia_dict(explicacao)
