# Fase 4 — governança de retenção

## Comportamento comprovado antes da alteração

O job `privacidade.verificar_retencao`, em `app/worker.py`, considerava vencido
todo `Lead` que atendesse simultaneamente a estes critérios:

1. pertencer à organização analisada;
2. ter `criado_em` anterior a `agora - Organizacao.retencao_dados_dias`;
3. não possuir `anonimizado_em`.

A consulta era ordenada do registro mais antigo para o mais novo e limitada a
200 IDs. O tamanho dessa amostra era apresentado como total no alerta
`RETENCAO_PENDENTE`. Portanto, acima de 200 registros, o total exibido ficava
truncado. O job apenas gerava ou atualizava o alerta; não excluía dados.

Não eram considerados status do lead, processo ativo, contrato de serviço,
documento obrigatório, legal hold ou vigência versionada de política. O
endpoint de descarte manual verificava somente organização, anonimização e
idade do lead antes de apagar arquivos e anonimizar os dados.

## Política implantada no código

- Descarte automático continua desabilitado.
- A categoria inicialmente suportada pela matriz é `lead`; o prazo legado da
  organização permanece como fallback, sem alteração automática dos clientes.
- Cada mudança cria uma versão append-only em `politicas_retencao`, com prazo,
  justificativa, vigência, responsável e confirmação de redução.
- Política futura só passa a ser considerada a partir da vigência.
- A simulação retorna total real, registro mais antigo, categoria, status,
  bloqueadores, elegíveis à revisão e amostra limitada a 200 IDs.
- Cada simulação fica registrada por até 24 horas, vinculada ao tenant,
  responsável e prazo; uma alteração só aceita uma simulação compatível e
  ainda não utilizada.
- Lead com atendimento não encerrado, processo ativo, contrato ativo,
  documento marcado como obrigatório ou legal hold não pode ser descartado.
- Legal holds são isolados por organização, auditados e podem ser liberados.
- Lead já anonimizado produz resposta idempotente.
- Falha na remoção física de arquivo impede anonimização e gera auditoria de
  falha sem registrar caminho ou conteúdo do documento.
- A trilha `EventoAuditoria` preserva somente metadados mínimos: recurso,
  responsável, resultado e contagens, sem copiar os dados pessoais removidos.

## Limites intencionais

Esta entrega não executa descarte em lote e não cria política automática para
outras entidades. `cliente`, `documento`, `processo` e `auditoria` só devem ser
adicionados à matriz quando houver base legal, vínculo de dados e prazo
aprovados. Até lá, funcionam como bloqueadores da categoria `lead`, nunca como
alvos de remoção.

| Categoria planejada | Estado nesta fase | Próxima validação necessária |
|---|---|---|
| Prospects | Não executável | Definir finalidade, origem do dado e marco de encerramento da prospecção |
| Leads | Executável somente com revisão humana | Política versionada implementada |
| Clientes | Protegida | Definir encerramento contratual e obrigações pós-contrato |
| Processos | Protegida | Definir trânsito/encerramento e prazos legais aplicáveis |
| Documentos | Bloqueadora quando obrigatória | Classificar cada tipo documental e sua obrigação jurídica |
| Dados financeiros | Protegida | Validar obrigações fiscais e contábeis por tipo de lançamento |
| Logs | Protegida | Separar segurança, auditoria e observabilidade técnica |
| Solicitações LGPD | Protegida | Definir trilha mínima de comprovação permitida |

## Operação segura

1. Informar o prazo pretendido na tela de Confiabilidade e LGPD.
2. Executar **Simular impacto** e revisar total, status e bloqueios.
3. Se o prazo mudar, informar justificativa e data de vigência.
4. Se houver redução, marcar a confirmação explícita.
5. Aplicar legal hold aos casos que precisem de preservação adicional.
6. Salvar a política; nenhum dado será descartado por essa ação.
7. O descarte permanece individual, autorizado e sujeito a nova verificação
   no servidor no momento da ação.
