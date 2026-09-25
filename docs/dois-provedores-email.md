# Dois provedores de e-mail

O sistema mantém compatibilidade com o SMTP principal e permite habilitar um
segundo remetente sem armazenar credenciais no banco de dados. As senhas devem
existir somente no arquivo de ambiente da VPS.

## Estratégias

- `single`: usa somente a conta principal (padrão e compatível com instalações existentes).
- `category`: envia `prospeccao_lead` e `passo_cadencia` pela conta secundária;
  os e-mails transacionais continuam na principal.
- `failover`: tenta a conta principal e muda para a secundária apenas quando
  recebe uma rejeição definitiva de cota `550/5.4.5`. Timeout ou erro de conexão
  não provoca troca, pois o primeiro provedor pode ter aceitado a mensagem.

## Configuração recomendada

```env
EMAIL_PROVIDER_STRATEGY=category
EMAIL_PROVIDER_QUOTA_COOLDOWN_MINUTES=60
EMAIL_SECONDARY_OPERATIONS=prospeccao_lead,passo_cadencia

SMTP_SECONDARY_ENABLED=true
SMTP_SECONDARY_NAME=Comercial
SMTP_SECONDARY_HOST=smtp.gmail.com
SMTP_SECONDARY_PORT=587
SMTP_SECONDARY_USERNAME=conta-comercial@gmail.com
SMTP_SECONDARY_PASSWORD=<senha-de-aplicativo>
SMTP_SECONDARY_FROM_ADDRESS=conta-comercial@gmail.com
SMTP_SECONDARY_FROM_NAME=Zé Registra
SMTP_SECONDARY_STARTTLS=true
SMTP_SECONDARY_SSL=false
SMTP_SECONDARY_TIMEOUT_SECONDS=10

EMAIL_SECONDARY_DAILY_LIMIT=500
EMAIL_SECONDARY_DAILY_WARNING_PERCENT=80
```

Para somar as cotas das duas contas, altere apenas:

```env
EMAIL_PROVIDER_STRATEGY=failover
```

## Segurança e operação

- Nunca cadastrar a senha de aplicativo pela interface administrativa.
- Nunca registrar destinatário, assunto ou credencial na telemetria de cota.
- Cada remetente possui contador, alerta e estado próprios na tela do lead.
- O cálculo visual considera uma janela móvel de 24 horas.
- Depois de uma rejeição de cota, o provedor entra em espera pelo período
  configurado e o sistema pode usar a conta de reserva conforme a estratégia.
- Uma entrega posterior bem-sucedida libera novamente o estado visual do
  remetente.

Após preencher o `.env`, recrie `api` e `worker` para aplicar as variáveis.
Não é necessária migration de banco para habilitar a segunda conta.
