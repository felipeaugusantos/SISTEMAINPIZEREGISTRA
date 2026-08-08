# Pesquisa de Marcas INPI

Aplicação para pesquisa indicativa de anterioridade em processos de marcas publicados pelo INPI. O escopo de consulta é exclusivo da **Seção V — Marcas** da Revista da Propriedade Industrial (RPI).

O visitante informa a marca, a atividade do negócio e seus dados de contato. A aplicação executa automaticamente a pesquisa exata e a pesquisa ampliada por radicais e variações ortográficas ou fonéticas, consolida os resultados sem duplicidade e libera um relatório com ocorrências, titulares, situação, depósito, classes e histórico publicado.

## Executar com Docker

Pré-requisito: Docker Desktop com WSL 2 funcional.

```powershell
docker compose up --build -d
```

As migrações são aplicadas automaticamente quando o contêiner inicia. Acesse:

- Pesquisa: http://localhost:8000
- Painel administrativo unificado: http://localhost:8000/admin
- Leads e pesquisas: http://localhost:8000/admin/pesquisas
- Validação técnica: http://localhost:8000/admin/validacao
- Motor de risco em modo sombra: http://localhost:8000/admin/risco
- IA explicativa interna: http://localhost:8000/admin/ia
- Produção e auditoria: http://localhost:8000/admin/producao
- Usuários e acessos: http://localhost:8000/admin/usuarios
- Empresas, planos e integrações (superadministrador): http://localhost:8000/admin/saas
- Swagger: http://localhost:8000/docs
- Saúde: http://localhost:8000/health

### Iniciar acesso externo automaticamente no Windows

Execute uma vez no PowerShell:

```powershell
cd C:\Users\Enzo\Documents\INPI
powershell -ExecutionPolicy Bypass -File .\scripts\instalar-inicializacao.ps1
```

A tarefa `ZeRegistra-AcessoExterno` será executada depois do login no Windows. Ela aguarda o Docker Desktop, sobe os contêineres, substitui o Quick Tunnel anterior, valida o novo endereço e registra o link em `%LOCALAPPDATA%\ZeRegistra\ultimo-link.txt`.

Para enviar o novo endereço pelo WhatsApp, preencha `%LOCALAPPDATA%\ZeRegistra\whatsapp.env` com o token permanente, o ID do número remetente, o destinatário com DDI e o nome de um modelo aprovado no WhatsApp Cloud API. Segredos ficam fora do repositório. O histórico da automação fica em `%LOCALAPPDATA%\ZeRegistra\acesso-externo.log`.

## Lógica de pesquisa

O cliente não precisa escolher parâmetros técnicos. Toda solicitação combina internamente a expressão completa, elementos isolados, radicais e variações ortográficas ou fonéticas, prioriza as correspondências exatas e remove processos repetidos. Cada ocorrência informa por que foi localizada. A classificação Nice permanece disponível nos dados apresentados, mas não é exigida no formulário.

O relatório também apresenta classes candidatas extraídas da atividade informada, situação processual padronizada e uma matriz inicial de afinidade. O despacho oficial permanece visível. Relações de afinidade pendentes aparecem como preliminares até aprovação nominal no painel de validação técnica.

Cada versão registra o termo, a expressão completa, os radicais, as variações, as contagens por critério, a versão do algoritmo e a qualidade da base. As ocorrências são ordenadas em quatro faixas de relevância explicáveis. A conclusão é sempre indicativa e nunca declara que uma marca está disponível ou que seu registro é garantido.

Os resultados são meramente indicativos e não substituem a busca oficial, a análise fonética, a especificação correta de produtos e serviços ou uma opinião jurídica.

## Alto renome

