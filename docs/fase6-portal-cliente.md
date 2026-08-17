# Fase 6 — Portal do cliente

O portal usa sessão própria em cookie `HttpOnly`, com expiração, revogação e
invalidação automática quando o acesso é bloqueado. A recuperação usa token
hashado, de uso único e validade de 30 minutos; nenhum token é devolvido pela API.

O responsável pelo atendimento (ou administrador) pode gerar, consultar, abrir e
revogar o acesso pelo lead. Todas as consultas e alterações relevantes registram
`EventoAuditoria`, organização, cliente, recurso, IP hash e horário.

O cliente só recebe dados derivados do seu `cliente_id`, `lead_id` e
`organizacao_id`. Processos, propostas, documentos, GRUs, pagamentos, parcelas,
mensagens, arquivos, notificações e eventos são filtrados por esse escopo. IDs de
outro tenant retornam 404 e não revelam a existência do recurso.

Novas rotas incluem recuperação de acesso, processos, download protegido de
arquivos, leitura de notificações e dados financeiros no resumo. O download de
arquivos valida também o caminho físico dentro da pasta do cliente.

Aplicar com `alembic upgrade head`; a revisão `e52f7a8b9c01` cria os tokens de
recuperação do portal.
