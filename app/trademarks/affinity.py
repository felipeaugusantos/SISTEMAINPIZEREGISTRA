from dataclasses import dataclass

from app.models import AfinidadeClasse


@dataclass(frozen=True, slots=True)
class ResultadoAfinidade:
    nivel: str
    rotulo: str
    justificativa: str
    revisao: str
    classes_atividade: tuple[str, ...]
    classes_processo: tuple[str, ...]


def avaliar_afinidade(
    classes_atividade: list[str],
    classes_processo: list[str],
    matriz: list[AfinidadeClasse],
) -> ResultadoAfinidade:
    atividade = tuple(dict.fromkeys(classes_atividade))
    processo = tuple(dict.fromkeys(classes_processo))
    if not atividade or not processo:
        return ResultadoAfinidade(
            "sem_dados",
            "Dados insuficientes",
            "Não há classes suficientes para comparar a atividade e o processo.",
            "pendente",
            atividade,
            processo,
        )

    iguais = sorted(set(atividade) & set(processo))
    if iguais:
        return ResultadoAfinidade(
            "identica",
            "Mesma classe",
            f"Classe(s) coincidente(s): {', '.join(iguais)}.",
            "nao_aplicavel",
            atividade,
            processo,
        )

    def nivel_canonico(valor: str) -> str:
        return {"media": "moderada", "média": "moderada"}.get(valor.lower(), valor.lower())

    por_par = {
        tuple(sorted((item.classe_origem, item.classe_destino))): item
        for item in matriz
        if item.status_revisao != "rejeitada"
    }
    encontrados = [
        por_par[tuple(sorted((origem, destino)))]
        for origem in atividade
        for destino in processo
        if tuple(sorted((origem, destino))) in por_par
    ]
    if encontrados:
        prioridade = {"alta": 0, "moderada": 1}
        melhor = sorted(
            encontrados,
            key=lambda item: prioridade.get(nivel_canonico(item.nivel), 9),
        )[0]
        nivel = nivel_canonico(melhor.nivel)
        pendente = melhor.status_revisao != "aprovada"
        return ResultadoAfinidade(
            nivel,
            "Alta afinidade" if nivel == "alta" else "Afinidade moderada",
            melhor.justificativa,
            "pendente" if pendente else "aprovada",
            atividade,
            processo,
        )

    return ResultadoAfinidade(
        "nao_mapeada",
        "Afinidade não mapeada",
        "A matriz inicial não contém relação entre estas classes.",
        "pendente",
        atividade,
        processo,
    )
