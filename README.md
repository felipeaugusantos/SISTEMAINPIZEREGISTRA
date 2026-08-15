# Zé Registra — Pesquisa e Gestão de Marcas INPI

Plataforma para pesquisa indicativa de anterioridade, análise de registrabilidade e gestão operacional de marcas publicadas pelo Instituto Nacional da Propriedade Industrial (INPI).

O sistema consulta exclusivamente a **Seção V — Marcas** da Revista da Propriedade Industrial (RPI), organiza os resultados encontrados e oferece recursos internos para atendimento comercial, acompanhamento de processos, análise técnica, aprendizado supervisionado e gestão financeira.

> **Aviso importante:** os resultados são indicativos. Eles não substituem a busca oficial, o exame de mérito do INPI, a correta especificação de produtos e serviços nem uma análise jurídica especializada. Nenhuma pontuação ou probabilidade representa garantia de registro.

O escopo comercial auditado, incluindo recursos parciais e ainda não implementados, está documentado na [matriz “prometido × implementado”](docs/matriz-prometido-implementado.md).

## Estado da Release Candidate

- **IMPLEMENTADO:** instalação e upgrade por Alembic, RPI monitorada, Busca V4 técnica, risco determinístico, revisão humana, SaaS/RLS, CRM, jurídico, financeiro operacional, auditoria e backup/restore.
- **EXPERIMENTAL:** indicador histórico de registrabilidade; permanece indisponível sem modelo `ACTIVE` aprovado pelos gates.
- **PENDENTE DE VALIDAÇÃO:** dataset candidato da busca e eficácia jurídica das métricas; o dataset continua `pendente_revisao_especialista`.
- **PLANEJADO:** itens marcados como não implementados na matriz comercial, sem promessa de disponibilidade.

## Funcionalidades

### Pesquisa de marcas

- pesquisa exata e ampliada executadas automaticamente;
- busca por expressão completa, termos isolados, radicais e variações;
- consolidação de ocorrências sem duplicidade;
- classificação por relevância e situação processual;
- identificação de titulares, procuradores, classes Nice e movimentações;
- consulta limitada à Seção V — Marcas da RPI;
- resumo público e relatório completo interno versionado;
- geração de PDF com pontuação de risco determinística, indicador histórico e critérios analisados.

### Central de análise

- validação técnica de situações, classes, afinidades e alto renome;
- motor determinístico de risco com regras e evidências auditáveis;
- matriz de registrabilidade baseada nos critérios do INPI;
- estimativa supervisionada com faixa de incerteza e cobertura;
- parecer humano separado da estimativa automática;
- agente de registrabilidade baseado em dados estruturados;
- versionamento e histórico dos relatórios.

### Leads, oportunidades e CRM

- captura de nome, empresa, e-mail, telefone e autorização de marketing;
- separação **empresa / contato / oportunidade**: empresa com CNPJ, segmento e dados de contato; várias pessoas (contatos) por empresa; cada oportunidade vinculada a um contato;
- pipeline comercial e **funil de fases** (do contato inicial ao processo no INPI), com **status e fase sincronizados** nos dois sentidos;
- responsável e próxima ação obrigatórios nas oportunidades abertas; cards clicáveis de ações atrasadas, sem responsável e sem próxima ação;
- **resultados e motivos de perda** estruturados (ganho/perdido: preço, concorrente, sem resposta, fora do perfil);
- **linha do tempo unificada** por oportunidade (criação, mudanças de fase, contatos, pesquisas, documentos, GRUs e desfecho);
- **documentos** por oportunidade (procuração, GRU, protocolo, oposição, certificado) e **checklist por etapa** do funil;
- **guias do INPI (GRU)**: sugestão por fase a partir da tabela de retribuições, registro da guia emitida e controle de vencimento;
- **dashboard de funil e produtividade** (conversão, ganhos/perdidas, perdas por motivo e desempenho por responsável) na Visão Geral;
- **automações**: ao mudar a fase ou o status, o sistema cria a tarefa correspondente — configurável em Configuração › Regras automáticas;
- **cadências**: sequências de passos (dia e canal — e-mail, WhatsApp, ligação) aplicáveis a uma oportunidade, que geram as tarefas nas datas certas;
- agrupamento de várias pesquisas no mesmo contato e identificação de pesquisas duplicadas;
- histórico de ligações, reuniões, WhatsApp, e-mails e outros contatos;
- busca por nome, empresa, marca, e-mail, telefone, CPF/CNPJ e status;
- lembretes com prazo, prioridade e responsável; agenda de retornos, propostas, documentos, processos e atualizações cadastrais;
- arquivamento e restauração de contatos;
- exclusão de pesquisas mediante senha ou aprovação administrativa.

