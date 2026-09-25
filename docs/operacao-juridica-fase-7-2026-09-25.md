# Operação Jurídica — Fase 7

Data: 25/09/2026

## Objetivo

Completar o fluxo de evidências de entrega. O backend já armazenava um anexo opcional com hash, mas a interface enviava somente descrição, protocolo e referência textual; também não existia download protegido nem varredura antimalware nesse endpoint.

## Implementação

- upload de comprovante pela tela de evidências do prazo;
- formatos permitidos: PDF, PNG e JPEG;
- limite de 15 MB validado no navegador e no servidor;
- identificação do tipo pelo conteúdo real, sem confiar apenas no nome ou MIME informado;
- rejeição de extensão incompatível com a assinatura do arquivo;
- varredura pelo ClamAV antes da gravação;
- armazenamento controlado com SHA-256, tamanho, tipo real e autor;
- listagem dos anexos por prazo, inclusive para prazos encerrados e usuários de consulta;
- download autenticado e isolado por organização, prazo e documento;
- validação do SHA-256 no momento do download;
- nome de download gerado pelo servidor;
- auditoria de cada download.

## Compatibilidade

Entregas apenas textuais continuam aceitas. Os anexos já armazenados permanecem consultáveis; não foi necessária migration.

## Segurança

O download local valida que o caminho resolvido permanece dentro de `juridico/{organizacao}/{prazo}`. Objetos S3 são lidos pela camada de armazenamento existente. Conteúdo ausente retorna 404 e divergência de integridade retorna 409 sem entregar o arquivo.

## Validação

- Ruff aprovado;
- JavaScript aprovado por `node --check`;
- 193 testes aprovados e 8 ignorados por ambiente nos conjuntos Jurídico, Web, Worker e Deploy;
- cenários específicos de conteúdo real, extensão divergente, ordem antivírus/gravação, download auditado e hash divergente.
