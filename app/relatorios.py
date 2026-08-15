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
from reportlab.lib.enums import TA_LEFT
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

_COR_TINTA = colors.HexColor("#17231C")
_COR_SUAVE = colors.HexColor("#5B665F")
_COR_CABECALHO = colors.HexColor("#2F8F46")
_COR_CABECALHO_ESCURO = colors.HexColor("#1F6C34")
_COR_MENTA = colors.HexColor("#E8F5E9")
_COR_LINHA = colors.HexColor("#E4E8E5")
_COR_ALTERNADA = colors.HexColor("#F5F7F5")
_CAMINHO_PERSONAGEM = (
    Path(__file__).resolve().parent / "web" / "static" / "assets" / "personagem.png"
)
_FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")

_DISCLAIMER = (
    "Relatório meramente indicativo, gerado a partir de publicações da Revista da "
    "Propriedade Industrial (RPI) importadas pela plataforma. Não substitui a consulta "
    "oficial ao INPI nem a análise jurídica de registrabilidade, semelhança fonética ou "
    "afinidade mercadológica."
)


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
        fatores = " · ".join(
            f"{fator.regra}: +{fator.peso:g}" for fator in item.fatores_score_busca[:3]
        )
        conteudo.append(
            Paragraph(
                _texto(
                    f"Score de busca {item.score_busca:g}/100 ({item.score_busca_versao}) · "
                    f"{fatores}"
                ),
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


def gerar_pdf_relatorio(
    relatorio: RelatorioMarcaResponse, *, incluir_ocorrencias: bool = True
) -> bytes:
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

    if relatorio.conclusao:
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

    if relatorio.prognostico_registrabilidade:
        prognostico = relatorio.prognostico_registrabilidade
        rotulos = {
            "favoravel": "Favorável",
            "atencao": "Atenção",
            "desfavoravel": "Desfavorável",
        }
        story.append(Paragraph("Triagem determinística de registrabilidade", estilos["secao"]))
        motivos = "".join(
            f"• <b>{_texto(item.criterio)}:</b> {_texto(item.conclusao)} "
            f"({_texto(item.referencia)})<br/>"
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
            f'<b><font color="{cor}">Leitura técnica: {tendencia}</font></b> — '
            f"{_texto(prognostico.titulo)}<br/>",
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
            f"{_formatar_data(qualidade.deposito_mais_antigo)} a "
            f"{_formatar_data(qualidade.deposito_mais_recente)}"
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
                    f"<b>Classe {_texto(classe.codigo)}</b> — "
                    f"{_texto(classe.titulo)} ({_texto(classe.tipo)})",
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
