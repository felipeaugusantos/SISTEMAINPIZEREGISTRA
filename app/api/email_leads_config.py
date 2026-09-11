from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import Organizacao

router = APIRouter(prefix="/v1/admin/configuracao/email-leads", tags=["configuração de e-mail (leads)"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManageDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("leads.manage"))]

DEFAULTS = {
    "assunto": "Proteja sua marca com a Zé Registra",
    "corpo": (
        "Olá, {{lead.nome}}, tudo bem?\n"
        "Meu nome é Letícia e faço parte da equipe da Zé Registra.\n"
        "Ajudamos empreendedores e empresas a protegerem suas marcas perante o Instituto "
        "Nacional da Propriedade Industrial — INPI, oferecendo acompanhamento desde a "
        "pesquisa inicial até o protocolo e monitoramento do processo.\n"
        "Nossos principais serviços incluem:\n"
        "- Pesquisa de marcas na base do INPI;\n"
        "- Análise indicativa de viabilidade e possíveis riscos;\n"
        "- Orientação sobre as classes adequadas à atividade da empresa;\n"
        "- Preparação e protocolo do pedido de registro;\n"
        "- Acompanhamento das movimentações do processo;\n"
        "- Orientação durante as etapas do pedido no INPI;\n"
        "- Monitoramento preventivo de possíveis marcas semelhantes.\n"
        "Nosso objetivo é tornar o processo mais claro e seguro, mantendo você informado "
        "em cada etapa.\n"
        "É importante destacar que a concessão do registro é uma decisão exclusiva do "
        "INPI. Nosso trabalho é realizar a análise, orientar a estratégia e acompanhar o "
        "processo com atenção técnica e transparência.\n"
        "Gostaríamos de entender melhor o seu caso. Você poderia nos informar:\n"
        "- Qual é o nome da marca?\n"
        "- Qual produto ou serviço ela identifica?\n"
        "- A marca já está sendo utilizada?\n"
        "- Você possui CPF ou CNPJ?\n"
        "- Já realizou algum pedido anteriormente no INPI?\n"
        "Com essas informações, conseguimos orientar o melhor próximo passo.\n"
        "Se preferir, também podemos conversar pelo WhatsApp: [número ou link do WhatsApp].\n"
        "Atenciosamente,\n"
        "Letícia [Sobrenome]\n"
        "Atendimento Comercial\n"
        "Zé Registra\n"
        "[Telefone/WhatsApp]\n"
        "[E-mail]\n"
        "[Site]"
    ),
    "reply_to": "",
}


class EmailLeadsConfigInput(BaseModel):
    assunto: str = Field(min_length=3, max_length=200)
    corpo: str = Field(min_length=10, max_length=5000)
    reply_to: str = Field(default="", max_length=254)


def config_email_leads(org: Organizacao | None) -> dict:
    atual = (org.branding or {}).get("email_leads") if org else None
    return {**DEFAULTS, **(atual or {})}


@router.get("")
async def obter_configuracao(session: SessionDep, usuario: ManageDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    return config_email_leads(org)


@router.put("")
async def salvar_configuracao(dados: EmailLeadsConfigInput, session: SessionDep, usuario: ManageDep) -> dict:
    org = await session.get(Organizacao, usuario.organizacao_id)
    if org is None:
        return {"status": "ok"}
    org.branding = {**(org.branding or {}), "email_leads": dados.model_dump()}
    await session.commit()
    return {"status": "ok", "configuracao": config_email_leads(org)}
