"""Dados sintéticos para homologação E2E. Recusa qualquer ambiente fora de test."""
import asyncio
from datetime import date

from sqlalchemy import select

from app.database import session_factory
from app.models import (
    AlertaSistema,
    EmpresaCRM,
    Movimentacao,
    MovimentacaoAvaliadaJuridico,
    Organizacao,
    Processo,
    ProcessoMonitorado,
    RegraPrazoJuridico,
    TipoProcesso,
)
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant


async def main() -> None:
    settings = get_settings()
    if settings.app_env != "test":
        raise RuntimeError("Fixtures funcionais permitidas somente em APP_ENV=test")
    async with session_factory() as session:
        org = (await session.execute(select(Organizacao).where(Organizacao.slug == "ze-registra"))).scalar_one()
        await aplicar_contexto_tenant(session, org.id)
        existing = await session.scalar(select(Processo.id).where(Processo.numero == "999230001"))
        if existing:
            return
        empresa = EmpresaCRM(organizacao_id=org.id, nome="Empresa Homologacao E2E", nome_normalizado="empresa homologacao e2e")
        processo = Processo(numero="999230001", numero_normalizado="999230001", tipo=TipoProcesso.MARCA, titulo="Marca Homologacao E2E", fonte="fixture-e2e")
        session.add_all([empresa, processo])
        await session.flush()
        session.add(ProcessoMonitorado(organizacao_id=org.id, processo_id=processo.id, empresa_id=empresa.id, vinculado_por="fixture-e2e"))
        mov = Movimentacao(processo_id=processo.id, codigo_despacho="IPAS010", descricao="Exigencia formal E2E", data_rpi=date(2026, 10, 25), numero_rpi=2900, fonte_arquivo="fixture-e2e", chave_origem="auditoria-funcional-230")
        session.add(mov)
        await session.flush()
        session.add(MovimentacaoAvaliadaJuridico(organizacao_id=org.id, movimentacao_id=mov.id, motivo="sem_prazo_mapeado"))
        session.add(RegraPrazoJuridico(codigo_despacho="010", descricao_oficial="Exigencia formal homologacao E2E", tipo_prazo="exigencia", acao="Responder exigencia E2E", dias_prazo=60, contagem="corridos", vigencia_inicio=date(2026, 10, 20), fonte_legal="Fixture sintetica de homologacao funcional E2E", confianca="homologada", ativo=True, aprovado_por="fixture-e2e"))
        session.add(AlertaSistema(organizacao_id=org.id, severidade="info", codigo="auditoria_funcional_e2e", mensagem="Notificacao sintetica de homologacao E2E"))
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
