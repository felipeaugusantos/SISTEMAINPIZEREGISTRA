"""Renderização em PDF do relatório de pesquisa de marcas.

O PDF é gerado a partir do ``RelatorioMarcaResponse`` já versionado (fonte da
verdade da governança de produção), e não de um novo cálculo. Assim o documento
baixado corresponde exatamente à versão exibida na tela.
"""

from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as ReportImage,
)
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.schemas import MarcaRelatorioItem, RelatorioMarcaResponse
from app.trademarks.consolidated import apresentacao_analise

_COR_TINTA = colors.HexColor("#17231C")
_COR_SUAVE = colors.HexColor("#5B665F")
_COR_CABECALHO = colors.HexColor("#2F8F46")
_COR_CABECALHO_ESCURO = colors.HexColor("#1F6C34")
_COR_MENTA = colors.HexColor("#E8F5E9")
_COR_LINHA = colors.HexColor("#E4E8E5")
_COR_ALTERNADA = colors.HexColor("#F5F7F5")
_CAMINHO_PERSONAGEM = Path(__file__).resolve().parent / "web" / "static" / "assets" / "personagem.png"
_FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")

_DISCLAIMER = (
    "Relatório meramente indicativo, gerado a partir de publicações da Revista da "
    "Propriedade Industrial (RPI) importadas pela plataforma. Não substitui a consulta "
    "oficial ao INPI nem a análise jurídica de registrabilidade, semelhança fonética ou "
    "afinidade mercadológica."
)


