# Operação Jurídica — Fases 5 e 6

Data: 25/09/2026

## Fase 5 — Gestão visual do catálogo

A página `/admin/operacao-juridica` passa a consultar e apresentar:

- regras de prazo por código de despacho, com vigência, fonte, confiança, checklist e evidências;
- suspensões, indisponibilidades e feriados oficiais cadastrados;
- autor da homologação e situação ativa/inativa.

Usuários com `legal.view` podem consultar o catálogo. O cadastro continua restrito a superadministradores e usa os endpoints append-only existentes. Uma nova vigência não edita silenciosamente o conteúdo histórico.

## Fase 6 — Reprocessamento controlado

Uma regra ativa e homologada pode ser usada para simular publicações anteriormente classificadas como `sem_prazo_mapeado`. A seleção exige simultaneamente:

- mesma organização do operador;
- processo ainda monitorado pela organização;
- mesmo código de despacho após normalização;
- data da RPI dentro da vigência da regra;
- marca anterior exatamente igual a `sem_prazo_mapeado`.

A execução exige permissão `legal.manage`, justificativa com no mínimo 20 caracteres e confirmação literal `REPROCESSAR`. Cada chamada é limitada a 500 avaliações. As linhas são bloqueadas com `FOR UPDATE SKIP LOCKED` para evitar que execuções concorrentes processem o mesmo lote.

Depois de liberar as avaliações selecionadas, o motor jurídico roda normalmente. Se o motor falhar, sua transação desfaz a liberação; a tentativa fica registrada no ledger sanitizado de execução. Em sucesso, o sistema registra o evento `reprocessar_juridico` com regra, código, justificativa, quantidade e resultado.

## Compatibilidade e rollback

Não há migration nova: as duas fases reutilizam `regras_prazo_juridico`, `excecoes_calendario_juridico`, `movimentacoes_avaliadas_juridico` e a auditoria existente. O rollback de aplicação consiste em retornar ao commit anterior; nenhum dado precisa ser convertido.

## Validação

- Ruff sem ocorrências;
- JavaScript validado por `node --check`;
- testes de operação jurídica, web, worker e deploy;
- cenários específicos de confirmação literal, simulação, remoção controlada, execução do motor e auditoria.