### Processos monitorados

- cadastro manual de processos;
- pesquisa em massa por procurador;
- vinculação dos resultados encontrados à carteira;
- associação com empresas e responsáveis internos;
- acompanhamento da situação no INPI e da última movimentação da RPI;
- filtros por número, marca, procurador, empresa e status interno.

### Operação jurídica

- motor de prazos com contagem em dias corridos ou úteis;
- sugestões extraídas das movimentações da RPI, sempre sujeitas a confirmação humana;
- agenda por processo, responsável, período, prioridade e status;
- alertas de antecedência, vencimento e escalonamento automático;
- central de notificações com registro individual de leitura;
- histórico auditável de criação, alteração, entrega, protocolo, escalonamento e leitura.

### Financeiro

- painel com indicadores e pendências;
- contas a pagar e contas a receber;
- cadastro e manutenção de lançamentos;
- parcelas, vencimentos e baixa integral por parcela; pagamento parcial de uma mesma parcela não é suportado;
- cadastro de formas de pagamento e limite de parcelas;
- tabela de retribuições do INPI (GRUs de marca) com código de serviço e valores normal e reduzido;
- baixas, cancelamentos e estornos com justificativa;
- log financeiro em formato de tabela;
- exportação de dados conforme as permissões do usuário.

### Administração SaaS

- organizações independentes no mesmo banco de dados;
- isolamento por organização com Row Level Security (RLS);
- planos, limites, períodos de teste e suspensão;
- branding por organização;
- convites, sessões e permissões específicas;
- chaves de integração revogáveis;
- domínios verificados;
- auditoria de ações administrativas e operacionais.

## Perfis e acessos

O acesso ao Centro de Operações é individual. Além do perfil inicial, as permissões podem ser ajustadas por módulo e ação.

| Perfil | Acesso principal |
| --- | --- |
| Superadministrador | Administração global das organizações e todos os módulos |
| Administrador | Acesso operacional completo, incluindo visão geral e log financeiro |
| CEO | Visão executiva e acesso funcional completo, sem execuções técnicas recentes |
| Tech | Acesso completo, incluindo execuções técnicas recentes |
| Financeiro | Módulo financeiro e respectivo log |
| Supervisor | Operação ampla, com restrições para usuários e exclusões |
| Técnico | Validação, risco e sincronização da RPI |
| Comercial | Leads, CRM, carteira e operação financeira comercial |
| Auditor | Consulta aos módulos e trilhas de auditoria |
| Operador | Acesso mínimo, ampliável pela matriz de permissões |

## Arquitetura local

O ambiente Docker é composto pelos seguintes serviços:

| Serviço | Responsabilidade |
| --- | --- |
| `api` | API FastAPI e páginas web |
| `db` | PostgreSQL 16 |
| `redis` | cache, rate limit e fila de tarefas |
| `worker` | execução de tarefas assíncronas |
| `rpi-sync` | atualização automática das RPIs |
| `mailpit` | captura local de e-mails de teste |
| `migrate` | aplicação das migrações Alembic antes da API |

Banco, Redis, API e Mailpit são publicados somente em `127.0.0.1` no ambiente local.

## Requisitos

- Windows 10/11, Linux ou macOS;
- Docker Desktop com WSL 2 funcional no Windows;
- Git;
- Python 3.11 e `uv` apenas para desenvolvimento fora do Docker.

## Início rápido com Docker

1. Clone o repositório:

```powershell
git clone https://github.com/felipeaugusantos/SISTEMAINPIZEREGISTRA.git
cd SISTEMAINPIZEREGISTRA
```

2. Crie o arquivo local de configuração:

```powershell
Copy-Item .env.example .env
```

3. No `.env`, defina pelo menos valores próprios para:

```dotenv
APP_DB_PASSWORD=troque-esta-senha
ADMIN_USERNAME=admin
ADMIN_EMAIL=seu-email@empresa.com.br
ADMIN_PASSWORD=troque-esta-senha
AUDIT_IP_SALT=troque-este-segredo
SECURITY_MASTER_KEY=troque-esta-chave
SECURITY_MASTER_KEY_VERSION=1
```