def gerar_pdf_proposta(proposta: dict) -> bytes:
    """Gera a proposta com o modelo institucional dinâmico da Zé Registra."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title=f"Proposta {proposta.get('numero', '')}",
        author="Zé Registra",
    )
    estilos = _estilos()
    corpo = ParagraphStyle("proposta_modelo_corpo", parent=estilos["sub"], fontSize=9, leading=12, textColor=_COR_TINTA)
    secao = ParagraphStyle(
        "proposta_modelo_secao",
        parent=estilos["secao"],
        fontSize=10,
        leading=12,
        spaceBefore=6,
        spaceAfter=5,
    )
    valor = ParagraphStyle("proposta_modelo_valor", parent=estilos["marca"], fontSize=15, leading=17)
    rodape = ParagraphStyle(
        "proposta_modelo_rodape",
        parent=estilos["sub"],
        fontSize=7.5,
        leading=9,
        alignment=TA_CENTER,
    )
    branco = ParagraphStyle("proposta_modelo_branco", parent=corpo, textColor=colors.white)
    branco_valor = ParagraphStyle("proposta_modelo_branco_valor", parent=valor, textColor=colors.white)
    configuracao = proposta.get("configuracao", {}) or {}
    cliente = proposta.get("cliente", {}) or {}

    def p(texto: object, estilo=corpo) -> Paragraph:
        return Paragraph(escape(str(texto or "")).replace("&lt;br/&gt;", "<br/>").replace("\n", "<br/>"), estilo)

    def moeda(valor_numerico: object) -> str:
        return f"R$ {float(valor_numerico or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    marcas = proposta.get("pesquisas") or [{"marca": proposta.get("marca"), "classes": proposta.get("classes")}]
    marca_rows = [
        [p(f"MARCA {i:02d}", estilos["rotulo"]), p(item.get("marca") or "A definir", valor)]
        for i, item in enumerate(marcas, 1)
    ]
    marca_rows.append(
        [
            p("CLASSES NICE", estilos["rotulo"]),
            p("; ".join(str(item.get("classes") or "A definir") for item in marcas)),
        ]
    )
    marcas_box = Table(marca_rows, colWidths=[27 * mm, 45 * mm])
    marcas_box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.7, _COR_CABECALHO),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, _COR_LINHA),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    investimento = Table(
        [
            [p("Honorários Zé Registra"), p(moeda(proposta.get("honorarios")))],
            [p("Taxas oficiais do INPI (GRU)"), p(moeda(proposta.get("taxa_gru")))],
            [p("TOTAL À VISTA", estilos["marca"]), p(moeda(proposta.get("total")), valor)],
        ],
        colWidths=[103 * mm, 45 * mm],
    )
    investimento.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _COR_MENTA),
                ("BOX", (0, 0), (-1, -1), 0.7, _COR_CABECALHO),
                ("LINEBELOW", (0, 1), (-1, 1), 0.35, _COR_CABECALHO),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    investimento_resumo = Table(
        [
            [p("Honorários"), p(moeda(proposta.get("honorarios")))],
            [p("Taxas INPI"), p(moeda(proposta.get("taxa_gru")))],
            [p("Total", estilos["marca"]), p(moeda(proposta.get("total")), estilos["marca"])],
        ],
        colWidths=[28 * mm, 18 * mm],
    )
    investimento_resumo.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _COR_MENTA),
                ("BOX", (0, 0), (-1, -1), 0.7, _COR_CABECALHO),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    prazo = (
        configuracao.get("prazo_texto")
        or "Após o aceite, confirmação do pagamento e recebimento integral dos documentos, protocolamos em até 24 horas úteis, salvo pendências ou indisponibilidade dos sistemas oficiais do INPI."
    )
    condicoes = (
        configuracao.get("condicoes_texto")
        or "O protocolo não representa garantia de concessão. A decisão final pertence ao INPI e a análise é indicativa."
    )
    cabecalho = Table(
        [
            [
                p("Zé Registra", branco_valor),
                p(
                    f"PROPOSTA COMERCIAL\nNº {proposta.get('numero', '')}\nVersão {proposta.get('versao', 1)}",
                    branco,
                ),
            ]
        ],
        colWidths=[100 * mm, 48 * mm],
    )
    cabecalho.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _COR_CABECALHO_ESCURO),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    prazo_box = Table([[p("Em até 24 horas úteis", branco_valor)], [p(prazo, branco)]], colWidths=[48 * mm])
    prazo_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _COR_CABECALHO_ESCURO),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    destaque = Table(
        [
            [p("Zé Registra", branco_valor)],
            [p("Vamos proteger suas marcas com transparência e agilidade.", branco)],
        ],
        colWidths=[151 * mm],
    )
    destaque.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _COR_CABECALHO_ESCURO),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    estrategia = Table(
        [
            [
                p(
                    "O cenário ideal é registrar o nome com o logotipo. O escopo desta proposta contempla as marcas e classes indicadas acima."
                ),
            ],
            [p("✓  Registro e acompanhamento do pedido no INPI")],
            [p("✓  Orientação documental e protocolo após aceite e pagamento")],
        ],
        colWidths=[103 * mm],
    )
    estrategia.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0F8F1")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#B8DEBD")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    pagamento = Table(
        [
            [p("PARCELADO", estilos["rotulo"]), p("À VISTA", branco)],
            [p(moeda(proposta.get("total")), valor), p(moeda(proposta.get("total")), branco_valor)],
            [p("Em até 10x no cartão"), p("Via Pix", branco)],
        ],
        colWidths=[24 * mm, 24 * mm],
    )
    pagamento.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F5F5F5")),
                ("BACKGROUND", (1, 0), (1, -1), _COR_CABECALHO_ESCURO),
                ("TEXTCOLOR", (1, 0), (1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.7, _COR_LINHA),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    # Ajuste de proporções para a composição em duas colunas do modelo de referência.
    prazo_box = Table([[p("Prazo de protocolo", branco_valor)], [p(prazo, branco)]], colWidths=[72 * mm])
    prazo_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _COR_CABECALHO_ESCURO),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    estrategia = Table(
        [
            [p("O escopo desta proposta contempla as marcas e classes indicadas acima.")],
            [p("Registro e acompanhamento do pedido no INPI")],
            [p("Orientação documental e protocolo após aceite e pagamento")],
        ],
        colWidths=[72 * mm],
    )
    estrategia.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0F8F1")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#B8DEBD")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    pagamento = Table(
        [
            [p("PARCELADO", estilos["rotulo"]), p("À VISTA", branco)],
            [p(moeda(proposta.get("total")), valor), p(moeda(proposta.get("total")), branco_valor)],
            [p("Em até 10x no cartão"), p("Via Pix", branco)],
        ],
        colWidths=[36 * mm, 36 * mm],
    )
    pagamento.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F5F5F5")),
                ("BACKGROUND", (1, 0), (1, -1), _COR_CABECALHO_ESCURO),
                ("BOX", (0, 0), (-1, -1), 0.7, _COR_LINHA),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    investimento_resumo = Table(
        [
            [p("Honorários"), p(moeda(proposta.get("honorarios")))],
            [p("Taxas INPI"), p(moeda(proposta.get("taxa_gru")))],
            [p("Total", estilos["marca"]), p(moeda(proposta.get("total")), estilos["marca"])],
        ],
        colWidths=[45 * mm, 27 * mm],
    )
    investimento_resumo.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _COR_MENTA),
                ("BOX", (0, 0), (-1, -1), 0.7, _COR_CABECALHO),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story = [
        cabecalho,
        Spacer(1, 5 * mm),
        p(
            f"Olá, {cliente.get('nome') or 'cliente'} — preparamos tudo para proteger suas marcas!",
            estilos["marca"],
        ),
        Spacer(1, 2 * mm),
        p(
            f"Prezado(a) {cliente.get('nome') or 'cliente'}, apresentamos a proposta da Zé Registra para garantir a exclusividade e a segurança jurídica das suas marcas junto ao INPI."
        ),
        Spacer(1, 4 * mm),
        Table(
            [
                [p("1  ESCOPO DO PROJETO", secao), p("3  PRAZO DE PROTOCOLO", secao)],
                [marcas_box, prazo_box],
                [p("2  ESCOPO E ESTRATÉGIA", secao), p("4  INVESTIMENTO E PAGAMENTO", secao)],
                [estrategia, Table([[pagamento], [investimento_resumo]], colWidths=[72 * mm])],
            ],
            colWidths=[74 * mm, 77 * mm],
            style=TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            ),
        ),
        Spacer(1, 3 * mm),
        p(
            proposta.get("condicoes_pagamento")
            or "Pagamento à vista via Pix ou parcelado no cartão, conforme condições comerciais."
        ),
        p("Após o envio da documentação e confirmação do pagamento, iniciamos o protocolo junto ao INPI."),
        Spacer(1, 4 * mm),
        destaque,
        Spacer(1, 3 * mm),
        p(condicoes, rodape),
        p(
            configuracao.get("rodape")
            or "Esta proposta foi gerada pelo Zé Registra e possui versão auditável no sistema.",
            rodape,
        ),
    ]
    doc.build(story)
    return buffer.getvalue()


def _gerar_pdf_proposta_legado(proposta: dict) -> bytes:
    """Gera a proposta comercial versionada em PDF a partir dos dados persistidos."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Proposta {proposta.get('numero', '')}",
        author=proposta.get("empresa", {}).get("nome", "Zé Registra"),
    )
    estilos = _estilos()
    estilos["proposta_titulo"] = ParagraphStyle(
        "proposta_titulo", parent=estilos["titulo"], fontSize=25, leading=29, spaceAfter=12
    )
    estilos["proposta_secao"] = ParagraphStyle(
        "proposta_secao", parent=estilos["secao"], fontSize=13, leading=16, spaceBefore=12
    )
    estilos["proposta_corpo"] = ParagraphStyle(
        "proposta_corpo", parent=estilos["sub"], fontSize=10, leading=15, textColor=_COR_TINTA
    )
    estilos["proposta_rodape"] = ParagraphStyle(
        "proposta_rodape", parent=estilos["sub"], fontSize=8, leading=10, alignment=TA_CENTER
    )
    empresa = proposta.get("empresa", {})
    configuracao = proposta.get("configuracao", {}) or {}

    def p(texto: object, estilo: str = "proposta_corpo") -> Paragraph:
        return Paragraph(escape(str(texto or "")).replace("\n", "<br/>"), estilos[estilo])

    story = [
        ReportImage(
            str(_CAMINHO_PERSONAGEM.parent / "logo-zeregistra.png"),
            width=58 * mm,
            height=15 * mm,
            kind="proportional",
        ),
        Spacer(1, 4 * mm),
        p(configuracao.get("titulo") or "PROPOSTA DE REGISTRO DE MARCA", "proposta_titulo"),
        p(empresa.get("nome", "Zé Registra"), "marca"),
        p(" · ".join(filter(None, [empresa.get("cnpj"), empresa.get("endereco")])), "sub"),
        p(
            " · ".join(filter(None, [empresa.get("telefone"), empresa.get("email"), empresa.get("site")])),
            "sub",
        ),
        Spacer(1, 10 * mm),
        p(f"Proposta {proposta.get('numero', '')} · Versão {proposta.get('versao', 1)}", "sub"),
        p("1. Objeto", "proposta_secao"),
        p(proposta.get("escopo") or configuracao.get("escopo_padrao") or "Registro de marca no INPI"),
        p("2. Dados da marca", "proposta_secao"),
        p(f"Marca: {proposta.get('marca') or 'A definir'}<br/>Classes Nice: {proposta.get('classes') or 'A definir'}"),
        p("3. Investimento", "proposta_secao"),
    ]
    itens_portfolio = proposta.get("pesquisas") or []
    if len(itens_portfolio) > 1:
        story.extend([p("3. Marcas incluídas", "proposta_secao")])
        for indice, item in enumerate(itens_portfolio, 1):
            story.append(
                p(
                    f"{indice}. {item.get('marca') or 'Marca a definir'} — Classes Nice: {item.get('classes') or 'A definir'}"
                )
            )
    valores = [
        [p("Item", "sub"), p("Valor", "sub")],
        [
            p("Honorários profissionais"),
            p(f"R$ {proposta.get('honorarios') or 0:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")),
        ],
        [
            p("Taxa oficial GRU estimada"),
            p(f"R$ {proposta.get('taxa_gru') or 0:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")),
        ],
        [
            p("Total estimado"),
            p(f"R$ {proposta.get('total') or 0:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")),
        ],
    ]
    tabela = Table(valores, colWidths=[125 * mm, 40 * mm])
    tabela.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _COR_MENTA),
                ("GRID", (0, 0), (-1, -1), 0.5, _COR_LINHA),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend(
        [
            tabela,
            p("4. Condições de pagamento", "proposta_secao"),
            p(proposta.get("condicoes_pagamento") or "A combinar"),
            p("5. Prazo operacional", "proposta_secao"),
            p(
                "Após o aceite, confirmação do pagamento e recebimento integral dos documentos, o protocolo será realizado em até 24 horas úteis, salvo pendências ou indisponibilidade dos sistemas oficiais do INPI."
            ),
            p("6. Condições importantes", "proposta_secao"),
            p(
                "O protocolo não representa garantia de concessão. A decisão final pertence ao INPI. A pesquisa e a análise são indicativas e não substituem exame oficial ou análise jurídica especializada."
            ),
            Spacer(1, 8 * mm),
            p(
                "Esta proposta foi gerada pelo Zé Registra e possui versão auditável no sistema.",
                "proposta_rodape",
            ),
        ]
    )
    if configuracao:
        story[-5] = p(configuracao.get("prazo_texto"), "proposta_corpo")
        story[-3] = p(configuracao.get("condicoes_texto"), "proposta_corpo")
        story[-1] = p(configuracao.get("rodape"), "proposta_rodape")
    doc.build(story)
    return buffer.getvalue()