A lista de marcas de alto renome é obtida da página oficial do INPI. Para sincronizar inclusões e expirações:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.sincronizar_alto_renome
```

O sistema identifica o número de processo oficial e também nomes nominativos legíveis na lista. Marcas mistas ou figurativas sem nome textual no PDF só são confirmadas pelo respectivo número de processo.

## Motor de risco em modo sombra

Ao gerar um relatório, o sistema calcula internamente uma pontuação determinística de 0 a 100 e classifica o caso em quatro níveis: baixo, moderado, alto ou crítico. O cálculo combina:

- grau de correspondência do nome;
- relevância da situação processual;
- identidade ou afinidade entre classes;
- coincidência com alto renome.

Cada ponto fica associado à regra e à evidência que o originou. A pontuação e os principais conflitos são armazenados com a versão do motor, mas não fazem parte da resposta pública do relatório.

No painel `/admin/fase3`, um especialista pode registrar sua própria classificação e fundamentação. O sistema mede concordância simples entre o nível determinístico e o nível humano, sem alterar pesos ou calibrar o motor automaticamente.

## IA explicativa interna

A Fase 4 é opt-in e não altera a pontuação. O modelo recebe somente a versão do motor, pontuação, nível, identificadores sequenciais dos conflitos e fatores controlados. Nome, e-mail, telefone, empresa, marca pesquisada, número de processo e classes Nice não são enviados.

A saída usa esquema fixo e passa por validação no servidor. Qualquer tentativa de alterar a pontuação, inventar conflito ou regra, citar processo, classe ou fundamento jurídico é bloqueada. Explicações de risco alto ou crítico ficam com status `aguardando_revisao` até decisão nominal de um especialista. Nada desta fase é incluído no relatório público.

Para habilitar a geração, configure no `.env`:

```dotenv
AI_EXPLANATIONS_ENABLED=true
OPENAI_API_KEY=...
OPENAI_EXPLANATION_MODEL=gpt-5.6-luna
```

Sem a chave e a habilitação explícita, o painel permanece disponível para auditoria, mas nenhuma chamada externa é feita.

## Governança de produção

A Fase 5 adiciona duas camadas independentes para a IA: a chave mestra `AI_EXPLANATIONS_ENABLED` no ambiente e o controle operacional em `/admin/producao`. A geração só ocorre quando ambas estão habilitadas, existe chave da API e a pesquisa pertence ao percentual de rollout configurado. A distribuição é determinística, portanto uma pesquisa não entra e sai do mesmo lote aleatoriamente.

O painel acompanha tempo e erros das operações nas últimas 24 horas, divergências entre motor e avaliação humana, quantidade de versões de relatório e eventos administrativos. Senhas nunca entram na auditoria e o endereço de origem é armazenado somente como hash com `AUDIT_IP_SALT`.

Relatórios públicos recebem número de versão, versão de esquema, data de geração e hash de conteúdo. Conteúdo idêntico reutiliza o snapshot anterior; mudanças nos dados públicos criam uma nova versão. Risco e explicações internas não fazem parte do snapshot público.

Riscos alto e crítico continuam exigindo avaliação humana do motor antes que uma explicação da IA possa ser aprovada.

## Dados corporativos e privacidade

Nome, empresa, e-mail de contato e telefone contextualizam e liberam o relatório. O aceite do aviso de privacidade é obrigatório. Consentimento para marketing é separado e opcional.

São aceitos tanto e-mails empresariais quanto endereços pessoais de provedores como Gmail, Hotmail, Outlook e Yahoo. Os dados pessoais não são enviados pela URL; o relatório usa um identificador UUID aleatório.

Defina `ADMIN_USERNAME` e `ADMIN_PASSWORD` no arquivo `.env` antes de publicar. Em produção, use HTTPS e informe o canal real do controlador no aviso de privacidade.

## Painel administrativo

### Operação SaaS multiempresa

O banco usa um catálogo compartilhado para os dados públicos do INPI e separa por organização os usuários, leads, pesquisas, avaliações, relatórios e eventos de auditoria. A migração cria a organização inicial `ze-registra`, associa os registros existentes a ela e promove o primeiro administrador atual a superadministrador.

No menu **Empresas e planos**, o superadministrador pode cadastrar uma empresa com seu administrador inicial, atribuir plano e limites, suspender ou reativar o acesso, cadastrar domínio e emitir uma chave de integração. A senha inicial e a chave são mostradas uma única vez. O administrador de cada empresa continua gerenciando somente sua própria equipe em **Usuários e acessos**.

Para integrar um site externo, envie a chave no cabeçalho `X-Integration-Key`. Também é aceito `Authorization: Bearer`, mantendo compatibilidade com a integração anterior. O token é armazenado somente como SHA-256 e identifica automaticamente a organização. Em domínios cadastrados, a organização também pode ser resolvida pelo host. Cobrança automática permanece preparada pelos campos de assinatura e provedor, mas exige integração futura com o gateway escolhido.

O endereço `/admin` centraliza os indicadores e as pendências de Leads, Validação Técnica, Motor de Risco e IA Explicativa. Os módulos usam a mesma navegação lateral e continuam separados internamente para preservar desempenho e manutenção. Os endereços antigos `/admin/leads` e `/admin/fase2` a `/admin/fase4` permanecem disponíveis por compatibilidade.

O acesso é feito em `/login` com uma conta individual. No primeiro start após a
migração, o sistema cria o usuário definido por `ADMIN_USERNAME`, `ADMIN_EMAIL` e
`ADMIN_PASSWORD`. A autenticação usa senha Argon2id e sessão revogável no PostgreSQL;
o HTTP Basic não é aceito. Para operadores, selecione um perfil inicial e ajuste a
matriz de permissões em `/admin/usuarios`. Contas não são apagadas: bloqueie a conta
e revogue suas sessões para preservar a trilha de auditoria.

## Base histórica BADEPI

Para carregar depósitos, titulares e classes Nice:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.importar_badepi_marcas `
  --arquivo data/raw/badepi/badepiv11_mrc_deposito.csv

