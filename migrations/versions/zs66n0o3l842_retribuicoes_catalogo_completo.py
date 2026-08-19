"""catálogo completo de retribuições de marca (tabela oficial 20/12/2025)

Adiciona os serviços da Tabela de Retribuições do INPI (marcas) que ainda não
estavam cadastrados e reordena todos na sequência oficial. Códigos que se
repetem em várias seções da tabela (ex.: 382, 340, 379, 381) entram uma única
vez. Fonte: Portaria GM/MDIC nº 110/2025, Portaria INPI/PR nº 10/2025 e
apostila 31/10/2025.

Revision ID: zs66n0o3l842
Revises: zr55m9n2k731
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zs66n0o3l842"
down_revision: str | None = "zr55m9n2k731"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOTA = (
    "Portaria GM/MDIC 110/2025; INPI/PR 10/2025; apostila 31/10/2025 "
    "(tabela vigente 20/12/2025)"
)

# Novos serviços: (ordem, codigo, servico, descricao, grupo, normal, reduzido, obs_extra)
NOVOS: list[tuple] = [
    (3, "3001", "divisao_processo", "Divisão de processo", "marca", 870, None, None),
    (5, "382", "exigencia_conformidade_peticao",
     "Cumprimento de exigência decorrente de exame de conformidade em petição",
     "marca", 180, 90, None),
    (6, "381", "apresentacao_documentos", "Apresentação de documentos", "marca", 100, None, None),
    (8, "3022", "oposicao_restrita",
     "Oposição com restrição de alegações (art. 124, XIX da LPI) - por classe",
     "marca", 360, 180, None),
    (9, "3020", "tramite_prioritario",
     "Trâmite prioritário de marcas por motivo estratégico ou de política pública",
     "marca", 890, 445, None),
    (10, "3019", "tramite_prioritario_gratuito",
     "Trâmite prioritário de marcas com direito à gratuidade", "marca", 0, None, None),
    (11, "3021", "comprovacao_distintividade",
     "Apresentação de documentos para comprovação de distintividade adquirida",
     "marca", 4700, None, None),
    (13, "340", "cumprimento_exigencia", "Cumprimento de exigência", "marca", 180, 90, None),
    (14, "379", "aditamento_peticao", "Aditamento à petição", "marca", 100, None, None),
    (15, "386", "reivindicacao_prioridade_suplementar",
     "Reivindicação suplementar de prioridade", "marca", 100, None, None),
    (17, "373", "concessao_1decenio_extraordinario",
     "Concessão 1º decênio - retribuição paga no prazo extraordinário - por classe",
     "marca", 0, 0, None),
    (20, "333", "recurso_marcas",
     "Recurso de marcas (exceto contra indeferimento de pedido de registro) - 1º processo",
     "marca", 700, 350, "Processo adicional: R$ 350,00 (com desconto R$ 175,00)"),
    (22, "3015", "contrarrazoes_recurso_nulidade",
     "Contrarrazões ao recurso/nulidade", "marca", 180, 90, None),
    (23, "376", "manifestacao_parecer_recurso",
     "Manifestação sobre parecer proferido em grau de recurso", "marca", 0, None, None),
    (24, "3016", "exigencia_grau_recurso_nulidade",
     "Cumprimento de exigência em grau de recurso/nulidade", "marca", 180, 90, None),
    (28, "380", "anotacao_limitacao_onus",
     "Anotação de limitação ou ônus - 1º processo", "marca", 100, None,
     "Processo adicional: R$ 50,00"),
    (30, "3002", "transferencia_parcial_divisao",
     "Anotação de transferência parcial de titular com divisão de processo",
     "marca", 1050, None, None),
    (31, "378", "correcao_dados_falha_interessado",
     "Correção de dados no processo devido à falha do interessado", "marca", 70, None, None),
    (32, "366", "retificacao_erro_rpi",
     "Retificação por erro de publicação na RPI", "marca", 0, None, None),
    (33, "385", "nomeacao_procurador",
     "Nomeação, destituição ou substituição de procurador", "marca", 90, None, None),
    (34, "387", "renuncia_mandato_procuracao",
     "Renúncia a mandato de procuração", "marca", 90, None, None),
    (35, "383", "desistencia_pedido", "Desistência de pedido de registro", "marca", 0, None, None),
    (36, "384", "desistencia_peticao", "Desistência de petição", "marca", 0, None, None),
    (37, "388", "renuncia_registro", "Renúncia a registro de marca", "marca", 0, None, None),
    (38, "3017", "desistencia_parcial_pedido",
     "Desistência parcial de pedido de registro", "marca", 170, None, None),
    (39, "3018", "renuncia_parcial_registro",
     "Renúncia parcial a registro de marca", "marca", 170, None, None),
    (40, "342", "devolucao_prazo_falha_inpi",
     "Pedido de devolução de prazo por falha do INPI", "marca", 0, None, None),
    (41, "341", "devolucao_prazo_impedimento",
     "Pedido de devolução de prazo por impedimento do interessado", "marca", 100, None, None),
    (42, "350", "certidao_atos_processo",
     "Certidão de atos relativos ao processo (dispensado de petição)", "marca", 90, None, None),
    (43, "352", "copia_oficial_prioridade_unionista",
     "Cópia oficial para reivindicação de prioridade unionista - por meio eletrônico",
     "marca", 90, None, "Em papel: R$ 180,00"),
    (44, "824", "copia_digital", "Cópia digital", "marca", 10, None, None),
    (46, "357", "consulta_classificacao",
     "Consulta à comissão de classificação de produtos e serviços - até 5 itens",
     "marca", 170, None, "Acima de 5 itens: acrescentar R$ 20,00 por produto/serviço adicional"),
    (47, "393", "alto_renome_pedido",
     "Pedido de reconhecimento de alto renome", "marca", 37580, None, None),
    (48, "362", "alto_renome_recurso",
     "Recurso com fundamento em alto renome", "marca", 2350, None, None),
    (49, "3004", "madri_certificacao_pedido",
     "Madri: certificação de pedido internacional (Art. 2) - por classe", "madri", 280, None, None),
    (50, "3005", "madri_correcao_certificacao",
     "Madri: correção de inconsistências em certificação (Regra 9)", "madri", 280, None, None),
    (51, "3006", "madri_manifestacao_irregularidade",
     "Madri: manifestação sobre irregularidade (Regras 11, 12 e 13)", "madri", 280, None, None),
    (52, "3007", "madri_transferencia_inscricao",
     "Madri: validação/transmissão de transferência de Inscrição Internacional (Art. 9)",
     "madri", 180, None, None),
    (53, "3008", "madri_transformacao_designacao",
     "Madri: transformação de designação em pedido nacional (Art. 9quinquies)",
     "madri", 510, None, None),
    (54, "3009", "madri_substituicao_registro",
     "Madri: anotação de substituição de registro nacional (Art. 4bis)", "madri", 510, None, None),
    (55, "3010", "madri_correcao_dados",
     "Madri: correção de dados em pedido internacional (Regra 28)", "madri", 0, None, None),
    (56, "3011", "madri_designacao_recebida",
     "Madri: designação recebida (Art. 3ter) - por classe", "madri", 1720, None, None),
    (57, "3012", "madri_concessao_certificado",
     "Madri: concessão de registro e certificado (Art. 8(7)a ii; Regra 34(3)) - por classe",
     "madri", 0, None, None),
    (58, "3013", "madri_prorrogacao",
     "Madri: prorrogação (Arts. 7 e 8(7)a ii; Regra 30) - por classe", "madri", 1000, None, None),
    (59, "3014", "madri_designacao_concessao_certificado",
     "Madri: designação recebida, concessão e certificado (Art. 8(7))", "madri", 1720, None, None),
    (60, "800", "complementacao_retribuicao",
     "Complementação de retribuição (informar o Nosso Número da GRU inicial)",
     "administracao", None, None, "Valor variável"),
    (61, "801", "restituicao_retribuicao",
     "Restituição de retribuição (informar o Nosso Número da GRU inicial)",
     "administracao", 0, None, None),
]

# Ordem oficial dos serviços que já existiam (por código).
ORDEM_EXISTENTES = {
    "389": 1, "394": 2, "338": 4, "332": 7, "339": 12, "372": 16, "374": 18,
    "375": 19, "3000": 21, "336": 25, "337": 26, "348": 27, "349": 29, "351": 45,
}


def upgrade() -> None:
    inserir = sa.text(
        "INSERT INTO retribuicoes_inpi "
        "(servico, descricao, grupo, codigo, valor_normal, valor_reduzido, "
        " confirmado, ativo, ordem, observacoes, atualizado_em) "
        "VALUES (:servico, :descricao, :grupo, :codigo, :normal, :reduzido, "
        " true, true, :ordem, :obs, now()) "
        "ON CONFLICT (servico) DO NOTHING"
    )
    for ordem, codigo, servico, descricao, grupo, normal, reduzido, extra in NOVOS:
        obs = f"{NOTA} · {extra}" if extra else NOTA
        op.execute(
            inserir.bindparams(
                servico=servico, descricao=descricao, grupo=grupo, codigo=codigo,
                normal=normal, reduzido=reduzido, ordem=ordem, obs=obs,
            )
        )
    atualizar = sa.text("UPDATE retribuicoes_inpi SET ordem = :ordem WHERE codigo = :codigo")
    for codigo, ordem in ORDEM_EXISTENTES.items():
        op.execute(atualizar.bindparams(ordem=ordem, codigo=codigo))


def downgrade() -> None:
    remover = sa.text("DELETE FROM retribuicoes_inpi WHERE servico = :servico")
    for _ordem, _codigo, servico, *_ in NOVOS:
        op.execute(remover.bindparams(servico=servico))