def _estilos() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle(
            "titulo",
            parent=base["Title"],
            textColor=_COR_TINTA,
            fontSize=24,
            leading=27,
            spaceAfter=5,
            alignment=TA_LEFT,
        ),
        "marca": ParagraphStyle(
            "marca",
            parent=base["Normal"],
            textColor=_COR_CABECALHO_ESCURO,
            fontSize=15,
            leading=18,
            fontName="Helvetica-Bold",
            spaceBefore=4,
        ),
        "sub": ParagraphStyle(
            "sub",
            parent=base["Normal"],
            textColor=_COR_SUAVE,
            fontSize=9.5,
            leading=14,
        ),
        "secao": ParagraphStyle(
            "secao",
            parent=base["Normal"],
            textColor=_COR_CABECALHO,
            fontSize=11,
            leading=14,
            fontName="Helvetica-Bold",
            spaceBefore=8,
            spaceAfter=6,
        ),
        "rotulo": ParagraphStyle(
            "rotulo",
            parent=base["Normal"],
            textColor=_COR_CABECALHO_ESCURO,
            fontSize=7.5,
            leading=10,
            fontName="Helvetica-Bold",
        ),
        "celula": ParagraphStyle(
            "celula",
            parent=base["Normal"],
            textColor=_COR_TINTA,
            fontSize=8,
            leading=10,
        ),
        "celula_menor": ParagraphStyle(
            "celula_menor",
            parent=base["Normal"],
            textColor=_COR_SUAVE,
            fontSize=7,
            leading=9,
            spaceBefore=2,
        ),
        "cabecalho": ParagraphStyle(
            "cabecalho",
            parent=base["Normal"],
            textColor=colors.white,
            fontSize=8,
            leading=10,
            fontName="Helvetica-Bold",
        ),
        "rodape": ParagraphStyle(
            "rodape",
            parent=base["Normal"],
            textColor=_COR_SUAVE,
            fontSize=7.5,
            leading=11,
        ),
    }


def _formatar_data(valor: object) -> str:
    if isinstance(valor, datetime):
        if valor.tzinfo is not None:
            valor = valor.astimezone(_FUSO_BRASILIA)
        return valor.strftime("%d/%m/%Y às %H:%M")
    return valor.strftime("%d/%m/%Y") if valor is not None else "—"


def _titulares(item: MarcaRelatorioItem) -> str:
    nomes = [titular.nome for titular in item.titulares if titular.nome]
    return " · ".join(nomes) if nomes else "Titular não informado"


def _classificacoes(item: MarcaRelatorioItem) -> str:
    rotulos: list[str] = []
    for classificacao in item.classificacoes:
        if not classificacao.codigo:
            continue
        if classificacao.sistema == "nice":
            rotulo = f"NCL {classificacao.codigo}"
        elif classificacao.sistema == "vienna":
            rotulo = f"Viena {classificacao.codigo}"
        else:
            rotulo = f"{classificacao.sistema.title()} {classificacao.codigo}"
        if rotulo not in rotulos:
            rotulos.append(rotulo)
    return ", ".join(rotulos) if rotulos else "—"


def _afinidade(item: MarcaRelatorioItem) -> str:
    afinidade = item.afinidade_classes
    if afinidade is None:
        return "—"
    texto = afinidade.rotulo
    if afinidade.revisao == "pendente":
        texto += " (pendente de validação)"
    return texto


def _texto(valor: object, padrao: str = "—") -> str:
    return escape(str(valor)) if valor not in (None, "") else padrao


def _rotulo_enum(valor: str) -> str:
    return {
        "favoravel": "favorável",
        "desfavoravel": "desfavorável",
        "atencao": "atenção",
        "alto_risco": "alto risco",
        "critico": "crítico",
        "critica": "crítica",
        "adequada": "adequada",
    }.get(valor, valor.replace("_", " "))


