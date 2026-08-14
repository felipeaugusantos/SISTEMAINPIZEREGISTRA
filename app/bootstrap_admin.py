import asyncio

from sqlalchemy import select

from app.auth import hash_senha
from app.database import session_factory
from app.models import Organizacao, PermissaoOperacoes, UsuarioOperacoes
from app.permissions import PERMISSOES
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant


async def bootstrap() -> None:
    settings = get_settings()
    async with session_factory() as session:
        organizacao = (await session.execute(
            select(Organizacao).where(Organizacao.slug == settings.default_organization_slug)
        )).scalar_one()
        await aplicar_contexto_tenant(session, organizacao.id)
        existentes = {
            p.chave: p for p in (await session.execute(select(PermissaoOperacoes))).scalars()
        }
        for ordem, definicao in enumerate(PERMISSOES):
            permissao = existentes.get(definicao.chave)
            if permissao is None:
                session.add(PermissaoOperacoes(
                    chave=definicao.chave, modulo=definicao.modulo, nome=definicao.nome,
                    descricao=definicao.descricao, ordem=ordem,
                ))
            else:
                permissao.modulo = definicao.modulo
                permissao.nome = definicao.nome
                permissao.descricao = definicao.descricao
                permissao.ordem = ordem
        usuario = (await session.execute(
            select(UsuarioOperacoes).where(UsuarioOperacoes.usuario == settings.admin_username.lower())
        )).scalar_one_or_none()
        if usuario is None:
            session.add(UsuarioOperacoes(
                organizacao_id=organizacao.id,
                nome="Administrador", usuario=settings.admin_username.lower(),
                email=settings.admin_email.lower(), perfil="administrador",
                senha_hash=hash_senha(settings.admin_password), ativo=True,
                superadmin=True,
                alterar_senha=settings.admin_password == "altere-esta-senha", criado_por="bootstrap",
            ))
        elif usuario.organizacao_id == organizacao.id and not usuario.superadmin:
            usuario.superadmin = True
        await session.commit()


if __name__ == "__main__":
    asyncio.run(bootstrap())
