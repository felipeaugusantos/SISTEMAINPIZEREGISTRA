from pathlib import Path

from app.models import TipoProcesso
from app.rpi.parsers import ler_marcas, ler_patentes


def test_parse_marca(tmp_path: Path) -> None:
    arquivo = tmp_path / "marca.xml"
    arquivo.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<revista numero="2897" data="14/07/2026">
  <processo numero="943906024" data-deposito="28/05/2026">
    <despachos><despacho codigo="IPAS009" nome="Publicação do pedido"/></despachos>
    <titulares><titular nome-razao-social="EMPRESA TESTE" pais="BR"/></titulares>
    <marca apresentacao="Mista" natureza="Produtos e/ou Serviço"><nome>MARCA TESTE</nome></marca>
    <classes-vienna><classe-vienna codigo="26.11.2" edicao="4"/></classes-vienna>
    <lista-classe-nice>
      <classe-nice codigo="32">
        <especificacao>Bebida energética;</especificacao><status>Pendente</status>
      </classe-nice>
    </lista-classe-nice>
    <procurador>PROCURADOR TESTE LTDA</procurador>
  </processo>
</revista>""",
        encoding="utf-8",
    )

    registro = next(ler_marcas(arquivo))

    assert registro.numero == "943906024"
    assert registro.tipo == TipoProcesso.MARCA
    assert registro.titulo == "MARCA TESTE"
    assert registro.titulares[0].nome == "EMPRESA TESTE"
    assert registro.movimentacoes[0].codigo == "IPAS009"
    assert registro.apresentacao == "Mista"
    assert registro.natureza == "Produtos e/ou Serviço"
    assert registro.elemento_nominativo == "MARCA TESTE"
    assert registro.procurador == "PROCURADOR TESTE LTDA"
    assert [classe.sistema for classe in registro.classificacoes] == ["vienna", "nice"]
    assert registro.classificacoes[1].especificacao == "Bebida energética;"


def test_parse_patente(tmp_path: Path) -> None:
    arquivo = tmp_path / "patente.xml"
    arquivo.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<revista numero="2897" dataPublicacao="14/07/2026" diretoria="Patente">
  <despacho>
    <codigo>121</codigo><titulo>Exigência</titulo>
    <processo-patente>
      <numero>BR 10 2013 010193-1</numero><data-deposito>26/04/2013</data-deposito>
      <titulo>INVENÇÃO TESTE</titulo>
      <titular-lista><titular><nome-completo>UNIVERSIDADE TESTE</nome-completo>
        <endereco><pais><sigla>BR</sigla></pais></endereco>
      </titular></titular-lista>
    </processo-patente>
    <comentario>Cumpra as exigências.</comentario>
  </despacho>
</revista>""",
        encoding="utf-8",
    )

    registro = next(ler_patentes(arquivo))

    assert registro.numero == "BR 10 2013 010193-1"
    assert registro.tipo == TipoProcesso.PATENTE
    assert registro.titulo == "INVENÇÃO TESTE"
    assert registro.situacao == "Exigência"
    assert registro.titulares[0].pais == "BR"


def test_patente_numera_despachos_em_ordem(tmp_path: Path) -> None:
    arquivo = tmp_path / "patente.xml"
    arquivo.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<revista numero="2897" dataPublicacao="14/07/2026" diretoria="Patente">
  <despacho>
    <codigo>1</codigo><titulo>Publicação</titulo>
    <processo-patente><numero>BR11</numero><titulo>A</titulo></processo-patente>
  </despacho>
  <despacho>
    <codigo>2</codigo><titulo>Deferimento</titulo>
    <processo-patente><numero>BR11</numero><titulo>A</titulo></processo-patente>
  </despacho>
</revista>""",
        encoding="utf-8",
    )

    registros = list(ler_patentes(arquivo))

    assert [r.ordem for r in registros] == [0, 1]
    # O despacho mais recente (maior ordem) deve ser o desempate no import em lote.
    assert registros[-1].situacao == "Deferimento"