def _decorar_pagina(canvas: object, doc: SimpleDocTemplate) -> None:
    canvas.saveState()
    largura, _ = A4
    canvas.setStrokeColor(_COR_LINHA)
    canvas.line(doc.leftMargin, 10 * mm, largura - doc.rightMargin, 10 * mm)
    canvas.setFillColor(_COR_SUAVE)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(doc.leftMargin, 6.5 * mm, "Zé Registra · Pesquisa e inteligência para marcas")
    canvas.drawRightString(
        largura - doc.rightMargin,
        6.5 * mm,
        f"Página {canvas.getPageNumber()}",
    )
    canvas.restoreState()


def _celula_marca(item: MarcaRelatorioItem, estilos: dict[str, ParagraphStyle]) -> list:
    conteudo: list = [
        Paragraph(_texto(item.titulo, "Elemento nominativo não informado"), estilos["celula"]),
        Paragraph(_texto(_titulares(item)), estilos["celula_menor"]),
    ]
    marcadores = list(item.criterios_encontro)
    if item.alto_renome:
        marcadores.append("coincide com alto renome")
    if marcadores:
        conteudo.append(Paragraph(_texto(" · ".join(marcadores)), estilos["celula_menor"]))
    if item.fatores_score_busca:
        fatores = " · ".join(f"{fator.regra}: +{fator.peso:g}" for fator in item.fatores_score_busca[:3])
        conteudo.append(
            Paragraph(
                _texto(f"Score de busca {item.score_busca:g}/100 ({item.score_busca_versao}) · {fatores}"),
                estilos["celula_menor"],
            )
        )
    conteudo.append(Paragraph(_texto(item.relevancia_rotulo), estilos["rotulo"]))
    return conteudo


def _celula_situacao(item: MarcaRelatorioItem, estilos: dict[str, ParagraphStyle]) -> list:
    conteudo = [Paragraph(_texto(item.situacao, "Situação não informada"), estilos["celula"])]
    if item.situacao_normalizada:
        conteudo.append(
            Paragraph(
                f"Leitura: {_texto(item.situacao_normalizada.replace('_', ' '))}",
                estilos["celula_menor"],
            )
        )
    return conteudo


def _celula_processo(item: MarcaRelatorioItem, estilos: dict[str, ParagraphStyle]) -> list:
    numero = _texto(item.numero)
    if item.url_busca_oficial:
        numero = f'<link href="{_texto(item.url_busca_oficial)}" color="#1F6C34">{numero}</link>'
    conteudo = [Paragraph(numero, estilos["celula"])]
    if item.ultima_rpi:
        conteudo.append(
            Paragraph(
                f"RPI {item.ultima_rpi} · {_formatar_data(item.data_ultima_rpi)}",
                estilos["celula_menor"],
            )
        )
    return conteudo


