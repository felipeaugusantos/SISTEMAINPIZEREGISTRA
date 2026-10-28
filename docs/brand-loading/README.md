# Personagem após carregar a marca do escritório

O painel renderiza primeiro /static/assets/personagem.png?v=1 no shell. Quando /v1/auth/me responde, aplicarMarcaDoEscritorio substitui a imagem por organizacao.marca.logo_url. A organização padrão ze-registra possui um upload antigo cadastrado (445 × 561, SHA-256 b1f944df2434593743f8794e668144d0665fd412b3beed2fd6621f05ad97f653). Essa configuração explica por que o personagem oficial aparece no Ctrl+F5 e logo depois muda.

A consulta em produção foi somente de leitura. O arquivo e a configuração cadastrados não foram alterados. O personagem oficial mantém SHA-256 9cd2a7e627edc066093e272c407b799d12532883dd26034f198670d5b1470442.

O ajuste restringe a substituição de imagem somente para a organização ze-registra no domínio app.zeregistra.com.br. O nome, as cores e os demais comportamentos de marca continuam existentes. Outras organizações, inclusive quando usam o domínio da plataforma, mantêm sua marca cadastrada. Outros domínios mantêm o comportamento anterior. O avatar flutuante continua com a arte oficial. CSS e o ajuste de tamanho e alinhamento da versão 1.2.31 não são modificados.

A suíte de regressão segura a resposta de /v1/auth/me até a arte oficial aparecer. Depois libera a resposta e verifica se ocorreu alteração no src, em desktop e celular. O teste foi executado antes da correção e falhou com a imagem cadastrada sobrescrevendo a oficial, reproduzindo o relato. Também cobre a marca de outro escritório no domínio oficial e a marca em outro domínio.

A proteção ocorre no shell compartilhado. Os 42 templates internos atualizam apenas a referência de admin-shell.js?v=57 para v58, seguindo o versionamento já adotado. Não houve mudança de banco, autenticação, permissões ou regras de negócio. Nenhum deploy foi executado nesta correção.

Validação após a correção: `node --test tests/frontend/*.test.cjs` aprovou os 32 testes, sem falhas ou ignorados. `git diff --check` passou. Não houve execução local de backend nem E2E com banco real nesta alteração de frontend.