docker compose exec api /app/.venv/bin/python -m app.cli.importar_badepi_titulares `
  --arquivo data/raw/badepi/badepiv11_mrc_depositante.csv

docker compose exec api /app/.venv/bin/python -m app.cli.importar_badepi_classes `
  --arquivo data/raw/badepi/badepiv11_mrc_classes.csv
```

Os importadores trabalham em lotes e podem ser repetidos sem duplicar registros.

## Atualizações da RPI

Sincronize somente marcas nas novas edições:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.sincronizar_rpis `
  --inicio 2818 --fim 2897 --tipo marca
```

Cada edição concluída fica registrada para retomada segura.

O serviço Docker `rpi-sync` consulta o portal oficial a cada seis horas e importa
automaticamente apenas as novas edições da Seção V — Marcas. Ele inicia junto com:

```powershell
docker compose up -d --build
docker compose logs -f rpi-sync
```

O intervalo pode ser alterado no `.env` por `RPI_SYNC_INTERVAL_SECONDS` (mínimo de
300 segundos). `RPI_SYNC_START_NUMBER` define a primeira edição em instalações vazias.
Um bloqueio no PostgreSQL impede que duas importações sejam executadas ao mesmo tempo.
O painel administrativo consulta o heartbeat do serviço, atualizado conforme
`RPI_SYNC_POLL_SECONDS` (10 segundos por padrão), e permite solicitar uma verificação
manual ou repetir uma execução que falhou.

Após importar um histórico existente, consolide a situação atual de cada marca pela movimentação mais recente:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.consolidar_situacoes_marcas
```

Titulares continuam disponíveis para identificação, mas CPF e CNPJ encontrados em nomes públicos são mascarados nas APIs e relatórios destinados ao visitante.

## Aprendizado supervisionado de registrabilidade

O módulo `/admin/aprendizado` constrói rótulos a partir de decisões de mérito da RPI,
separando indeferimentos de arquivamentos formais. Para cada pedido rotulado, os candidatos
são limitados às marcas depositadas anteriormente, evitando vazamento temporal. Os atributos
incluem semelhança textual, fonética, trigramas, classes, afinidade validada, situação anterior
e alto renome.

Para preparar um lote e treinar um candidato:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.treinar_registrabilidade `
  --dataset --limite 500 --candidatos 8

docker compose exec api /app/.venv/bin/python -m app.cli.treinar_registrabilidade `
  --treinar
```

O conjunto é dividido cronologicamente em 70% para treino, 15% para calibração/validação e
15% para teste. O modelo registra matriz de confusão, recall, especificidade, acurácia
balanceada, F1, Brier e erro de calibração. Um conjunto bootstrap versionado produz a faixa
de incerteza de cada previsão; a cobertura mede se a consulta está próxima dos exemplos que
o modelo realmente observou. A ativação inicial sempre ocorre em modo sombra:

```powershell
docker compose exec api /app/.venv/bin/python -m app.cli.treinar_registrabilidade `
  --treinar --ativar --administrador admin
```

A exibição ao cliente fica bloqueada enquanto não forem simultaneamente atendidos os mínimos
de amostras históricas e temporais de teste, revisões humanas, recall, especificidade, Brier e
ECE definidos no painel. Cada previsão ainda precisa respeitar a largura máxima da faixa e a
cobertura mínima. Modelos antigos sem bootstrap continuam disponíveis apenas em modo sombra.

