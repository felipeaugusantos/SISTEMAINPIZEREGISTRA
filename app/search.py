import re
import unicodedata

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ClassificacaoMarca, Processo, TipoProcesso

PALAVRAS_IGNORADAS = {
    "A",
    "AS",
    "COM",
    "DA",
    "DAS",
    "DE",
    "DO",
    "DOS",
    "E",
    "EM",
    "O",
    "OS",
    "PARA",
}
MAX_VARIACOES_POR_PALAVRA = 8
MAX_VARIACOES_TOTAL = 24
VERSAO_BUSCA = "busca-marcas-2.0"


def normalizar_texto(valor: str) -> str:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", valor)
        if not unicodedata.combining(caractere)
    )
    return " ".join(re.findall(r"[A-Z0-9]+", sem_acentos.upper()))


def extrair_palavras(marca: str) -> list[str]:
    return [
        palavra
        for palavra in normalizar_texto(marca).split()
        if palavra not in PALAVRAS_IGNORADAS and len(palavra) >= 3
    ]


def extrair_radicais(marca: str) -> list[str]:
    radicais: list[str] = []
    for palavra in extrair_palavras(marca):
        if len(palavra) >= 7:
            radical = palavra[:-2]
        elif len(palavra) >= 5:
            radical = palavra[:-1]
        else:
            radical = palavra
        if radical not in radicais:
            radicais.append(radical)
    return radicais


def gerar_variacoes_palavra(palavra: str) -> list[str]:
    """Gera aproximações ortográficas comuns sem explosão combinatória."""
    palavra = normalizar_texto(palavra).replace(" ", "")
    if len(palavra) < 3:
        return []

    variacoes: list[str] = []

    def adicionar(valor: str) -> None:
        if len(valor) >= 3 and valor != palavra and valor not in variacoes:
            variacoes.append(valor)

    if palavra.startswith("F"):
        adicionar(f"PH{palavra[1:]}")
        adicionar(f"FH{palavra[1:]}")
    if palavra.startswith("PH") or palavra.startswith("FH"):
        adicionar(f"F{palavra[2:]}")
    if palavra.startswith("C"):
        adicionar(f"K{palavra[1:]}")
    if palavra.startswith("K"):
        adicionar(f"C{palavra[1:]}")

    adicionar(palavra.replace("QU", "K"))
    adicionar(palavra.replace("K", "C"))
    adicionar(palavra.replace("Y", "I"))
    adicionar(palavra.replace("W", "V"))

    sem_repeticoes = re.sub(r"(.)\1+", r"\1", palavra)
    adicionar(sem_repeticoes)

    if palavra.endswith("INHO") and len(palavra) > 5:
        base = palavra[:-4]
        adicionar(f"{base}O")
        if base.startswith("C"):
            adicionar(f"K{base[1:]}")

    return variacoes[:MAX_VARIACOES_POR_PALAVRA]


def gerar_variacoes(marca: str) -> list[str]:
    variacoes: list[str] = []
    palavras = extrair_palavras(marca)
    radicais = extrair_radicais(marca)
    for palavra in [*palavras, *radicais]:
        for variacao in gerar_variacoes_palavra(palavra):
            if variacao not in variacoes:
                variacoes.append(variacao)
    return variacoes[:MAX_VARIACOES_TOTAL]


def identificar_criterios(titulo: str | None, marca: str) -> list[str]:
    if not titulo:
        return ["Dados nominativos incompletos"]

    titulo_normalizado = normalizar_texto(titulo)
    marca_normalizada = normalizar_texto(marca)
    palavras = extrair_palavras(marca)
    radicais = extrair_radicais(marca)
    variacoes = gerar_variacoes(marca)
    criterios: list[str] = []

    if titulo_normalizado == marca_normalizada:
        criterios.append("Nome idêntico")
    elif marca_normalizada and marca_normalizada in titulo_normalizado:
        criterios.append("Expressão completa")

    if any(palavra in titulo_normalizado for palavra in palavras):
        criterios.append("Elemento do nome")
    if any(radical in titulo_normalizado for radical in radicais):
        criterios.append("Radical semelhante")
    if any(variacao in titulo_normalizado for variacao in variacoes):
        criterios.append("Variação ortográfica ou fonética")

    return criterios[:3] or ["Aproximação nominativa"]


