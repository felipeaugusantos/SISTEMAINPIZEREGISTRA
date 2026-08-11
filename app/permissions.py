from dataclasses import dataclass


@dataclass(frozen=True)
class PermissaoDef:
    chave: str
    modulo: str
    nome: str
    descricao: str


PERMISSOES = (
    PermissaoDef(
        "dashboard.view", "Visao geral", "Visualizar painel", "Acessar os indicadores gerais."
    ),
    PermissaoDef("leads.view", "Leads", "Visualizar leads", "Consultar leads e pesquisas."),
    PermissaoDef(
        "leads.pii.view", "Leads", "Visualizar contatos", "Visualizar e-mail e telefone completos."
    ),
    PermissaoDef("leads.manage", "Leads", "Alterar leads", "Atualizar o andamento comercial."),
    PermissaoDef("leads.export", "Leads", "Exportar leads", "Exportar dados comerciais."),
    PermissaoDef(
        "leads.delete",
        "Leads",
        "Arquivar leads",
        "Arquivar e restaurar contatos sem apagar pesquisas.",
    ),
    PermissaoDef(
        "portfolio.view",
        "Processos monitorados",
        "Visualizar carteira",
        "Consultar processos acompanhados e pesquisar por procurador.",
    ),
    PermissaoDef(
        "portfolio.manage",
        "Processos monitorados",
        "Gerenciar carteira",
        "Cadastrar, vincular e atualizar processos acompanhados.",
    ),
    PermissaoDef(
        "finance.view", "Financeiro", "Visualizar financeiro", "Consultar contas e indicadores."
    ),
    PermissaoDef(
        "finance.manage", "Financeiro", "Gerenciar lançamentos", "Criar contas e registrar baixas."
    ),
    PermissaoDef(
        "finance.approve",
        "Financeiro",
        "Aprovar ajustes",
        "Cancelar lançamentos e estornar baixas com justificativa.",
    ),
    PermissaoDef(
        "finance.export", "Financeiro", "Exportar financeiro", "Exportar lançamentos em CSV."
    ),
    PermissaoDef(
        "validation.view", "Validacao", "Visualizar validacao", "Consultar classes e situacoes."
    ),
    PermissaoDef(
        "validation.review", "Validacao", "Revisar validacao", "Registrar revisoes tecnicas."
    ),
    PermissaoDef("risk.view", "Risco", "Visualizar risco", "Consultar o motor deterministico."),
    PermissaoDef("risk.review", "Risco", "Revisar risco", "Registrar avaliacao humana de risco."),
    PermissaoDef(
        "learning.view", "Aprendizado", "Visualizar aprendizado", "Consultar modelos e metricas."
    ),
    PermissaoDef(
        "learning.manage",
        "Aprendizado",
        "Gerenciar aprendizado",
        "Preparar dados, treinar, ativar e revisar modelos.",
    ),
    PermissaoDef(
        "rpi.view", "RPI", "Visualizar sincronizacao", "Consultar a cobertura das revistas."
    ),
    PermissaoDef("rpi.sync", "RPI", "Sincronizar RPI", "Iniciar e controlar importacoes."),
    PermissaoDef(
        "production.view",
        "Producao",
        "Visualizar producao",
        "Consultar saude, versoes e auditoria.",
    ),
    PermissaoDef(
        "production.manage",
        "Producao",
        "Configurar producao",
        "Alterar rollout e controles de producao.",
    ),
    PermissaoDef(
        "audit.view", "Auditoria", "Visualizar auditoria", "Consultar eventos administrativos."
    ),
    PermissaoDef("users.view", "Usuarios", "Visualizar usuarios", "Consultar usuarios e acessos."),
    PermissaoDef(
        "users.manage", "Usuarios", "Gerenciar usuarios", "Criar, alterar e bloquear usuarios."
    ),
    PermissaoDef("users.reset_password", "Usuarios", "Redefinir senha", "Emitir senha temporaria."),
    PermissaoDef(
        "users.revoke_sessions", "Usuarios", "Revogar sessoes", "Encerrar acessos ativos."
    ),
)

CHAVES_PERMISSAO = frozenset(p.chave for p in PERMISSOES)

PERFIS = {
    "administrador": CHAVES_PERMISSAO,
    "supervisor": CHAVES_PERMISSAO
    - {"users.manage", "users.reset_password", "users.revoke_sessions", "leads.delete"},
    "tecnico": frozenset(
        {
            "dashboard.view",
            "validation.view",
            "validation.review",
            "risk.view",
            "risk.review",
            "rpi.view",
        }
    ),
    "comercial": frozenset(
        {
            "dashboard.view",
            "leads.view",
            "leads.pii.view",
            "leads.manage",
            "leads.export",
            "portfolio.view",
            "portfolio.manage",
            "finance.view",
            "finance.manage",
            "finance.export",
            "risk.view",
        }
    ),
    "auditor": frozenset(
        {
            "dashboard.view",
            "leads.view",
            "portfolio.view",
            "finance.view",
            "validation.view",
            "risk.view",
            "learning.view",
            "rpi.view",
            "production.view",
            "audit.view",
            "users.view",
        }
    ),
    "operador": frozenset({"dashboard.view"}),
}


def permissoes_do_perfil(perfil: str) -> frozenset[str]:
    return PERFIS.get(perfil, PERFIS["operador"])