No relatório, a pontuação determinística de conflito permanece separada da probabilidade
histórica. Quando elegível, a estimativa informa probabilidade central, faixa de incerteza,
confiança, cobertura, tamanho e corte da base, versão do modelo e fatores de maior influência.
O alvo é explicitamente o **deferimento no exame de mérito**, nunca uma garantia de concessão
final. Resultados de risco alto, crítico ou confiança insuficiente exigem revisão humana.

## Endpoints principais

- `POST /v1/pesquisas-marca`
- `GET /v1/pesquisas-marca/{id}/relatorio`
- `GET /v1/processos?nome={nome}`
- `GET /v1/processos/{numero}`
- `GET /v1/admin/leads`
- `GET /v1/admin/leads.csv`
- `GET /v1/admin/resumo`
- `GET /v1/admin/fase2`
- `PATCH /v1/admin/fase2/afinidades/{id}`
- `GET /v1/admin/fase3`
- `PATCH /v1/admin/fase3/avaliacoes/{id}`
- `GET /v1/admin/fase4`
- `POST /v1/admin/fase4/avaliacoes/{id}/gerar`
- `PATCH /v1/admin/fase4/explicacoes/{id}/revisao`
- `GET /v1/admin/producao`
- `PATCH /v1/admin/producao/controle`
- `GET /v1/admin/aprendizado`
- `POST /v1/admin/aprendizado/dataset`
- `POST /v1/admin/aprendizado/treinar`
- `POST /v1/admin/aprendizado/modelos/{id}/ativar`
- `PATCH /v1/admin/aprendizado/rotulos/{id}`
- `PATCH /v1/admin/aprendizado/previsoes/{id}`
- `PATCH /v1/admin/aprendizado/controle`

## Desenvolvimento

```powershell
uv sync --python 3.11
uv run ruff check .
uv run pytest
uv run uvicorn app.main:app --reload
```

## Confiabilidade local e preparação SaaS

O ambiente de teste separa a conta administrativa de migração (`inpi`) da conta de
execução (`inpi_app`). A segunda não é superusuária e as tabelas de leads, pesquisas,
relatórios, risco, IA, cobrança, alertas e privacidade usam Row Level Security (RLS).
Assim, o PostgreSQL bloqueia acesso entre organizações mesmo se uma consulta da API
esquecer o filtro de tenant.

O `docker compose up -d --build` inicia PostgreSQL, API, sincronizador de RPI, Redis e
worker. Banco e Redis ficam publicados somente em `127.0.0.1`. Confira a saúde em
`http://localhost:8000/health` e a governança em `/admin/confiabilidade`.

Antes de compartilhar o ambiente, copie `.env.example` para `.env` e troque ao menos
`APP_DB_PASSWORD`, `ADMIN_PASSWORD`, `AUDIT_IP_SALT` e `SECURITY_MASTER_KEY`. O modo
`production` também exige HTTPS, autenticação de integração, token forte, banco sem
credenciais padrão e CORS explícito.

### Backup e recuperação

Instale o backup diário local (20h, retenção padrão de 14 dias):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\instalar-backup-diario.ps1
```

Crie um backup manual:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup-banco.ps1
```

Restaure somente após validar o arquivo e com confirmação explícita. A rotina cria outro
backup antes, para API, sincronização e worker, restaura o banco e religa os serviços:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\restaurar-banco.ps1 `
  -Arquivo .\backups\inpi-AAAAMMDD-HHMMSS.dump -Confirmar
```

### Controles disponíveis no teste

- MFA TOTP e códigos de recuperação nas rotas `/v1/auth/mfa/*`;
- recuperação de senha com token de 30 minutos (o token só é retornado em desenvolvimento);
- convites com validade de sete dias e permissões definidas;
- chaves de integração listáveis e revogáveis;
- verificação de domínio por registro TXT `_ze-registra.dominio`;
- trial, pagamento, falha e cancelamento simulados, sem cobrança real;
- limites por plano e suspensão automática de trial expirado;
- fila Redis com worker e registro de falhas;
- identidade visual por organização;
- relatório de uso e alertas operacionais;
- exportação e anonimização de leads mediante solicitação LGPD;
- política de retenção que sinaliza dados vencidos para revisão humana, sem apagamento cego.
