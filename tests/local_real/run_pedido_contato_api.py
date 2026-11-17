"""API + banco real: pedido de exclusão de contato (lead) com aprovação. Sai com código 1 se algo falhar."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as h
import httpx
from sqlalchemy import text

from app.auth import obter_usuario_atual
from app.main import app

ids = h.semear()
cur = {"k": ("a", "op")}
app.dependency_overrides[obter_usuario_atual] = lambda: h.usuario(*cur["k"])
resultados: list[tuple[str, bool]] = []


def check(nome: str, cond, extra: str = "") -> None:
    resultados.append((nome, bool(cond)))
    print(("OK    " if cond else "FALHA ") + nome, extra if not cond else "")


async def q(sql: str, **kw):
    async with h.Sessao() as s:
        return (await s.execute(text(sql), kw)).all()


async def main() -> None:
    A, B = ids["a"], ids["b"]
    LA, LB = A["leads"], B["leads"]
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://t", headers={"X-CSRF-Token": "csrf-sintetico"}) as c:
        def como(org: str, perfil: str):
            cur["k"] = (org, perfil)

        url = lambda lead: f"/v1/admin/leads/{lead}/solicitar-exclusao"  # noqa: E731
        arq = lambda i: q("select arquivado_em is not null from leads where id=:i", i=i)  # noqa: E731

        como("a", "op")
        r = await c.post(url(LA[2]), json={"motivo": "ab"})
        check("motivo curto -> 422", r.status_code == 422)
        r = await c.post(url(999999), json={"motivo": "Contato duplicado"})
        check("lead inexistente -> 404", r.status_code == 404)
        r = await c.post(url(LB[0]), json={"motivo": "Lead de outra organização"})
        check("lead de OUTRA organização -> 404 (isolamento)", r.status_code == 404)
        r = await c.post(url(LA[2]), json={"motivo": "Contato duplicado"})
        sid = r.json().get("id")
        check("operador (sem leads.delete) pede -> 201 pendente", r.status_code == 201 and r.json()["status"] == "pendente" and r.json()["tipo"] == "lead", r.text)
        check("pedir NÃO arquiva nem apaga", (await arq(LA[2]))[0][0] is False)
        r2 = await c.post(url(LA[2]), json={"motivo": "De novo"})
        check("pedido repetido devolve o mesmo (200), sem 2ª pendente", r2.status_code == 200 and r2.json()["id"] == sid and (await q("select count(*) from solicitacoes_exclusao_lead where status='pendente'"))[0][0] == 1)
        lista = (await c.get("/v1/admin/leads")).json()["itens"]
        flag = {i["id"]: i["exclusao_pendente"] for i in lista}
        check("lista de leads marca exclusao_pendente só no lead pedido", flag.get(LA[2]) is True and not any(v for k, v in flag.items() if k != LA[2]), str(flag))
        check("operador 403 ao arquivar direto", (await c.delete(f"/v1/admin/leads/{LA[2]}")).status_code == 403)
        check("operador 403 na fila de leads", (await c.get("/v1/admin/exclusoes-leads")).status_code == 403)
        check("operador 403 ao decidir", (await c.post(f"/v1/admin/exclusoes-leads/{sid}/decidir", json={"decisao": "aprovar", "senha": h.SENHA})).status_code == 403)
        check("operador 403 no lote", (await c.post("/v1/admin/exclusoes-leads/decidir-lote", json={"ids": [sid], "decisao": "rejeitar"})).status_code == 403)

        # concorrência: dois pedidos simultâneos do mesmo lead -> uma só pendente
        r = await asyncio.gather(*[c.post(url(LA[3]), json={"motivo": "Pedido simultâneo"}) for _ in range(4)])
        check("4 pedidos simultâneos -> 1 pendente no banco", (await q("select count(*) from solicitacoes_exclusao_lead where lead_id=:i and status='pendente'", i=LA[3]))[0][0] == 1 and all(x.status_code in (200, 201) for x in r), str([x.status_code for x in r]))
        sid_conc = (await q("select id from solicitacoes_exclusao_lead where lead_id=:i", i=LA[3]))[0][0]

        # IDs colidentes: pedido de pesquisa e de lead com o mesmo número
        r = await c.post(f"/v1/admin/pesquisas/{A['pesq'][0]}/solicitar-exclusao", json={"motivo": "Pesquisa duplicada"})
        pid = r.json()["id"]
        check("pedido de pesquisa segue funcionando (id próprio)", r.status_code == 201 and r.json().get("tipo") is None)

        como("a", "adm")
        fila = (await c.get("/v1/admin/exclusoes-leads")).json()
        check("admin vê só pedidos de LEAD na fila de leads", fila["total"] == 2 and all(i["tipo"] == "lead" for i in fila["itens"]))
        filap = (await c.get("/v1/admin/exclusoes-pesquisas")).json()
        check("fila de pesquisas continua só com pesquisas", filap["total"] == 1 and filap["itens"][0]["id"] == pid)
        # decidir um id de pedido-de-pesquisa na rota de leads não pode afetar a pesquisa
        colide = await c.post(f"/v1/admin/exclusoes-leads/{pid + 1000}/decidir", json={"decisao": "rejeitar"})
        check("id inexistente na rota de leads -> 404", colide.status_code == 404)

        # outra organização não decide nem vê
        como("b", "adm")
        check("admin de OUTRA org não vê pedidos", (await c.get("/v1/admin/exclusoes-leads")).json()["total"] == 0)
        check("admin de OUTRA org -> 404 ao decidir", (await c.post(f"/v1/admin/exclusoes-leads/{sid}/decidir", json={"decisao": "rejeitar"})).status_code == 404)
        check("admin de OUTRA org no lote ignora tudo", (await c.post("/v1/admin/exclusoes-leads/decidir-lote", json={"ids": [sid], "decisao": "rejeitar"})).json()["processadas"] == 0)

        como("a", "adm")
        r = await c.post(f"/v1/admin/exclusoes-leads/{sid}/decidir", json={"decisao": "rejeitar", "observacao": "Manter"})
        check("rejeitar: lead segue ativo", r.status_code == 200 and (await arq(LA[2]))[0][0] is False and r.json()["status"] == "rejeitada")
        check("rejeitar de novo -> 409 (decisão repetida)", (await c.post(f"/v1/admin/exclusoes-leads/{sid}/decidir", json={"decisao": "rejeitar"})).status_code == 409)
        como("a", "op")
        sid2 = (await c.post(url(LA[2]), json={"motivo": "Pedido de novo"})).json()["id"]
        check("após rejeição pode pedir de novo", sid2 != sid)
        como("a", "adm")
        check("aprovar com senha errada -> 403, nada muda", (await c.post(f"/v1/admin/exclusoes-leads/{sid2}/decidir", json={"decisao": "aprovar", "senha": "errada"})).status_code == 403 and (await arq(LA[2]))[0][0] is False)
        check("aprovar sem senha -> 403", (await c.post(f"/v1/admin/exclusoes-leads/{sid2}/decidir", json={"decisao": "aprovar"})).status_code == 403)
        r = await c.post(f"/v1/admin/exclusoes-leads/{sid2}/decidir", json={"decisao": "aprovar", "senha": h.SENHA})
        check("aprovar com senha: executada, lead ARQUIVADO (linha preservada)", r.status_code == 200 and (await arq(LA[2]))[0][0] is True and (await q("select count(*) from leads where id=:i", i=LA[2]))[0][0] == 1, r.text)
        check("pesquisas do lead preservadas", (await q("select count(*) from pesquisas_marca where lead_id=:i", i=LA[2]))[0][0] == 1)
        check("aprovar de novo -> 409 (aprovação repetida)", (await c.post(f"/v1/admin/exclusoes-leads/{sid2}/decidir", json={"decisao": "aprovar", "senha": h.SENHA})).status_code == 409)
        check("restaurar volta o lead", (await c.post(f"/v1/admin/leads/{LA[2]}/restaurar")).status_code == 200 and (await arq(LA[2]))[0][0] is False)

        # arquivado durante a pendência
        await c.delete(f"/v1/admin/leads/{LA[3]}")
        r = await c.post(f"/v1/admin/exclusoes-leads/{sid_conc}/decidir", json={"decisao": "aprovar", "senha": h.SENHA})
        check("lead arquivado durante a pendência: aprovar individual -> 409", r.status_code == 409 and (await q("select status from solicitacoes_exclusao_lead where id=:i", i=sid_conc))[0][0] == "pendente", r.text)
        r = await c.post("/v1/admin/exclusoes-leads/decidir-lote", json={"ids": [sid_conc], "decisao": "aprovar", "senha": h.SENHA})
        check("mesmo caso no lote: encerra como executada, registra 'já arquivado'", r.json()["ja_removidas"] == 1 and (await q("select status, decisao_observacao from solicitacoes_exclusao_lead where id=:i", i=sid_conc))[0] == ("executada", "O contato já estava arquivado"), r.text)
        check("pedir para lead arquivado -> 404", (await c.post(url(LA[3]), json={"motivo": "xxxx"})).status_code == 404)

        # lote misto: leads e pesquisas, cada um na sua rota
        como("a", "op")
        a4 = (await c.post(url(LA[4]), json={"motivo": "Lote lead"})).json()["id"]
        como("a", "adm")
        r = await c.post("/v1/admin/exclusoes-leads/decidir-lote", json={"ids": [a4], "decisao": "aprovar", "senha": "errada"})
        check("lote de leads com senha errada -> 403", r.status_code == 403 and (await arq(LA[4]))[0][0] is False)
        r = await c.post("/v1/admin/exclusoes-leads/decidir-lote", json={"ids": [a4], "decisao": "aprovar", "senha": h.SENHA})
        check("lote de leads aprova e arquiva", r.json()["processadas"] == 1 and (await arq(LA[4]))[0][0] is True)
        r = await c.post("/v1/admin/exclusoes-pesquisas/decidir-lote", json={"ids": [pid], "decisao": "aprovar", "senha": h.SENHA})
        check("lote de pesquisas continua excluindo a pesquisa", r.json()["processadas"] == 1 and (await q("select count(*) from pesquisas_marca where id=:i", i=A["pesq"][0]))[0][0] == 0)
        ev = await q("select acao from eventos_auditoria where recurso like 'lead:%' and acao in ('solicitar_exclusao','aprovar_exclusao','rejeitar_exclusao')")
        check("auditoria registrada para os pedidos de lead", len(ev) >= 6, str(len(ev)))

        # descarte independente
        como("a", "op")
        r = await c.patch(f"/v1/admin/leads/{LA[0]}", json={"status": "descartado"})
        check("descarte sem motivo -> 422 exato", r.status_code == 422 and r.json()["detail"] == "Motivo de perda é obrigatório ao descartar a oportunidade")
        r = await c.patch(f"/v1/admin/leads/{LA[0]}", json={"status": "descartado", "motivo_perda": "preco"})
        check("descarte com motivo: grava e NÃO arquiva nem cria pedido", r.status_code == 200 and (await arq(LA[0]))[0][0] is False and (await q("select count(*) from solicitacoes_exclusao_lead where lead_id=:i", i=LA[0]))[0][0] == 0)
        a = (await c.get("/v1/admin/leads")).json()["acoes"]
        check("operador: acoes.arquivar falso", a["arquivar"] is False)


asyncio.run(main())
falhas = [n for n, ok in resultados if not ok]
print(f"RESUMO {len(resultados) - len(falhas)}/{len(resultados)}")
sys.exit(1 if falhas else 0)
