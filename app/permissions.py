from dataclasses import dataclass


@dataclass(frozen=True)
class PermissaoDef:
    chave: str
    modulo: str
    nome: str
    descricao: str


PERMISSOES = (
    PermissaoDef("dashboard.view", "Visao geral", "Visualizar painel", "Acessar os indicadores gerais."),
    PermissaoDef("leads.view", "Leads", "Visualizar leads", "Consultar leads e pesquisas."),
    PermissaoDef("leads.pii.view", "Leads", "Visualizar contatos", "Visualizar e-mail e telefone completos."),
    PermissaoDef("leads.manage", "Leads", "Alterar leads", "Atualizar o andamento comercial."),
    PermissaoDef("leads.export", "Leads", "Exportar leads", "Exportar dados comerciais."),
    PermissaoDef(
        "leads.delete",
        "Leads",
        "Arquivar leads",
        "Arquivar e restaurar contatos sem apagar pesquisas.",
    ),
    PermissaoDef(
        "crm.view",
        "CRM",
        "Visualizar CRM",
        "Consultar empresas, contatos, oportunidades e timeline.",
    ),
    PermissaoDef("crm.manage", "CRM", "Gerenciar CRM", "Alterar empresas, contatos, oportunidades e tarefas."),
    PermissaoDef(
        "prospeccao.view",
        "Prospecção",
        "Visualizar radar",
        "Consultar prospects.",
    ),
    PermissaoDef(
        "prospeccao.manage",
        "Prospecção",
        "Gerenciar radar",
        "Cadastrar, importar e triar prospects manualmente.",
    ),
    PermissaoDef(
        "prospeccao.convert",
        "Prospecção",
        "Converter em lead",
        "Transformar um prospect em oportunidade comercial (lead).",
    ),
    PermissaoDef(
        "prospeccao.export",
        "Prospecção",
        "Exportar prospects",
        "Exportar dados do radar de prospecção.",
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
        "legal.view",
        "Operacao juridica",
        "Visualizar operacao juridica",
        "Consultar agenda, prazos, notificacoes e historico juridico.",
    ),
    PermissaoDef(
        "legal.manage",
        "Operacao juridica",
        "Gerenciar operacao juridica",
        "Criar, confirmar, atribuir e concluir prazos juridicos.",
    ),
    PermissaoDef("finance.view", "Financeiro", "Visualizar financeiro", "Consultar contas e indicadores."),
    PermissaoDef("finance.manage", "Financeiro", "Gerenciar lançamentos", "Criar contas e registrar baixas."),
    PermissaoDef(
        "finance.approve",
        "Financeiro",
        "Aprovar ajustes",
        "Cancelar lançamentos e estornar baixas com justificativa.",
    ),
    PermissaoDef("finance.export", "Financeiro", "Exportar financeiro", "Exportar lançamentos em CSV."),
    PermissaoDef("validation.view", "Validacao", "Visualizar validacao", "Consultar classes e situacoes."),
    PermissaoDef("validation.review", "Validacao", "Revisar validacao", "Registrar revisoes tecnicas."),
    PermissaoDef("risk.view", "Risco", "Visualizar risco", "Consultar o motor deterministico."),
    PermissaoDef("risk.review", "Risco", "Revisar risco", "Registrar avaliacao humana de risco."),
    PermissaoDef("learning.view", "Aprendizado", "Visualizar aprendizado", "Consultar modelos e metricas."),
    PermissaoDef(
        "learning.manage",
        "Aprendizado",
        "Gerenciar aprendizado",
        "Preparar dados, treinar, ativar e revisar modelos.",
    ),
    PermissaoDef("rpi.view", "RPI", "Visualizar sincronizacao", "Consultar a cobertura das revistas."),
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
    PermissaoDef("audit.view", "Auditoria", "Visualizar auditoria", "Consultar eventos administrativos."),
    PermissaoDef("users.view", "Usuarios", "Visualizar usuarios", "Consultar usuarios e acessos."),
    PermissaoDef("users.manage", "Usuarios", "Gerenciar usuarios", "Criar, alterar e bloquear usuarios."),
    PermissaoDef("users.reset_password", "Usuarios", "Redefinir senha", "Emitir senha temporaria."),
    PermissaoDef("users.revoke_sessions", "Usuarios", "Revogar sessoes", "Encerrar acessos ativos."),
)

CHAVES_PERMISSAO = frozenset(p.chave for p in PERMISSOES)

PERMISSOES_FINANCEIRO = frozenset({"finance.view", "finance.manage", "finance.approve", "finance.export"})

PERFIS = {
    "administrador": CHAVES_PERMISSAO,
    "ceo": CHAVES_PERMISSAO,
    "tech": CHAVES_PERMISSAO,
    "supervisor": CHAVES_PERMISSAO - {"users.manage", "users.reset_password", "users.revoke_sessions", "leads.delete"},
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
    # Achado H9/P1 da auditoria (10/09/2026): o perfil "comercial" tinha
    # permissao de GERENCIAR financeiro, juridico e carteira de processos,
    # muito alem do que o atendimento comercial (leads/CRM/prospeccao)
    # precisa. Confirmado com o negocio que ninguem no perfil de fato edita
    # esses dados -- so consulta para dar contexto no atendimento (ex.: ver
    # se o cliente ja pagou, prazos juridicos, processos em andamento).
    # Reduzido a view-only nesses tres modulos; manage/export ficaram de
    # fora (quem precisar deles usa outro perfil, ex. financeiro/tech).
    "comercial": frozenset(
        {
            "dashboard.view",
            "leads.view",
            "leads.pii.view",
            "leads.manage",
            "leads.export",
            "crm.view",
            "crm.manage",
            "prospeccao.view",
            "prospeccao.manage",
            "prospeccao.convert",
            "prospeccao.export",
            "portfolio.view",
            "legal.view",
            "finance.view",
            "risk.view",
        }
    ),
    "financeiro": PERMISSOES_FINANCEIRO,
    "auditor": frozenset(
        {
            "dashboard.view",
            "leads.view",
            "crm.view",
            "prospeccao.view",
            "portfolio.view",
            "legal.view",
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

# Fase 4 da reanalise de 12/09/2026: perfis com acesso amplo ou a dados
# sensiveis (financeiro, producao, usuarios) passam a exigir MFA ativo.
# Contas com superadmin=True tambem exigem, independente do perfil (ver
# app/auth.py::obter_usuario_atual).
PERFIS_MFA_OBRIGATORIO = frozenset({"administrador", "ceo", "tech", "supervisor", "financeiro"})


def permissoes_do_perfil(perfil: str) -> frozenset[str]:
    return PERFIS.get(perfil, PERFIS["operador"])


def destino_inicial(perfil: str, permissoes: set[str] | frozenset[str]) -> str:
    """Retorna a primeira tela que a conta efetivamente pode acessar."""
    if perfil == "financeiro":
        return "/admin/financeiro"
    if perfil == "administrador" or "dashboard.view" in permissoes:
        return "/admin"
    destinos = (
        ("finance.view", "/admin/financeiro"),
        ("crm.view", "/admin/crm"),
        ("leads.view", "/admin/pesquisas"),
        ("portfolio.view", "/admin/processos-monitorados"),
        ("legal.view", "/admin/operacao-juridica"),
        ("validation.view", "/admin/validacao"),
        ("risk.view", "/admin/risco"),
        ("learning.view", "/admin/aprendizado"),
        ("production.view", "/admin/producao"),
        ("users.view", "/admin/usuarios"),
    )
    return next((pagina for chave, pagina in destinos if chave in permissoes), "/admin")
