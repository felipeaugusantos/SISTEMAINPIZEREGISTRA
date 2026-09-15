"""Pacote de modelos SQLAlchemy, dividido por domínio (Fase 4 -- modularização
segura, missão de maturidade técnica). Reexporta tudo com os mesmos nomes de
antes: nenhum outro módulo do projeto precisa mudar seus imports
(`from app.models import X` continua funcionando).

Extraído até agora: vigilância e portal do cliente (domínios isolados, sem
relationship() cruzado com o restante). O grosso das classes permanece em
_core.py até as próximas extrações, uma por vez.
"""

from app.models._core import *  # noqa: F403
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
