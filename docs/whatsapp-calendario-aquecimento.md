# WhatsApp: calendário de aquecimento dos dois números

Não há método garantido: o WhatsApp não publica as regras de banimento. Este calendário reduz o risco ao fazer cada
número parecer o de uma pessoa ou empresa de verdade, com conversas reais nos dois sentidos, crescendo devagar.

- **Número A (vigilância)**: fala com clientes que consentiram. É o número que não pode ser perdido.
- **Número B (prospecção)**: primeiro contato comercial. Tratado como descartável.

**Dia 1** = o dia em que o chip é ativado no celular. Anote a data e preencha a coluna "Data".
Um celular por número, ligado e com internet. Nada de OpenWA até o Dia 15.

## Parte 1: aquecimento com uso normal (Dias 1 a 14)

"Conversas" = pessoas diferentes com quem houve troca nos DOIS sentidos. Meta mínima; passar um pouco é bom, correr não.

| Dia | Data | Número A | Número B | Observações |
|---|---|---|---|---|
| 1 | ___ | Ativar, perfil Business completo (nome, foto, descrição, e-mail, site). Receber de 3 conhecidos e responder | Igual ao A | Sem grupos, sem links |
| 2 | ___ | 3 a 5 conversas (só receber e responder) | 3 a 5 conversas | Peça que mandem áudio e foto também |
| 3 | ___ | 3 a 5 conversas | 3 a 5 conversas | |
| 4 | ___ | 5 conversas, você inicia 2 | 5 conversas, você inicia 2 | Mensagens diferentes entre si |
| 5 | ___ | 5 a 8 conversas | 5 a 8 conversas | Figurinhas, áudios e fotos |
| 6 | ___ | 5 a 8 conversas | 5 a 8 conversas | |
| 7 | ___ | 8 conversas | 8 conversas | **Revisão da semana 1**: nenhum aviso, nada de "enviada" sem resposta |
| 8 | ___ | 8 a 10 conversas | 8 a 10 conversas | Pode entrar em 1 grupo pequeno e legítimo |
| 9 | ___ | 10 conversas | 10 conversas | |
| 10 | ___ | 10 conversas | 10 a 12 conversas | |
| 11 | ___ | 10 a 12 conversas | 10 a 12 conversas | |
| 12 | ___ | 12 conversas | 12 conversas | Pelo menos metade com resposta rápida |
| 13 | ___ | 12 a 15 conversas | 12 a 15 conversas | |
| 14 | ___ | 15 conversas | 15 conversas | **Revisão da semana 2** (critérios abaixo) |

### Critérios para avançar (Dia 14)

Só siga para a Parte 2 se, para o número:
- [ ] não houve aviso, restrição, pedido de verificação nem desconexão;
- [ ] a maioria das mensagens aparece como entregue;
- [ ] a maioria das conversas teve resposta de volta;
- [ ] ninguém bloqueou nem denunciou.

Se algo falhou, repita a semana anterior sem aumentar o volume e refaça a revisão uma semana depois.

## Parte 2: conectar ao gateway e começar a automação (Dias 15 a 35)

| Dia | Data | Número A (vigilância) | Número B (prospecção) |
|---|---|---|---|
| 15 | ___ | Seguir como no Dia 14. Subir o gateway e ler o QR code só deste número (guia `whatsapp-openwa-runbook.md`, etapas 2 a 5) | Seguir como no Dia 14, sem gateway ainda |
| 16 a 18 | ___ | Registrar o consentimento de 5 a 10 clientes na tela. Ligar `WHATSAPP_ENABLED` e enviar de **3 a 5** avisos/dia | Seguir como no Dia 14 |
| 19 a 21 | ___ | **5 a 10** avisos/dia | Seguir como no Dia 14. Dia 21: ler o QR code do número B no gateway |
| 22 a 25 | ___ | 10 a 20 avisos/dia, só para consentimentos válidos | **5** primeiras mensagens/dia, em dia útil, textos curtos, personalizados, sem link |
| 26 a 28 | ___ | 20 a 40 avisos/dia | **8 a 10**/dia, se houver respostas e nenhuma denúncia |
| 29 a 35 | ___ | Ritmo normal (até o teto de 200/dia do sistema) | Subir de **5 em 5** a cada 3 dias, até **20**/dia (o teto do sistema) |

Regras durante a Parte 2:
- Responda manualmente quem escrever de volta: conversa de dois lados protege o número.
- A prospecção só sai em dia útil, das 9h às 18h. O sistema já faz isso e espera de 60 a 180 s entre mensagens.
- Revise a tela *WhatsApp e consentimentos* todo dia: mensagens "incertas", "falhou" e "bloqueada".

## Quando parar

Pare a automação do número (`WHATSAPP_ENABLED=false` e deploy) e volte ao uso normal por 3 a 5 dias se houver:
- alerta de restrição ou de desconexão repetida no painel;
- várias mensagens que não passam de "enviada" para "entregue";
- aviso do próprio WhatsApp sobre atividade suspeita;
- duas ou mais denúncias ou bloqueios seguidos na prospecção.

Depois de uma pausa, retome com **metade** do último volume e suba de novo devagar. Se o número B for banido, não
reutilize o chip: arranje outro e refaça o calendário. O número A só volta a automatizar depois de uma semana
inteira sem nenhum alerta.

## O que não fazer, em nenhuma fase

- Mandar a mesma mensagem para muita gente, ou links logo no início.
- Usar o número em outro aparelho, trocar de chip ou usar VPN durante o aquecimento.
- Adicionar pessoas em grupos sem pedir ou usar listas de transmissão.
- Misturar os dois usos: prospecção só no número B.
- Insistir com quem pediu para sair. O sistema respeita "SAIR", mas vale também para conversas manuais.
