# RPO e RTO

Achado da Fase 6 da missão de maturidade técnica (14/09/2026): a política de
backup e o simulado de restauração já existiam e funcionavam
(`docs/operacao-recuperacao-desastre.md`, `docker/backup-banco.sh`,
`docker/simulado-restauracao.sh`), mas nenhum documento declarava
explicitamente um RPO (quanto dado a organização aceita perder) e um RTO
(quanto tempo aceita ficar fora do ar) numéricos. Este documento fecha essa
lacuna com números derivados do que o sistema já faz de verdade — não
valores escolhidos a priori.

## RPO (Recovery Point Objective)

**Objetivo: ≤ 24 horas. Limite de alerta: 26 horas.**

Origem dos números, não escolhidos livremente:

- `docker/deploy.sh` roda `docker/backup-banco.sh` automaticamente antes de
  qualquer deploy com migration pendente, e a VPS tem um backup diário
  agendado independente de deploy (ver `docs/operacao-recuperacao-desastre.md`,
  item 6 — o simulado semanal roda "depois do backup diário de madrugada").
  Ou seja, o intervalo real entre dois backups é de até 24h.
- `app/settings.py::alerta_backup_max_horas` (usado em
  `app/alertas_plataforma.py`) já dispara o alerta crítico `BACKUP_AUSENTE`
  quando o backup mais recente tem mais de 26h — 2h de folga sobre o
  intervalo esperado de 24h, para não gerar falso positivo por variação
  normal do horário do cron.

Na pior hipótese (perda de dado logo antes do próximo backup previsto), o
RPO real é de até 24h. Se o backup atrasar além de 26h, o próprio sistema já
avisa antes que o RPO seja violado sem que ninguém perceba.

**O que reduziria o RPO** (não implementado agora, registrado como
possibilidade futura): backup incremental via WAL archiving do Postgres
reduziria o RPO para minutos, mas é uma mudança de infraestrutura maior
(armazenamento contínuo de WAL, não só dumps diários) — fora do escopo desta
fase.

## RTO (Recovery Time Objective)

**Objetivo provisório: ≤ 2 horas. Ainda não validado por medição real ponta
a ponta — ver "O que falta medir" abaixo.**

O procedimento completo de recuperação (`docs/operacao-recuperacao-desastre.md`)
tem 6 passos: preparar ambiente e segredos, restaurar o backup, validar
schema/Alembic, subir e validar a aplicação, critérios de interrupção, e o
simulado periódico. Hoje só uma fatia desse procedimento é **medida
automaticamente e de forma recorrente**:

- `docker/simulado-restauracao.sh` (reforçado nesta mesma fase — ver commit
  desta sessão) agora imprime a duração real de "subir `db-test` + restaurar
  o dump + conferir contagens das tabelas centrais" a cada execução, em
  `logs/simulado-restauracao.log` (crontab semanal, domingo 04:00, já
  documentado em `docs/operacao-recuperacao-desastre.md`).
- Os demais passos (recuperar segredos do cofre operacional, subir a
  aplicação completa, checklist manual de saúde) são procedimentais e
  dependem de um operador humano — nunca foram cronometrados numa
  restauração real ou simulada ponta a ponta.

### O que falta medir

Antes de tratar "≤ 2 horas" como uma meta validada (e não só uma estimativa
razoável para um banco de dezenas de GB + checklist manual), é preciso:

1. Deixar o simulado semanal rodar algumas semanas e observar a duração real
   registrada em `logs/simulado-restauracao.log` (agora impressa
   automaticamente).
2. Cronometrar pelo menos uma execução completa do procedimento de
   `docs/operacao-recuperacao-desastre.md` de ponta a ponta (pode ser em
   ambiente descartável, sem tocar produção) para medir os passos manuais
   que hoje não têm cronômetro.

Até essa medição existir, tratar "2 horas" como estimativa de trabalho, não
como RTO comprovado — revisar este documento assim que houver dado real.

## Responsável

Mesmo responsável operacional de `docs/operacao-recuperacao-desastre.md` e
`docs/slo-e-criterios-incidente.md` — quem recebe o alerta `BACKUP_AUSENTE`
e o e-mail de `ADMIN_EMAIL` (`app/alertas_plataforma.py`) é quem aciona a
recuperação.
