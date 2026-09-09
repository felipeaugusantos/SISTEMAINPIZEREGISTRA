# Fase 6 — Reporte de problemas

Continuação da Fase 3 (central de atualizações, `docs` não escrito na
época) — o "Reportar problema" já existia (`ProblemaVersaoSistema`), mas
só tinha categoria/módulo/descrição livre. Esta fase transforma isso num
reporte de bug de verdade.

## Formulário (`POST /v1/admin/atualizacoes/{versao_id}/problemas`)

| Campo pedido | Onde vem |
| --- | --- |
| Módulo | `modulo` (opcional, restrito aos módulos da versão) |
| Descrição | `descricao` (obrigatório, min 20 caracteres) |
| Etapas para reproduzir | `etapas_reproduzir` (opcional) |
| Resultado esperado | `resultado_esperado` (opcional) |
| Resultado encontrado | `resultado_encontrado` (opcional) |
| Gravidade | `gravidade` (baixa/média/alta/crítica, padrão média) |
| Identificação automática da versão | `versao_id` na URL — vem do card que o operador clicou "Reportar problema", nunca digitado |
| Organização e usuário | `usuario.organizacao_id`/`usuario.id` da sessão autenticada — nunca digitado, nunca vem do corpo da requisição |
| Anexo opcional | `anexo` (nome/content_type/base64) |

## "Sem dados sensíveis" — como isso é garantido

- **Nunca é capturado nada automaticamente.** Sem screenshot, sem dump de
  DOM/console/localStorage, sem cookies. O único conteúdo que chega ao
  backend é o que o operador digitou nos campos de texto e o arquivo que
  ele escolheu explicitamente no seletor de anexo.
- **Anexo é opt-in e restrito por tipo**: só imagens comuns (PNG/JPEG/GIF/
  WEBP), PDF e texto plano (`TIPOS_ANEXO_PERMITIDOS`, `app/api/
  atualizacoes.py`) — nada de `.docx`/`.zip`/executável, superfície mínima
  para segredo embutido em metadado de arquivo.
- **Varredura de antivírus** (`app.malware_scan.escanear_upload_ou_rejeitar`,
  generalizado do achado FASE6-13 original que só cobria o portal do
  cliente) antes de persistir — falha fechada se o ClamAV não responder.
- **Limite de 8 MB** e nome de arquivo sempre salvo com hash SHA-256 no
  caminho (`problemas-versao/{organizacao_id}/{problema_id}/{hash}-{nome
  sanitizado}`), mesmo padrão de `DocumentoEntregaJuridico`.
- **Aviso explícito na tela** ("não anexe prints ou logs com senhas,
  tokens ou dados de clientes/processos") — não existe filtro automático
  confiável de PII em texto livre ou dentro de uma imagem; a
  responsabilidade de não incluir dado sensível é do operador, orientado
  claramente no formulário.
- Texto livre (`descricao`, `etapas_reproduzir`, etc.) passa por
  `_texto_seguro` (mesma sanitização usada em `versoes_sistema`/
  `feature_flags`) — rejeita padrões óbvios de credencial (`token=`,
  `senha=`, etc.) na descrição, mas isso é defesa complementar, não a
  garantia principal (que é "nada é capturado automaticamente").

## Critério de aceite: vinculado à versão, acompanhável até a resolução

- `versao_sistema_id` é `NOT NULL` desde a Fase 3 (FK `RESTRICT` —
  impossível excluir uma versão com problemas relatados pendurados nela).
- `status` (`aberto`/`em_analise`/`resolvido`) já existia; a tela de
  auditoria (`/admin/atualizacoes`, restrita a Tech) já permitia mudar o
  status — agora mostra também gravidade, etapas/resultado (dentro de um
  `<details>` para não poluir a tabela) e um link pro anexo, quando
  houver.
- Novo endpoint `GET /v1/admin/atualizacoes/problemas/{id}/anexo` (Tech)
  para baixar o anexo — nunca expõe o caminho de armazenamento bruto, só
  o conteúdo com o `Content-Type` original.

## Fora de escopo (registrado)

- Filtro automático de PII/segredo dentro de texto livre ou de imagens —
  não existe abordagem confiável sem falsos negativos perigosos; a defesa
  é o aviso explícito + restrição de tipo de arquivo + varredura de
  malware, não uma tentativa de "redigir" conteúdo arbitrário.
- Múltiplos anexos por relato — só um, suficiente para o caso de uso
  (um print ou um log por vez); pedir mais de um vira anexar de novo
  como comentário futuro se a necessidade aparecer.
