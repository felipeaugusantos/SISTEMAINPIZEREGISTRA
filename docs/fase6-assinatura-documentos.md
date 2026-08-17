# Fase 6 — Assinatura digital e documentos

Documentos do lead agora possuem versão, hash, validade, obrigatoriedade e estado de assinatura. A assinatura pelo portal registra o cliente, IP hash, data/hora, versão e SHA-256 do conteúdo canônico.

Alterações posteriores nos metadados anulam a assinatura anterior e incrementam a versão. Documentos expirados não podem ser assinados. A estrutura `AssinaturaDocumentoLead` mantém o histórico de assinaturas e o campo `provedor` permite futura integração com Clicksign, DocuSign ou outro provedor.

Rotas principais:

- `POST /v1/portal/documentos/{id}/assinar`
- `GET /v1/portal/resumo`

O checklist continua controlando pendências e a procuração assinada é usada como requisito para liberar o SLA de protocolo.
