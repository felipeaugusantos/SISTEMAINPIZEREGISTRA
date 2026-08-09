import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClasseNiceCandidata:
    codigo: str
    titulo: str
    tipo: str
    termos_encontrados: tuple[str, ...]
    confianca: float = 0.0


# Caputs resumidos da NCL (13) 2026. As palavras-chave são apenas um índice
# inicial para converter a descrição leiga da atividade em classes candidatas.
CLASSES_NICE: dict[str, tuple[str, tuple[str, ...]]] = {
    "01": (
        "Produtos químicos para indústria, ciência e agricultura",
        ("quimico", "fertilizante", "adubo", "resina"),
    ),
    "02": (
        "Tintas, vernizes e produtos contra corrosão",
        ("tinta", "verniz", "corante", "pigmento"),
    ),
    "03": (
        "Cosméticos, perfumaria e produtos de limpeza",
        ("cosmetico", "perfume", "maquiagem", "sabonete", "limpeza"),
    ),
    "04": (
        "Óleos, combustíveis, lubrificantes e velas",
        ("combustivel", "lubrificante", "vela", "oleo industrial"),
    ),
    "05": (
        "Produtos farmacêuticos e preparações médicas",
        ("medicamento", "farmaceutico", "suplemento", "veterinario", "higiene"),
    ),
    "06": (
        "Metais comuns, ferragens e construções metálicas",
        ("metal", "ferragem", "serralheria", "estrutura metalica"),
    ),
    "07": (
        "Máquinas, motores e ferramentas mecânicas",
        ("maquina", "motor", "equipamento industrial", "ferramenta eletrica"),
    ),
    "08": ("Ferramentas e instrumentos manuais", ("ferramenta manual", "cutelaria", "barbeador")),
    "09": (
        "Software, eletrônicos e instrumentos científicos",
        ("software", "aplicativo", "eletronico", "computador", "tecnologia", "oculos"),
    ),
    "10": (
        "Aparelhos e instrumentos médicos",
        ("aparelho medico", "protese", "equipamento hospitalar", "odontologico"),
    ),
    "11": (
        "Iluminação, aquecimento, refrigeração e saneamento",
        ("iluminacao", "lampada", "ar condicionado", "refrigeracao", "aquecimento"),
    ),
    "12": (
        "Veículos e meios de transporte",
        ("veiculo", "automovel", "moto", "bicicleta", "barco"),
    ),
    "13": ("Armas, munições e fogos de artifício", ("arma", "municao", "fogos de artificio")),
    "14": ("Joias, relógios e metais preciosos", ("joia", "relogio", "bijuteria", "ouro", "prata")),
    "15": ("Instrumentos musicais", ("instrumento musical", "violao", "piano", "musica")),
    "16": (
        "Papelaria, impressos e materiais de escritório",
        ("papelaria", "livro", "impresso", "material escolar", "embalagem de papel"),
    ),
    "17": (
        "Borracha, plásticos e materiais isolantes",
        ("borracha", "plastico", "isolante", "mangueira"),
    ),
    "18": (
        "Couro, bolsas, malas e artigos para animais",
        ("bolsa", "mala", "mochila", "couro", "coleira"),
    ),
    "19": (
        "Materiais de construção não metálicos",
        ("material de construcao", "cimento", "madeira", "vidro", "ceramica"),
    ),
    "20": (
        "Móveis, espelhos e recipientes não metálicos",
        ("movel", "mobilia", "colchao", "espelho", "decoracao"),
    ),
    "21": (
        "Utensílios domésticos, cozinha e vidro",
        ("utensilio", "cozinha", "panela", "copo", "louca"),
    ),
    "22": ("Cordas, redes, tendas e matérias têxteis brutas", ("corda", "rede", "tenda", "lona")),
    "23": ("Fios para uso têxtil", ("fio textil", "linha de costura", "la")),
    "24": ("Tecidos e roupas de cama e mesa", ("tecido", "roupa de cama", "toalha", "cortina")),
    "25": (
        "Vestuário, calçados e chapelaria",
        ("roupa", "vestuario", "calcado", "sapato", "camiseta", "moda"),
    ),
    "26": (
        "Rendas, bordados e acessórios de costura",
        ("renda", "bordado", "botao", "costura", "acessorio de cabelo"),
    ),
    "27": (
        "Tapetes e revestimentos de pisos e paredes",
        ("tapete", "carpete", "revestimento de piso", "papel de parede"),
    ),
    "28": (
        "Jogos, brinquedos e artigos esportivos",
        ("jogo", "brinquedo", "esporte", "academia", "video game"),
    ),
    "29": (
        "Carnes, laticínios, frutas e alimentos processados",
        ("carne", "leite", "queijo", "fruta processada", "alimento congelado"),
    ),
    "30": (
        "Café, farinhas, pães, doces e condimentos",
        ("cafe", "farinha", "pao", "doce", "chocolate", "tempero"),
    ),
    "31": (
        "Produtos agrícolas, animais vivos e rações",
        ("agricola", "semente", "animal vivo", "racao", "fruta fresca"),
    ),
    "32": (
        "Cervejas e bebidas não alcoólicas",
        ("cerveja", "bebida nao alcoolica", "suco", "agua mineral", "energetico"),
    ),
    "33": (
        "Bebidas alcoólicas, exceto cervejas",
        ("vinho", "bebida alcoolica", "destilado", "licor"),
    ),
    "34": ("Tabaco e artigos para fumantes", ("tabaco", "cigarro", "vape", "isqueiro")),
    "35": (
        "Publicidade, gestão de negócios, varejo e comércio eletrônico",
        (
            "venda",
            "varejo",
            "atacado",
            "loja",
            "comercio",
            "marketplace",
            "publicidade",
            "marketing",
        ),
    ),
    "36": (
        "Serviços financeiros, seguros e imobiliários",
        ("financeiro", "banco", "credito", "seguro", "imobiliaria", "investimento"),
    ),
    "37": (
        "Construção, instalação e reparação",
        ("construcao", "instalacao", "reparo", "manutencao", "oficina"),
    ),
    "38": (
        "Telecomunicações",
        ("telecomunicacao", "telefonia", "comunicacao digital", "transmissao"),
    ),
    "39": (
        "Transporte, armazenagem e viagens",
        ("transporte", "logistica", "entrega", "armazenagem", "viagem"),
    ),
    "40": (
        "Tratamento de materiais e fabricação sob encomenda",
        ("fabricacao", "tratamento de material", "impressao", "reciclagem"),
    ),
    "41": (
        "Educação, treinamento, entretenimento e esporte",
        ("educacao", "curso", "treinamento", "escola", "entretenimento", "evento", "esporte"),
    ),
    "42": (
        "Serviços científicos, tecnológicos e desenvolvimento de software",
        (
            "desenvolvimento de software",
            "programacao",
            "saas",
            "consultoria de tecnologia",
            "engenharia",
            "design",
        ),
    ),
    "43": (
        "Alimentação e hospedagem",
        ("restaurante", "lanchonete", "bar", "hotel", "hospedagem", "buffet"),
    ),
    "44": (
        "Serviços médicos, veterinários, beleza e agricultura",
        (
            "clinica",
            "medico",
            "dentista",
            "veterinario",
            "salao de beleza",
            "estetica",
            "agronomia",
        ),
    ),
    "45": (
        "Serviços jurídicos, segurança e serviços pessoais",
        ("advocacia", "juridico", "seguranca", "investigacao", "servico pessoal"),
    ),
}