def gerar_pdf_relatorio(relatorio: RelatorioMarcaResponse, *, incluir_ocorrencias: bool = True) -> bytes:
    """Monta o PDF do relatório de marcas e devolve os bytes.

    O resumo entregue ao cliente usa ``incluir_ocorrencias=False`` e termina
    ao final da primeira página. O detalhamento permanece reservado ao
    relatório completo gerado no Centro de Operações.
    """
    estilos = _estilos()
    if not incluir_ocorrencias:
        estilos["secao"].spaceBefore = 5
        estilos["secao"].spaceAfter = 3
        estilos["celula"].fontSize = 7.5
        estilos["celula"].leading = 9
        estilos["celula_menor"].fontSize = 6.7
        estilos["celula_menor"].leading = 8
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=(16 if incluir_ocorrencias else 12) * mm,
        bottomMargin=(16 if incluir_ocorrencias else 12) * mm,
        title=f"Relatório de pesquisa de anterioridade — {relatorio.marca}",
        author="Zé Registra",
    )

    emitido = _formatar_data(relatorio.gerado_em or relatorio.criado_em)
    base_rpi = f"RPI {relatorio.ultima_rpi}" if relatorio.ultima_rpi else "Não informado"
    matriz = (
        "validada por especialista"
        if relatorio.matriz_afinidade_status == "validada"
        else "inicial (pendente de validação)"
    )

    personagem = ReportImage(str(_CAMINHO_PERSONAGEM), width=15 * mm, height=27 * mm)
    cabecalho_marca = Table(
        [
            [
                personagem,
                [
                    Paragraph("Zé Registra®", estilos["marca"]),
                    Paragraph("Pesquisa e inteligência para marcas", estilos["sub"]),
                ],
            ]
        ],
        colWidths=[20 * mm, 158 * mm],
    )
    cabecalho_marca.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, -1), 1, _COR_CABECALHO),
            ]
        )
    )
    destaque = Table(
        [[Paragraph("RELATÓRIO INDICATIVO · SEÇÃO V — MARCAS", estilos["rotulo"])]],
        colWidths=[178 * mm],
    )
    destaque.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _COR_MENTA),
                ("BOX", (0, 0), (-1, -1), 0.5, _COR_LINHA),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story: list = [
        cabecalho_marca,
        Spacer(1, 8 if not incluir_ocorrencias else 12),
        destaque,
        Spacer(1, 10 if not incluir_ocorrencias else 15),
        Paragraph("Pesquisa de anterioridade de marca", estilos["titulo"]),
        Paragraph(_texto(relatorio.marca), estilos["marca"]),
        Paragraph(f"Atividade informada: {_texto(relatorio.atividade)}", estilos["sub"]),
        Spacer(1, 4 if not incluir_ocorrencias else 8),
        Paragraph(
            f"Emitido em {emitido} &nbsp;·&nbsp; Versão {relatorio.versao} "
            f"({_texto(relatorio.schema_versao)}) &nbsp;·&nbsp; "
            f"Base atualizada até {_texto(base_rpi)}",
            estilos["sub"],
        ),
        Spacer(1, 8 if not incluir_ocorrencias else 14),
    ]

    # Métricas
    metricas = Table(
        [
            [
                Paragraph("Total localizado", estilos["cabecalho"]),
                Paragraph(
                    "Exibidos" if incluir_ocorrencias else "Análise técnica",
                    estilos["cabecalho"],
                ),
                Paragraph("Matriz de afinidade", estilos["cabecalho"]),
            ],
            [
                Paragraph(str(relatorio.total), estilos["celula"]),
                Paragraph(
                    str(relatorio.limite_exibido)
                    if incluir_ocorrencias
                    else (
                        f"{relatorio.risco_pontuacao} pontos · risco "
                        f"{_texto((relatorio.risco_nivel or '').replace('_', ' '))}"
                        if relatorio.risco_pontuacao is not None
                        else "Pontuação ainda indisponível"
                    ),
                    estilos["celula"],
                ),
                Paragraph(_texto(matriz), estilos["celula"]),
            ],
        ],
        colWidths=[45 * mm, 45 * mm, 88 * mm],
    )
    metricas.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _COR_CABECALHO),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("BOX", (0, 0), (-1, -1), 0.4, _COR_LINHA),
                ("LINEBELOW", (0, 0), (-1, 0), 0.4, _COR_LINHA),
            ]
        )
    )
    story.append(metricas)
    if not incluir_ocorrencias and relatorio.risco_pontuacao is not None:
        story.append(
            Paragraph(
                "A pontuação acima mede risco de conflito: quanto maior o valor, maior a "
                "atenção necessária. Ela não é uma probabilidade de registro.",
                estilos["celula_menor"],
            )
        )

    if relatorio.analise_consolidada:
        analise = relatorio.analise_consolidada
        apresentacao = apresentacao_analise(analise)
        revisao = analise.get("revisao") or {}
        estado = "VALIDADO POR ESPECIALISTA" if incluir_ocorrencias and revisao.get("validada") else "PRELIMINAR — REVISÃO HUMANA NECESSÁRIA"
        story.append(Paragraph("Análise de registrabilidade", estilos["secao"]))
        story.append(Paragraph(_texto(estado), estilos["celula"]))
        parecer_humano = analise.get("parecer_humano")
        if incluir_ocorrencias and parecer_humano and parecer_humano.get("observacoes"):
            # Justificativa do especialista para o parecer de validação -- só no
            # relatório interno (incluir_ocorrencias=True), nunca no público.
            story.append(Paragraph("Parecer do especialista", estilos["secao"]))
            story.append(Paragraph(_texto(parecer_humano["observacoes"]), estilos["celula"]))
        _cor_situacao = {
            "favoravel": "#176a3a",
            "desfavoravel": "#8d2923",
            "inconclusiva": "#775512",
        }.get(apresentacao["situacao"]["codigo"], "#31483c")
        story.append(Paragraph(
            f'Situação da análise automática: <font color="{_cor_situacao}">'
            f'{_texto(apresentacao["situacao"]["rotulo"])}</font>',
            estilos["marca"],
        ))
        story.append(Paragraph(_texto(apresentacao["situacao"]["explicacao"]), estilos["celula"]))
        story.append(Paragraph(_texto(analise.get("titulo")), estilos["marca"]))
        story.append(Paragraph(_texto(analise.get("recomendacao")), estilos["celula"]))
        resultado = analise.get("conclusao_preliminar") or {}
        for titulo, chave in (("Possíveis impedimentos", "impedimentos"), ("Pontos de atenção", "pontos_atencao")):
            achados = apresentacao[chave]
            if achados:
                story.append(Paragraph(f"{titulo} ({len(achados)})", estilos["secao"]))
                for achado in achados:
                    story.append(Paragraph(
                        f"<b>{_texto(achado['criterio'])}:</b> {_texto(achado['justificativa'])}",
                        estilos["celula"],
                    ))
                    if incluir_ocorrencias:
                        if achado["evidencia"]:
                            story.append(Paragraph(f"Evidência: {_texto(achado['evidencia'])}", estilos["celula_menor"]))
                        if achado["referencia"]:
                            story.append(Paragraph(f"Referência: {_texto(achado['referencia'])}", estilos["celula_menor"]))
        for motivo in apresentacao["fundamentos_tecnicos"]:
            story.append(Paragraph(f"• {_texto(motivo)}", estilos["celula"]))
        story.append(Paragraph(_texto(resultado.get("aviso")), estilos["celula_menor"]))
        if incluir_ocorrencias:
            for pendencia in analise.get("pendencias", []):
                story.append(Paragraph(
                    f"<b>{_texto(pendencia.get('criterio'))}:</b> {_texto(pendencia.get('descricao'))}",
                    estilos["celula"],
                ))
            estatistica = analise.get("estatistica") or {}
            story.append(Paragraph("Apoio estatístico (informação do sistema)", estilos["secao"]))
            story.append(Paragraph(_texto(apresentacao["mensagem_apoio"]), estilos["celula_menor"]))
            estimativa = estatistica.get("estimativa") or {}
            if estatistica.get("disponivel") and estimativa.get("probabilidade_deferimento") is not None:
                story.append(Paragraph(
                    f"Indicador histórico: {estimativa['probabilidade_deferimento']:.0%}.",
                    estilos["celula"],
                ))
            # O parecer humano (classificação, observações internas do especialista)
            # e' propositalmente omitido daqui -- este relatorio completo e' entregue
            # ao cliente como prova da pesquisa, e o parecer e' controle interno.
            # O selo de validação abaixo continua aparecendo (é um sinal de
            # confiança para o cliente, não conteúdo interno).
            if revisao.get("validada"):
                story.append(Paragraph(
                    f"Validado por {_texto(revisao.get('validado_por'))} em {_texto(revisao.get('validado_em'))}.",
                    estilos["celula_menor"],
                ))
        story.append(Paragraph(
            f"Análise {_texto(analise.get('versao_motor'))} · versão do relatório {relatorio.versao}.",
            estilos["celula_menor"],
        ))

    if relatorio.conclusao and not relatorio.analise_consolidada:
        story.append(Paragraph("Conclusão indicativa", estilos["secao"]))
        conclusao = Table(
            [
                [Paragraph(_texto(relatorio.conclusao.titulo), estilos["marca"])],
                [Paragraph(_texto(relatorio.conclusao.resumo), estilos["celula"])],
                [
                    Paragraph(
                        (
                            "Revisão humana recomendada."
                            if relatorio.conclusao.revisao_humana_recomendada
                            else "Revisão humana recomendada antes de qualquer decisão de depósito."
                        ),
                        estilos["celula_menor"],
                    )
                ],
            ],
            colWidths=[178 * mm],
        )
        conclusao.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _COR_MENTA),
                    ("BOX", (0, 0), (-1, -1), 0.5, _COR_LINHA),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(conclusao)

    if incluir_ocorrencias and relatorio.principais_conflitos_risco:
        story.append(Paragraph("Composição do risco", estilos["secao"]))
        story.append(
            Paragraph(
                "Detalhamento das regras que compuseram a pontuação de risco, por processo "
                "conflitante — uso interno para embasar a conversa comercial e técnica.",
                estilos["celula_menor"],
            )
        )
        for conflito in relatorio.principais_conflitos_risco[:8]:
            numero = _texto(conflito.get("numero"))
            titulo = _texto(conflito.get("titulo"), "Sem título")
            pontuacao = conflito.get("pontuacao")
            nivel = _texto(str(conflito.get("nivel") or "").replace("_", " "))
            fatores = conflito.get("fatores") or []
            linhas_fatores = "".join(
                f"• <b>{_texto(fator.get('regra'))}:</b> "
                f"{'+' if (fator.get('pontos') or 0) >= 0 else ''}{fator.get('pontos')} pontos<br/>"
                for fator in fatores
            )
            story.append(
                Paragraph(
                    f"<b>{numero} — {titulo}</b> ({pontuacao} pontos · risco {nivel})<br/>{linhas_fatores}",
                    estilos["celula"],
                )
            )
            story.append(Spacer(1, 4))

    if relatorio.prognostico_registrabilidade and not relatorio.analise_consolidada:
        prognostico = relatorio.prognostico_registrabilidade
        rotulos = {
            "favoravel": "Favorável",
            "atencao": "Atenção",
            "desfavoravel": "Desfavorável",
        }
        story.append(Paragraph("Triagem determinística de registrabilidade", estilos["secao"]))
        motivos = "".join(
            f"• <b>{_texto(item.criterio)}:</b> {_texto(item.conclusao)} ({_texto(item.referencia)})<br/>"
            for item in prognostico.motivos
        )
        pendencias = " · ".join(_texto(item) for item in prognostico.pendencias)
        tendencia = _texto(rotulos.get(prognostico.veredito, prognostico.veredito))
        cores_veredito = {
            "favoravel": "#146c3f",
            "atencao": "#8a6400",
            "desfavoravel": "#9c1f18",
        }
        cor = cores_veredito.get(prognostico.veredito, "#10251d")
        partes = [
            f'<b><font color="{cor}">Leitura técnica: {tendencia}</font></b> — {_texto(prognostico.titulo)}<br/>',
            f"{_texto(prognostico.resumo)}<br/>",
        ]
        if motivos:
            partes.append(f"<b>Motivos identificados:</b><br/>{motivos}")
        if pendencias:
            partes.append(f"<b>Ainda dependem de avaliação:</b> {pendencias}.<br/>")
        partes.append(_texto(prognostico.ressalva))
        story.append(Paragraph("".join(partes), estilos["celula"]))

    if relatorio.qualidade_base:
        qualidade = relatorio.qualidade_base
        status_qualidade = _rotulo_enum(qualidade.status).title()
        story.append(Paragraph("Qualidade e cobertura da base", estilos["secao"]))
        cobertura = (
            f"{_formatar_data(qualidade.deposito_mais_antigo)} a {_formatar_data(qualidade.deposito_mais_recente)}"
        )
        story.append(
            Paragraph(
                f"<b>Status:</b> {_texto(status_qualidade)} &nbsp;·&nbsp; "
                f"<b>Última RPI:</b> {_texto(qualidade.ultima_rpi)} "
                f"({_formatar_data(qualidade.data_ultima_rpi)}) &nbsp;·&nbsp; "
                f"<b>Cobertura de depósitos:</b> {cobertura}",
                estilos["celula"],
            )
        )
        for aviso in qualidade.avisos:
            story.append(Paragraph(f"- {_texto(aviso)}", estilos["celula_menor"]))

    if relatorio.evidencias_busca:
        evidencia = relatorio.evidencias_busca
        story.append(Paragraph("Evidências e critérios da pesquisa", estilos["secao"]))
        story.append(
            Paragraph(
                f"<b>Expressão completa:</b> {_texto(evidencia.expressao_completa)}<br/>"
                f"<b>Radicais:</b> {_texto(', '.join(evidencia.radicais))}<br/>"
                f"<b>Variações:</b> {_texto(', '.join(evidencia.variacoes))}<br/>"
                f"<b>Contagens:</b> {evidencia.nomes_identicos} nome(s) idêntico(s), "
                f"{evidencia.expressoes_completas} com a expressão completa e "
                f"{evidencia.ocorrencias_por_radical} por radical/variação.<br/>"
                f"<b>Algoritmo:</b> {_texto(evidencia.versao_algoritmo)}",
                estilos["celula"],
            )
        )

    # Classes de atividade sugeridas (Nice)
    story.append(Paragraph("Classes de atividade sugeridas (Nice)", estilos["secao"]))
    if relatorio.classes_atividade:
        for classe in relatorio.classes_atividade:
            termos = ", ".join(classe.termos_encontrados) or "—"
            story.append(
                Paragraph(
                    f"<b>Classe {_texto(classe.codigo)}</b> — {_texto(classe.titulo)} ({_texto(classe.tipo)})",
                    estilos["celula"],
                )
            )
            story.append(Paragraph(f"Identificada por: {_texto(termos)}", estilos["celula_menor"]))
    else:
        story.append(
            Paragraph(
                "Não foi possível sugerir classes com segurança a partir da atividade informada.",
                estilos["celula"],
            )
        )

    story.append(Spacer(1, 4 if not incluir_ocorrencias else 10))
    story.append(Paragraph(_DISCLAIMER, estilos["rodape"]))

    # O cliente recebe somente o resumo executivo da primeira página. As
    # ocorrências e a rastreabilidade detalhada pertencem ao relatório
    # completo, liberado pela equipe no Centro de Operações.
    if incluir_ocorrencias:
        story.append(Paragraph("Ocorrências encontradas", estilos["secao"]))
    if incluir_ocorrencias and not relatorio.itens:
        story.append(
            Paragraph(
                "Nenhuma ocorrência foi localizada com os parâmetros informados.",
                estilos["celula"],
            )
        )
    elif incluir_ocorrencias:
        linhas = [
            [
                Paragraph("Nº do processo", estilos["cabecalho"]),
                Paragraph("Marca / Titular", estilos["cabecalho"]),
                Paragraph("Situação", estilos["cabecalho"]),
                Paragraph("Classes", estilos["cabecalho"]),
                Paragraph("Afinidade", estilos["cabecalho"]),
            ]
        ]
        for item in relatorio.itens:
            linhas.append(
                [
                    _celula_processo(item, estilos),
                    _celula_marca(item, estilos),
                    _celula_situacao(item, estilos),
                    Paragraph(_texto(_classificacoes(item)), estilos["celula"]),
                    Paragraph(_texto(_afinidade(item)), estilos["celula"]),
                ]
            )
        tabela = Table(
            linhas,
            colWidths=[26 * mm, 62 * mm, 40 * mm, 20 * mm, 30 * mm],
            repeatRows=1,
        )
        tabela.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _COR_CABECALHO),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _COR_ALTERNADA]),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, _COR_LINHA),
                ]
            )
        )
        story.append(tabela)
        if relatorio.total > relatorio.limite_exibido:
            story.append(Spacer(1, 6))
            story.append(
                Paragraph(
                    f"O relatório apresenta as {relatorio.limite_exibido} ocorrências mais "
                    f"relevantes de um total de {relatorio.total} localizadas.",
                    estilos["celula_menor"],
                )
            )

    if incluir_ocorrencias:
        story.append(Paragraph("Rastreabilidade", estilos["secao"]))
        story.append(
            Paragraph(
                "Cada número identifica o processo e, na versão web, abre sua página detalhada. "
                "No PDF, o link conduz ao BuscaWeb; a conferência oficial também deve considerar "
                "a Seção V da RPI. "
                f"Hash desta versão: {_texto(relatorio.conteudo_hash or 'não informado')}.",
                estilos["rodape"],
            )
        )
    doc.build(story, onFirstPage=_decorar_pagina, onLaterPages=_decorar_pagina)
    return buffer.getvalue()