async def buscar_marcas(
    session: AsyncSession,
    marca: str,
    tipo_pesquisa: str,
    classe_nice: str | None,
    limite: int = 200,
) -> tuple[int, list[tuple[Processo, list[str]]], dict]:
    # A expressão deve ser idêntica ao índice GIN trigram para evitar varredura
    # completa dos milhões de processos.
    titulo_normalizado = func.immutable_unaccent(Processo.titulo)
    marca_limpa = marca.strip()
    frase = f"%{marca_limpa}%"
    filtro_frase = titulo_normalizado.ilike(func.immutable_unaccent(frase))
    filtro_identico = func.lower(titulo_normalizado) == func.lower(
        func.immutable_unaccent(marca_limpa)
    )

    radicais = extrair_radicais(marca_limpa)
    variacoes = gerar_variacoes(marca_limpa)
    termos_ampliados = list(dict.fromkeys([*radicais, *variacoes]))
    filtros_ampliados = [
        titulo_normalizado.ilike(func.immutable_unaccent(f"%{termo}%"))
        for termo in termos_ampliados
    ]

    if tipo_pesquisa == "exata" or not filtros_ampliados:
        filtro_texto = filtro_frase
    elif tipo_pesquisa == "radical":
        filtro_texto = or_(*filtros_ampliados)
    else:
        filtro_texto = or_(filtro_frase, *filtros_ampliados)

    filtros_base = [Processo.tipo == TipoProcesso.MARCA]
    if classe_nice:
        filtros_base.append(
            exists(
                select(ClassificacaoMarca.id).where(
                    ClassificacaoMarca.processo_id == Processo.id,
                    ClassificacaoMarca.sistema == "nice",
                    ClassificacaoMarca.codigo == classe_nice,
                )
            )
        )
    filtros = [*filtros_base, filtro_texto]

    filtro_radical = or_(*filtros_ampliados) if filtros_ampliados else filtro_frase
    filtro_candidatos = or_(filtro_frase, filtro_radical)
    contagens = (
        await session.execute(
            select(
                func.count().filter(filtro_identico),
                func.count().filter(filtro_frase),
                func.count().filter(filtro_radical),
                func.count().filter(filtro_texto),
            )
            .select_from(Processo)
            .where(*filtros_base, filtro_candidatos)
        )
    ).one()

    total = int(contagens[3] or 0)
    consulta = (
        select(Processo)
        .where(*filtros)
        .options(
            selectinload(Processo.titulares),
            selectinload(Processo.classificacoes),
            selectinload(Processo.movimentacoes),
        )
        .order_by(
            case(
                (filtro_identico, 0),
                (filtro_frase, 1),
                else_=2,
            ),
            func.similarity(titulo_normalizado, func.immutable_unaccent(marca_limpa)).desc(),
            Processo.atualizado_em.desc(),
            Processo.numero,
        )
        .limit(limite)
    )
    processos = (await session.execute(consulta)).scalars().all()
    ocorrencias = [
        (processo, identificar_criterios(processo.titulo, marca_limpa)) for processo in processos
    ]
    evidencias = {
        "termo_original": marca_limpa,
        "expressao_completa": normalizar_texto(marca_limpa),
        "radicais": radicais,
        "variacoes": variacoes,
        "nomes_identicos": int(contagens[0] or 0),
        "expressoes_completas": int(contagens[1] or 0),
        "ocorrencias_por_radical": int(contagens[2] or 0),
        "criterios_considerados": [
            "nome idêntico",
            "expressão completa",
            "elementos nominativos",
            "radicais",
            "variações ortográficas e fonéticas",
            "situação do processo",
            "classes e afinidade da atividade",
            "alto renome",
        ],
        "versao_algoritmo": VERSAO_BUSCA,
    }
    return total, ocorrencias, evidencias
