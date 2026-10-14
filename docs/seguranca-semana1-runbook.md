# Segurança — Semana 1 (auditoria de 13/10/2026): passo a passo na VPS

Tudo abaixo roda na VPS (`/opt/zeregistra`), depois do merge do PR. A ordem importa.

## 1. Trocar a senha do superusuário do Postgres (`inpi`)

A imagem do Postgres só lê `POSTGRES_PASSWORD` na criação do volume. Como o banco já
existe, **primeiro** altere a role, **depois** grave a mesma senha no `.env` e faça o deploy.

```bash
cd /opt/zeregistra
NOVA=$(openssl rand -hex 24)                      # só hexadecimal: entra em URL sem escape
docker compose exec -T db psql -U inpi -d inpi -c "ALTER ROLE inpi PASSWORD '$NOVA';"
echo "POSTGRES_PASSWORD=$NOVA" >> .env            # guarde também no seu gerenciador de senhas
FORCAR=1 ./docker/backup-banco.sh                 # backup antes do deploy
./docker/deploy.sh
```

Verificação: `docker compose exec -T db psql -U inpi -d inpi -c "select 1"` continua
funcionando (socket local) e `./docker/deploy.sh` roda a migração sem erro de autenticação.
A role `inpi_app` (usada pela aplicação) **não muda**.

## 2. Backup externo cifrado

```bash
apt install -y rclone openssl
rclone config                                      # crie um remote (ex.: b2, s3, drive)
openssl rand -base64 48 > /root/.backup-passphrase && chmod 600 /root/.backup-passphrase
# COPIE o conteúdo de /root/.backup-passphrase para o gerenciador de senhas AGORA:
# sem ele o backup cifrado não pode ser restaurado.
cat > /opt/zeregistra/.backup-offsite.env <<'ENV'
BACKUP_OFFSITE_REMOTE=meuremote:zeregistra-backups/db
BACKUP_PASSPHRASE_FILE=/root/.backup-passphrase
ENV
chmod 600 /opt/zeregistra/.backup-offsite.env
cd /opt/zeregistra && set -a && . ./.backup-offsite.env && set +a && FORCAR=1 ./docker/backup-offsite.sh
```

## 3. Agendar a rotina diária (backup → envio externo cifrado → simulado aos domingos)

```bash
sh ./docker/instalar-cron-seguranca.sh
```

O cron roda às 02:00 (hora local da VPS, Brasília) e encadeia tudo em `docker/rotina-diaria-backup.sh`.
O instalador substitui as linhas antigas de `backup-banco.sh` e `simulado-restauracao.sh`.

**Atenção — rotina no PC que baixa e apaga o dump da VPS:** ela só pode apagar um dump depois que
o marcador `backups/.offsite-<nome-do-dump>` existir na VPS (prova de que a cópia cifrada já está no
destino externo). Sem essa checagem, o PC pode apagar o dump antes do envio e antes do simulado.

## 4. Testar a restauração de verdade (anotar o tempo)

Baixe um `.enc` do destino externo e confira que decifra e restaura no banco de teste:

```bash
rclone copyto meuremote:zeregistra-backups/db/inpi-AAAAMMDD-HHMMSS.dump.enc /tmp/teste.dump.enc
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -in /tmp/teste.dump.enc \
  -out /tmp/teste.dump -pass file:/root/.backup-passphrase
./docker/simulado-restauracao.sh /tmp/teste.dump ; rm -f /tmp/teste.dump /tmp/teste.dump.enc
```

## 5. Conferir o RLS em produção

```bash
./docker/verificar-rls.sh
```

Saída `OK` = nada a fazer. Se listar tabelas, envie a lista para criarmos a migration
de correção (não corrija à mão em produção).

## 6. Painel de saúde dos backups (superadmin)

Em **Observabilidade → Saúde dos backups** (visível só para o superadmin) o painel mostra
dump, envio externo, simulado, cópia no PC e disco, com semáforo e histórico de 14 dias.

