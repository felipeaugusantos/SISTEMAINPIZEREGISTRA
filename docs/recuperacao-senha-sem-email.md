# Recuperação de senha sem depender de e-mail

As contas atuais continuam sendo usadas. Esta correção não cria usuários nem altera perfis, permissões ou senhas existentes. A nova coluna de validade começa vazia para preservar os acessos atuais. O prazo de 60 minutos vale para novas senhas temporárias emitidas após a atualização e pode ser configurado por `TEMPORARY_PASSWORD_MINUTES` (5 a 1440 minutos).

## Atendimento pelo painel

Um usuário autorizado acessa **Usuários e acessos**, encontra a conta e usa **Redefinir senha**. O painel mostra a senha e seu vencimento. O responsável entrega a senha por um canal seguro após confirmar a identidade da pessoa. A pessoa entra com seu usuário habitual e troca a senha antes de acessar os módulos. Esse procedimento não depende do SMTP nem do suporte da hospedagem.

O procedimento limpa o bloqueio por tentativas falhas, cancela links de recuperação anteriores e encerra sessões antigas. A senha temporária expirada não permite entrar nem continuar uma sessão provisória. A operação permanece auditada. Uma conta desativada precisa ser reativada explicitamente antes de emitir a senha, respeitando as regras existentes.

Ter login administrativo não garante permissão para redefinir outras contas. É preciso ter `users.view` e `users.reset_password`. Os perfis Tech, CEO e Administrador têm acesso amplo. As regras de autoridade continuam valendo, inclusive a restrição de que só outro superadministrador pode redefinir uma conta de superadministrador. Não é necessário criar outras credenciais para conceder permissões a contas existentes.

O MFA já ativado permanece obrigatório. Perder o autenticador é um incidente diferente e exige os códigos de recuperação ou um procedimento específico de recuperação do segundo fator.

## Recuperação por e-mail

A resposta pública continua igual para conta existente ou inexistente quando o envio está habilitado, inclusive em falha SMTP. Ela orienta a usar o link mais recente e procurar o administrador caso a mensagem não chegue. Não é seguro revelar quais endereços têm conta. A falha de envio continua registrada no log da API.

Em produção, se o envio estiver desligado ou o endereço público não for HTTPS válido, a rota responde com indisponibilidade e orienta o atendimento pelo painel antes de consultar a conta. Confira `EMAIL_ENABLED`, `APP_PUBLIC_URL`, remetente e configurações SMTP. O provedor de envio pode ser independente da hospedagem. Trocar de provedor exige configurar as credenciais e, conforme o provedor, os registros de domínio, sem necessidade de suporte se a equipe já possuir esses acessos.

Solicitar outro link invalida os anteriores. Uma troca de senha, seja pelo painel, pelo próprio usuário ou pela ferramenta de emergência, cancela os links pendentes. A solicitação e o consumo dos links usam a mesma trava da conta que a redefinição administrativa, evitando uma recuperação antiga sobrescrever uma redefinição concluída em paralelo.

## Publicação e conferência

Antes de publicar, registre o commit em produção e faça backup. Aplique a mudança em homologação e execute `alembic upgrade head` antes de iniciar a nova API. O projeto possui o procedimento `docker/deploy.sh`, que aplica migrations durante a publicação. A revisão desta entrega acrescenta `zm96k2i9jf70` após `zl85j1h8ie69`.

Com uma conta de teste da organização, confira a emissão de senha, a indicação de vencimento, o login, a troca obrigatória e a continuidade do MFA. Confira também que a senha anterior e os links emitidos antes da redefinição deixaram de funcionar. Se o e-mail estiver habilitado, faça um envio controlado e confira logs, domínio do link e recebimento na caixa de destino. A recuperação administrativa é a alternativa disponível quando o envio falha.

Para reverter, pare a versão nova, execute `alembic downgrade zl85j1h8ie69` e restaure a versão anterior da aplicação. A reversão remove a informação de validade e perde essa proteção para as senhas provisórias. Não execute downgrade de produção sem avaliar o estado da implantação e guardar o backup.

## Emergência sem nenhuma sessão disponível

Com acesso técnico autorizado ao servidor, use o container da API. O comando solicita a nova senha sem exibi-la no terminal:

```sh
docker compose exec -e ADMIN_USUARIO=usuario_existente api /app/.venv/bin/python -m app.cli.redefinir_admin
```

Use a mesma conta existente. A ferramenta exige senha forte de pelo menos 12 caracteres, limpa o bloqueio e o prazo provisório, cancela links pendentes, encerra sessões anteriores e registra a redefinição na auditoria. Ela não reativa conta desativada nem remove MFA. Sem acesso ao painel e sem acesso técnico ao servidor, será necessário recuperar primeiro um desses acessos com quem administra a infraestrutura.

## Validação desta entrega

Foram aprovados 128 testes de recuperação, troca de senha, autoridade sobre contas, sessões, permissões, MFA, login social, envio de e-mail, configuração e limites de requisição. Um teste que exige PostgreSQL real foi pulado por indisponibilidade do banco local. Também passaram Ruff, a verificação de sintaxe do JavaScript e a geração offline do SQL de avanço e reversão da migração.

A falha preexistente em `test_producao_rejeita_salt_de_auditoria_curto` foi reproduzida com a classe Settings original. O teste agora fornece uma chave mestra válida para que alcance a validação do salt que pretende exercitar, sem alterar a validação de produção.

Não foi feita alteração no servidor nem envio real de e-mail. Aplicação e reversão em PostgreSQL real, comportamento concorrente sob RLS e o fluxo no ambiente publicado ainda precisam de validação em homologação antes da liberação.
