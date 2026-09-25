"""Pacote de modelos SQLAlchemy, dividido por domínio (Fase 4 -- modularização
segura, missão de maturidade técnica). Reexporta tudo com os mesmos nomes de
antes: nenhum outro módulo do projeto precisa mudar seus imports
(`from app.models import X` continua funcionando).

Extraído até agora: vigilância, portal do cliente, carteira/processos
monitorados, enums compartilhados, prospecção, observabilidade/sistema,
jurídico, portfólio de PI/RPI, marca/busca/ML e financeiro. O grosso das
classes permanece em _core.py até as próximas extrações, uma por vez.

`processo_titulares` (Table de associação, não classe) é importado
diretamente por vários módulos de app/api/ -- reexportado explicitamente
abaixo pelo mesmo motivo dos enums: não é usado dentro de nenhuma classe
de _core.py, então não chega via `_core import *`.

Nota sobre relationship() entre módulos: SQLAlchemy resolve nomes de classe
em `Mapped["NomeDaClasse"]` (string) via seu registry compartilhado (o mesmo
`Base`), então funciona entre módulos sem import direto -- usado nas
referências de carteira.py de volta para _core.py (ex: PrazoJuridico). Já
uma referência sem aspas (ex: `Mapped[Processo]`) exige import real da
classe no módulo onde a relationship() é declarada -- é o que carteira.py
faz para `Processo` e `EmpresaCRM`, que continuam em _core.py.

Nota sobre os enums: CanalContato, FaseLead, StatusLead e TipoProcesso já
chegam via `_core import *` abaixo -- _core.py os reimporta de enums.py
para uso interno em `Mapped[...]` (que, ao contrário de relationship(),
não aceita forward reference em string). StatusProspect e TipoAtivoPI
precisam de reexport explícito aqui: nenhuma classe de _core.py os usa
diretamente (StatusProspect só é usado dentro de prospeccao.py agora).
"""

