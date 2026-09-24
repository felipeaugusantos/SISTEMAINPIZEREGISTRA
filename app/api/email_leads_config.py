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

# Achado da Fase 15.2 (auditoria fina de Leads, 23/09/2026): o template
# padrão tinha a marca "Zé Registra" e a assinatura "Letícia" hardcoded,
# com placeholders literais ("[Sobrenome]", "[Telefone/WhatsApp]",
# "[E-mail]", "[Site]") que nunca eram substituídos -- se uma organização-
# tenant não customizasse o próprio modelo, esse texto ia direto pro
# cliente com a marca ERRADA e placeholders crus visíveis. Agora usa
# placeholders "{{organizacao.*}}", substituídos com os dados reais da
# organização em enviar_email_prospeccao (mesmo mecanismo já usado pra
# "{{lead.nome}}"), com fallback vazio (nunca um placeholder cru) quando
# o dado não está cadastrado.
DEFAULTS = {
    "assunto": "Proteja sua marca com a {{organizacao.nome}}",
    "corpo": (
        "Olá, {{lead.nome}}, tudo bem?\n"
        "Meu nome é {{organizacao.nome}} e fazemos o acompanhamento de registro de marcas.\n"
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
        "Atenciosamente,\n"
        "Atendimento Comercial\n"
        "{{organizacao.nome}}\n"
        "{{organizacao.telefone}}\n"
        "{{organizacao.email}}\n"
        "{{organizacao.site}}"
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


def substituir_placeholders_organizacao(texto: str, org: Organizacao | None) -> str:
    """Substitui "{{organizacao.*}}" pelos dados reais da organização, com
    fallback vazio (nunca deixa um placeholder cru visível pro cliente)."""
    branding = (org.branding or {}) if org else {}
    valores = {
        "{{organizacao.nome}}": (org.nome if org else "") or "",
        "{{organizacao.telefone}}": (org.telefone_contato if org else "") or branding.get("telefone") or "",
        "{{organizacao.email}}": (org.email_contato if org else "") or branding.get("email") or "",
        "{{organizacao.site}}": branding.get("site") or "",
    }
    for placeholder, valor in valores.items():
        texto = texto.replace(placeholder, valor)
    return texto


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
