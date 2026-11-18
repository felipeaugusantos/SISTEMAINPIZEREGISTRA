# Painel de TV de monitoramento (Zé Registra + EZRA ERP)

Página `/tv/monitoramento`: os dois sistemas lado a lado, só números agregados, atualização a cada 30 s.

## Acesso (sem login)
- A TV usa uma **chave de leitura própria**, que só lê `GET /v1/monitoramento/tv`. Não abre nenhuma outra rota nem sessão.
- A VPS guarda apenas o **hash SHA-256** da chave (`TV_MONITORAMENTO_TOKEN_SHA256` no `.env`). Vazio = painel desligado (404).
- Endereço da TV: `https://app.zeregistra.com.br/tv/monitoramento#t=<chave>`. A chave vem no fragmento (não vai para log do servidor), fica guardada no navegador da TV e some da barra de endereço.
- **Revogar/trocar**: gere outra chave, troque o hash no `.env` e reinicie a API. A chave antiga para de valer na hora; abra o endereço novo na TV.
- Limite de 60 requisições/min por IP; resposta com `Cache-Control: no-store`.

## O que aparece
- Zé Registra: erros de servidor (5xx) na última hora, erros e taxa em 24 h, usuários ativos, ações, organizações com uso, série de 7 dias, top 3 erros por operação.
  - Saúde: 0 erros 5xx na última hora = verde; 1 a 9 = amarelo; 10 ou mais = vermelho. Respostas 4xx (senha errada, limite de taxa) não contam.
- EZRA ERP: erros novos/abertos, usuários, ações, clientes com uso e sem uso, série de 7 dias, top 3 erros por rota. Saúde pelos erros novos na última hora (mesma escala).
  - Exceção aprovada pelo dono em 17/11/2026: a lista de **clientes sem uso em 7 dias** mostra o nome e há quantos dias ("nunca usou" quando não há registro), limitada a 20. A contagem total continua no rodapé do cartão.
- Fora essa lista, nunca aparecem nome de organização/cliente, usuário, e-mail, IP nem texto de erro.

## Disponibilidade do mês e incidentes (17/11/2026)
- Uma **sonda no host da VPS** (`docker/sonda-disponibilidade.sh`, cron a cada minuto) grava 1 linha por alvo em `backups/disponibilidade.jsonl` e fecha cada dia (fuso de Brasília) em `backups/disponibilidade-diario.jsonl`. Alvos: Zé Registra (`/health`, só 200 vale) e EZRA ERP (endereço público, qualquer resposta < 500 vale).
- Minuto "fora" = sonda falhou ou não há linha no minuto (sem linha = VPS fora/reiniciando = app fora para o usuário). **Incidente** = 2+ minutos fora seguidos; encerra com 2 minutos ok. Queda de 1 minuto conta na disponibilidade mas não é listada.
- Disponibilidade do mês = minutos no ar / minutos decorridos do mês (Brasília), do dia 1 ou do início da coleta, o mais recente. Enquanto o mês não está coberto, o cartão mostra "desde <data>". Sem nenhum dado: cinza "coletando".
- Retenção: raw 14 dias (detalhe dos incidentes); agregado diário para sempre (histórico dos meses).
- O worker calcula no ciclo de manutenção horário e publica no mesmo instantâneo do Redis (`saude:backups`, chave `disponibilidade`); a API só normaliza. Ou seja, o cartão atualiza a cada hora - queda em andamento aparece antes nos cartões de saúde em tempo real.

## EZRA ERP
A central busca o ERP **de servidor para servidor** (`ERP_TV_URL` = `https://<domínio do ERP>/api/public/tv/monitoramento`, `ERP_TV_TOKEN` = chave de leitura do ERP; só o hash dela está no código do ERP). Sem CORS e sem credencial no navegador.

Se o ERP cair: o cartão dele fica cinza com "indisponível desde HH:MM" e o último dado com a hora; o lado do Zé Registra continua normal. Se a central cair: a TV mostra "Sem conexão com a central" e o último dado esmaece depois de 3 min.

## Variáveis (`.env` da VPS)
`TV_MONITORAMENTO_TOKEN_SHA256`, `ERP_TV_URL`, `ERP_TV_TOKEN`.
