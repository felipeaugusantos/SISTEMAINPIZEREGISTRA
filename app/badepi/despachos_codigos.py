"""Descrições oficiais dos códigos de despacho de marcas.

Fonte: INPI — "Tabela de Códigos de Despachos - Marcas", publicada na RPI
(https://revistas.inpi.gov.br/rpi/download/despachos/300), transcrita da versão
em PDF em 2026-08-12.

A chave é o sufixo numérico do código (3 dígitos com zero à esquerda; códigos de
4 dígitos permanecem com 4). Os códigos do BADEPI vêm como ``DESPnnn`` ou
``IPASnnn`` — ambos compartilham este mesmo espaço numérico, então basta extrair
os dígitos e normalizar com ``codigo_numerico`` para consultar a descrição.

Códigos ``DESP`` legados que não constam mais da tabela atual da RPI (p.ex. 000,
003, 145, 150, 351) ficam sem descrição de propósito — não há fonte pública para
eles e não se deve inventar texto.
"""

import re

DESCRICOES_DESPACHO: dict[str, str] = {
    # Despachos em processos
    "005": "Exigência formal",
    "009": "Publicação de pedido de registro para oposição (exame formal concluído)",
    "024": "Indeferimento do pedido",
    "029": "Deferimento do pedido",
    "033": "Decisão de considerar pedido inexistente por exigência de pagamento não respondida",
    "047": "Decisão de considerar pedido inexistente por falta de pagamento",
    "091": "Decisão de considerar pedido inexistente por exigência de pagamento não cumprida",
    "106": "Arquivamento definitivo de pedido de registro por falta de procuração",
    "112": "Decisão de considerar pedido inexistente por exigência formal não respondida",
    "113": "Decisão de considerar pedido inexistente por exigência formal não cumprida",
    "135": "Republicação de pedido (por perda da prioridade)",
    "136": "Exigência de mérito",
    "139": (
        "Arquivamento definitivo de pedido de registro por falta de cumprimento de "
        "exigência de mérito"
    ),
    "142": "Sobrestamento do exame de mérito",
    "157": "Arquivamento definitivo de pedido de registro por falta de pagamento da concessão",
    "158": "Concessão de registro",
    "161": "Extinção de registro pela expiração do prazo de vigência",
    "289": (
        "Arquivamento definitivo de pedido de registro por falta de documentos de "
        "marca de certificação"
    ),
    "291": (
        "Arquivamento definitivo de pedido de registro por falta de documentos de "
        "marca coletiva"
    ),
    "304": "Extinção de registro pela caducidade",
    "395": "Exigência de pagamento",
    "402": "Anulação de despacho (em processo)",
    "404": "Arquivamento de ofício de pedido de registro de marca",
    "409": "Cancelamento de ofício de registro de marca",
    "414": "Extinção de registro pela inobservância do disposto no art. 217 da LPI",
    "421": "Republicação de pedido",
    "423": "Notificação de oposição",
    "654": "Deferimento do pedido (em retificação)",
    "658": "Indeferimento do pedido (em retificação)",
    "668": "Notificação de oposição (em retificação)",
    # Despachos em petições
    "089": "Exigência de pagamento (em petição)",
    "185": "Arquivamento de petição por falta de procuração",
    "192": "Exigência de conformidade",
    "227": "Sobrestamento do exame de mérito (em petição)",
    "235": "Recurso não provido (decisão mantida)",
    "236": "Notificação de novo impedimento legal em grau de recurso",
    "237": "Recurso provido (decisão reformada para: Deferimento)",
    "238": "Recurso provido (decisão reformada para: Deferimento parcial)",
    "267": "Exigência de mérito (em petição)",
    "270": "Deferimento da petição",
    "271": "Indeferimento da petição",
    "337": "Indeferimento da petição por falta de legítimo interesse",
    "338": "Notificação de caducidade",
    "349": "Deferimento parcial da petição",
    "360": "Notificação de recurso",
    "362": "Exigência sobre alto renome",
    "369": "Recurso provido (decisão reformada para: Indeferimento)",
    "370": "Recurso provido (outros)",
    "400": "Notificação de instauração de processo de nulidade a requerimento",
    "403": "Anulação de despacho (em petição)",
    "428": "Decisão de não conhecer da petição",
    "437": "Notificação de instauração de processo de nulidade de ofício",
    "462": "Notificação de procedimento judicial",
    "499": "Sobrestamento da instrução técnica",
    "530": "Requerimento provido (nulo o registro)",
    "531": "Requerimento provido (outros)",
    "532": "Requerimento não provido (mantida a concessão)",
    "533": "Requerimento não provido (outros)",
    "534": "Requerimento provido parcialmente (outros)",
    "535": "Recurso provido parcialmente (decisão reformada para: Deferimento parcial)",
    "536": "Recurso provido parcialmente (outros)",
    "566": "Petição de retificação atendida",
    "567": "Petição de retificação não atendida",
    "639": "Publicação de decisão judicial transitada em julgado",
    "669": "Deferimento da petição de caducidade",
    "699": "Ato de prejudicar petição",
    "975": (
        "Recurso provido (decisão reformada com necessidade de devolução dos autos "
        "para a primeira instância)"
    ),
    "1043": "Solicitação de cópia reprográfica não atendida",
    "1054": "Petição de trâmite prioritário atendida",
    "1055": "Petição de trâmite prioritário não atendida",
    "1069": "Petição de trâmite prioritário apta (aguardando término de prazo legal)",
    "1072": (
        "Notificação de confirmação da ausência de distintividade inerente da marca "
        "(em recurso)"
    ),
    "1073": (
        "Notificação de confirmação da ausência de distintividade inerente da marca "
        "(em nulidade)"
    ),
    # Despachos em serviços avulsos
    "356": "Emissão de certidão de busca de marca por classe",
    "537": "Emissão de certidão de busca de marca por titular",
    "538": "Emissão do Parecer da Comissão de Classificação de Produtos e Serviços",
    "572": "Emissão do Parecer da Comissão de Classificação de Elementos Figurativos",
    # Despachos de emissão de documentos
    "523": "Emissão de Certidão de andamento",
    "575": "Emissão de Cópia oficial de registro de marca",
    "576": "Emissão de Cópia oficial de pedido de registro",
    "577": "Emissão de folha de rosto de cópia reprográfica simples",
    "578": "Emissão de folha de rosto de cópia reprográfica autenticada",
    "579": "Emissão de segunda via de certificado de registro",
    "971": "Emissão de segunda via de certificado de registro em designação",
    # Despachos em processos designados ao Brasil pela via do Protocolo de Madri
    "753": (
        "Notificação de obrigatoriedade de apresentação de documentos de marca "
        "coletiva ou de certificação"
    ),
    "755": (
        "Arquivamento definitivo de designação por falta de docs. de marca coletiva "
        "ou de certificação"
    ),
    "756": "Publicação de pedido de registro para oposição (exame formal de Designação concluído)",
    "757": "Republicação de designação",
    "768": "Deferimento de designação",
    "770": "Concessão de registro em designação",
    "771": "Sobrestamento do exame de mérito de designação",
    "772": "Exigência de mérito em designação",
    "773": "Arquivamento definitivo de designação por falta de cumprimento de exigência de mérito",
    "774": "Indeferimento de designação",
    "775": "Indeferimento de designação (em retificação)",
    "780": (
        "Arquivamento definitivo de designação por falta de pagamento da segunda "
        "parte da retribuição"
    ),
    "781": "Deferimento parcial de designação",
    "782": (
        "Notificação de prazo para pagamento da segunda parte da retribuição "
        "relativa a designação"
    ),
    "1028": (
        "Notificação de processo decorrente de transferência parcial (Madri) da "
        "inscrição internacional"
    ),
    "1029": (
        "Notificação de processo criado por transferência parcial (Madri) "
        "(aguardando fim de sobrestamento)"
    ),
    "1030": (
        "Notificação de processo criado por transferência parcial (Madri) "
        "(exame de recurso: indeferimento)"
    ),
    "1031": (
        "Notificação de processo criado por transferência parcial (Madri) "
        "(exame de recurso: defer. parcial)"
    ),
    "1032": (
        "Notificação de processo criado por transferência parcial (Madri) "
        "(exame de recurso: arquiv. ofício)"
    ),
    "1033": (
        "Notificação de processo criado por transferência parcial (Madri) "
        "(exame de recurso: cancel. ofício)"
    ),
    "1045": "Notificação de processo criado por transferência parcial (registro de marca (Madri))",
    # Despachos em petições referentes a serviços do Protocolo de Madri
    "790": (
        "Decisão de considerar Pedido Internacional inexistente por não cumprir "
        "requisitos de certificação"
    ),
    "791": "Notificação de inconsistência em Pedido Internacional",
    "794": (
        "Decisão de considerar Pedido Internacional inexistente pela não resposta à "
        "notif. de inconsistência"
    ),
    "797": "Pedido Internacional certificado e enviado à Secretaria Internacional",
    "815": "Comunicação ao usuário de irregularidade notificada pela SI",
    "820": "Irregularidade respondida à Secretaria Internacional",
    "831": "Decisão de considerar Pedido Internacional inexistente por falta de pagamento",
    "834": "Notificação de inconsistência em Pedido Internacional relativa a pagamento",
    "837": (
        "Decisão de considerar Pedido Internacional inexistente por inconsistência de "
        "pagamento não sanada"
    ),
    "838": (
        "Decisão de considerar Pedido Internacional inexistente por inconsistência de "
        "pag. não respondida"
    ),
    "846": "Petição de correção de dados em Pedido Internacional não atendida",
    "847": "Petição de correção de dados em Pedido Internacional atendida",
    "849": "Anulação de despacho (em Pedido Internacional)",
    # Despachos específicos para comunicações Madri
    "900": "Anotação de alteração de nome e/ou endereço em designação",
    "901": "Anotação de transferência de titularidade em designação",
    "902": "Anotação de cancelamento parcial de especificação em designação",
    "903": "Retificação de dados em designação",
    "905": "Anotação de restrição de especificação em designação",
    "906": "Indeferimento de anotação de restrição de especificação em designação",
    "907": "Anotação de transferência parcial de titularidade em designação",
    "908": "Sobrestamento de anotação de transferência de titularidade em designação",
    "963": "Renúncia total a registro de marca em razão de cancelamento da Inscrição Internacional",
    "964": "Desistência total de designação em razão de cancelamento da Inscrição Internacional",
    "965": (
        "Renúncia total a registro de marca em razão de renúncia à Inscrição "
        "Internacional que afeta o Brasil"
    ),
    "966": (
        "Desistência total de designação em razão de renúncia à Inscrição "
        "Internacional que afeta o Brasil"
    ),
    "972": "Anulação de despacho (em comunicação de Madri)",
    "973": "Ato de prejudicar comunicação de Madri",
    "1016": (
        "Anotação de transferência parcial com efeito de arquivamento de designação "
        "ou extinção de registro"
    ),
}


def codigo_numerico(codigo: str | None) -> str | None:
    """Extrai o sufixo numérico normalizado de um código de despacho.

    ``DESP009`` e ``IPAS009`` → ``009``; ``DESP1073`` → ``1073``. Retorna ``None``
    quando o código não tem parte numérica.
    """
    if not codigo:
        return None
    correspondencia = re.search(r"\d+", codigo)
    if correspondencia is None:
        return None
    return correspondencia.group(0).zfill(3)


def descricao_despacho(codigo: str | None) -> str | None:
    """Descrição oficial do despacho, ou ``None`` se o código não estiver na tabela."""
    numero = codigo_numerico(codigo)
    if numero is None:
        return None
    return DESCRICOES_DESPACHO.get(numero)
