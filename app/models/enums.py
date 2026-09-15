from enum import StrEnum


class TipoProcesso(StrEnum):
    MARCA = "marca"
    PATENTE = "patente"


class StatusLead(StrEnum):
    NOVO = "novo"
    EM_CONTATO = "em_contato"
    QUALIFICADO = "qualificado"
    PROPOSTA_ENVIADA = "proposta_enviada"
    SEM_RETORNO = "sem_retorno"
    CONVERTIDO = "convertido"
    DESCARTADO = "descartado"


class StatusProspect(StrEnum):
    """Situação de um Prospect (Fase 1 do Radar de Prospecção, 03/09/2026).

    Vocabulário deliberadamente restrito ao que a Fase 1 sabe produzir --
    enriquecimento/triagem/score (Fases 2-5) trazem estados intermediários
    novos por migração própria, quando o código que os produz existir."""

    NOVO = "novo"
    APROVADO = "aprovado"
    REJEITADO = "rejeitado"
    DUPLICADO = "duplicado"
    CONVERTIDO_LEAD = "convertido_lead"


class FaseLead(StrEnum):
    """Etapa do lead no funil de atendimento (do 1º contato ao processo no INPI).

    Expandida na auditoria de CRM/financeiro (04/09/2026, achado CRM-11) de 7
    para 10 fases: "qualificado" passa a ser uma fase própria (antes só
    existia como StatusLead, sem posição no funil), "pagamento_realizado"
    virou duas fases (aguardando_pagamento / pagamento_confirmado -- a
    anterior não distinguia cobrança emitida de pagamento efetivamente
    recebido), e "ganho" passa a ser uma fase do funil, não só o campo
    ``Lead.resultado``. Decisão de produto tomada (não implementada): NÃO
    existe uma fase "contrato_assinado" separada de "proposta_aceita" --
    neste sistema, assinar a proposta (AssinaturaPropostaComercial) É o
    próprio ato de aceitá-la, mesmo evento e mesmo timestamp; uma fase
    própria para isso ficaria sempre vazia (o lead nunca fica "parado" nela).
    """

    CONTATO_INICIAL = "contato_inicial"
    QUALIFICADO = "qualificado"
    RELATORIO_ENVIADO = "relatorio_enviado"
    PROPOSTA_ENVIADA = "proposta_enviada"
    PROPOSTA_ACEITA = "proposta_aceita"
    AGUARDANDO_PAGAMENTO = "aguardando_pagamento"
    PAGAMENTO_CONFIRMADO = "pagamento_confirmado"
    GANHO = "ganho"
    PROTOCOLO_INPI = "protocolo_inpi"
    PROCESSO_INPI = "processo_inpi"


class TipoAtivoPI(StrEnum):
    MARCA = "marca"
    PATENTE = "patente"
    MODELO_UTILIDADE = "modelo_utilidade"
    DESENHO_INDUSTRIAL = "desenho_industrial"
    CONTRATO = "contrato"
    CESSAO = "cessao"
    LICENCA = "licenca"
    FRANQUIA = "franquia"


class CanalContato(StrEnum):
    TELEFONE = "telefone"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    REUNIAO = "reuniao"
    OUTRO = "outro"