def _normalizar(valor: str) -> str:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", valor)
        if not unicodedata.combining(caractere)
    )
    texto = re.sub(r"\s+", " ", sem_acentos.lower()).strip()
    palavras = [
        palavra[:-1] if len(palavra) > 4 and palavra.endswith("s") else palavra
        for palavra in texto.split()
    ]
    return " ".join(palavras)


def mapear_atividade(atividade: str, limite: int = 4) -> list[ClasseNiceCandidata]:
    texto = _normalizar(atividade)
    tokens_texto = set(texto.split())
    candidatos: list[tuple[int, str, tuple[str, ...]]] = []
    for codigo, (_, palavras_chave) in CLASSES_NICE.items():
        lista_encontrados: list[str] = []
        pontuacao = 0
        for termo in palavras_chave:
            termo_normalizado = _normalizar(termo)
            if re.search(rf"(?<!\w){re.escape(termo_normalizado)}(?!\w)", texto):
                lista_encontrados.append(termo)
                pontuacao += 4 if " " in termo_normalizado else 2
                continue
            tokens_termo = set(termo_normalizado.split())
            if len(tokens_termo) > 1 and tokens_termo <= tokens_texto:
                lista_encontrados.append(termo)
                pontuacao += 2
        encontrados = tuple(lista_encontrados)
        if encontrados:
            candidatos.append((pontuacao, codigo, encontrados))

    candidatos.sort(key=lambda item: (-item[0], item[1]))
    return [
        ClasseNiceCandidata(
            codigo=codigo,
            titulo=CLASSES_NICE[codigo][0],
            tipo="produto" if int(codigo) <= 34 else "serviço",
            termos_encontrados=encontrados,
            confianca=min(1.0, pontuacao / 8),
        )
        for pontuacao, codigo, encontrados in candidatos[:limite]
    ]
