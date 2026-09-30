from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SENHAS_PLACEHOLDER = frozenset({"", "altere-esta-senha", "defina-uma-senha-forte"})
TAMANHO_MINIMO_SENHA = 12
TAMANHO_MINIMO_TOKEN_INTEGRACAO = 32


class Settings(BaseSettings):
    app_name: str = "INPI API"
    app_env: str = "development"
    app_version: str = "development"
    # Fase 7 (painel tecnico): promovido de ARG pra ENV no Dockerfile --
    # "unknown" fora de um build via docker/deploy.sh (ex.: rodando local).
    git_sha: str = "unknown"
    database_url: str = "postgresql+asyncpg://inpi:inpi@localhost:5432/inpi"
    app_db_password: str | None = None
    admin_username: str = "admin"
    admin_password: str = "altere-esta-senha"
    admin_email: str = "admin@zeregistra.local"
    session_duration_hours: int = 8
    session_idle_minutes: int = 60
    alto_renome_page_url: str = "https://www.gov.br/inpi/pt-br/servicos/marcas/alto-renome/"
    # dadosabertos.rfb.gov.br não responde a partir da rede desta VPS (timeout de
    # TCP, confirmado em 03/09/2026 -- outros hosts gov.br respondem normalmente,
    # então não é bloqueio de saída local). Compartilhamento público via WebDAV
    # (Nextcloud/SERPRO+) é a rota que efetivamente funciona; token pode expirar/
    # rotacionar, por isso fica configurável em vez de fixo no código.
    rfb_cnpj_base_url: str = "https://arquivos.receitafederal.gov.br/public.php/webdav"
    rfb_cnpj_share_token: str = "YggdBLfdninEJX9"
    # Cache em disco dos arquivos baixados (achado de 03/09/2026: esta VPS
    # reinicia sozinha algumas vezes por dia -- cada arquivo baixado fica
    # salvo aqui pra uma nova tentativa não precisar rebaixar vários GB do
    # zero). Caminho dentro do volume persistente ./data:/app/data, não /tmp
    # (que é limpo no reinício).
    rfb_cnpj_cache_dir: str = "/app/data/rfb_cnpj_cache"
    audit_ip_salt: str = "desenvolvimento-local"
    security_master_key: str = "desenvolvimento-local-chave-mestra"
    security_master_key_version: int = 1
    security_master_key_previous: str = ""
    security_master_key_previous_version: int = 0
    admin_force_https: bool = False
    integration_auth_enabled: bool = False
    inpi_integration_token: str = ""
    rpi_sync_interval_seconds: int = 21_600
    rpi_sync_start_number: int = 2900
    rpi_sync_poll_seconds: int = 10
    rpi_stale_hours: float = 12.0
    rpi_minimum_record_ratio: float = 0.50
    rpi_anomaly_reference_minimum: int = 1_000
    # Achado FASE6-9 da auditoria (04/09/2026): limiares de alerta de
    # plataforma (fila de falhas, taxa de erro e latência da API) --
    # verificados por app.alertas_plataforma.verificar_saude_plataforma.
    alerta_fila_falhas_limite: int = 5
    alerta_api_taxa_erro_limite: float = 0.05
    alerta_api_latencia_media_ms_limite: float = 2000.0
    # Achado FASE6-9d: diretório montado (somente leitura) com os dumps de
    # docker/backup-banco.sh -- 26h de folga sobre o cron diário (item
    # FASE6-11) cobre um atraso ocasional sem gerar alerta a cada execução.
    backups_dir: str = "/app/backups"
    alerta_backup_max_horas: float = 26.0
    # Fase 3 (notificações e confirmação de leitura): tempo de tolerância
    # antes de lembrar (via app.avisos_versao) as organizações com usuários
    # que ainda não confirmaram um AvisoVersao crítico.
    aviso_critico_lembrete_horas: float = 24.0
    # Achado FASE6-13 da auditoria (04/09/2026): varredura de malware nos
    # uploads do portal do cliente (app/api/portal_cliente.py). Desligado
    # por padrão -- só liga quando o serviço clamav estiver disponível
    # (compose.yaml); quando ligado e o scan falhar/der erro, o upload é
    # recusado (falha fechada -- nunca aceita um arquivo sem confirmação).
    clamav_enabled: bool = False
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    clamav_timeout_seconds: float = 15.0
    default_organization_slug: str = "ze-registra"
    default_organization_id: int = 1
    cors_allowed_origins: str = "*"
    redis_url: str = "redis://localhost:6379/0"
    redis_required: bool = False
    queue_retry_base_seconds: int = 5
    queue_retry_max_seconds: int = 300
    queue_idempotency_ttl_seconds: int = 86_400
    # Usa Redis para o rate limiting (necessário com múltiplos workers/instâncias).
    # Desligado por padrão: em processo único a janela em memória basta.
    ratelimit_redis_enabled: bool = False
    trusted_proxy_networks: str = "127.0.0.1/32,::1/128"
    password_reset_minutes: int = 30
    anonimizacao_token_minutos: int = 1440
    app_public_url: str = "http://localhost:8000"
    email_enabled: bool = False
    email_from_address: str = "nao-responda@zeregistra.local"
    email_from_name: str = "Zé Registra"
    # Destino do alerta de nova pesquisa recebida (equipe de atendimento). Vazio = desativado.
    equipe_atendimento_email: str = ""
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    # SSL implícito (ex.: porta 465, como Titan Email), em vez de STARTTLS (ex.: porta 587).
    smtp_ssl: bool = False
    smtp_timeout_seconds: float = 10.0
    smtp_max_attempts: int = 3
    # Limite contratado/configurado do remetente SMTP. O sistema não tenta
    # adivinhar a cota do provedor; usa este valor para o aviso preventivo.
    email_daily_limit: int = 500
    email_daily_warning_percent: int = 80
    # Pool opcional de SMTP. "single" preserva o comportamento legado;
    # "category" separa os e-mails comerciais no provedor secundário;
    # "failover" usa o secundário quando o principal esgota a cota.
    email_provider_strategy: str = "single"
    email_provider_quota_cooldown_minutes: int = 60
    email_secondary_operations: str = "prospeccao_lead,passo_cadencia"
    smtp_secondary_enabled: bool = False
    smtp_secondary_name: str = "Comercial"
    smtp_secondary_host: str = ""
    smtp_secondary_port: int = 587
    smtp_secondary_username: str = ""
    smtp_secondary_password: str = ""
    smtp_secondary_from_address: str = ""
    smtp_secondary_from_name: str = "Zé Registra"
    smtp_secondary_starttls: bool = True
    smtp_secondary_ssl: bool = False
    smtp_secondary_timeout_seconds: float = 10.0
    email_secondary_daily_limit: int = 500
    email_secondary_daily_warning_percent: int = 80
    # CAPTCHA da consulta pública (Cloudflare Turnstile, decisão do usuário
    # em 29/09/2026 -- pendência da Fase 12). Só é exigido com as duas chaves
    # preenchidas; vazias = desligado (comportamento anterior).
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""
    turnstile_timeout_seconds: float = 5.0
    oauth_attempt_minutes: int = 10
    oauth_auto_link_verified_email: bool = True
    google_oauth_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    apple_oauth_enabled: bool = False
    apple_client_id: str = ""
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key: str = ""
    gateway_webhook_secret: str = ""
    # SaaS B2B subscriptions use Stripe Checkout. Credentials are injected
    # through deployment secrets; never returned by admin APIs.
    stripe_saas_enabled: bool = False
    stripe_saas_secret_key: str = ""
    stripe_saas_webhook_secret: str = ""
    stripe_saas_success_url: str = "https://app.zeregistra.com.br/contratacao/retorno?status=sucesso"
    stripe_saas_cancel_url: str = "https://app.zeregistra.com.br/contratacao/retorno?status=cancelado"
    clicksign_enabled: bool = False
    clicksign_base_url: str = "https://sandbox.clicksign.com/api/v3"
    clicksign_api_token: str = ""
    clicksign_webhook_secret: str = ""
    clicksign_webhook_url: str = ""
    health_api_key: str = ""
    # Cadências reais por e-mail (Fase 9 do plano Leads/CRM). Sem webhook de
    # provedor (SMTP puro), "enviado" é o máximo garantido -- não "entregue".
    cadencia_email_horario_inicio: int = 8
    cadencia_email_horario_fim: int = 19
    cadencia_email_max_tentativas: int = 3
    # Detecção de resposta + pausa automática exige uma caixa de e-mail dedicada
    # via IMAP. Desligado por padrão -- fica inerte até credenciais reais serem
    # configuradas (mesmo princípio de email_enabled).
    imap_enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_ssl: bool = True
    # IA em sombra (leads): resumo de histórico + sugestão de próxima ação,
    # nunca rascunho de mensagem pro cliente e nunca envio automático --
    # modelo local via Ollama, sem chave de API nem custo por chamada, para
    # nenhum dado de lead sair do servidor. Desligado por padrão -- fica
    # inerte até o serviço "ollama" (compose.yaml, profile "ia-sombra") ser
    # subido manualmente e a flag ser ligada (mesmo princípio de
    # imap_enabled/clamav_enabled: nunca liga sozinho).
    ia_sombra_enabled: bool = False
    ia_sombra_host: str = "ollama"
    ia_sombra_port: int = 11434
    ia_sombra_modelo: str = "qwen2.5:7b-instruct-q4_K_M"
    # Modelo de embeddings (mesmo servidor Ollama) usado pelo RAG local
    # (pgvector) para indexar leads com resultado conhecido e buscar
    # precedentes parecidos -- ver app.ia_sombra.gerar_embedding_ollama.
    # O RAG continua sempre local (embeddings nunca saem para uma API
    # externa), mesmo quando o provider de geração abaixo é "gemini".
    ia_sombra_embedding_modelo: str = "nomic-embed-text"
    ia_sombra_timeout_segundos: float = 120.0
    # Provider do modelo de geração (resumo/sugestão/explicação/qualificação)
    # usado pela IA em sombra -- "ollama" (padrão, local, sem chave de API)
    # ou "gemini" (API do Google, decisão do usuário em 11/09/2026: trocar o
    # modelo generalista da IA em sombra por Gemini). O RAG (embeddings)
    # continua sempre em Ollama, ver comentário acima -- só a geração do
    # texto final muda de provider. "gemini" exige gemini_api_key
    # preenchida; sem ela, gerar_sugestao_lead/gerar_explicacao_risco/
    # gerar_qualificacao_lead registram o erro em vez de travar o job (mesmo
    # tratamento de falha que já existia para o Ollama fora do ar).
    ia_sombra_provider: str = "ollama"
    gemini_api_key: str = ""
    # "gemini-2.5-flash" foi descontinuado para novos usuarios em 2026 (a
    # propria API passou a devolver 404 recomendando a troca) -- usamos o
    # alias "-latest" para sempre apontar ao modelo flash vigente, em vez de
    # fixar uma versao que a Google pode aposentar sem aviso prévio no app.
    gemini_modelo: str = "gemini-flash-latest"
    # Fallback automático (achado do usuário, 11/09/2026): quando o modelo
    # principal devolve 429 (cota por minuto do plano gratuito estourada,
    # visto ao vivo em produção -- 20 RPM para o "flash" cheio), a chamada
    # tenta na hora o modelo mais leve "flash-lite", que tem cota gratuita
    # mais generosa. Nunca troca por qualquer outro erro (só 429) -- outros
    # erros (chave inválida, timeout, etc.) continuam falhando direto.
    # Vazio desativa o fallback (só o modelo principal é tentado).
    gemini_modelo_fallback: str = "gemini-flash-lite-latest"
    gemini_timeout_segundos: float = 60.0
    # Salvaguarda de cota (achado do usuário, 11/09/2026): o plano gratuito
    # do Gemini tem só 10-15 RPM e 250 requisições/dia. Sem limite proprio,
    # os jobs horários de IA em sombra (até MAXIMO_LEADS_POR_EXECUCAO=20
    # leads cada, pensado pra Ollama local sem cota) estourariam a cota
    # diária em menos de uma tarde. Só se aplica quando ia_sombra_provider
    # == "gemini" -- Ollama continua usando MAXIMO_LEADS_POR_EXECUCAO normal.
    gemini_max_chamadas_por_execucao: int = 5
    gemini_intervalo_minimo_segundos: float = 7.0
    # Assistente interno de CRM (chat, decisão do usuário em 11/09/2026):
    # diferente da IA em sombra acima (que só sugere em segundo plano), este
    # é um chat que a equipe usa direto -- ainda assim só LÊ dados (nenhuma
    # ferramenta de escrita), sempre restrito à organização do usuário
    # logado, e reusa a mesma chave/modelo/pacing do Gemini acima.
    # Desligado por padrão, como todo recurso de IA deste projeto.
    assistente_crm_enabled: bool = False
    # Busca ao vivo por CNPJ direto no site público do INPI (pePI), além do
    # match por nome já feito na nossa base local -- achado do usuário
    # (08/09/2026): o pePI tem busca por CNPJ/CPF de titular, mais precisa
    # que nome normalizado. Desligada por padrão: pePI é um sistema legado
    # confirmado instável em teste manual (502/timeout repetidos) -- kill
    # switch para desligar sem deploy se piorar ou se o INPI bloquear
    # tráfego automatizado. Best-effort mesmo ligada: qualquer erro cai em
    # silêncio no fallback da base local (ver app/inpi_titular_live.py).
    prospeccao_titularidade_inpi_ao_vivo_enabled: bool = False
    prospeccao_titularidade_inpi_timeout_segundos: float = 8.0
    prospeccao_titularidade_inpi_cache_segundos: int = 86_400
    prospeccao_titularidade_inpi_cache_falha_segundos: int = 300
    prospeccao_titularidade_inpi_max_chamadas_minuto: int = 10
    prospeccao_titularidade_inpi_cache_maximo: int = 5_000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        origens = [origem.strip() for origem in self.cors_allowed_origins.split(",")]
        return [origem for origem in origens if origem]

    @property
    def trusted_proxy_cidrs(self) -> list[str]:
        return [item.strip() for item in self.trusted_proxy_networks.split(",") if item.strip()]

    @model_validator(mode="after")
    def exigir_senha_forte_em_producao(self) -> "Settings":
        if self.stripe_saas_enabled and not (self.stripe_saas_secret_key and self.stripe_saas_webhook_secret):
            raise ValueError(
                "STRIPE_SAAS_SECRET_KEY e STRIPE_SAAS_WEBHOOK_SECRET são obrigatórios "
                "quando STRIPE_SAAS_ENABLED estiver ativo."
            )
        if self.google_oauth_enabled and not (self.google_client_id and self.google_client_secret):
            raise ValueError(
                "GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET sao obrigatorios quando o Google OAuth estiver ativo."
            )
        if self.apple_oauth_enabled and not all(
            (self.apple_client_id, self.apple_team_id, self.apple_key_id, self.apple_private_key)
        ):
            raise ValueError(
                "APPLE_CLIENT_ID, APPLE_TEAM_ID, APPLE_KEY_ID e APPLE_PRIVATE_KEY "
                "sao obrigatorios quando o Apple OAuth estiver ativo."
            )
        if self.apple_oauth_enabled and (
            not self.app_public_url.startswith("https://") or "localhost" in self.app_public_url
        ):
            raise ValueError("Sign in with Apple exige APP_PUBLIC_URL HTTPS com dominio real.")
        if self.integration_auth_enabled and len(self.inpi_integration_token) < TAMANHO_MINIMO_TOKEN_INTEGRACAO:
            raise ValueError(
                "INPI_INTEGRATION_TOKEN deve ter ao menos "
                f"{TAMANHO_MINIMO_TOKEN_INTEGRACAO} caracteres quando a autenticação estiver ativa."
            )
        if self.app_env.lower() != "production":
            return self
        if self.admin_password in SENHAS_PLACEHOLDER:
            raise ValueError(
                "ADMIN_PASSWORD não pode usar o valor padrão em produção. Defina uma senha administrativa própria."
            )
        if len(self.admin_password) < TAMANHO_MINIMO_SENHA:
            raise ValueError(f"ADMIN_PASSWORD deve ter ao menos {TAMANHO_MINIMO_SENHA} caracteres em produção.")
        if len(self.audit_ip_salt) < 16:
            raise ValueError("AUDIT_IP_SALT deve ter ao menos 16 caracteres em produção.")
        if len(self.security_master_key) < 32:
            raise ValueError("SECURITY_MASTER_KEY deve ter ao menos 32 caracteres em produção.")
        if self.security_master_key_version < 1:
            raise ValueError("SECURITY_MASTER_KEY_VERSION deve ser positivo.")
        if self.security_master_key_previous and (
            self.security_master_key_previous_version < 1
            or self.security_master_key_previous_version == self.security_master_key_version
        ):
            raise ValueError("SECURITY_MASTER_KEY_PREVIOUS_VERSION deve identificar uma versao anterior.")
        if "inpi:inpi@" in self.database_url or "change-me" in self.database_url:
            raise ValueError("DATABASE_URL usa credenciais padrão em produção.")
        if not self.admin_force_https:
            raise ValueError("ADMIN_FORCE_HTTPS deve estar ativo em produção.")
        if not self.integration_auth_enabled:
            raise ValueError("INTEGRATION_AUTH_ENABLED deve estar ativo em produção.")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ALLOWED_ORIGINS não pode usar curinga em produção.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
