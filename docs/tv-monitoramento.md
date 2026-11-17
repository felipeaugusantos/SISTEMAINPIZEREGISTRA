# Painel de TV de monitoramento (Zé Registra + EZRA ERP)

Página `/tv/monitoramento`: os dois sistemas lado a lado, só números agregados, atualização a cada 30 s.

## Acesso (sem login)
- A TV usa uma **chave de leitura própria**, que só lê `GET /v1/monitoramento/tv`. Não abre nenhuma outra rota nem sessão.
- A VPS guarda apenas o **hash SHA-256** da chave (`TV_MONITORAMENTO_TOKEN_SHA256` no `.env`). Vazio = painel desligado (404).
- Endereço da TV: `https://app.zeregistra.com.br/tv/monitoramento#t=<chave>`. A chave vem no fragmento (não vai para log do servidor), fica guardada no navegador da TV e some da barra de endereço.
- **Revogar/trocar**: gere outra chave, troque o hash no `.env` e reinicie a API. A chave antiga para de valer na hora; abra o endereço novo na TV.
- Limite de 60 requisições/min por IP; resposta com `Cache-Control: no-store`.

## O que aparece (agregado, sem nomes)
- Zé Registra: erros de servidor (5xx) na última hora, erros e taxa em 24 h, usuários ativos, ações, organizações com uso, série de 7 dias, top 3 erros por operação.
  - Saúde: 0 erros 5xx na última hora = verde; 1 a 9 = amarelo; 10 ou mais = vermelho. Respostas 4xx (senha errada, limite de taxa) não contam.
- EZRA ERP: erros novos/abertos, usuários, ações, clientes com uso e sem uso, série de 7 dias, top 3 erros por rota. Saúde pelos erros novos na última hora (mesma escala).
- Nunca aparecem nome de organização/cliente, usuário, e-mail, IP nem texto de erro.

## EZRA ERP
A central busca o ERP **de servidor para servidor** (`ERP_TV_URL` = `https://<domínio do ERP>/api/public/tv/monitoramento`, `ERP_TV_TOKEN` = chave de leitura do ERP; só o hash dela está no código do ERP). Sem CORS e sem credencial no navegador.

Se o ERP cair: o cartão dele fica cinza com "indisponível desde HH:MM" e o último dado com a hora; o lado do Zé Registra continua normal. Se a central cair: a TV mostra "Sem conexão com a central" e o último dado esmaece depois de 3 min.

## Variáveis (`.env` da VPS)
`TV_MONITORAMENTO_TOKEN_SHA256`, `ERP_TV_URL`, `ERP_TV_TOKEN`.
