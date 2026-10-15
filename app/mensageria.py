"""Mensageria (WhatsApp): consentimento, lista de supressão e fila de envio.

Esta camada NÃO fala com o WhatsApp. Ela decide SE e QUANDO uma mensagem pode sair; quem entrega é um
provedor (``ProvedorMensagens``), ligado só quando houver número e gateway configurados. Enquanto
``settings.whatsapp_enabled`` for falso, ou não houver provedor, a fila apenas acumula e nada é enviado.

Regras que protegem o número e o titular:
- vigilância e andamento só saem para telefones com consentimento registrado;
- prospecção só sai para telefones que NÃO estão na lista de supressão, em dia útil e horário comercial, com
  limite diário baixo, espera aleatória entre mensagens e pausa automática se as falhas aumentarem;
- toda mensagem de prospecção leva a instrução para sair; "SAIR" por resposta revoga todas as finalidades;
- o consentimento é conferido de novo no momento do envio (revogar cancela o que estava na fila).
"""

import logging
import random
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cadencia_email import FUSO_COMERCIAL, dentro_do_horario_comercial, pausar_envios_pendentes_do_lead
from app.models import AlertaSistema, ConsentimentoCanal, HistoricoAlertaVigilancia, Lead, MensagemSaida, PausaCanal
from app.settings import get_settings

logger = logging.getLogger("ze_registra.mensageria")

CANAL_WHATSAPP = "whatsapp"
FINALIDADES = ("vigilancia", "andamento", "prospeccao")
FINALIDADES_COM_CONSENTIMENTO = frozenset({"vigilancia", "andamento"})
BASES_LEGAIS = ("consentimento", "legitimo_interesse")
AVISO_SAIDA = "Para não receber mais mensagens, responda SAIR."
TAMANHO_MAXIMO_MENSAGEM = 4000
PALAVRAS_DE_SAIDA = frozenset(
    {"sair", "parar", "pare", "cancelar", "cancela", "stop", "descadastrar", "remover", "nao quero receber"}
)


class ProvedorMensagens(Protocol):
    """Quem entrega a mensagem de fato (ex.: um gateway de WhatsApp). Devolve o id da mensagem no provedor.

    ``chave_idempotencia`` é estável por mensagem: o provedor deve usá-la para não duplicar um envio repetido.
    """

    async def enviar_texto(self, sessao: str, telefone: str, texto: str, chave_idempotencia: str) -> str: ...


class ErroEnvioPermanente(Exception):
    """O provedor recusou de um jeito que repetir não adianta (ex.: número que não existe no WhatsApp)."""


class ErroEnvioIncerto(Exception):
    """Não se sabe se a mensagem saiu (ex.: a conexão caiu depois do pedido). Reenviar poderia duplicar:
    a mensagem fica "incerta" para conferência humana, sem tentativa automática."""


def obter_provedor() -> ProvedorMensagens | None:
    """Provedor configurado (gateway OpenWA), ou None quando faltam a URL ou a chave."""
    from app.openwa import criar_provedor

    return criar_provedor()


def normalizar_telefone(valor: str | None) -> str | None:
    """Celular brasileiro em dígitos com DDI (55 + DDD + 9 + 8 dígitos), ou None se não for um celular válido."""
    digitos = re.sub(r"\D", "", valor or "").lstrip("0")
    if len(digitos) in (10, 11):
        digitos = "55" + digitos
    if len(digitos) != 13 or not digitos.startswith("55"):
        return None
    ddd, primeiro = digitos[2:4], digitos[4]
    if not ("11" <= ddd <= "99") or ddd.startswith("0") or primeiro != "9":
        return None
    return digitos


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _tem_instrucao_de_saida(texto: str) -> bool:
    """True se o texto já ensina a sair (ex.: "responda SAIR"), e não só contém a palavra "sair" por acaso."""
    normalizado = _sem_acento(texto.lower())
    return bool(re.search(r"\b(responda|digite|envie|mande|escreva|responder)\b[^.\n]{0,30}\bsair\b", normalizado))


