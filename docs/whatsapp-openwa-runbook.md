# WhatsApp com OpenWA: operação

O Zé Registra envia WhatsApp pelo gateway [OpenWA](https://github.com/rmyndharis/OpenWA), que controla uma conta
comum do WhatsApp Web. **Não é a API oficial da Meta**: existe risco de restrição ou banimento do número. Por isso:
dois números separados, envio desligado por padrão e limites baixos.

| Número | Sessão no gateway | Uso | Risco |
|---|---|---|---|
| Vigilância | `vigilancia` | avisos a clientes que consentiram (vigilância e andamento) | baixo |
| Prospecção | `prospeccao` | primeiro contato comercial | alto, aceito |

Nunca use o número principal do escritório nem misture os dois usos num número.

## 1. Antes de começar

1. Dois chips novos. Use o WhatsApp Business no celular (nome da empresa, foto e descrição).
2. **Aquecimento de 2 a 3 semanas**: use cada número normalmente, só com conversas reais, antes de ligar qualquer
   automação. Número novo que dispara mensagens no primeiro dia costuma ser banido.
3. Vigilância e andamento só saem para telefones com consentimento registrado (tela
   *Configuração → WhatsApp e consentimentos*). Na prospecção, quem pediu para sair nunca volta por decisão interna.

## 2. Chaves (uma vez)

No `.env` da VPS (`/opt/zeregistra/.env`), sem exibir os valores no terminal:

```bash
cd /opt/zeregistra
printf 'OPENWA_API_KEY=%s\n' "$(openssl rand -hex 24)" >> .env
printf 'OPENWA_WEBHOOK_SECRET=%s\n' "$(openssl rand -hex 24)" >> .env
```

`WHATSAPP_ENABLED` continua ausente (falso): nada é enviado até a etapa 6.

## 3. Subir o gateway

O gateway tem arquivo de composição próprio, fora do deploy normal. Use sempre os três arquivos:

```bash
cd /opt/zeregistra
docker compose -f compose.yaml -f compose.production.yaml -f compose.openwa.yaml up -d openwa
docker compose -f compose.yaml -f compose.production.yaml -f compose.openwa.yaml ps openwa
```

Para recarregar `api` e `worker` com as variáveis novas, rode o deploy de sempre (`docker/deploy.sh`).
**Nunca use `down` com `--profile` ou sem nome de serviço**: já apagou produção uma vez.

O gateway fica na rede do projeto, sem `docker.sock`, e as regras de `docker/saida-rede-docker.sh` valem para ele
(só internet pública).

## 4. Criar as sessões e os webhooks

```bash
cd /opt/zeregistra
docker compose exec -T api /app/.venv/bin/python -m app.cli.openwa_configurar
```

Cria as sessões `vigilancia` e `prospeccao`, inicia as que estiverem paradas e registra o webhook de cada uma
(`/v1/webhooks/openwa`, assinado com `OPENWA_WEBHOOK_SECRET`). Pode rodar de novo: não duplica nada.

## 5. Ler o QR code (um número por vez)

O painel do gateway só escuta no próprio servidor. Do seu computador, abra um túnel e acesse pelo navegador:

```bash
ssh -L 2785:127.0.0.1:2785 root@IP_DA_VPS
```

Abra `http://localhost:2785`, entre com a `OPENWA_API_KEY` e, na sessão certa, escaneie o QR pelo WhatsApp do
celular: *Aparelhos conectados → Conectar um aparelho*. Faça `vigilancia` com o chip da vigilância e `prospeccao`
com o outro. Feche o túnel ao terminar.

## 6. Ligar o envio

Confira antes: `GET /v1/admin/mensageria/status` (ou a tela do painel) mostra gateway configurado. Então, no `.env`:

```bash
echo 'WHATSAPP_ENABLED=true' >> /opt/zeregistra/.env
cd /opt/zeregistra && docker/deploy.sh
```

**Trava de emergência**: troque para `WHATSAPP_ENABLED=false` e rode o deploy (ou
`docker compose -f compose.yaml -f compose.production.yaml up -d --force-recreate worker`). A fila para na hora; o
que está pendente fica guardado.

Limites padrão (podem ser ajustados por variável de ambiente): 200 mensagens/dia na vigilância e 20 na prospecção;
prospecção só em dia útil, das 9h às 18h, com 60 a 180 s entre mensagens e pausa automática depois de 3 falhas na
última hora.

## 7. Acompanhar

- Tela *Configuração → WhatsApp e consentimentos*: situação, consentimentos, fila.
- Mensagem **incerta**: o worker caiu no meio do envio. Confira no WhatsApp do aparelho antes de reenviar.
- Alertas no painel: `WHATSAPP_RESTRICAO` (o WhatsApp restringiu o número: os envios daquela sessão são pausados por
  24 horas), `WHATSAPP_DESCONECTADO` e `WHATSAPP_RECONEXAO_EM_LOOP` (reler o QR code ou ver o aparelho).
- Respostas "SAIR" (e variações: parar, pare, cancelar, stop) revogam o telefone em todas as finalidades.

## 8. Backup da sessão

O volume `zeregistra_openwa_data` guarda as sessões logadas: **quem tem o volume tem os números**. O backup do banco
não o inclui. Se quiser copiar (sem enviar a lugar nenhum não cifrado):

```bash
cd /opt/zeregistra && mkdir -p backups && umask 077
docker run --rm -v zeregistra_openwa_data:/dados:ro -v "$PWD/backups:/saida" alpine \
  tar czf "/saida/openwa-$(date +%F).tgz" -C /dados .
```

Se a sessão se perder, basta ler o QR code de novo (etapa 5).

## 9. Atualizar o gateway

A imagem está fixada em uma versão (`compose.openwa.yaml`). Para atualizar, troque o número da versão, leia as notas
da versão no repositório do OpenWA e rode o comando da etapa 3. Confira a sessão (`status` no painel do gateway)
depois.