`SECURITY_MASTER_KEY` protege os segredos TOTP com Fernet (AES/HMAC). O ciphertext inclui
a versao da chave, mas a chave nunca e armazenada no banco. Para rotacao sem interromper
o MFA existente, configure a nova chave e versao em `SECURITY_MASTER_KEY` e
`SECURITY_MASTER_KEY_VERSION`, mantendo temporariamente a anterior em
`SECURITY_MASTER_KEY_PREVIOUS` e `SECURITY_MASTER_KEY_PREVIOUS_VERSION`. Depois que os
segredos ativos forem regravados, remova a chave anterior. O comprometimento dessa chave
permite revelar segredos MFA e exige rotacao imediata e revisao das contas afetadas.

4. Construa e inicie os serviços:

```powershell
docker compose up -d --build
docker compose ps
```

As migrações são aplicadas pelo serviço `migrate` antes da inicialização da API.

5. Verifique os logs, se necessário:

```powershell
docker compose logs -f api
docker compose logs -f rpi-sync
docker compose logs -f worker
```

## Endereços locais

| Recurso | Endereço |
| --- | --- |
| Pesquisa pública | <http://localhost:8000> |
| Login administrativo | <http://localhost:8000/login> |
| Centro de Operações | <http://localhost:8000/admin> |
| Leads e pesquisas | <http://localhost:8000/admin/pesquisas> |
| CRM | <http://localhost:8000/admin/crm> |
| Processos monitorados | <http://localhost:8000/admin/processos-monitorados> |
| Operação jurídica | <http://localhost:8000/admin/operacao-juridica> |
| Financeiro | <http://localhost:8000/admin/financeiro> |
| Contas a pagar | <http://localhost:8000/admin/financeiro/contas-a-pagar> |
| Contas a receber | <http://localhost:8000/admin/financeiro/contas-a-receber> |
| Formas de pagamento | <http://localhost:8000/admin/financeiro/formas-pagamento> |
| Log financeiro | <http://localhost:8000/admin/producao/log-financeiro> |
| Usuários e acessos | <http://localhost:8000/admin/usuarios> |
| Empresas e planos | <http://localhost:8000/admin/saas> |
| Mailpit | <http://localhost:8025> |
| Swagger | <http://localhost:8000/docs> |
| Saúde da aplicação | <http://localhost:8000/health> |

## Fluxo operacional recomendado

1. O visitante informa a marca e os dados de contato.
2. O sistema executa automaticamente as estratégias de busca configuradas.
3. O cliente recebe um resumo indicativo de uma página.
4. A pesquisa entra no painel de Leads e no histórico do contato.
5. Um operador revisa validação técnica, risco, matriz INPI e estimativas disponíveis.
6. O especialista registra o parecer humano quando necessário.
7. O relatório completo é gerado internamente e utilizado no contato comercial.
8. Quando convertido, o cliente pode ser associado à carteira de processos e ao financeiro.

## Atualização automática da RPI

O serviço `rpi-sync` consulta periodicamente novas edições e importa somente a Seção V — Marcas. O intervalo padrão é de seis horas.

Configurações disponíveis no `.env`:

```dotenv
RPI_SYNC_INTERVAL_SECONDS=21600
RPI_SYNC_START_NUMBER=2900
RPI_SYNC_POLL_SECONDS=10
RPI_STALE_HOURS=12
RPI_MINIMUM_RECORD_RATIO=0.50
RPI_ANOMALY_REFERENCE_MINIMUM=1000
```

Cada importação registra checksum SHA-256, tamanho do XML, estatísticas e anomalias. Edições
vazias, lacunas, quedas abruptas e resultados não reproduzíveis geram alertas para revisão,
sem bloquear automaticamente uma edição oficial pequena. O endpoint `GET /health/rpi` expõe
o estado `ok`, `atrasado`, `erro` ou `processando` para monitoramento.

As requisições recebem `X-Request-ID`, propagado para auditoria, logs estruturados e jobs.
Retries do worker usam backoff exponencial; produtores podem informar uma chave idempotente
ao enfileirar operações que não devem ser duplicadas.

Para acompanhar a execução:

```powershell
docker compose logs -f rpi-sync
```

Para executar uma faixa manualmente:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.sincronizar_rpis `
  --inicio 2818 --fim 2897 --tipo marca
```