def eh_pedido_de_saida(texto: str | None) -> bool:
    """True se a resposta do titular é um pedido para parar de receber mensagens."""
    limpo = re.sub(r"[^a-z ]", "", _sem_acento((texto or "").lower())).strip()
    return limpo in PALAVRAS_DE_SAIDA


def _validar_finalidade(finalidade: str) -> None:
    if finalidade not in FINALIDADES:
        raise ValueError(f"Finalidade inválida: use {', '.join(FINALIDADES)}")


async def _consentimento(
    session: AsyncSession, organizacao_id: int, finalidade: str, telefone: str
) -> ConsentimentoCanal | None:
    return (
        await session.execute(
            select(ConsentimentoCanal).where(
                ConsentimentoCanal.organizacao_id == organizacao_id,
                ConsentimentoCanal.canal == CANAL_WHATSAPP,
                ConsentimentoCanal.finalidade == finalidade,
                ConsentimentoCanal.telefone == telefone,
            )
        )
    ).scalar_one_or_none()


async def registrar_consentimento(
    session: AsyncSession,
    *,
    organizacao_id: int,
    finalidade: str,
    telefone: str,
    origem: str,
    registrado_por: str,
    base_legal: str = "consentimento",
    titular_tipo: str | None = None,
    titular_id: int | None = None,
    evidencia: dict | None = None,
    agora: datetime | None = None,
) -> ConsentimentoCanal:
    """Registra (ou renova) o consentimento de um telefone. Não commita: quem chama decide."""
    _validar_finalidade(finalidade)
    numero = normalizar_telefone(telefone)
    if numero is None:
        raise ValueError("Telefone inválido: informe um celular brasileiro com DDD")
    if base_legal not in BASES_LEGAIS:
        raise ValueError("Base legal inválida")
    if finalidade in FINALIDADES_COM_CONSENTIMENTO and base_legal != "consentimento":
        raise ValueError("Vigilância e andamento exigem consentimento do titular")
    if base_legal == "consentimento" and not (evidencia and origem.strip()):
        raise ValueError("Consentimento exige a origem e a evidência (ex.: data, canal e texto aceito)")
    agora = agora or datetime.now(UTC)
    registro = await _consentimento(session, organizacao_id, finalidade, numero)
    if registro is not None and registro.status == "revogado" and base_legal != "consentimento":
        # Quem pediu para sair só volta com um novo consentimento do próprio titular, nunca por decisão interna.
        raise ValueError("Telefone na lista de supressão: só volta com novo consentimento do titular")
    if registro is None:
        registro = ConsentimentoCanal(
            organizacao_id=organizacao_id, canal=CANAL_WHATSAPP, finalidade=finalidade, telefone=numero
        )
        session.add(registro)
    registro.status = "concedido"
    registro.base_legal = base_legal
    registro.titular_tipo = titular_tipo
    registro.titular_id = titular_id
    registro.origem = origem.strip()[:60]
    registro.evidencia = dict(evidencia or {})
    registro.registrado_por = registrado_por[:254]
    registro.concedido_em = agora
    registro.revogado_em = None
    return registro


async def _cancelar_pendentes(session: AsyncSession, organizacao_id: int, finalidade: str, telefone: str) -> int:
    resultado = await session.execute(
        update(MensagemSaida)
        .where(
            MensagemSaida.organizacao_id == organizacao_id,
            MensagemSaida.canal == CANAL_WHATSAPP,
            MensagemSaida.finalidade == finalidade,
            MensagemSaida.telefone == telefone,
            MensagemSaida.status == "pendente",
        )
        .values(status="cancelada", motivo_bloqueio="consentimento_revogado")
        .execution_options(synchronize_session=False)
    )
    return resultado.rowcount or 0


