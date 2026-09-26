# Operação Jurídica — implementação das Fases 0 e 3

Data da implementação: 25/09/2026
Linha de base: `cad7d87fb3582089e45423162bcc043bf5ba226d`

## Fase 0 — estabilização operacional

A Fase 0 foi implementada como diagnóstico assistido. Ela não confirma,
descarta, conclui ou redistribui prazo automaticamente.

O endpoint `GET /v1/admin/juridico/estabilizacao`, isolado por organização,
consolida:

- prazos ativos;
- prazos sem responsável;
- sugestões aguardando confirmação;
- sugestões aguardando confirmação há mais de sete dias;
- prazos vencidos e críticos;
- existência de política jurídica explicitamente salva;
- regras homologadas, exceções de calendário e evidências armazenadas.

A tela da Operação Jurídica apresenta essas pendências como fila de
saneamento e informa quando a política exibida ainda é apenas o padrão de
compatibilidade. A regularização continua sob responsabilidade de um usuário
com `legal.manage`.

Na linha de base de produção auditada antes desta implementação existiam 12
sugestões aguardando confirmação, 11 prazos ativos sem responsável, 11
sugestões com mais de sete dias, quatro prazos ativos vencidos e uma prioridade
crítica. Esses números são evidência histórica e devem ser medidos novamente
após o deploy; não foram alterados por esta mudança de código.

## Fase 3 — comunicação rastreável

Foi criada a tabela tenant-aware `saidas_email_juridico`, protegida por RLS.
A notificação de painel e a intenção de e-mail passam a ser gravadas na mesma
transação. O envio SMTP não ocorre mais antes do commit do motor jurídico.

O worker:

- reivindica lotes com `FOR UPDATE SKIP LOCKED`;
- entrega a caixa de saída a cada minuto;
- registra status, quantidade de tentativas, provedor e data de envio;
- aplica retentativa exponencial e encerra após cinco tentativas;
- recupera itens presos em processamento há mais de 15 minutos;
- salva somente o tipo da exceção, sem mensagem SMTP ou credenciais;
- usa `Message-ID` determinístico como proteção adicional contra duplicidade;
- cria um resumo diário, idempotente, por responsável com agenda ativa;
- envia no resumo apenas contagens; detalhes permanecem no painel autenticado.

O endpoint `GET /v1/admin/juridico/comunicacao` apresenta os totais de
pendentes, processando, enviados e falhas. O destinatário aparece mascarado.

## Migration e rollback

- Upgrade: `i1d2e3f4g5h6`.
- Downgrade: remove somente `saidas_email_juridico`.
- Em rollback de aplicação, interromper primeiro o worker novo; depois executar
  o downgrade. Notificações do painel continuam preservadas em
  `notificacoes_juridicas`.

## Validação

- Ruff nos arquivos alterados: aprovado.
- Testes direcionados de Jurídico, e-mail e worker: aprovados.
- Alembic: uma única cabeça (`i1d2e3f4g5h6`).
- A suíte completa local depende de PostgreSQL e de diretório temporário com
  permissão; as falhas ambientais devem ser reexecutadas no CI.
