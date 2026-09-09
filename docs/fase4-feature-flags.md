# Fase 4 — Feature flags por organização

## Resultado

`FeatureFlag` (cadastro global: código, nome, descrição, módulos
envolvidos, dependências, estado padrão, responsável, kill-switch geral,
data de ativação/expiração) e `FeatureFlagOrganizacao` (autorização
granular por organização — sem registro para o par flag/organização, a
organização usa `estado_padrao`). Ações por organização: **Ativar agora**,
**Testar somente com administradores**, **Adiar ativação**, **Desativar
funcionalidade** — cada uma vira um `estado` (`ativo` /
`somente_administradores` / `adiado` / `desativado`) no registro daquela
organização, sem afetar as demais.

`app.feature_flags.flag_ativa(session, codigo, usuario)` fecha em falso
sempre que houver dúvida (flag inexistente, kill-switch geral desligado,
expirada, organização sem override caindo em `estado_padrao="desligado"`).
`app.feature_flags.exigir_feature_ativa(codigo)` é a dependency FastAPI que
qualquer endpoint futuro usa para se gatear por trás de uma flag —
`Depends(exigir_feature_ativa("codigo-da-flag"))`, mesmo padrão de
`exigir_permissao`.

## Proteções

- **Migrations não podem depender da decisão do usuário** — regra de
  processo para qualquer migration futura associada a uma flag: o schema
  muda incondicionalmente (a coluna/tabela existe sempre), só o
  *comportamento em runtime* é que a flag controla. Não há como impor isso
  via schema; fica documentado aqui e no docstring de `app.feature_flags`
  para quem escrever a próxima migration ligada a uma flag.
- **API e banco devem continuar compatíveis com a flag desligada** — mesma
  natureza: regra de design para quem implementar uma funcionalidade atrás
  de uma flag (sempre precisa existir um caminho "desligado" que funciona).
  `flag_ativa()` fecha em falso por padrão, o que ajuda a não esquecer o
  caminho desligado, mas a garantia real é de quem escreve o código atrás
  da flag.
- **Correção de segurança não pode ser feature flag** — operacionalizada
  como confirmação explícita obrigatória na criação
  (`confirmar_nao_e_correcao_seguranca`, mesmo padrão de
  `confirmar_publicacao`/`confirmar_arquivamento` em
  `app.api.versoes_sistema`). Uma correção de segurança sempre é deploy
  normal e incondicional (via `VersaoSistema`, não via `FeatureFlag`).
- **O backend deve validar a flag; ocultar só a interface não é
  suficiente** — enforced via `exigir_feature_ativa`, testado em
  `tests/test_feature_flags.py` chamando a dependency diretamente e
  confirmando o 403 quando a flag está desligada, sem depender de nenhuma
  interação de tela.

## Permissões

Cadastro e ações restritas a superadmin (`SuperAdminDep`, mesmo nível de
`app.api.versoes_sistema`, Fase 1) — é a equipe de Tech quem decide qual
organização testa o quê, não autoatendimento pela própria organização.
`GET /v1/admin/feature-flags/{codigo}/verificar` é aberto a qualquer
usuário autenticado (é o que o frontend consultaria para decidir mostrar
algo na interface) — nunca é a proteção de verdade.

## Critério de aceite

Uma organização pode testar uma funcionalidade sem afetar as demais: cada
ação (`ativar`, `testar-administradores`, `adiar`, `desativar`) grava um
`FeatureFlagOrganizacao` filtrado por `(feature_flag_id, organizacao_id)`
— alterar o estado de uma organização nunca toca o registro de outra.