def gerar_pdf_resumo_cliente(relatorio: RelatorioMarcaResponse) -> bytes:
    """Gera o resumo público de uma página, sem a lista de ocorrências."""
    return gerar_pdf_relatorio(relatorio, incluir_ocorrencias=False)


_DISCLAIMER_PROCESSO_MONITORADO = (
    "Relatório de acompanhamento gerado a partir de publicações da Revista da "
    "Propriedade Industrial (RPI) importadas pela plataforma. A situação e a fase "
    "exibidas refletem a última publicação processada -- não substitui a consulta "
    "oficial ao INPI."
)


_DISCLAIMER_ATUALIZACOES = (
    "Relatório gerado a partir das atualizações publicadas na Central de Atualizações da "
    "plataforma. Cada versão exibe apenas o impacto para o usuário, sem detalhes técnicos "
    "internos de implementação."
)


def gerar_pdf_atualizacoes(dados: dict) -> bytes:
    """Relatório simples (uma tabela) do histórico de versões publicadas, para
    o cliente baixar e guardar -- achado do usuário (12/09/2026): a Central de
    Atualizações só podia ser lida na tela, sem forma de exportar um resumo.

    `dados` esperado:
    {
        "organizacao": str, "gerado_em": datetime, "gerado_por": str,
        "de": date | None, "ate": date | None,
        "itens": [{"versao": str, "titulo": str, "classificacao": str,
                    "impacto_usuario": str, "implantada_em": datetime}, ...],
    }
    """
    estilos = _estilos()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Central de atualizações",
        author="Zé Registra",
    )

    personagem = ReportImage(str(_CAMINHO_PERSONAGEM), width=15 * mm, height=27 * mm)
    cabecalho_marca = Table(
        [
            [
                personagem,
                [
                    Paragraph("Zé Registra®", estilos["marca"]),
                    Paragraph("Pesquisa e inteligência para marcas", estilos["sub"]),
                ],
            ]
        ],
        colWidths=[20 * mm, 158 * mm],
    )
    cabecalho_marca.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, -1), 1, _COR_CABECALHO),
            ]
        )
    )
    destaque = Table(
        [[Paragraph("RELATÓRIO · CENTRAL DE ATUALIZAÇÕES", estilos["rotulo"])]],
        colWidths=[178 * mm],
    )
    destaque.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _COR_MENTA),
                ("BOX", (0, 0), (-1, -1), 0.5, _COR_LINHA),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )

    if dados.get("de") or dados.get("ate"):
        periodo = f"Período: {_formatar_data(dados.get('de')) if dados.get('de') else 'início'} a {_formatar_data(dados.get('ate')) if dados.get('ate') else 'hoje'}"
    else:
        periodo = "Período: todas as atualizações publicadas"

    story: list = [
        cabecalho_marca,
        Spacer(1, 12),
        destaque,
        Spacer(1, 15),
        Paragraph("Histórico de atualizações", estilos["titulo"]),
        Paragraph(_texto(dados.get("organizacao"), "Organização não informada"), estilos["marca"]),
        Paragraph(periodo, estilos["sub"]),
        Paragraph(
            f"Emitido em {_formatar_data(dados['gerado_em'])} por {_texto(dados.get('gerado_por'))}",
            estilos["sub"],
        ),
        Spacer(1, 14),
    ]

    itens = dados.get("itens") or []
    if itens:
        linhas: list[list] = [
            [
                Paragraph("Versão", estilos["cabecalho"]),
                Paragraph("Data", estilos["cabecalho"]),
                Paragraph("Tipo", estilos["cabecalho"]),
                Paragraph("O que mudou", estilos["cabecalho"]),
            ]
        ]
        for item in itens:
            titulo_e_impacto = [Paragraph(_texto(item["titulo"]), estilos["celula"])]
            if item.get("impacto_usuario"):
                titulo_e_impacto.append(Paragraph(_texto(item["impacto_usuario"]), estilos["celula_menor"]))
            linhas.append(
                [
                    Paragraph(_texto(item["versao"]), estilos["celula"]),
                    Paragraph(_formatar_data(item["implantada_em"]), estilos["celula"]),
                    Paragraph(_rotulo_tipo_atualizacao(item["classificacao"]), estilos["celula"]),
                    titulo_e_impacto,
                ]
            )
        tabela = Table(linhas, colWidths=[20 * mm, 26 * mm, 28 * mm, 104 * mm], repeatRows=1)
        estilo_tabela = [
            ("BACKGROUND", (0, 0), (-1, 0), _COR_CABECALHO),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, _COR_LINHA),
        ]
        for indice in range(1, len(linhas)):
            if indice % 2 == 0:
                estilo_tabela.append(("BACKGROUND", (0, indice), (-1, indice), _COR_ALTERNADA))
        tabela.setStyle(TableStyle(estilo_tabela))
        story.append(tabela)
    else:
        story.append(Paragraph("Nenhuma atualização publicada nesse período.", estilos["celula"]))

    story.append(Spacer(1, 18))
    story.append(Paragraph(_DISCLAIMER_ATUALIZACOES, estilos["rodape"]))

    doc.build(story, onFirstPage=_decorar_pagina, onLaterPages=_decorar_pagina)
    return buffer.getvalue()


