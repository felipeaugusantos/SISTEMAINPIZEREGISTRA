import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

from sqlalchemy import case, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ClassificacaoMarca, Processo, TipoProcesso, Titular
from app.search_model import resultado_busca_exige_revisao_humana
from app.search_ranking import ScoreBusca, calcular_score_nominativo, configuracao_ranking

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
VERSAO_BUSCA = "busca-marcas-4.0"
LIMIAR_TRIGRAMA = 0.30


def chave_fonetica(valor: str) -> str:
    """Chave fonética determinística, explicável e independente de extensões SQL."""
    texto = normalizar_texto(valor).replace(" ", "")
    if not texto:
        return ""
    texto = texto.translate(str.maketrans({"K": "C", "Q": "C", "Y": "I", "W": "V", "Z": "S", "X": "S"}))
    texto = texto.replace("PH", "F").replace("CH", "X").replace("LH", "L").replace("NH", "N")
    texto = re.sub(r"[AEIOU]+", "A", texto)
    texto = re.sub(r"(.)\1+", r"\1", texto)
    return texto[:16]


@dataclass(frozen=True, slots=True)
class OcorrenciaBusca:
    processo: Processo
    criterios: list[str]
    score: ScoreBusca


def normalizar_texto(valor: str) -> str:
    sem_acentos = "".join(
        caractere for caractere in unicodedata.normalize("NFKD", valor) if not unicodedata.combining(caractere)
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
        # O truncamento nunca deixa o radical com menos de 5 letras: um radical de
        # 4 letras (ex.: "SINAL" -> "SINA") colide por acaso com siglas e palavras
        # nao relacionadas (SINAFRESP, SINAP, SINAIT...), gerando falso positivo de
        # conflito tanto na busca quanto no motor de risco que reusa esse criterio.
        if len(palavra) >= 8:
            radical = palavra[:-2]
        elif len(palavra) >= 6:
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
    elif titulo_normalizado in palavras:
        # A marca candidata inteira e' uma das palavras distintivas da busca (ex.:
        # candidata "DHF" para a busca "INSTITUTO DHF"). Sinal de conflito real,
        # mesmo quando a similaridade de trigrama da frase inteira e' baixa --
        # sem esse criterio, marcas compostas cujo elemento distintivo e curto
        # perdem para radicais genericos (tipo "INSTITU") que aparecem em
        # centenas de marcas nao relacionadas.
        criterios.append("Elemento nominativo isolado")

    if any(palavra in titulo_normalizado for palavra in palavras):
        criterios.append("Elemento do nome")
    if any(radical in titulo_normalizado for radical in radicais):
        criterios.append("Radical semelhante")
    if any(variacao in titulo_normalizado for variacao in variacoes):
        criterios.append("Variação ortográfica ou fonética")
    if SequenceMatcher(None, titulo_normalizado, marca_normalizada).ratio() >= 0.55:
        criterios.append("Semelhança global do nome")

    return criterios[:3] or ["Aproximação nominativa"]


async def buscar_marcas(
    session: AsyncSession,
    marca: str,
    tipo_pesquisa: str,
    classe_nice: str | None,
    limite: int = 200,
    estrategia: str = "completa",
    titular: str | None = None,
    situacao: str | None = None,
    data_inicio: date | None = None,
    data_fim: date | None = None,
    apresentacao: str | None = None,
    codigos_viena: list[str] | None = None,
) -> tuple[int, list[OcorrenciaBusca], dict]:
    # A expressão deve ser idêntica ao índice GIN trigram para evitar varredura
    # completa dos milhões de processos.
    titulo_normalizado = func.immutable_unaccent(Processo.titulo)
    marca_limpa = marca.strip()
    frase = f"%{marca_limpa}%"
    filtro_frase = titulo_normalizado.ilike(func.immutable_unaccent(frase))
    filtro_identico = func.lower(titulo_normalizado) == func.lower(func.immutable_unaccent(marca_limpa))
    filtro_trigrama = titulo_normalizado.bool_op("%")(func.immutable_unaccent(marca_limpa))

    radicais = extrair_radicais(marca_limpa)
    variacoes = gerar_variacoes(marca_limpa)
    termos_ampliados = list(dict.fromkeys([*radicais, *variacoes]))
    filtros_ampliados = [titulo_normalizado.ilike(func.immutable_unaccent(f"%{termo}%")) for termo in termos_ampliados]

    # Uma marca cujo titulo inteiro e' identico a uma das palavras distintivas da
    # busca (ex.: candidata "DHF" para a busca "INSTITUTO DHF") e' um sinal muito
    # mais forte de conflito real do que a similaridade de trigrama da frase inteira
    # sugere -- e nao sofre o falso-positivo de radicais genericos/curtos (tipo
    # "INSTITU") que aparecem em milhares de marcas nao relacionadas.
    palavras_busca = [palavra for palavra in extrair_palavras(marca_limpa) if len(palavra) >= 3]
    filtro_token_exato = (
        or_(*[func.lower(titulo_normalizado) == palavra.lower() for palavra in palavras_busca])
        if palavras_busca
        else literal(False)
    )

    estrategia = estrategia.lower().strip()
    if estrategia not in {
        "exata",
        "radical",
        "prefixo",
        "sufixo",
        "fonetica",
        "similaridade",
        "completa",
    }:
        raise ValueError("Estratégia de busca inválida")
    if estrategia == "exata" or tipo_pesquisa == "exata" or not filtros_ampliados:
        filtro_texto = filtro_frase
    elif tipo_pesquisa == "radical":
        filtro_texto = or_(*filtros_ampliados)
    elif estrategia == "prefixo":
        filtro_texto = titulo_normalizado.ilike(func.immutable_unaccent(f"{marca_limpa}%"))
    elif estrategia == "sufixo":
        filtro_texto = titulo_normalizado.ilike(func.immutable_unaccent(f"%{marca_limpa}"))
    elif estrategia in {"fonetica", "similaridade"}:
        filtro_texto = or_(filtro_frase, filtro_trigrama, *filtros_ampliados)
    else:
        filtro_texto = or_(filtro_frase, filtro_trigrama, *filtros_ampliados)

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
    if titular and titular.strip():
        filtros_base.append(Processo.titulares.any(Titular.nome.ilike(f"%{titular.strip()}%")))
    if situacao:
        situacao_limpa = situacao.strip().lower()
        filtros_base.append(
            or_(
                Processo.situacao_normalizada == situacao_limpa,
                Processo.situacao.ilike(f"%{situacao.strip()}%"),
            )
        )
    if data_inicio:
        filtros_base.append(Processo.data_deposito >= data_inicio)
    if data_fim:
        filtros_base.append(Processo.data_deposito <= data_fim)
    if apresentacao:
        filtros_base.append(Processo.apresentacao == apresentacao)
    if codigos_viena:
        filtros_base.append(
            exists(
                select(ClassificacaoMarca.id).where(
                    ClassificacaoMarca.processo_id == Processo.id,
                    ClassificacaoMarca.sistema.in_(["vienna", "viena"]),
                    ClassificacaoMarca.codigo.in_(codigos_viena),
                )
            )
        )
    filtros = [*filtros_base, filtro_texto]

    filtro_radical = or_(*filtros_ampliados) if filtros_ampliados else filtro_frase
    filtro_candidatos = or_(filtro_frase, filtro_radical, filtro_trigrama)
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
    similaridade_sql = func.similarity(titulo_normalizado, func.immutable_unaccent(marca_limpa))
    consulta = (
        select(Processo, similaridade_sql.label("similaridade_nominativa"))
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
                # Titulo inteiro da candidata == uma palavra distintiva da busca (ex.:
                # "DHF" para "INSTITUTO DHF") precisa furar a fila antes do corte do
                # LIMIT: e' um sinal de conflito muito mais forte que a similaridade
                # de trigrama da frase inteira sugere, sem o falso-positivo de
                # radicais genericos curtos que aparecem em milhares de marcas.
                (filtro_token_exato, 2),
                (filtro_radical, 3),
                else_=4,
            ),
            similaridade_sql.desc(),
            Processo.atualizado_em.desc(),
            Processo.numero,
        )
        .limit(limite)
    )
    candidatos = (await session.execute(consulta)).all()
    ocorrencias: list[OcorrenciaBusca] = []
    for processo, similaridade in candidatos:
        criterios = identificar_criterios(processo.titulo, marca_limpa)
        titulo_chave = chave_fonetica(processo.titulo or "")
        marca_chave = chave_fonetica(marca_limpa)
        if estrategia == "fonetica" and marca_chave and marca_chave in titulo_chave:
            criterios = ["Correspondência fonética", *criterios]
        if estrategia == "prefixo" and normalizar_texto(processo.titulo or "").startswith(
            normalizar_texto(marca_limpa)
        ):
            criterios = ["Prefixo correspondente", *criterios]
        if estrategia == "sufixo" and normalizar_texto(processo.titulo or "").endswith(normalizar_texto(marca_limpa)):
            criterios = ["Sufixo correspondente", *criterios]
        if apresentacao in {"mista", "figurativa"}:
            criterios = [f"Marca {apresentacao}", *criterios]
        classes_nice = {
            classificacao.codigo for classificacao in processo.classificacoes if classificacao.sistema == "nice"
        }
        score = calcular_score_nominativo(
            criterios=criterios,
            similaridade=float(similaridade or 0),
            processo=processo.numero,
            titulo=processo.titulo,
            mesma_classe=classe_nice if classe_nice in classes_nice else None,
            situacao_ativa=processo.relevancia_situacao in {"alta", "ativa"},
        )
        ocorrencias.append(OcorrenciaBusca(processo=processo, criterios=criterios, score=score))
    ocorrencias.sort(key=lambda item: (-item.score.total, item.processo.numero))
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
        "estrategia": estrategia,
        "filtros": {
            "titular": titular,
            "situacao": situacao,
            "data_inicio": data_inicio.isoformat() if data_inicio else None,
            "data_fim": data_fim.isoformat() if data_fim else None,
            "apresentacao": apresentacao,
            "nice": classe_nice,
            "viena": codigos_viena or [],
        },
        "estrategias_executadas": [
            "exata",
            "radical",
            "fonetica",
            "similaridade_trigrama",
        ],
        "termos_consultados": list(dict.fromkeys([normalizar_texto(marca_limpa), *radicais, *variacoes])),
        "limiar_trigrama": LIMIAR_TRIGRAMA,
        "ranking": configuracao_ranking(),
        **resultado_busca_exige_revisao_humana(),
    }
    return total, ocorrencias, evidencias