Após importar um histórico, consolide a situação mais recente:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.consolidar_situacoes_marcas
```

## Dados históricos BADEPI

Os importadores trabalham em lotes e podem ser repetidos sem duplicar registros:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.importar_badepi_marcas `
  --arquivo data/raw/badepi/badepiv11_mrc_deposito.csv

docker compose exec api /app/.venv/bin/python -m app.cli.importar_badepi_titulares `
  --arquivo data/raw/badepi/badepiv11_mrc_depositante.csv

docker compose exec api /app/.venv/bin/python -m app.cli.importar_badepi_classes `
  --arquivo data/raw/badepi/badepiv11_mrc_classes.csv
```

## Alto renome

Para sincronizar a lista pública de marcas de alto renome:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.sincronizar_alto_renome
```

Marcas figurativas ou mistas sem elemento nominativo legível são confirmadas pelo número oficial do processo.

## Motor de risco e registrabilidade

O motor determinístico calcula uma pontuação de conflito de 0 a 100 e classifica o caso em quatro níveis:

| Faixa | Leitura operacional |
| --- | --- |
| Baixo | poucos conflitos relevantes localizados |
| Moderado | existem pontos de atenção que exigem conferência |
| Alto | conflitos relevantes e maior possibilidade de impedimento |
| Crítico | conflitos fortes; análise especializada prioritária |

A pontuação considera correspondência do nome, situação processual, classes, afinidade mercadológica e alto renome. Cada ponto permanece associado à regra e à evidência que o originou.

O indicador histórico de registrabilidade é separado da pontuação determinística. Ele é uma estimativa baseada em casos históricos semelhantes, não representa previsão do INPI e só é exibido quando o modelo `ACTIVE` atende aos critérios mínimos de amostra, validação temporal, recall, especificidade, calibração, cobertura e revisão humana.

### Preparação do aprendizado supervisionado

```powershell
docker compose exec api /app/.venv/bin/python `
  -m app.cli.coletar_fundamentos_indeferimentos --limite 500 --atraso 0.6

docker compose exec api /app/.venv/bin/python -m app.cli.treinar_registrabilidade `
  --dataset --limite 500 --candidatos 8

docker compose exec api /app/.venv/bin/python -m app.cli.treinar_registrabilidade `
  --treinar
```

O ciclo formal é `SHADOW → VALIDATION → ACTIVE → DISABLED`. Modelos começam em `SHADOW`; os gates técnicos podem encaminhá-los para `VALIDATION`, mas somente uma promoção administrativa explícita os torna `ACTIVE`. A ativação não elimina a revisão humana e nunca transforma a estimativa histórica em garantia de deferimento.

### Workflow humano da análise

O relatório completo validado segue o fluxo `DRAFT → PENDING_REVIEW → IN_REVIEW → VALIDATED`. O revisor pode enviar uma análise em andamento para `CHANGES_REQUESTED`, retomá-la em `IN_REVIEW` ou reabrir uma versão `VALIDATED`. A validação final exige parecer humano de risco, notas, permissão de revisão técnica e permissão de revisão de risco.

Cada transição, inclusive tentativas bloqueadas, é registrada na auditoria com estado anterior, ação, responsável e data. A validação fica vinculada à versão corrente do relatório; a criação de uma nova versão remove a liberação anterior e retorna a análise para `PENDING_REVIEW`. Enquanto o estado não for `VALIDATED`, o endpoint de relatório completo responde com conflito e não emite o documento como parecer validado.

## E-mail e recuperação de senha

No ambiente local, a API envia mensagens para o Mailpit:

```dotenv
EMAIL_ENABLED=true
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_STARTTLS=false
```

As mensagens podem ser visualizadas em <http://localhost:8025>. Em produção, substitua os valores pelo provedor SMTP escolhido e configure `APP_PUBLIC_URL` com o domínio HTTPS real.

## Login com Google e Apple

Os provedores são opcionais e ficam desativados por padrão.

### Google

Cadastre no Google Auth Platform uma credencial OAuth 2.0 do tipo **Aplicativo da Web** e use o callback:

```text
http://localhost:8000/v1/auth/social/google/callback
```

Configure:

```dotenv
GOOGLE_OAUTH_ENABLED=true
GOOGLE_CLIENT_ID=seu-client-id
GOOGLE_CLIENT_SECRET=seu-client-secret
```

### Apple

A Apple exige domínio HTTPS real e não aceita `localhost` como URL de retorno:

```text
https://seu-dominio/v1/auth/social/apple/callback
```

Configure `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` e `APPLE_OAUTH_ENABLED=true`.

O vínculo automático só ocorre para usuário existente e ativo, com e-mail verificado e correspondência exata.

## Segurança e privacidade

- senhas protegidas com Argon2id;
- sessões individuais, revogáveis e com expiração;
- MFA TOTP compatível com Google Authenticator;
- códigos de recuperação de MFA;
- recuperação de senha com token de curta duração;
- proteção CSRF nas operações administrativas;
- rate limit com Redis;
- isolamento multiempresa com RLS no PostgreSQL;
- trilha de auditoria para acessos e alterações sensíveis;
- armazenamento de IP somente como hash auditável;
- chaves de integração armazenadas como hash;
- consentimento de marketing separado do aviso de privacidade;
- exportação, anonimização e política de retenção para dados pessoais.

Não armazene segredos no Git. O arquivo `.env` é local e deve permanecer fora do repositório.

## Integração com site externo

Uma integração pode usar uma chave emitida no painel SaaS:

```http
X-Integration-Key: sua-chave
```

Também existe compatibilidade com:

```http
Authorization: Bearer seu-token
```

Em ambiente público, use HTTPS, CORS explícito, domínio fixo e chaves diferentes por integração. A autenticação pode ser exigida com `INTEGRATION_AUTH_ENABLED=true`.

## Backup e restauração

Instalar o backup diário local:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\instalar-backup-diario.ps1
```