async def revogar_consentimento(
    session: AsyncSession,
    *,
    organizacao_id: int,
    finalidade: str,
    telefone: str,
    origem: str,
    registrado_por: str,
    agora: datetime | None = None,
) -> ConsentimentoCanal:
    """Revoga o consentimento (cria o registro de supressão se não havia) e cancela o que estava na fila."""
    _validar_finalidade(finalidade)
    numero = normalizar_telefone(telefone)
    if numero is None:
        raise ValueError("Telefone inválido: informe um celular brasileiro com DDD")
    agora = agora or datetime.now(UTC)
    registro = await _consentimento(session, organizacao_id, finalidade, numero)
    if registro is None:
        registro = ConsentimentoCanal(
            organizacao_id=organizacao_id, canal=CANAL_WHATSAPP, finalidade=finalidade, telefone=numero, evidencia={}
        )
        session.add(registro)
    registro.status = "revogado"
    registro.revogado_em = agora
    registro.origem = origem.strip()[:60]
    registro.registrado_por = registrado_por[:254]
    await _cancelar_pendentes(session, organizacao_id, finalidade, numero)
    return registro


async def processar_pedido_de_saida(
    session: AsyncSession, *, organizacao_id: int, telefone: str, texto: str, agora: datetime | None = None
) -> bool:
    """Resposta "SAIR" do titular: revoga TODAS as finalidades do telefone. Devolve True se era um pedido."""
    if not eh_pedido_de_saida(texto) or normalizar_telefone(telefone) is None:
        return False
    for finalidade in FINALIDADES:
        await revogar_consentimento(
            session,
            organizacao_id=organizacao_id,
            finalidade=finalidade,
            telefone=telefone,
            origem="resposta_sair",
            registrado_por="titular",
            agora=agora,
        )
    return True


async def verificar_permissao_envio(
    session: AsyncSession, organizacao_id: int, finalidade: str, telefone: str
) -> tuple[bool, str | None]:
    """(pode_enviar, motivo_se_nao). Vigilância/andamento pedem consentimento; prospecção só não pode estar suprimida."""
    if finalidade not in FINALIDADES:
        return False, "finalidade_invalida"
    registro = await _consentimento(session, organizacao_id, finalidade, telefone)
    if registro is not None and registro.status == "revogado":
        return False, "consentimento_revogado"
    if finalidade in FINALIDADES_COM_CONSENTIMENTO and registro is None:
        return False, "sem_consentimento"
    return True, None


async def enfileirar_mensagem(
    session: AsyncSession,
    *,
    organizacao_id: int,
    finalidade: str,
    telefone: str,
    corpo: str,
    chave_idempotencia: str,
    titular_tipo: str | None = None,
    titular_id: int | None = None,
    agendada_para: datetime | None = None,
) -> MensagemSaida:
    """Coloca uma mensagem na fila. Sem permissão, ela fica registrada como "bloqueada" (trilha de auditoria)."""
    _validar_finalidade(finalidade)
    numero = normalizar_telefone(telefone)
    if numero is None:
        raise ValueError("Telefone inválido: informe um celular brasileiro com DDD")
    texto = (corpo or "").strip()
    if not texto:
        raise ValueError("A mensagem não pode ser vazia")
    if finalidade == "prospeccao" and not _tem_instrucao_de_saida(texto):
        texto = f"{texto}\n\n{AVISO_SAIDA}"
    if len(texto) > TAMANHO_MAXIMO_MENSAGEM:
        raise ValueError("Mensagem longa demais")
    existente = await _mensagem_por_chave(session, organizacao_id, chave_idempotencia)
    if existente is not None:
        if existente.status == "bloqueada":
            # Bloqueada por falta de consentimento: se ele chegou depois, a mesma mensagem passa a poder sair.
            permitido_agora, _ = await verificar_permissao_envio(session, organizacao_id, finalidade, numero)
            if permitido_agora:
                existente.status = "pendente"
                existente.motivo_bloqueio = None
                existente.agendada_para = agendada_para or datetime.now(UTC)
        return existente
    permitido, motivo = await verificar_permissao_envio(session, organizacao_id, finalidade, numero)
    mensagem = MensagemSaida(
        organizacao_id=organizacao_id,
        canal=CANAL_WHATSAPP,
        finalidade=finalidade,
        telefone=numero,
        titular_tipo=titular_tipo,
        titular_id=titular_id,
        corpo=texto,
        status="pendente" if permitido else "bloqueada",
        motivo_bloqueio=motivo,
        tentativas=0,
        agendada_para=agendada_para or datetime.now(UTC),
        chave_idempotencia=chave_idempotencia[:120],
    )
    try:
        async with session.begin_nested():
            session.add(mensagem)
            await session.flush()
    except IntegrityError:
        # Outra requisição enfileirou a mesma chave ao mesmo tempo: vale a que chegou primeiro.
        existente = await _mensagem_por_chave(session, organizacao_id, chave_idempotencia)
        if existente is None:
            raise
        return existente
    return mensagem


