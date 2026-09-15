"""Pacote de modelos SQLAlchemy, dividido por domínio (Fase 4 -- modularização
segura, missão de maturidade técnica). Reexporta tudo com os mesmos nomes de
antes: nenhum outro módulo do projeto precisa mudar seus imports
(`from app.models import X` continua funcionando).

Extraído até agora: vigilância, portal do cliente, carteira/processos
monitorados e os enums compartilhados. O grosso das classes permanece em
_core.py até as próximas extrações, uma por vez.

Nota sobre relationship() entre módulos: SQLAlchemy resolve nomes de classe
em `Mapped["NomeDaClasse"]` (string) via seu registry compartilhado (o mesmo
`Base`), então funciona entre módulos sem import direto -- usado nas
referências de carteira.py de volta para _core.py (ex: PrazoJuridico). Já
uma referência sem aspas (ex: `Mapped[Processo]`) exige import real da
classe no módulo onde a relationship() é declarada -- é o que carteira.py
faz para `Processo` e `EmpresaCRM`, que continuam em _core.py.

Nota sobre os enums: CanalContato, FaseLead, StatusLead, StatusProspect e
TipoProcesso já chegam via `_core import *` abaixo -- _core.py os reimporta
de enums.py para uso interno em `Mapped[...]` (que, ao contrário de
relationship(), não aceita forward reference em string). Só TipoAtivoPI
precisa de reexport explícito aqui: nenhuma classe de _core.py o usa
diretamente.
"""

from app.models._core import *  # noqa: F403
from app.models.carteira import HistoricoEtapaCarteira as HistoricoEtapaCarteira
from app.models.carteira import PreCadastroProcesso as PreCadastroProcesso
from app.models.carteira import ProcessoMonitorado as ProcessoMonitorado
from app.models.enums import TipoAtivoPI as TipoAtivoPI
from app.models.portal_cliente import ArquivoClientePortal as ArquivoClientePortal
from app.models.portal_cliente import ClientePortal as ClientePortal
from app.models.portal_cliente import MensagemClientePortal as MensagemClientePortal
from app.models.portal_cliente import NotificacaoClientePortal as NotificacaoClientePortal
from app.models.portal_cliente import RecuperacaoClientePortal as RecuperacaoClientePortal
from app.models.portal_cliente import SessaoClientePortal as SessaoClientePortal
from app.models.vigilancia import ColidenciaVigilancia as ColidenciaVigilancia
from app.models.vigilancia import HistoricoAlertaVigilancia as HistoricoAlertaVigilancia
from app.models.vigilancia import PreferenciaVigilancia as PreferenciaVigilancia
from app.models.vigilancia import VigilanciaExecucao as VigilanciaExecucao

__all__ = [name for name in dir() if not name.startswith("_")]