from app.models._core import *  # noqa: F403
from app.models.carteira import HistoricoEtapaCarteira as HistoricoEtapaCarteira
from app.models.carteira import PreCadastroProcesso as PreCadastroProcesso
from app.models.carteira import ProcessoMonitorado as ProcessoMonitorado
from app.models.enums import StatusProspect as StatusProspect
from app.models.enums import TipoAtivoPI as TipoAtivoPI
from app.models.financeiro import ApontamentoHoras as ApontamentoHoras
from app.models.financeiro import CategoriaFinanceira as CategoriaFinanceira
from app.models.financeiro import CentroCustoFinanceiro as CentroCustoFinanceiro
from app.models.financeiro import ComissaoFinanceira as ComissaoFinanceira
from app.models.financeiro import ContratacaoServico as ContratacaoServico
from app.models.financeiro import DepartamentoFinanceiro as DepartamentoFinanceiro
from app.models.financeiro import ExtratoBancario as ExtratoBancario
from app.models.financeiro import FormaPagamentoFinanceira as FormaPagamentoFinanceira
from app.models.financeiro import HistoricoFinanceiro as HistoricoFinanceiro
from app.models.financeiro import LancamentoFinanceiro as LancamentoFinanceiro
from app.models.financeiro import NotaFiscalServico as NotaFiscalServico
from app.models.financeiro import ParcelaFinanceira as ParcelaFinanceira
from app.models.financeiro import PlanoContas as PlanoContas
from app.models.financeiro import ReciboFinanceiro as ReciboFinanceiro
from app.models.financeiro import RenovacaoFinanceira as RenovacaoFinanceira
from app.models.financeiro import RetribuicaoInpi as RetribuicaoInpi
from app.models.financeiro import ServicoFinanceiro as ServicoFinanceiro
from app.models.financeiro import TransacaoBancaria as TransacaoBancaria
from app.models.financeiro import WebhookFinanceiro as WebhookFinanceiro
from app.models.juridico import ContratoJuridico as ContratoJuridico
from app.models.juridico import CustoJuridico as CustoJuridico
from app.models.juridico import DocumentoEntregaJuridico as DocumentoEntregaJuridico
from app.models.juridico import EventoJuridico as EventoJuridico
from app.models.juridico import ExcecaoCalendarioJuridico as ExcecaoCalendarioJuridico
from app.models.juridico import ExecucaoMotorJuridico as ExecucaoMotorJuridico
from app.models.juridico import FornecedorJuridico as FornecedorJuridico
from app.models.juridico import ItemChecklistPrazo as ItemChecklistPrazo
from app.models.juridico import MovimentacaoAvaliadaJuridico as MovimentacaoAvaliadaJuridico
from app.models.juridico import NotificacaoJuridica as NotificacaoJuridica
from app.models.juridico import PoliticaJuridica as PoliticaJuridica
from app.models.juridico import PrazoJuridico as PrazoJuridico
from app.models.juridico import RegraJuridicaVersionada as RegraJuridicaVersionada
from app.models.juridico import RegraPrazoJuridico as RegraPrazoJuridico
from app.models.marca_busca import AfinidadeClasse as AfinidadeClasse
from app.models.marca_busca import AfinidadeViena as AfinidadeViena
from app.models.marca_busca import AvaliacaoRiscoMarca as AvaliacaoRiscoMarca
from app.models.marca_busca import ControleAprendizadoMarca as ControleAprendizadoMarca
from app.models.marca_busca import EvidenciaDecisaoMarca as EvidenciaDecisaoMarca
from app.models.marca_busca import ExecucaoAgenteRegistrabilidade as ExecucaoAgenteRegistrabilidade
from app.models.marca_busca import ExecucaoAprendizadoMarca as ExecucaoAprendizadoMarca
from app.models.marca_busca import ExplicacaoRiscoIA as ExplicacaoRiscoIA
from app.models.marca_busca import MarcaAltoRenome as MarcaAltoRenome
from app.models.marca_busca import ModeloRankingBusca as ModeloRankingBusca
from app.models.marca_busca import ModeloRegistrabilidade as ModeloRegistrabilidade
from app.models.marca_busca import ParTreinamentoMarca as ParTreinamentoMarca
from app.models.marca_busca import PesquisaMarca as PesquisaMarca
from app.models.marca_busca import PrevisaoRegistrabilidade as PrevisaoRegistrabilidade
from app.models.marca_busca import ProjetoBuscaMarca as ProjetoBuscaMarca
from app.models.marca_busca import RotuloHistoricoMarca as RotuloHistoricoMarca
from app.models.marca_busca import SolicitacaoExclusaoPesquisa as SolicitacaoExclusaoPesquisa
from app.models.marca_busca import VersaoRelatorioMarca as VersaoRelatorioMarca
from app.models.observabilidade import ControleProducao as ControleProducao
from app.models.observabilidade import EventoAuditoria as EventoAuditoria
from app.models.observabilidade import EventoOperacional as EventoOperacional
from app.models.observabilidade import FeatureFlag as FeatureFlag
from app.models.observabilidade import FeatureFlagEvento as FeatureFlagEvento
from app.models.observabilidade import FeatureFlagOrganizacao as FeatureFlagOrganizacao
from app.models.observabilidade import InteracaoVersaoSistema as InteracaoVersaoSistema
from app.models.observabilidade import ProblemaVersaoSistema as ProblemaVersaoSistema
from app.models.observabilidade import ProcessoHeartbeat as ProcessoHeartbeat
from app.models.observabilidade import VersaoSistema as VersaoSistema
from app.models.portal_cliente import ArquivoClientePortal as ArquivoClientePortal
from app.models.portal_cliente import ClientePortal as ClientePortal
from app.models.portal_cliente import CodigoConfirmacaoPortal as CodigoConfirmacaoPortal
from app.models.portal_cliente import MaterialMarcaCliente as MaterialMarcaCliente
from app.models.portal_cliente import MensagemClientePortal as MensagemClientePortal
from app.models.portal_cliente import RecuperacaoClientePortal as RecuperacaoClientePortal
from app.models.portal_cliente import SessaoClientePortal as SessaoClientePortal
from app.models.portfolio_pi import AtivoPartePI as AtivoPartePI
from app.models.portfolio_pi import AtivoPI as AtivoPI
from app.models.portfolio_pi import AtivoProcessoPI as AtivoProcessoPI
from app.models.portfolio_pi import ClassificacaoMarca as ClassificacaoMarca
from app.models.portfolio_pi import DocumentoAtivoPI as DocumentoAtivoPI
from app.models.portfolio_pi import Movimentacao as Movimentacao
from app.models.portfolio_pi import Processo as Processo
from app.models.portfolio_pi import RpiImportacao as RpiImportacao
from app.models.portfolio_pi import RpiImportacaoHistorico as RpiImportacaoHistorico
from app.models.portfolio_pi import RpiSyncEstado as RpiSyncEstado
from app.models.portfolio_pi import RpiSyncExecucao as RpiSyncExecucao
from app.models.portfolio_pi import Titular as Titular
from app.models.portfolio_pi import processo_titulares as processo_titulares
from app.models.prospeccao import CacheEstabelecimentoRFB as CacheEstabelecimentoRFB
from app.models.prospeccao import CampanhaProspeccao as CampanhaProspeccao
from app.models.prospeccao import HistoricoStatusProspect as HistoricoStatusProspect
from app.models.prospeccao import ImportacaoCnpjRfb as ImportacaoCnpjRfb
from app.models.prospeccao import PoliticaProspeccao as PoliticaProspeccao
from app.models.prospeccao import Prospect as Prospect
from app.models.prospeccao import ProspectEnriquecimento as ProspectEnriquecimento
from app.models.prospeccao import ProspectFonte as ProspectFonte
from app.models.prospeccao import ProspectTriagem as ProspectTriagem
from app.models.prospeccao import SupressaoProspeccao as SupressaoProspeccao
from app.models.vigilancia import ColidenciaVigilancia as ColidenciaVigilancia
from app.models.vigilancia import HistoricoAlertaVigilancia as HistoricoAlertaVigilancia
from app.models.vigilancia import PreferenciaVigilancia as PreferenciaVigilancia
from app.models.vigilancia import VigilanciaExecucao as VigilanciaExecucao

__all__ = [name for name in dir() if not name.startswith("_")]
