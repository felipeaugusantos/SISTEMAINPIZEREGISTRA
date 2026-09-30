import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.alertas_plataforma import verificar_saude_plataforma
from app.api.juridico import executar_motor_organizacao
from app.api.versoes_sistema import lembrar_atualizacoes_pendentes
from app.cadencia_email import processar_envios_cadencia_pendentes
from app.cli.sincronizar_alto_renome import sincronizar as sincronizar_alto_renome
from app.crm import (
    gerar_lembretes_reengajamento_inatividade,
    gerar_lembretes_sla_primeiro_atendimento,
    reconciliar_automacoes_fluxo_contratacao,
)
from app.database import session_factory
from app.emailing import enviar_alerta_atividades_atrasadas
from app.feature_flags import avaliar_circuito_flags
from app.ia_sombra import (
    gerar_explicacoes_risco_pendentes,
    gerar_qualificacao_lead,
    gerar_sugestoes_ia_pendentes,
    indexar_embeddings_leads_pendentes,
)
from app.imap_polling import verificar_respostas_email
from app.juridico_comunicacao import agendar_resumos_juridicos_diarios, processar_saidas_email_juridico
from app.models import (
    AlertaSistema,
    Lead,
    Movimentacao,
    Organizacao,
    Processo,
    ProcessoHeartbeat,
    ProcessoMonitorado,
    RenovacaoFinanceira,
    UsuarioOperacoes,
)
from app.queueing import (
    FAILED_KEY,
    MAX_ATTEMPTS,
    METRICS_KEY,
    PROCESSING_KEY,
    QUEUE_KEY,
    agendar_retry,
    cliente_redis,
    enfileirar,
    promover_retentativas,
)
from app.request_context import definir_request_id, request_id_atual, restaurar_request_id
from app.retencao import simular_retencao_leads
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant
from app.trademarks.agent import reconciliar_resultados_reais, reprocessar_agentes_pendentes
from app.trademarks.learning import (
    executar_pipeline_aprendizado,
    reprocessar_previsoes_pendentes,
)
from app.trademarks.model_status import StatusModelo
from app.trademarks.status import normalizar_despacho

logger = logging.getLogger("ze_registra.worker")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False


def _somar_anos(referencia: date, anos: int) -> date:
    try:
        return referencia.replace(year=referencia.year + anos)
    except ValueError:
        # 29 de fevereiro em ano nao bissexto -> usa 28/02 do ano de destino.
        return referencia.replace(year=referencia.year + anos, day=28)