async def _mensagem_por_chave(session: AsyncSession, organizacao_id: int, chave: str) -> MensagemSaida | None:
    return (
        await session.execute(
            select(MensagemSaida).where(
                MensagemSaida.organizacao_id == organizacao_id, MensagemSaida.chave_idempotencia == chave[:120]
            )
        )
    ).scalar_one_or_none()


def _inicio_do_dia_utc(agora: datetime) -> datetime:
    local = agora.astimezone(FUSO_COMERCIAL)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def _limite_diario(finalidade: str) -> int:
    settings = get_settings()
    if finalidade == "prospeccao":
        return settings.whatsapp_limite_diario_prospeccao
    return settings.whatsapp_limite_diario_vigilancia


def _sessao_do_provedor(finalidade: str) -> str:
    return "prospeccao" if finalidade == "prospeccao" else "vigilancia"


async def _enviadas_hoje(session: AsyncSession, organizacao_id: int, finalidade: str, agora: datetime) -> int:
    grupo = ("prospeccao",) if finalidade == "prospeccao" else ("vigilancia", "andamento")
    return (
        await session.execute(
            select(func.count())
            .select_from(MensagemSaida)
            .where(
                MensagemSaida.organizacao_id == organizacao_id,
                MensagemSaida.finalidade.in_(grupo),
                MensagemSaida.status == "enviada",
                MensagemSaida.enviada_em >= _inicio_do_dia_utc(agora),
            )
        )
    ).scalar_one()


async def _prospeccao_pausada_por_falhas(session: AsyncSession, organizacao_id: int, agora: datetime) -> bool:
    erros = (
        await session.execute(
            select(func.count())
            .select_from(MensagemSaida)
            .where(
                MensagemSaida.organizacao_id == organizacao_id,
                MensagemSaida.finalidade == "prospeccao",
                MensagemSaida.ultima_falha_em >= agora - timedelta(hours=1),
            )
        )
    ).scalar_one()
    return erros >= get_settings().whatsapp_pausa_falhas_por_hora


async def _ultimo_envio_prospeccao(session: AsyncSession, organizacao_id: int) -> datetime | None:
    return (
        await session.execute(
            select(func.max(MensagemSaida.enviada_em)).where(
                MensagemSaida.organizacao_id == organizacao_id,
                MensagemSaida.finalidade == "prospeccao",
                MensagemSaida.status == "enviada",
            )
        )
    ).scalar_one_or_none()


async def _refletir_no_alerta_da_vigilancia(
    session: AsyncSession, mensagem: MensagemSaida, status: str, agora: datetime
) -> None:
    """Mensagem de vigilância: o alerta de origem (mesma chave) passa a "enviado" ou "falhou" no histórico."""
    if mensagem.finalidade != "vigilancia" or not mensagem.chave_idempotencia.startswith("vigilancia:"):
        return
    valores = {"status": status}
    if status == "enviado":
        valores["enviado_em"] = agora
    await session.execute(
        update(HistoricoAlertaVigilancia)
        .where(
            HistoricoAlertaVigilancia.organizacao_id == mensagem.organizacao_id,
            HistoricoAlertaVigilancia.idempotency_key == mensagem.chave_idempotencia,
        )
        .values(**valores)
        .execution_options(synchronize_session=False)
    )


async def _refletir_incertas_no_historico_da_vigilancia(session: AsyncSession) -> None:
    """Mensagem "incerta" (resposta perdida ou envio interrompido): o alerta de origem também vira "incerta",
    para o relatório da vigilância não mostrar "pendente" para sempre."""
    incertas = select(MensagemSaida.chave_idempotencia).where(
        MensagemSaida.finalidade == "vigilancia", MensagemSaida.status == "incerta"
    )
    await session.execute(
        update(HistoricoAlertaVigilancia)
        .where(HistoricoAlertaVigilancia.status == "pendente", HistoricoAlertaVigilancia.idempotency_key.in_(incertas))
        .values(status="incerta")
        .execution_options(synchronize_session=False)
    )