- A rotina `docker/rotina-diaria-backup.sh` grava cada etapa em `backups/historico-rotina.jsonl`.
- O worker lê `backups/` (somente leitura) a cada hora, publica o resultado no Redis e abre o
  alerta `BACKUP_ROTINA` (e-mail de plataforma) se alguma etapa estiver crítica. A API não
  enxerga os dumps.
- O script do PC (`scripts/sincronizar-backup-vps-pc.ps1`) grava `backups/.pc-<dump>` na VPS
  depois de validar o hash; sem isso a linha "Cópia no PC" fica "Sem dados".
- O painel só passa a ter dados depois do **deploy do worker** e da primeira execução da rotina diária.

## 7. Limpeza de disco pelo painel (superadmin)

Em **Observabilidade → Limpeza de disco** o superadmin vê quanto cada categoria ocupa e pode
solicitar a limpeza. A API **não** executa nada nem tem acesso ao Docker: ela só grava um
pedido no Redis, e o script do host `docker/manutencao-limpeza.sh` (cron, a cada 5 minutos,
instalado por `sh docker/instalar-cron-seguranca.sh`) atende o pedido.

**O que é limpo (lista fixa):** imagens Docker órfãs, cache de build com mais de 7 dias,
containers parados fora do projeto, resíduos `inpi-*.dump` (mais de 1 dia) no `/tmp` do
container do banco, temporários antigos do `/tmp` da VPS, `backups/*.part` e logs
`/var/log/zeregistra-*.log` acima de 50 MB.

**O que nunca é tocado:** volumes (postgres, redis, clamav, ollama), `backups/`, `data/`,
uploads de clientes; o script não contém `prune -a`, `volume prune`, `system prune` nem `down`.

- Relatório de espaço: publicado a cada 2 h (ou logo após cada limpeza).
- Alerta `DISCO_USO_ALTO` (aviso) quando o disco passa de 80%; acima de 90% entra em
  `BACKUP_ROTINA` (crítico).
- Para atualizar o relatório na hora: `sh docker/manutencao-limpeza.sh --relatorio`.
- Log do cron: `/var/log/zeregistra-manutencao.log`.

## 8. Rate limit (Semana 2)

**Aplicação (vale com o deploy):** `POST /v1/auth/recuperacao/redefinir`, `POST /v1/auth/convites/aceitar`
e `POST /v1/privacidade/confirmar-exclusao` passam a responder 429 após 10 tentativas em 5 minutos por IP.

**Nginx (manual na VPS, com teste antes de recarregar):**

```bash
cd /opt/zeregistra && git pull
cp docker/nginx-limit-req.conf /etc/nginx/conf.d/zeregistra-limit-req.conf
cp /etc/nginx/sites-enabled/zeregistra /root/zeregistra-nginx.bak      # ajuste o nome do arquivo do site
# no bloco "location /" do site, acrescente as duas linhas limit_req que estão em nginx-zeregistra.conf
nginx -t && systemctl reload nginx
```

Se `nginx -t` acusar erro, **não recarregue**: restaure o backup (`cp /root/zeregistra-nginx.bak ...`)
e remova o `conf.d/zeregistra-limit-req.conf`. Rollback depois de recarregado: remova as linhas
`limit_req` do site, apague o arquivo do `conf.d` e `systemctl reload nginx`.
Respostas 429 do Nginx aparecem em `/var/log/nginx/error.log` (nível `warn`).

**Atualização (auditoria externa, A02):** o mapa de rotas sensíveis passou a incluir
`/v1/auth/social/mfa`. Depois do deploy, reaplique o arquivo do `conf.d` e recarregue:

```bash
cp /opt/zeregistra/docker/nginx-limit-req.conf /etc/nginx/conf.d/zeregistra-limit-req.conf && nginx -t && systemctl reload nginx
```