async def processar(tipo: str, payload: dict) -> None:
    async with session_factory() as session:
        await aplicar_contexto_tenant(session, 1, superadmin=True)
        if tipo == "assinaturas.verificar":
            agora = datetime.now(UTC)
            orgs = (
                await session.execute(
                    select(Organizacao).where(
                        Organizacao.status == "trial",
                        Organizacao.trial_ate < agora,
                        Organizacao.suspender_automaticamente.is_(True),
                    )
                )
            ).scalars()
            for org in orgs:
                org.status, org.assinatura_status = "suspensa", "trial_expirado"
                session.add(
                    AlertaSistema(
                        organizacao_id=org.id,
                        severidade="aviso",
                        codigo="TRIAL_EXPIRADO",
                        mensagem="Trial expirado; acesso suspenso automaticamente.",
                    )
                )
        elif tipo == "privacidade.verificar_retencao":
            filtro_org = payload.get("organizacao_id")
            consulta_orgs = select(Organizacao)
            if filtro_org is not None:
                consulta_orgs = consulta_orgs.where(Organizacao.id == int(filtro_org))
            orgs = (await session.execute(consulta_orgs)).scalars()
            for org in orgs:
                simulacao = await simular_retencao_leads(session, org)
                total = simulacao["total_afetado"]
                existente = (
                    await session.execute(
                        select(AlertaSistema).where(
                            AlertaSistema.organizacao_id == org.id,
                            AlertaSistema.codigo == "RETENCAO_PENDENTE",
                            AlertaSistema.resolvido_em.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if total:
                    detalhes = {
                        **simulacao,
                        "data_corte": simulacao["data_corte"].isoformat(),
                        "registro_mais_antigo": (
                            simulacao["registro_mais_antigo"].isoformat()
                            if simulacao["registro_mais_antigo"]
                            else None
                        ),
                    }
                    if not existente:
                        session.add(
                            AlertaSistema(
                                organizacao_id=org.id,
                                severidade="aviso",
                                codigo="RETENCAO_PENDENTE",
                                mensagem=(f"{total} lead(s) excedem a política de retenção e aguardam revisão humana."),
                                detalhes=detalhes,
                            )
                        )
                    else:
                        existente.detalhes = detalhes
                elif existente:
                    existente.resolvido_em = datetime.now(UTC)
        elif tipo == "crm.reengajamento_inatividade":
            # Achado da auditoria do CRM: a política de "próxima ação obrigatória"
            # (app/crm.py::aplicar_politica_oportunidade) só é aplicada quando
            # alguém mexe no lead -- sozinho, um lead esquecido continua esquecido
            # para sempre. Este job varre periodicamente e cria um lembrete para
            # o operador retomar contato. Lógica em
            # app.crm.gerar_lembretes_reengajamento_inatividade (testável
            # isoladamente, mesmo padrão de gerar_lembretes_sla_primeiro_atendimento).
            semana = datetime.now(UTC).strftime("%G-W%V")
            criados, atrasados_por_responsavel = await gerar_lembretes_reengajamento_inatividade(session)
            # Achado P2 da auditoria de Leads: nenhuma notificação ativa avisava
            # o responsável de uma atividade atrasada -- só aparecia se ele
            # entrasse no sistema. Um e-mail por responsável, no máximo uma vez
            # por semana por lead (mesma idempotência do lembrete acima).
            if atrasados_por_responsavel:
                linhas_operadores = (
                    await session.execute(
                        select(UsuarioOperacoes.id, UsuarioOperacoes.nome, UsuarioOperacoes.email).where(
                            UsuarioOperacoes.id.in_(atrasados_por_responsavel.keys())
                        )
                    )
                ).all()
                operadores = {row[0]: (row[1], row[2]) for row in linhas_operadores}
                for responsavel_id, leads_atrasados in atrasados_por_responsavel.items():
                    operador = operadores.get(responsavel_id)
                    if operador is None:
                        continue
                    nome, email = operador
                    await enviar_alerta_atividades_atrasadas(
                        nome, email, [(item.nome, item.marca) for item in leads_atrasados]
                    )
            # Achado 17.4: antes um único alerta com o total de TODAS as
            # organizações, gravado fixo na organização 1 (e visível ao
            # comercial dela). Agora um alerta por organização, só com os
            # números dela.
            for organizacao_id, criados_org in criados.items():
                session.add(
                    AlertaSistema(
                        organizacao_id=organizacao_id,
                        severidade="info",
                        codigo="REENGAJAMENTO_CRM_EXECUTADO",
                        mensagem=f"Reengajamento por inatividade: {criados_org} lembrete(s) criado(s).",
                        detalhes={"criados": criados_org, "semana": semana},
                    )
                )
        elif tipo == "crm.sla_primeiro_atendimento":
            # Achado item 14 da auditoria completa do CRM (06/09/2026): só
            # existia a média histórica agregada de tempo até o primeiro
            # contato (app/api/leads.py::_tempo_medio_primeiro_atendimento_horas),
            # sem alerta individual por lead -- diferente do SLA de
            # proposta/protocolo, que já tem prazo e status próprios. Lógica
            # em app.crm.gerar_lembretes_sla_primeiro_atendimento (testável
            # isoladamente, mesmo padrão de aplicar_politica_oportunidade).
            criados_sla = await gerar_lembretes_sla_primeiro_atendimento(session)
            for organizacao_id, criados_org in criados_sla.items():
                session.add(
                    AlertaSistema(
                        organizacao_id=organizacao_id,
                        severidade="alerta",
                        codigo="SLA_PRIMEIRO_ATENDIMENTO_VENCIDO",
                        mensagem=f"SLA de primeiro atendimento: {criados_org} lembrete(s) criado(s).",
                        detalhes={"criados": criados_org},
                    )
                )
        elif tipo == "crm.fluxo_contratacao":
            await reconciliar_automacoes_fluxo_contratacao(session)
        elif tipo == "crm.gerar_sugestoes_ia":
            # IA em sombra (leads): resumo + sugestao de proxima acao, nunca
            # rascunho de mensagem nem envio automatico. Inerte por padrao
            # (devolve 0 sem nenhum efeito colateral) ate settings.ia_sombra_enabled
            # E PoliticaCRM.ia_sombra_ativa de pelo menos uma organizacao
            # estarem ligados -- ver app.ia_sombra.
            await gerar_sugestoes_ia_pendentes(session)
        elif tipo == "leads.indexar_rag":
            # RAG local da IA em sombra (pgvector): indexa leads com
            # resultado conhecido (ganho/perdido) para servir de precedente
            # as sugestoes de outros leads (ver
            # app.ia_sombra.gerar_sugestao_lead). Mesmo par de flags dos
            # jobs de IA em sombra acima.
            await indexar_embeddings_leads_pendentes(session)
        elif tipo == "atualizacoes.lembrar_pendentes":
            # Fase 3 da central de atualizacoes (continuacao da Fase 2,
            # app.api.atualizacoes): mantem um AlertaSistema aberto
            # (app.alertas_plataforma) enquanto uma versao critica ou de
            # correcao tiver usuario ativo pendente de confirmacao de
            # leitura -- resolve sozinho quando zera. Ver
            # app.api.versoes_sistema.lembrar_atualizacoes_pendentes.
            await lembrar_atualizacoes_pendentes(session)
        elif tipo == "feature_flags.avaliar_circuito":
            # Fase 5 (liberacao gradual): circuito de interrupcao
            # automatica. Roda a cada hora (mesma cadencia desta lista);
            # avaliar_circuito_flags olha a janela movel dos ultimos
            # JANELA_CIRCUITO_MINUTOS (app.feature_flags), entao cobre o
            # intervalo continuamente mesmo rodando so uma vez por hora.
            await avaliar_circuito_flags(session)
        elif tipo == "analise.gerar_explicacoes_risco":
            # IA em sombra (analise de marca): traduz em linguagem simples o
            # risco ja calculado pelo motor deterministico -- nunca
            # recalcula nem substitui. Mesmo par de flags do job acima.
            await gerar_explicacoes_risco_pendentes(session)
        elif tipo == "leads.qualificar_ia":
            # IA em sombra (captacao de leads): prioridade + observacao
            # sugeridas uma unica vez, no momento em que o lead chega
            # (formulario publico ou conversao do Radar de Prospeccao) --
            # job sob demanda (enfileirado na criacao), nao um sweep
            # periodico como os dois acima. Idempotente por
            # UniqueConstraint(lead_id) em QualificacaoIALead.
            lead_qualificacao = (
                await session.execute(select(Lead).where(Lead.id == payload["lead_id"]))
            ).scalar_one_or_none()
            if lead_qualificacao is not None:
                await gerar_qualificacao_lead(session, lead_qualificacao)
        elif tipo == "crm.gerar_renovacoes_marca":
            # Achado da auditoria: RenovacaoFinanceira e o endpoint de criar ja
            # existiam (app/api/contratacoes.py), mas so eram usados manualmente --
            # nada gerava a renovacao sozinho quando a marca era concedida. Gatilho
            # confiavel: Processo.situacao_normalizada == "registrada" (concessao de
            # registro, ja calculado pelo importador da RPI). Vencimento = data da
            # concessao (extraida da movimentacao que causou a mudanca de situacao)
            # + 10 anos, prazo padrao de vigencia do registro de marca no Brasil.
            pendentes = (
                await session.execute(
                    select(ProcessoMonitorado.organizacao_id, ProcessoMonitorado.processo_id)
                    .join(Processo, Processo.id == ProcessoMonitorado.processo_id)
                    .where(
                        Processo.situacao_normalizada == "registrada",
                        ~exists(
                            select(1).where(
                                RenovacaoFinanceira.organizacao_id == ProcessoMonitorado.organizacao_id,
                                RenovacaoFinanceira.processo_id == ProcessoMonitorado.processo_id,
                                RenovacaoFinanceira.tipo == "renovacao",
                            )
                        ),
                    )
                    .distinct()
                )
            ).all()
            criadas_por_organizacao: dict[int, int] = {}
            for organizacao_id, processo_id in pendentes:
                movimentacoes = (
                    await session.execute(
                        select(Movimentacao)
                        .where(Movimentacao.processo_id == processo_id)
                        .order_by(Movimentacao.data_rpi.desc())
                    )
                ).scalars()
                data_concessao = next(
                    (
                        mov.data_rpi
                        for mov in movimentacoes
                        if normalizar_despacho(mov.codigo_despacho, mov.descricao).codigo == "registrada"
                    ),
                    None,
                )
                if data_concessao is None:
                    continue
                inserido = (
                    await session.execute(
                        pg_insert(RenovacaoFinanceira)
                        .values(
                            organizacao_id=organizacao_id,
                            processo_id=processo_id,
                            tipo="renovacao",
                            referencia=f"registro-{data_concessao.isoformat()}",
                            vencimento=_somar_anos(data_concessao, 10),
                            status="pendente",
                        )
                        .on_conflict_do_nothing(constraint="uq_renovacao_financeira")
                        .returning(RenovacaoFinanceira.id)
                    )
                ).scalar_one_or_none()
                if inserido is not None:
                    criadas_por_organizacao[organizacao_id] = criadas_por_organizacao.get(organizacao_id, 0) + 1
            # Achado 17.4: um alerta por organização (antes, total agregado
            # fixo na organização 1).
            for organizacao_id, criadas in criadas_por_organizacao.items():
                session.add(
                    AlertaSistema(
                        organizacao_id=organizacao_id,
                        severidade="info",
                        codigo="RENOVACOES_GERADAS",
                        mensagem=f"{criadas} renovação(ões) de marca gerada(s) automaticamente.",
                        detalhes={"criadas": criadas},
                    )
                )
        elif tipo == "registrabilidade.reconciliar_resultados":
            await reconciliar_resultados_reais(session)
        elif tipo == "registrabilidade.reprocessar_previsoes":
            resultado = await reprocessar_previsoes_pendentes(
                session,
                organizacao_id=payload.get("organizacao_id"),
            )
            sem_modelo = resultado["status"] == "sem_modelo_ativo"
            session.add(
                AlertaSistema(
                    organizacao_id=payload.get("organizacao_id") or 1,
                    severidade="aviso" if sem_modelo else "info",
                    codigo="PREVISOES_REPROCESSADAS",
                    mensagem=(
                        "Nenhuma previsão foi criada: não existe modelo supervisionado ativo."
                        if sem_modelo
                        else (f"Reprocessamento concluído: {resultado['processadas']} previsão(ões) criada(s).")
                    ),
                    detalhes=resultado,
                )
            )
        elif tipo == "registrabilidade.reprocessar_agentes":
            resultado = await reprocessar_agentes_pendentes(
                session,
                organizacao_id=payload.get("organizacao_id"),
            )
            session.add(
                AlertaSistema(
                    organizacao_id=payload.get("organizacao_id") or 1,
                    severidade="info",
                    codigo="AGENTES_REPROCESSADOS",
                    mensagem=(f"Agentes atualizados: {resultado['processadas']} pesquisa(s) processada(s)."),
                    detalhes=resultado,
                )
            )
        elif tipo == "registrabilidade.pipeline_aprendizado":
            resultado = await executar_pipeline_aprendizado(
                session,
                administrador=payload.get("solicitado_por") or "worker",
                limite_dataset=int(payload.get("limite_dataset") or 3000),
            )
            aguardando_revisoes = resultado["modelo_status"] == StatusModelo.VALIDATION.value and any(
                "Revisões humanas insuficientes" in bloqueio for bloqueio in resultado["bloqueios"]
            )
            session.add(
                AlertaSistema(
                    organizacao_id=payload.get("organizacao_id") or 1,
                    severidade="info" if resultado["ativado"] else "aviso",
                    codigo=(
                        "MODELO_APRENDIZADO_ATIVADO"
                        if resultado["ativado"]
                        else (
                            "MODELO_APRENDIZADO_AGUARDANDO_REVISOES"
                            if aguardando_revisoes
                            else "MODELO_APRENDIZADO_BLOQUEADO"
                        )
                    ),
                    mensagem=(
                        (
                            f"Modelo em VALIDATION; "
                            f"{resultado['reprocessamento']['processadas']} previsão(ões) "
                            "interna(s) preparada(s). Ativação bloqueada: " + "; ".join(resultado["bloqueios"])
                        )
                        if aguardando_revisoes
                        else (
                            f"Pipeline concluído com o modelo {resultado['modelo_versao']}: "
                            f"{resultado['modelo_status']}."
                        )
                    ),
                    detalhes=resultado,
                )
            )
        elif tipo == "registrabilidade.ativar_candidato":
            # Compatibilidade com tarefas antigas já enfileiradas. Promoções agora exigem
            # ação autenticada e explícita no endpoint administrativo.
            session.add(
                AlertaSistema(
                    organizacao_id=payload.get("organizacao_id") or 1,
                    severidade="aviso",
                    codigo="ATIVACAO_AUTOMATICA_MODELO_IGNORADA",
                    mensagem="Ativação automática ignorada; use a promoção explícita VALIDATION → ACTIVE.",
                    detalhes={"solicitado_por": payload.get("solicitado_por")},
                )
            )
        elif tipo == "alto_renome.sincronizar":
            await sincronizar_alto_renome(get_settings().alto_renome_page_url)
        elif tipo == "juridico.executar_motor":
            organizacoes = (
                await session.execute(select(Organizacao.id).where(Organizacao.status != "suspensa"))
            ).scalars()
            for organizacao_id in organizacoes:
                await executar_motor_organizacao(session, organizacao_id)
        elif tipo == "juridico.agendar_resumos":
            await agendar_resumos_juridicos_diarios(session)
        elif tipo == "juridico.processar_comunicacoes":
            await processar_saidas_email_juridico(session)
        elif tipo == "cadencia.enviar_emails_pendentes":
            resultado = await processar_envios_cadencia_pendentes(session)
            # Achado 17.4: um alerta por organização, só com os números
            # dela (antes, total agregado fixo na organização 1).
            for organizacao_id, contagem in (resultado.get("por_organizacao") or {}).items():
                if not (contagem["enviados"] or contagem["falhas"]):
                    continue
                session.add(
                    AlertaSistema(
                        organizacao_id=organizacao_id,
                        severidade="aviso" if contagem["falhas"] else "info",
                        codigo="CADENCIA_EMAILS_PROCESSADOS",
                        mensagem=(
                            f"{contagem['enviados']} e-mail(s) de cadência enviado(s), {contagem['falhas']} falha(s)."
                        ),
                        detalhes=dict(contagem),
                    )
                )
        elif tipo == "cadencia.verificar_respostas_email":
            resultado = await verificar_respostas_email(session)
            for organizacao_id, pausados in (resultado.get("pausados_por_organizacao") or {}).items():
                session.add(
                    AlertaSistema(
                        organizacao_id=organizacao_id,
                        severidade="info",
                        codigo="CADENCIA_PAUSADA_POR_RESPOSTA",
                        mensagem=f"{pausados} envio(s) de cadência pausado(s) por resposta do titular.",
                        detalhes={"pausados": pausados},
                    )
                )
        elif tipo == "vigilancia.executar_semanal":
            from app.vigilancia import executar_vigilancia_semanal

            # confirmar=False: as execuções marcadas como "concluida" e os
            # alertas abaixo vão no mesmo commit do fim de processar() -- se
            # ele falhar, o retry refaz a semana e recria os alertas.
            resultado = await executar_vigilancia_semanal(session, payload.get("organizacao_id"), confirmar=False)
            # Achado 17.5: sem organizacao_id no payload a vigilância roda
            # para TODAS as organizações; antes o total agregado ia num único
            # alerta da organização 1 (e as demais não recebiam nada). Agora
            # um alerta por organização processada, só com os números dela.
            for organizacao_id, contagem in (resultado.get("por_organizacao") or {}).items():
                session.add(
                    AlertaSistema(
                        organizacao_id=organizacao_id,
                        severidade="info",
                        codigo="VIGILANCIA_SEMANAL_CONCLUIDA",
                        mensagem=f"Vigilancia semanal concluida: {contagem['criadas']} colidencia(s) nova(s).",
                        detalhes={"chave": resultado["chave"], **contagem},
                    )
                )
        elif tipo == "prospeccao.coletar_campanha":
            # Fase 2 do Radar de Prospecção (03/09/2026): a RFB nao oferece
            # consulta sob demanda por CNAE/UF -- so consulta o cache local
            # (cache_estabelecimentos_rfb), alimentado a parte por
            # app/cli/importar_cnpj_rfb.py. Nunca baixa nada da RFB aqui.
            from app.api.prospeccao import _criar_prospect, obter_ou_criar_fonte_cnae_publico
            from app.models import CacheEstabelecimentoRFB, CampanhaProspeccao
            from app.rfb_cnpj import normalizar_cidade
            from app.schemas import ProspectCreate

            organizacao_id = payload["organizacao_id"]
            campanha = (
                await session.execute(
                    select(CampanhaProspeccao).where(
                        CampanhaProspeccao.id == payload["campanha_id"],
                        CampanhaProspeccao.organizacao_id == organizacao_id,
                    )
                )
            ).scalar_one_or_none()
            if campanha is not None:
                try:
                    criterios = campanha.criterios_busca or {}
                    filtros_cache = [CacheEstabelecimentoRFB.situacao_cadastral == "ativa"]
                    if criterios.get("cnae_principal"):
                        filtros_cache.append(CacheEstabelecimentoRFB.cnae_principal == criterios["cnae_principal"])
                    # Achado do usuário: campanha só filtrava 1 UF/cidade por vez --
                    # criterios_busca agora guarda listas (compatível com campanhas
                    # antigas, que gravaram um valor string único em vez de lista).
                    ufs_criterio = criterios.get("uf") or []
                    ufs_criterio = [ufs_criterio] if isinstance(ufs_criterio, str) else ufs_criterio
                    if ufs_criterio:
                        filtros_cache.append(CacheEstabelecimentoRFB.uf.in_(ufs_criterio))
                    cidades_criterio = criterios.get("cidade") or []
                    cidades_criterio = [cidades_criterio] if isinstance(cidades_criterio, str) else cidades_criterio
                    cidades_normalizadas = [c for c in (normalizar_cidade(item) for item in cidades_criterio) if c]
                    if cidades_normalizadas:
                        filtros_cache.append(
                            or_(*[CacheEstabelecimentoRFB.cidade.ilike(f"%{item}%") for item in cidades_normalizadas])
                        )
                    if criterios.get("porte"):
                        filtros_cache.append(CacheEstabelecimentoRFB.porte == criterios["porte"])
                    if criterios.get("data_abertura_de"):
                        # criterios_busca vem de JSON -- datas chegam como string ISO, não date.
                        filtros_cache.append(
                            CacheEstabelecimentoRFB.data_abertura >= date.fromisoformat(criterios["data_abertura_de"])
                        )
                    if criterios.get("data_abertura_ate"):
                        filtros_cache.append(
                            CacheEstabelecimentoRFB.data_abertura <= date.fromisoformat(criterios["data_abertura_ate"])
                        )

                    limite = min(campanha.meta_prospects or 500, 2000)
                    candidatos = (
                        (await session.execute(select(CacheEstabelecimentoRFB).where(*filtros_cache).limit(limite)))
                        .scalars()
                        .all()
                    )
                    fonte = await obter_ou_criar_fonte_cnae_publico(session, organizacao_id)
                    criados = 0
                    for candidato in candidatos:
                        dados = ProspectCreate(
                            razao_social=candidato.razao_social,
                            nome_fantasia=candidato.nome_fantasia,
                            cnpj=candidato.cnpj,
                            cnae_principal=candidato.cnae_principal,
                            cnaes_secundarios=candidato.cnaes_secundarios,
                            porte=candidato.porte,
                            uf=candidato.uf,
                            cidade=candidato.cidade,
                            telefone=candidato.telefone,
                            email=candidato.email,
                        )
                        _, resultado_item = await _criar_prospect(
                            session,
                            organizacao_id,
                            dados,
                            "radar:coleta-automatica",
                            fonte_id=fonte.id,
                            campanha_id=campanha.id,
                        )
                        if resultado_item == "criado":
                            criados += 1
                    campanha.status = "concluida"
                    campanha.encerrada_em = datetime.now(UTC)
                    logger.info(
                        "Campanha %s: %d/%d candidatos viraram prospect novo", campanha.id, criados, len(candidatos)
                    )
                except Exception:
                    # Achado do usuário (13/09/2026): sem isso, uma exceção no
                    # meio da coleta (ou o worker sendo reiniciado) deixava a
                    # campanha travada em "ativa" para sempre -- e o botão
                    # "Excluir" fica bloqueado de propósito nesse status, sem
                    # nenhuma forma de destravar (ver também a rota manual
                    # /campanhas/{id}/cancelar em app/api/prospeccao.py, para
                    # o caso do processo morrer antes de chegar aqui).
                    campanha.status = "pausada"
                    campanha.encerrada_em = datetime.now(UTC)
                    await session.commit()
                    raise
        elif tipo == "prospeccao.enriquecer_prospect":
            # Fase 3 do Radar de Prospecção (03/09/2026): fonte escolhida foi
            # só verificar se o site responde, sem provedor pago.
            from app.models import Prospect, ProspectEnriquecimento
            from app.verificacao_site import verificar_site

            prospect = (
                await session.execute(
                    select(Prospect).where(
                        Prospect.id == payload["prospect_id"], Prospect.organizacao_id == payload["organizacao_id"]
                    )
                )
            ).scalar_one_or_none()
            if prospect is not None:
                resultado = await verificar_site(prospect.site)
                session.add(
                    ProspectEnriquecimento(
                        organizacao_id=payload["organizacao_id"],
                        prospect_id=prospect.id,
                        provedor="verificacao_site",
                        tipo="presenca_digital",
                        payload=resultado,
                        sucesso=resultado["erro"] is None,
                        erro=resultado["erro"],
                    )
                )
                prospect.presenca_digital = resultado
        elif tipo == "prospeccao.triar_marca_prospect":
            # Fase 4 do Radar de Prospecção (03/09/2026): SOMENTE indicativo,
            # nunca "disponível" -- ver app/prospeccao_triagem.py.
            from app.models import Prospect, ProspectTriagem
            from app.prospeccao_triagem import triar_marca_prospect

            prospect = (
                await session.execute(
                    select(Prospect).where(
                        Prospect.id == payload["prospect_id"], Prospect.organizacao_id == payload["organizacao_id"]
                    )
                )
            ).scalar_one_or_none()
            if prospect is not None:
                resultado = await triar_marca_prospect(
                    session, prospect.razao_social, prospect.nome_fantasia, prospect.cnpj
                )
                session.add(
                    ProspectTriagem(
                        organizacao_id=payload["organizacao_id"], prospect_id=prospect.id, **resultado
                    )
                )
                prospect.triagem_marca_status = resultado["classificacao"]
                prospect.triagem_marca_em = datetime.now(UTC)
                # Fase 5 (03/09/2026): score encadeado logo após a triagem --
                # é o último dado que falta pra calcular o score comercial.
                await enfileirar(
                    "prospeccao.calcular_score",
                    {"prospect_id": prospect.id, "organizacao_id": payload["organizacao_id"]},
                    idempotency_key=f"{prospect.id}:score:{datetime.now(UTC).date().isoformat()}",
                )
        elif tipo == "prospeccao.calcular_score":
            # Fase 5 do Radar de Prospecção (03/09/2026): fatores explícitos
            # e versionados -- ver app/prospeccao_score.py.
            from app.models import HistoricoStatusProspect, PoliticaProspeccao, Prospect
            from app.prospeccao_score import calcular_score

            prospect = (
                await session.execute(
                    select(Prospect).where(
                        Prospect.id == payload["prospect_id"], Prospect.organizacao_id == payload["organizacao_id"]
                    )
                )
            ).scalar_one_or_none()
            if prospect is not None:
                score = calcular_score(
                    situacao_cadastral=prospect.situacao_cadastral,
                    data_abertura=prospect.data_abertura,
                    presenca_digital=prospect.presenca_digital,
                    triagem_marca_status=prospect.triagem_marca_status,
                )
                prospect.score = score.total
                prospect.score_detalhe = {"total": score.total, "versao": score.versao, "fatores": score.fatores_json()}
                prospect.score_calculado_em = datetime.now(UTC)

                if prospect.status == "novo":
                    politica = (
                        await session.execute(
                            select(PoliticaProspeccao).where(
                                PoliticaProspeccao.organizacao_id == payload["organizacao_id"]
                            )
                        )
                    ).scalar_one_or_none()
                    if (
                        politica is not None
                        and politica.aprovacao_automatica_ativa
                        and politica.score_minimo_aprovacao is not None
                        and score.total >= politica.score_minimo_aprovacao
                    ):
                        prospect.status = "aprovado"
                        session.add(
                            HistoricoStatusProspect(
                                organizacao_id=payload["organizacao_id"],
                                prospect_id=prospect.id,
                                status="aprovado",
                                por="radar:aprovacao-automatica",
                            )
                        )
        elif tipo == "prospeccao.importar_cnpj_rfb":
            # Disparado pela tela do Radar (superadmin, 03/09/2026) -- roda no
            # worker, não preso à sessão HTTP/SSH de quem clicou. Cada
            # checkpoint de progresso comita numa sessão própria (não só no
            # fim) para o polling da tela enxergar progresso em tempo real.
            from app.cli.importar_cnpj_rfb import ImportacaoInterrompida, importar
            from app.models import ImportacaoCnpjRfb

            execucao_id = payload["execucao_id"]

            async def _progresso(etapa: str, processados: int, validos: int) -> None:
                async with session_factory() as sessao_progresso:
                    execucao = await sessao_progresso.get(ImportacaoCnpjRfb, execucao_id)
                    # Parada pela tela (30/09/2026): encerra no próximo checkpoint.
                    if execucao is not None and execucao.status == "cancelado":
                        raise ImportacaoInterrompida(execucao.status)
                    if execucao is not None:
                        # Uma nova tentativa da fila retomou: sai do "erro" da anterior.
                        execucao.status = "executando"
                        execucao.erro = None
                        execucao.concluido_em = None
                        execucao.etapa_atual = etapa
                        if processados or validos:
                            execucao.total_processados = processados
                            execucao.total_validos = validos
                        await sessao_progresso.commit()

            try:
                resultado = await importar(
                    periodo=payload.get("periodo"), limite_linhas=payload.get("limite_linhas"), progresso=_progresso
                )
            except ImportacaoInterrompida:
                logger.info("Importação do cache nacional %s interrompida pela tela.", execucao_id)
                return
            except Exception as exc:  # noqa: BLE001 - registra o erro na execução antes de propagar para o retry padrão da fila
                # Achado (30/09/2026): o erro ia para a sessão principal, que não
                # comita quando a exceção propaga -- a tela ficava "Em andamento"
                # 0% em vez de "Falhou". Grava numa sessão própria.
                async with session_factory() as sessao_erro:
                    execucao = await sessao_erro.get(ImportacaoCnpjRfb, execucao_id)
                    if execucao is not None and execucao.status != "cancelado":
                        execucao.status = "erro"
                        execucao.erro = f"Falha ao acessar a Receita Federal ou processar os arquivos: {exc}"[:4000]
                        execucao.concluido_em = datetime.now(UTC)
                        await sessao_erro.commit()
                raise
            execucao = await session.get(ImportacaoCnpjRfb, execucao_id)
            # Parada pedida depois do último checkpoint prevalece (revisão do Codex, PR #161).
            if execucao is not None and execucao.status != "cancelado":
                execucao.status = "concluido"
                execucao.periodo = resultado["periodo"]
                execucao.etapa_atual = "Concluído"
                execucao.total_processados = resultado["processados"]
                execucao.total_validos = resultado["validos"]
                execucao.concluido_em = datetime.now(UTC)
        elif tipo == "prospeccao.importar_cache_enviado":
            # Pedido do usuário (30/09/2026): a Receita recusa a VPS; os arquivos
            # chegam por envio agendado e a importação dispara sozinha.
            from app.prospeccao_cache_rfb import disparar_importacao_de_arquivos_enviados

            await disparar_importacao_de_arquivos_enviados(session)
        elif tipo == "plataforma.verificar_saude":
            # Achado FASE6-9 da auditoria (04/09/2026): fila de falhas, RPI
            # desatualizada e latência/erro de API viravam número num painel,
            # sem ninguém ser avisado -- ver app/alertas_plataforma.py.
            await verificar_saude_plataforma(session)
        else:
            raise ValueError(f"Tipo de trabalho desconhecido: {tipo}")
        await session.commit()


async def processar_rastreado(tipo: str, payload: dict, request_id: str | None = None) -> None:
    token_contexto = definir_request_id(request_id)
    request_id_valor = request_id_atual()
    inicio = datetime.now(UTC)
    try:
        await processar(tipo, payload)
        logger.info(
            json.dumps(
                {
                    "event": "WORKER_JOB_COMPLETED",
                    "request_id": request_id_valor,
                    "job_type": tipo,
                    "duration_ms": round((datetime.now(UTC) - inicio).total_seconds() * 1000),
                },
                ensure_ascii=False,
            )
        )
    finally:
        restaurar_request_id(token_contexto)


TAREFAS_MANUTENCAO_HORARIA: tuple[str, ...] = (
    "assinaturas.verificar",
    "privacidade.verificar_retencao",
    "crm.reengajamento_inatividade",
    "crm.sla_primeiro_atendimento",
    "crm.fluxo_contratacao",
    "crm.gerar_sugestoes_ia",
    "leads.indexar_rag",
    "analise.gerar_explicacoes_risco",
    "crm.gerar_renovacoes_marca",
    "cadencia.enviar_emails_pendentes",
    "cadencia.verificar_respostas_email",
    "registrabilidade.reconciliar_resultados",
    "juridico.executar_motor",
    "juridico.agendar_resumos",
    "vigilancia.executar_semanal",
    "plataforma.verificar_saude",
    "prospeccao.importar_cache_enviado",
    "atualizacoes.lembrar_pendentes",
    "feature_flags.avaliar_circuito",
)
INTERVALO_MANUTENCAO_HORARIA = timedelta(hours=1)
INTERVALO_COMUNICACAO_JURIDICA = timedelta(minutes=1)
INTERVALO_ALTO_RENOME = timedelta(days=7)


async def _executar_tarefa_manutencao(redis, tarefa: str) -> None:
    try:
        await processar_rastreado(tarefa, {})
    except Exception as exc:
        await redis.rpush(
            FAILED_KEY,
            json.dumps(
                {
                    "job": tarefa,
                    "erro": str(exc) or type(exc).__name__,
                    "falhou_em": datetime.now(UTC).isoformat(),
                }
            ),
        )


async def _loop_fila_principal(redis) -> None:
    """Consome QUEUE_KEY -- jobs sob demanda (disparados por ação do
    usuário: enriquecer_prospect, calcular_score etc)."""
    while True:
        await promover_retentativas(redis)
        bruto = await redis.brpoplpush(QUEUE_KEY, PROCESSING_KEY, timeout=5)
        if not bruto:
            continue
        try:
            job = json.loads(bruto)
            await processar_rastreado(job["tipo"], job.get("payload", {}), job.get("request_id"))
            await redis.lrem(PROCESSING_KEY, 1, bruto)
            await redis.hincrby(METRICS_KEY, "concluidos", 1)
        except Exception as exc:
            await redis.lrem(PROCESSING_KEY, 1, bruto)
            try:
                job = json.loads(bruto)
            except (TypeError, json.JSONDecodeError):
                job = {"id": "invalido", "tipo": "desconhecido", "payload": {}}
            job["tentativas"] = int(job.get("tentativas", 0)) + 1
            job["ultimo_erro"] = type(exc).__name__
            job["ultima_falha_em"] = datetime.now(UTC).isoformat()
            logger.exception(
                json.dumps(
                    {
                        "event": "WORKER_JOB_FAILED",
                        "request_id": job.get("request_id"),
                        "job_id": job.get("id"),
                        "job_type": job.get("tipo"),
                        "attempt": job["tentativas"],
                        "error": type(exc).__name__,
                    },
                    ensure_ascii=False,
                )
            )
            if job["tentativas"] < MAX_ATTEMPTS:
                await agendar_retry(redis, job)
            else:
                await redis.rpush(FAILED_KEY, json.dumps(job))
                await redis.hincrby(METRICS_KEY, "falhas", 1)


async def _atualizar_heartbeat_worker() -> None:
    """Fase 7 (painel técnico): prova de vida do worker -- consumidor de
    fila em loop, sem endpoint HTTP próprio pra ser checado ao vivo como a
    API. Falha silenciosa: heartbeat nunca pode derrubar a manutenção."""
    try:
        async with session_factory() as session:
            await session.execute(
                pg_insert(ProcessoHeartbeat)
                .values(processo="worker")
                .on_conflict_do_update(index_elements=["processo"], set_={"heartbeat_em": datetime.now(UTC)})
            )
            await session.commit()
    except Exception:
        return


async def _loop_manutencao(redis) -> None:
    """Achado FASE6-6/7/8 da auditoria (04/09/2026): antes, motor jurídico,
    cadências, retenção, vigilância e alto renome só rodavam dentro do "if
    not bruto" de _loop_fila_principal -- ou seja, só quando QUEUE_KEY ficava
    vazia por 5s seguidos. Se a fila de jobs sob demanda nunca esvaziasse,
    essas tarefas nunca rodariam. Loop independente, com seu próprio
    agendamento (sleep fixo), roda em paralelo via asyncio.gather em main() e
    nunca depende do estado da fila principal.

    Não usamos uma fila Redis própria para o AGENDAMENTO em si (a lista de
    tarefas é fixa e enumerada, não produzida dinamicamente por alguém) --
    FAILED_KEY continua sendo a fila de falhas compartilhada, para manter uma
    única fonte de "jobs que falharam" em vez de duas.
    """
    proxima_manutencao = datetime.now(UTC)
    proximo_alto_renome = datetime.now(UTC)
    while True:
        agora = datetime.now(UTC)
        if agora >= proxima_manutencao:
            for tarefa in TAREFAS_MANUTENCAO_HORARIA:
                await _executar_tarefa_manutencao(redis, tarefa)
            proxima_manutencao = datetime.now(UTC) + INTERVALO_MANUTENCAO_HORARIA
        if agora >= proximo_alto_renome:
            await _executar_tarefa_manutencao(redis, "alto_renome.sincronizar")
            proximo_alto_renome = datetime.now(UTC) + INTERVALO_ALTO_RENOME
        await _atualizar_heartbeat_worker()
        await asyncio.sleep(30)


async def _loop_comunicacao_juridica(redis) -> None:
    """Entrega a caixa de saída sem esperar o próximo ciclo horário."""
    proxima_execucao = datetime.now(UTC)
    while True:
        if datetime.now(UTC) >= proxima_execucao:
            await _executar_tarefa_manutencao(redis, "juridico.processar_comunicacoes")
            proxima_execucao = datetime.now(UTC) + INTERVALO_COMUNICACAO_JURIDICA
        await asyncio.sleep(10)


async def main() -> None:
    redis = cliente_redis()
    # Recupera trabalhos que ficaram em processamento apos encerramento abrupto.
    while await redis.llen(PROCESSING_KEY):
        bruto_pendente = await redis.rpop(PROCESSING_KEY)
        if bruto_pendente:
            await redis.lpush(QUEUE_KEY, bruto_pendente)
    await asyncio.gather(_loop_fila_principal(redis), _loop_manutencao(redis), _loop_comunicacao_juridica(redis))


if __name__ == "__main__":
    asyncio.run(main())