PRAZO_ENVIO_INTERROMPIDO = timedelta(minutes=15)


async def _marcar_envios_interrompidos(session: AsyncSession, agora: datetime) -> int:
    """Mensagem reservada ("enviando") há muito tempo: o worker caiu no meio do envio.

    Não sabemos se a mensagem saiu. Reenviar poderia duplicar para o titular; por isso ela vira "incerta"
    e fica para conferência humana (aparece na fila), sem reenvio automático.
    """
    resultado = await session.execute(
        update(MensagemSaida)
        .where(MensagemSaida.status == "enviando", MensagemSaida.atualizado_em < agora - PRAZO_ENVIO_INTERROMPIDO)
        .values(status="incerta", ultimo_erro="Envio interrompido: confira no WhatsApp antes de reenviar.")
        .execution_options(synchronize_session=False)
    )
    return resultado.rowcount or 0


async def _reservar(session: AsyncSession, mensagem: MensagemSaida) -> bool:
    """Reserva a mensagem para este worker de forma atômica (pendente -> enviando).

    Só vale se ela ainda estiver pendente: um cancelamento ou revogação que tenha acontecido depois da
    leitura impede o envio, e dois workers nunca enviam a mesma mensagem.
    """
    resultado = await session.execute(
        update(MensagemSaida)
        .where(MensagemSaida.id == mensagem.id, MensagemSaida.status == "pendente")
        .values(status="enviando", tentativas=MensagemSaida.tentativas + 1)
        .returning(MensagemSaida.id)
        .execution_options(synchronize_session=False)
    )
    if resultado.first() is None:
        return False
    mensagem.status = "enviando"
    mensagem.tentativas = (mensagem.tentativas or 0) + 1
    await session.commit()  # a reserva fica gravada ANTES de falar com o provedor
    return True


def _adiar(mensagem: MensagemSaida, agora: datetime, minutos: int) -> None:
    """Empurra para frente uma mensagem que não pode sair agora, para não travar a cabeça da fila."""
    mensagem.agendada_para = agora + timedelta(minutes=minutos)


