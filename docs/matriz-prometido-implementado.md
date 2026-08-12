# Matriz “prometido × implementado”

Revisão técnica em **11/08/2026**. Esta matriz transforma promessas comerciais em critérios verificáveis e deve ser atualizada antes de cada demonstração, proposta ou liberação para cliente.

## Como interpretar

- **Implementado:** existe fluxo utilizável, persistência e cobertura de testes no sistema atual.
- **Parcial:** existe uma parte funcional, mas há dependência operacional ou escopo incompleto.
- **Não implementado:** não deve ser anunciado como funcionalidade disponível.
- **Operacional:** depende da equipe, contrato ou integração externa, e não apenas do software.

## Pesquisa, análise e relatório

| Promessa ao cliente | Situação | Evidência atual | Lacuna / condição de aceite |
| --- | --- | --- | --- |
| Pesquisa indicativa de marcas no INPI | Implementado | Importação e consulta da Seção V das RPIs, busca exata e ampliada | Manter sincronização, qualidade e monitoramento da cobertura das RPIs |
| Resultado inicial automatizado | Implementado | Resumo público, motor determinístico, matriz INPI e chance indicativa | Deve continuar identificado como estimativa, nunca garantia de registro |
| Relatório técnico completo | Parcial | PDF interno versionado e liberado pela equipe em Leads | A qualidade final depende da revisão e do parecer humano |
| Análise de registrabilidade | Parcial | Regras auditáveis, alto renome, afinidade, classes e agente de registrabilidade | Critérios “não analisados” exigem dados complementares ou revisão especializada |
| Probabilidade baseada em histórico | Parcial | Pipeline supervisionado, validação temporal, faixa de incerteza e gate de produção | Só exibir previsão quando existir modelo aprovado pelos critérios automáticos |
| Garantia de concessão da marca | Não implementado | O sistema não faz nem deve fazer essa promessa | O exame e a decisão pertencem ao INPI |

## Atendimento e operação

| Promessa ao cliente | Situação | Evidência atual | Lacuna / condição de aceite |
| --- | --- | --- | --- |
| Acompanhamento comercial estruturado | Implementado | Leads agrupados por contato, pipeline, responsável, próxima ação, tags e CRM | Definir SLA e disciplina operacional da equipe |
| Histórico de contatos | Implementado | Ligações, reuniões, WhatsApp, e-mail e observações em linha do tempo auditável | A interação precisa ser registrada pelo operador |
| Acompanhamento de processos no INPI | Implementado | Carteira, cadastro manual, busca por procurador, vinculação e movimentações da RPI | Não equivale a monitoramento oficial de prazo jurídico |
| Alertas por e-mail e WhatsApp | Parcial | Infraestrutura de e-mail existe para autenticação; links de WhatsApp existem no CRM | Não há automação transacional completa de alertas de processo por e-mail/WhatsApp |
| Atendimento humano especializado | Operacional | Parecer humano, responsáveis e histórico são suportados pelo sistema | Depende de equipe qualificada, escala e SLA contratados |
| Protocolo de pedido em 24 horas | Não implementado | Não há protocolo automatizado de depósito no INPI | Exige fluxo jurídico, procuração, documentos, pagamento e integração específica |

## Plataforma e gestão

| Promessa ao cliente | Situação | Evidência atual | Lacuna / condição de aceite |
| --- | --- | --- | --- |
| Centro de Operações multiempresa | Implementado | Organizações, usuários, perfis, permissões, sessões, limites e RLS | Executar teste recorrente de isolamento entre organizações |
| Gestão financeira | Implementado | Contas a pagar/receber, parcelas, formas, baixas, estornos, cancelamentos e log | Não substitui contabilidade fiscal nem conciliação bancária |
| Segurança administrativa | Implementado | Senhas Argon2, MFA TOTP, CSRF, sessões, trilha de auditoria e rate limit | MFA deve ser obrigatório para perfis privilegiados antes da produção ampla |
| Login Google e Apple | Parcial | Fluxos OAuth e vinculação de identidade estão implementados | Depende das credenciais, branding e aprovação dos provedores |
| Recuperação de senha por e-mail | Implementado em teste | Tokens seguros e Mailpit no ambiente local | Produção requer provedor SMTP, domínio e políticas SPF/DKIM/DMARC |
| Painel do próprio cliente | Não implementado | O painel atual é operacional/interno | Criar autenticação e experiência específicas para o titular/contratante |
| Cobrança automática de assinatura | Parcial | Planos, trial, limites e suspensão existem no núcleo SaaS | Falta integração com gateway, webhook idempotente e conciliação |
| Monitoramento de marketplaces e domínios | Não implementado | O escopo atual é INPI/RPI | Exige novas fontes, termos de uso, conectores e regras próprias |
| Gestão de patentes e contratos | Não implementado | O escopo atual de consulta é exclusivamente marcas | Não anunciar até existir modelo de dados, ingestão e fluxos específicos |

## Regras para materiais comerciais

1. Usar “pesquisa indicativa”, “chance estimada” e “apoio à decisão”.
2. Não usar “marca aprovada”, “registro garantido” ou “análise jurídica automática”.
3. Explicar quando uma função depende de revisão humana ou de configuração externa.
4. Vincular novas promessas a uma evidência verificável e a um teste de aceite antes da publicação.
5. Revisar esta matriz em toda mudança relevante de produto.