Criar um backup manual:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup-banco.ps1
```

Restaurar um backup exige confirmação explícita:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\restaurar-banco.ps1 `
  -Arquivo .\backups\inpi-AAAAMMDD-HHMMSS.dump -Confirmar
```

O procedimento completo, incluindo secrets, migrations, RPI e validação pós-restore, está em [Operação e recuperação de desastre](docs/operacao-recuperacao-desastre.md).

## Desenvolvimento

Instale as dependências:

```powershell
uv sync --frozen
```

Execute a API fora do Docker:

```powershell
uv run uvicorn app.main:app --reload
```

Aplicar e verificar migrações:

```powershell
uv run alembic upgrade head
uv run alembic check
```

Executar a mesma validação principal do CI:

```powershell
uv run ruff check app tests migrations --ignore E501
uv run pytest -q
```

O pipeline do GitHub também valida RLS e constrói a imagem Docker de produção.

## Benchmark da Busca V4

Cada resultado possui `score_busca` (0–100), versão e fatores explicáveis com regra, peso,
evidência e processo. Esse score ordena a busca: não é probabilidade de registro nem score de
risco.

Copie `data/search-benchmark.example.json`, preencha casos conferidos por um especialista e
execute:

```powershell
uv run python -m app.cli.avaliar_busca_marcas `
  .\data\search-benchmark.json `
  --limite 20 `
  --saida .\data\search-benchmark-result.json
```

O relatório registra Recall e Precision @5/10/20, MRR, latência média/p50/p95/p99, posição e
score dos resultados esperados e falsos negativos críticos. Para bloquear regressões:

```powershell
uv run python -m app.cli.avaliar_busca_marcas `
  .\data\search-benchmark.v1.json `
  --baseline .\data\search-benchmark-baseline.v1.json `
  --limiares .\data\search-benchmark-thresholds.v1.json `
  --gate
```

Datasets pendentes são recusados por padrão. A opção `--permitir-pendente` existe apenas para
gerar uma baseline candidata, nunca para aprovação jurídica. A governança e a evidência de
`EXPLAIN ANALYZE` estão em `docs/busca-benchmark-fase4.md`.

## API

A documentação interativa completa fica disponível em <http://localhost:8000/docs>. Entre os grupos principais estão:

- pesquisas e relatórios de marcas;
- processos e movimentações;
- leads, CRM e exclusões;
- carteira de processos monitorados;
- financeiro e formas de pagamento;
- validação, risco e aprendizado;
- sincronização da RPI;
- usuários, autenticação e MFA;
- organizações, planos e integrações;
- produção, confiabilidade e auditoria.

## Estado do projeto

O projeto está em fase de testes e validação operacional. Antes de disponibilizá-lo para novos clientes, revise as configurações de segurança, domínio, SMTP, backup, monitoramento, credenciais e políticas de privacidade do ambiente de destino.