_ROTULOS_TIPO_ATUALIZACAO = {
    "critica": "Correção crítica",
    "correcao": "Correção",
    "melhoria": "Melhoria",
    "funcionalidade": "Nova funcionalidade",
}


def _rotulo_tipo_atualizacao(valor: str) -> str:
    return _ROTULOS_TIPO_ATUALIZACAO.get(valor, valor)


def gerar_pdf_processo_monitorado(dados: dict) -> bytes:
    """Relatório de acompanhamento de um processo monitorado, para envio ao
    cliente -- achado do usuário (08/09/2026): faltava uma forma de mostrar
    ao cliente a fase atual do processo, sem precisar dar acesso ao sistema
    interno.

    `dados` esperado (dict simples, montado por quem chama -- ver
    app/api/carteira.py):
    {
        "numero": str, "titulo": str | None, "tipo": str,
        "data_deposito": date | None, "situacao": str | None,
        "titulares": list[str], "procurador": str | None,
        "empresa": str | None, "responsavel": str | None,
        "status": str, "etapa_kanban_label": str,
        "movimentacoes": [{"data_rpi": date, "numero_rpi": int, "descricao": str}, ...],
        "observacoes_relatorio": str | None,  # digitado na hora, só para este PDF
        "gerado_em": datetime, "gerado_por": str,
    }
    """
    estilos = _estilos()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Acompanhamento do processo {dados['numero']}",
        author="Zé Registra",
    )

    personagem = ReportImage(str(_CAMINHO_PERSONAGEM), width=15 * mm, height=27 * mm)
    cabecalho_marca = Table(
        [
            [
                personagem,
                [
                    Paragraph("Zé Registra®", estilos["marca"]),
                    Paragraph("Pesquisa e inteligência para marcas", estilos["sub"]),
                ],
            ]
        ],
        colWidths=[20 * mm, 158 * mm],
    )
    cabecalho_marca.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, -1), 1, _COR_CABECALHO),
            ]
        )
    )
    destaque = Table(
        [[Paragraph("RELATÓRIO DE ACOMPANHAMENTO · PROCESSO MONITORADO", estilos["rotulo"])]],
        colWidths=[178 * mm],
    )
    destaque.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _COR_MENTA),
                ("BOX", (0, 0), (-1, -1), 0.5, _COR_LINHA),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )

    titulares = " · ".join(dados["titulares"]) if dados["titulares"] else "Titular não informado"
    story: list = [
        cabecalho_marca,
        Spacer(1, 12),
        destaque,
        Spacer(1, 15),
        Paragraph("Acompanhamento de processo", estilos["titulo"]),
        Paragraph(_texto(dados.get("titulo"), "Título não informado pelo INPI"), estilos["marca"]),
        Paragraph(f"Processo nº {_texto(dados['numero'])} · {_texto(titulares)}", estilos["sub"]),
        Spacer(1, 8),
        Paragraph(
            f"Emitido em {_formatar_data(dados['gerado_em'])} &nbsp;·&nbsp; "
            f"Depósito: {_formatar_data(dados.get('data_deposito'))}",
            estilos["sub"],
        ),
        Spacer(1, 14),
    ]

    status_atual = Table(
        [
            [
                Paragraph("Situação no INPI", estilos["cabecalho"]),
                Paragraph("Fase no escritório", estilos["cabecalho"]),
                Paragraph("Responsável", estilos["cabecalho"]),
            ],
            [
                Paragraph(_texto(dados.get("situacao"), "Não informada"), estilos["celula"]),
                Paragraph(_texto(dados["etapa_kanban_label"]), estilos["celula"]),
                Paragraph(_texto(dados.get("responsavel"), "Não atribuído"), estilos["celula"]),
            ],
        ],
        colWidths=[59 * mm, 59 * mm, 60 * mm],
    )
    status_atual.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _COR_CABECALHO),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("GRID", (0, 0), (-1, -1), 0.5, _COR_LINHA),
                ("BACKGROUND", (0, 1), (-1, 1), _COR_ALTERNADA),
            ]
        )
    )
    story.append(status_atual)
    story.append(Spacer(1, 16))

    observacoes = dados.get("observacoes_relatorio")
    if observacoes:
        story.append(Paragraph("Observações", estilos["secao"]))
        story.append(Paragraph(_texto(observacoes), estilos["celula"]))
        story.append(Spacer(1, 14))

    story.append(Paragraph("Histórico de movimentações", estilos["secao"]))
    movimentacoes = dados.get("movimentacoes") or []
    if movimentacoes:
        linhas: list[list] = [
            [
                Paragraph("RPI", estilos["cabecalho"]),
                Paragraph("Data", estilos["cabecalho"]),
                Paragraph("Descrição", estilos["cabecalho"]),
            ]
        ]
        for item in movimentacoes:
            linhas.append(
                [
                    Paragraph(_texto(item["numero_rpi"]), estilos["celula"]),
                    Paragraph(_formatar_data(item["data_rpi"]), estilos["celula"]),
                    Paragraph(_texto(item["descricao"]), estilos["celula"]),
                ]
            )
        tabela_movimentacoes = Table(linhas, colWidths=[20 * mm, 28 * mm, 130 * mm], repeatRows=1)
        estilo_tabela = [
            ("BACKGROUND", (0, 0), (-1, 0), _COR_CABECALHO),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, _COR_LINHA),
        ]
        for indice in range(1, len(linhas)):
            if indice % 2 == 0:
                estilo_tabela.append(("BACKGROUND", (0, indice), (-1, indice), _COR_ALTERNADA))
        tabela_movimentacoes.setStyle(TableStyle(estilo_tabela))
        story.append(tabela_movimentacoes)
    else:
        story.append(Paragraph("Nenhuma movimentação publicada na RPI até o momento.", estilos["celula"]))

    story.append(Spacer(1, 18))
    story.append(Paragraph(_DISCLAIMER_PROCESSO_MONITORADO, estilos["rodape"]))

    doc.build(story, onFirstPage=_decorar_pagina, onLaterPages=_decorar_pagina)
    return buffer.getvalue()