async def processar_fila(
    session: AsyncSession,
    provedor: ProvedorMensagens | None,
    *,
    agora: datetime | None = None,
    sortear: Callable[[float, float], float] = random.uniform,
) -> dict:
    """Job do worker: envia as mensagens vencidas respeitando consentimento, limites e horários."""
    settings = get_settings()
    agora = agora or datetime.now(UTC)
    if not settings.whatsapp_enabled:
        return {"enviadas": 0, "falhas": 0, "bloqueadas": 0, "motivo": "desligado"}
    if provedor is None:
        return {"enviadas": 0, "falhas": 0, "bloqueadas": 0, "motivo": "sem_provedor"}

    interrompidas = await _marcar_envios_interrompidos(session, agora)
    pausadas = await _pausas_vigentes(session, agora)
    pendentes = (
        (
            await session.execute(
                select(MensagemSaida)
                .where(
                    MensagemSaida.canal == CANAL_WHATSAPP,
                    MensagemSaida.status == "pendente",
                    MensagemSaida.agendada_para <= agora,
                )
                .order_by(MensagemSaida.agendada_para)
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    enviadas = falhas = bloqueadas = 0
    prospeccao_ja_enviou_nesta_rodada: set[int] = set()
    for mensagem in pendentes:
        permitido, motivo = await verificar_permissao_envio(
            session, mensagem.organizacao_id, mensagem.finalidade, mensagem.telefone
        )
        if not permitido:
            mensagem.status = "bloqueada"
            mensagem.motivo_bloqueio = motivo
            bloqueadas += 1
            continue
        if mensagem.titular_tipo == "lead" and mensagem.finalidade == "prospeccao":
            # Passo de cadência agendado dias antes: o lead é conferido de novo na hora de enviar.
            lead = await session.get(Lead, mensagem.titular_id)
            if lead is None or lead.anonimizado_em or lead.arquivado_em or lead.cadencia_opt_out_em:
                mensagem.status = "cancelada"
                mensagem.motivo_bloqueio = "lead_indisponivel"
                bloqueadas += 1
                continue
        if (mensagem.organizacao_id, _sessao_do_provedor(mensagem.finalidade)) in pausadas:
            _adiar(mensagem, agora, 10)  # sessão pausada (ex.: restrição do número); volta sozinha quando acabar
            continue
        if await _enviadas_hoje(session, mensagem.organizacao_id, mensagem.finalidade, agora) >= _limite_diario(
            mensagem.finalidade
        ):
            _adiar(mensagem, agora, 10)
            continue
        if mensagem.finalidade == "prospeccao":
            if mensagem.organizacao_id in prospeccao_ja_enviou_nesta_rodada:
                _adiar(mensagem, agora, 1)
                continue
            if not dentro_do_horario_comercial(agora, settings.whatsapp_horario_inicio, settings.whatsapp_horario_fim):
                _adiar(mensagem, agora, 10)
                continue
            if await _prospeccao_pausada_por_falhas(session, mensagem.organizacao_id, agora):
                logger.warning("Prospecção por WhatsApp pausada por falhas (org %s).", mensagem.organizacao_id)
                _adiar(mensagem, agora, 10)
                continue
            ultimo = await _ultimo_envio_prospeccao(session, mensagem.organizacao_id)
            espera = sortear(settings.whatsapp_intervalo_min_segundos, settings.whatsapp_intervalo_max_segundos)
            if ultimo is not None and (agora - ultimo).total_seconds() < espera:
                _adiar(mensagem, agora, 1)
                continue
        if not await _reservar(session, mensagem):
            continue  # cancelada (ou tratada por outro worker) depois da leitura
        try:
            id_provedor = await provedor.enviar_texto(
                _sessao_do_provedor(mensagem.finalidade),
                mensagem.telefone,
                mensagem.corpo,
                mensagem.chave_idempotencia,
            )
        except Exception as exc:
            mensagem.ultimo_erro = str(exc)[:300] or type(exc).__name__
            mensagem.ultima_falha_em = agora
            if isinstance(exc, ErroEnvioIncerto):
                mensagem.status = "incerta"
            elif isinstance(exc, ErroEnvioPermanente) or mensagem.tentativas >= settings.whatsapp_max_tentativas:
                mensagem.status = "falhou"
            else:
                mensagem.status = "pendente"
                mensagem.agendada_para = agora + timedelta(minutes=5 * mensagem.tentativas)
            falhas += 1
            logger.warning("Falha ao enviar mensagem (id=%s): %s", mensagem.id, exc)
            if mensagem.status == "falhou":
                await _refletir_no_alerta_da_vigilancia(session, mensagem, "falhou", agora)
            await session.commit()
            continue
        mensagem.status = "enviada"
        mensagem.enviada_em = agora
        mensagem.provedor_mensagem_id = (id_provedor or "")[:120] or None
        mensagem.ultimo_erro = None
        enviadas += 1
        if mensagem.finalidade == "prospeccao":
            prospeccao_ja_enviou_nesta_rodada.add(mensagem.organizacao_id)
        await _refletir_no_alerta_da_vigilancia(session, mensagem, "enviado", agora)
        await session.commit()  # grava o resultado já, sem esperar o fim do lote
    await _refletir_incertas_no_historico_da_vigilancia(session)
    return {"enviadas": enviadas, "falhas": falhas, "bloqueadas": bloqueadas, "interrompidas": interrompidas}


# --- eventos do gateway (webhook) ---

FINALIDADES_DA_SESSAO = {"vigilancia": ("vigilancia", "andamento"), "prospeccao": ("prospeccao",)}
PAUSA_POR_RESTRICAO = timedelta(hours=24)
JANELA_ALERTA_REPETIDO = timedelta(minutes=10)


def telefone_do_remetente(dados: dict) -> str | None:
    """Telefone de quem escreveu (celular em dígitos), ou None para grupos, mensagens nossas e ids sem número."""
    if dados.get("fromMe") or dados.get("isGroup"):
        return None
    candidato = str(dados.get("senderPhone") or "")
    if not candidato:
        origem = str(dados.get("from") or "")
        if origem.endswith("@c.us"):
            candidato = origem[: -len("@c.us")]
    return normalizar_telefone(candidato)


async def _alertar(
    session: AsyncSession, organizacao_id: int, severidade: str, codigo: str, texto: str, detalhes: dict, agora: datetime
) -> None:
    """Cria um alerta no painel, sem repetir o mesmo alerta nos últimos minutos (o webhook entrega ao menos uma vez)."""
    recente = (
        await session.execute(
            select(func.count())
            .select_from(AlertaSistema)
            .where(
                AlertaSistema.organizacao_id == organizacao_id,
                AlertaSistema.codigo == codigo,
                AlertaSistema.criado_em >= agora - JANELA_ALERTA_REPETIDO,
            )
        )
    ).scalar_one()
    if recente:
        return
    session.add(
        AlertaSistema(
            organizacao_id=organizacao_id, severidade=severidade, codigo=codigo, mensagem=texto, detalhes=detalhes
        )
    )


async def _pausar_conversa_do_telefone(session: AsyncSession, telefone: str) -> bool:
    """Resposta comum de quem recebeu uma mensagem nossa: a equipe assume e a cadência automática daquele
    telefone para. Vale só para a organização da conversa (a da mensagem enviada mais recentemente para o
    número), e inclui os e-mails pendentes da cadência dos leads daquele telefone. Devolve False se nunca
    enviamos nada para o número (a resposta não é de uma conversa nossa)."""
    organizacao = (
        await session.execute(
            select(MensagemSaida.organizacao_id)
            .where(
                MensagemSaida.telefone == telefone,
                MensagemSaida.canal == CANAL_WHATSAPP,
                MensagemSaida.status == "enviada",
            )
            .order_by(MensagemSaida.enviada_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if organizacao is None:
        return False
    leads = (
        (
            await session.execute(
                select(MensagemSaida.titular_id)
                .where(
                    MensagemSaida.organizacao_id == organizacao,
                    MensagemSaida.telefone == telefone,
                    MensagemSaida.titular_tipo == "lead",
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    for lead_id in leads:
        await pausar_envios_pendentes_do_lead(session, organizacao, lead_id)  # e-mails e WhatsApp do lead
    await session.execute(
        update(MensagemSaida)
        .where(
            MensagemSaida.organizacao_id == organizacao,
            MensagemSaida.telefone == telefone,
            MensagemSaida.canal == CANAL_WHATSAPP,
            MensagemSaida.finalidade == "prospeccao",
            MensagemSaida.status == "pendente",
        )
        .values(status="cancelada", motivo_bloqueio="cliente_respondeu")
        .execution_options(synchronize_session=False)
    )
    return True


async def _organizacoes_do_telefone(session: AsyncSession, telefone: str, padrao: int) -> list[int]:
    """Organizações com registro ou mensagem para o telefone, mais a padrão (consulta cross-tenant, só leitura)."""
    achadas = set(
        (await session.execute(select(ConsentimentoCanal.organizacao_id).where(ConsentimentoCanal.telefone == telefone)))
        .scalars()
        .all()
    )
    achadas |= set(
        (await session.execute(select(MensagemSaida.organizacao_id).where(MensagemSaida.telefone == telefone)))
        .scalars()
        .all()
    )
    return sorted(achadas | {padrao})


async def _definir_pausa(session: AsyncSession, organizacao_id: int, sessao: str, ate: datetime, motivo: str) -> None:
    pausa = (
        await session.execute(
            select(PausaCanal).where(PausaCanal.organizacao_id == organizacao_id, PausaCanal.sessao == sessao)
        )
    ).scalar_one_or_none()
    if pausa is None:
        pausa = PausaCanal(organizacao_id=organizacao_id, sessao=sessao)
        session.add(pausa)
    pausa.ate = ate
    pausa.motivo = motivo


async def _pausas_vigentes(session: AsyncSession, agora: datetime) -> set[tuple[int, str]]:
    linhas = (await session.execute(select(PausaCanal).where(PausaCanal.ate > agora))).scalars().all()
    return {(pausa.organizacao_id, pausa.sessao) for pausa in linhas}


async def _registrar_recibo(session: AsyncSession, organizacao_id: int, dados: dict, agora: datetime) -> bool:
    id_provedor = str(dados.get("messageId") or dados.get("id") or "")
    status = dados.get("status")
    if not id_provedor or status not in ("delivered", "read", "failed"):
        return False
    mensagem = (
        await session.execute(
            select(MensagemSaida).where(MensagemSaida.provedor_mensagem_id == id_provedor)
        )
    ).scalar_one_or_none()
    if mensagem is None:
        return False
    if status == "failed":
        mensagem.status = "falhou"
        mensagem.ultimo_erro = "A entrega falhou no WhatsApp."
        mensagem.ultima_falha_em = agora
        return True
    mensagem.entregue_em = mensagem.entregue_em or agora
    if status == "read":
        mensagem.lida_em = mensagem.lida_em or agora
    return True


async def processar_evento_gateway(
    session: AsyncSession,
    *,
    organizacao_id: int,
    evento: str,
    dados: dict,
    sessao_logica: str | None = None,
    agora: datetime | None = None,
) -> str:
    """Aplica um evento do gateway. Devolve o que foi feito: saida, recibo, alerta ou ignorado. Não commita."""
    agora = agora or datetime.now(UTC)
    if evento == "message.received":
        telefone = telefone_do_remetente(dados)
        if telefone and eh_pedido_de_saida(dados.get("body")):
            # O telefone sai de TODAS as organizações que já falaram com ele (o gateway é da plataforma).
            for organizacao in await _organizacoes_do_telefone(session, telefone, organizacao_id):
                await processar_pedido_de_saida(
                    session, organizacao_id=organizacao, telefone=telefone, texto=dados["body"], agora=agora
                )
            return "saida"
        if telefone and await _pausar_conversa_do_telefone(session, telefone):
            return "resposta"  # resposta comum (não é pedido de saída): a equipe assume a conversa
        return "ignorado"
    if evento in ("message.ack", "message.failed"):
        if evento == "message.failed":
            dados = {**dados, "status": "failed"}
        return "recibo" if await _registrar_recibo(session, organizacao_id, dados, agora) else "ignorado"
    if evento == "session.restriction":
        if dados.get("active"):
            await _alertar(
                session,
                organizacao_id,
                "critico",
                "WHATSAPP_RESTRICAO",
                "O WhatsApp restringiu o número: os envios dele foram pausados por 24 horas.",
                {"sessao": sessao_logica, "tipo": dados.get("kind"), "codigo": dados.get("code")},
                agora,
            )
            if sessao_logica in FINALIDADES_DA_SESSAO:
                # Pausa PERSISTIDA por sessão: vale também para mensagens enfileiradas depois do aviso.
                await _definir_pausa(
                    session, organizacao_id, sessao_logica, agora + PAUSA_POR_RESTRICAO, "restricao_do_numero"
                )
        else:
            if sessao_logica in FINALIDADES_DA_SESSAO:
                await _definir_pausa(session, organizacao_id, sessao_logica, agora, "restricao_encerrada")
            await _alertar(
                session, organizacao_id, "info", "WHATSAPP_RESTRICAO_ENCERRADA",
                "A restrição do número no WhatsApp foi encerrada.", {"sessao": sessao_logica}, agora,
            )
        return "alerta"
    if evento in ("session.disconnected", "session.reconnect_loop"):
        codigo = "WHATSAPP_DESCONECTADO" if evento == "session.disconnected" else "WHATSAPP_RECONEXAO_EM_LOOP"
        await _alertar(
            session, organizacao_id, "aviso", codigo,
            "A sessão do WhatsApp perdeu a conexão. Confira o aparelho e o QR code no gateway.",
            {"sessao": sessao_logica, "motivo": dados.get("reason")}, agora,
        )
        return "alerta"
    return "ignorado"
