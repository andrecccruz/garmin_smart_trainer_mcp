"""Servidor MCP mínimo para publicar treinos no Intervals.icu.

Escopo deliberadamente pequeno:
- só fala com https://intervals.icu (URL fixa no código);
- só atletas listados em INTERVALS_ATHLETES;
- atualiza/apaga apenas treinos criados por este servidor (external_id com prefixo "tmcp-");
- lê atividades concluídas só em resumo (sem streams, GPS ou dados de bem-estar).
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

BASE_URL = "https://intervals.icu/api/v1"
MARCA = "tmcp-"
ESPORTES = {
    "Ride", "VirtualRide", "MountainBikeRide", "GravelRide",
    "Run", "VirtualRun", "TrailRun",
    "Swim", "OpenWaterSwim",
    "Walk", "Hike", "WeightTraining", "Workout",
}
CATEGORIAS_LISTADAS = {"WORKOUT", "RACE_A", "RACE_B", "RACE_C"}
DIAS_FUTURO_MAX = 120
DIAS_PASSADO_MAX_LISTAGEM = 90
INTERVALO_LISTAGEM_MAX = 90
DIAS_ATIVIDADES_MAX = 42
CAMPOS_ATIVIDADE = [
    "id", "start_date_local", "type", "name", "moving_time", "distance",
    "average_heartrate", "max_heartrate", "icu_training_load",
    "icu_hr_zone_times", "perceived_exertion",
]
RE_ATLETA_ID = re.compile(r"^i\d+$")
RE_APELIDO = re.compile(r"^[a-z0-9_-]{1,32}$")

# .env na raiz do repo (src/intervals_mcp/server.py -> ../../.env).
# No container a configuração chega por --env-file e este arquivo não existe.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

LEITURA = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
ESCRITA = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
REMOCAO = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

# Permite injetar um transporte falso nos testes.
_transport: httpx.BaseTransport | None = None

mcp = MCPServer(
    "intervals",
    instructions=(
        "Publica e consulta treinos planejados no Intervals.icu, que sincroniza com o Garmin. "
        "Sempre proponha os treinos ao usuário e espere aprovação antes de chamar "
        "criar_treino, atualizar_treino ou apagar_treino. Depois de criar, confira o campo "
        "'aviso': se existir, o texto do treino não foi interpretado como treino estruturado."
    ),
)


class ErroValidacao(ToolError):
    """Entrada recusada antes de chegar na API (mensagem visível para o modelo)."""


class ErroIntervals(ToolError):
    """A API do Intervals.icu respondeu com erro (mensagem visível para o modelo)."""


# ---------------------------------------------------------------- utilidades

def _atletas() -> dict[str, str]:
    bruto = os.environ.get("INTERVALS_ATHLETES", "").strip()
    if not bruto:
        raise ErroValidacao("INTERVALS_ATHLETES não configurado no .env (formato: apelido=i123456,outro=i654321).")
    atletas: dict[str, str] = {}
    for par in bruto.split(","):
        if not par.strip():
            continue
        apelido, _, atleta_id = par.partition("=")
        apelido, atleta_id = apelido.strip().lower(), atleta_id.strip()
        if not RE_APELIDO.match(apelido) or not RE_ATLETA_ID.match(atleta_id):
            raise ErroValidacao(f"Entrada inválida em INTERVALS_ATHLETES: '{par.strip()}'. Use apelido=i123456.")
        atletas[apelido] = atleta_id
    return atletas


def _id_atleta(atleta: str) -> str:
    atletas = _atletas()
    chave = atleta.strip().lower()
    if chave not in atletas:
        raise ErroValidacao(f"Atleta '{atleta}' não está na allowlist. Disponíveis: {', '.join(sorted(atletas))}.")
    return atletas[chave]


def _data(texto: str, campo: str = "data") -> date:
    try:
        return date.fromisoformat(texto.strip())
    except (ValueError, AttributeError):
        raise ErroValidacao(f"{campo} inválida: '{texto}'. Use AAAA-MM-DD.") from None


def _data_futura(texto: str) -> date:
    d = _data(texto)
    hoje = date.today()
    if d < hoje:
        raise ErroValidacao(f"{d} já passou. Só publico treinos de hoje em diante.")
    if d > hoje + timedelta(days=DIAS_FUTURO_MAX):
        raise ErroValidacao(f"{d} está a mais de {DIAS_FUTURO_MAX} dias. Publique em blocos menores.")
    return d


def _validar_conteudo(nome: str | None, esporte: str | None, texto: str | None) -> None:
    if nome is not None and not (1 <= len(nome.strip()) <= 100):
        raise ErroValidacao("nome precisa ter entre 1 e 100 caracteres.")
    if esporte is not None and esporte not in ESPORTES:
        raise ErroValidacao(f"esporte '{esporte}' não suportado. Use um de: {', '.join(sorted(ESPORTES))}.")
    if texto is not None and not (1 <= len(texto.strip()) <= 4000):
        raise ErroValidacao("texto precisa ter entre 1 e 4000 caracteres.")


def _dry_run() -> bool:
    return os.environ.get("INTERVALS_DRY_RUN", "").strip() in {"1", "true", "sim"}


def _cliente() -> httpx.Client:
    chave = os.environ.get("INTERVALS_API_KEY", "").strip()
    if not chave:
        raise ErroValidacao("INTERVALS_API_KEY não configurada no .env.")
    return httpx.Client(
        base_url=BASE_URL,
        auth=httpx.BasicAuth("API_KEY", chave),
        timeout=20.0,
        follow_redirects=False,  # a chave nunca segue redirecionamento para outro host
        transport=_transport,
        headers={"User-Agent": "treinos-mcp"},
    )


def _req(metodo: str, caminho: str, **kwargs: Any) -> Any:
    with _cliente() as c:
        r = c.request(metodo, caminho, **kwargs)
    if r.status_code >= 400:
        dica = ""
        if r.status_code in (401, 403):
            dica = " Verifique a API key e se esse atleta te aceitou como coach no Intervals."
        elif r.status_code == 404:
            dica = " Recurso não encontrado (id de evento ou atleta errado?)."
        raise ErroIntervals(f"Intervals.icu respondeu HTTP {r.status_code}.{dica} Detalhe: {r.text[:200]}")
    return r.json() if r.content else None


def _resumo_evento(ev: dict[str, Any]) -> dict[str, Any]:
    tempo = ev.get("moving_time")
    return {
        "id": ev.get("id"),
        "data": (ev.get("start_date_local") or "")[:10],
        "categoria": ev.get("category"),
        "esporte": ev.get("type"),
        "nome": ev.get("name"),
        "texto": ev.get("description"),
        "duracao_min": round(tempo / 60) if tempo else None,
        "carga": ev.get("icu_training_load"),
        "criado_pelo_mcp": str(ev.get("external_id") or "").startswith(MARCA),
    }


def _com_aviso(resumo: dict[str, Any]) -> dict[str, Any]:
    if not resumo.get("duracao_min"):
        resumo["aviso"] = (
            "O Intervals não calculou duração para este treino: provavelmente o texto não foi "
            "reconhecido como passos estruturados e o relógio não vai ter alvos. Revise a sintaxe."
        )
    return resumo


def _evento_do_mcp(atleta_id: str, evento_id: int) -> dict[str, Any]:
    ev = _req("GET", f"/athlete/{atleta_id}/events/{int(evento_id)}")
    if not str(ev.get("external_id") or "").startswith(MARCA):
        raise ErroValidacao(
            f"O evento {evento_id} não foi criado por este MCP. Por segurança só altero ou apago "
            "treinos que eu mesmo publiquei; ajuste esse direto no Intervals."
        )
    return ev


# ------------------------------------------------------------------ ferramentas

@mcp.tool(annotations=LEITURA)
def listar_atletas() -> list[str]:
    """Lista os apelidos de atletas autorizados neste MCP."""
    return sorted(_atletas())


@mcp.tool(annotations=LEITURA)
def listar_treinos(atleta: str, de: str, ate: str) -> list[dict[str, Any]]:
    """Lista treinos planejados e provas no calendário do atleta entre duas datas (AAAA-MM-DD, até 90 dias).

    'criado_pelo_mcp' indica se o treino pode ser alterado/apagado por este MCP.
    """
    atleta_id = _id_atleta(atleta)
    inicio, fim = _data(de, "de"), _data(ate, "ate")
    if fim < inicio:
        raise ErroValidacao("'ate' precisa ser igual ou depois de 'de'.")
    if (fim - inicio).days > INTERVALO_LISTAGEM_MAX:
        raise ErroValidacao(f"Intervalo máximo de {INTERVALO_LISTAGEM_MAX} dias.")
    if inicio < date.today() - timedelta(days=DIAS_PASSADO_MAX_LISTAGEM):
        raise ErroValidacao(f"Listagem limitada aos últimos {DIAS_PASSADO_MAX_LISTAGEM} dias.")
    eventos = _req("GET", f"/athlete/{atleta_id}/events",
                   params={"oldest": inicio.isoformat(), "newest": fim.isoformat()}) or []
    return [_resumo_evento(e) for e in eventos if e.get("category") in CATEGORIAS_LISTADAS]


@mcp.tool(annotations=ESCRITA)
def criar_treino(atleta: str, data: str, nome: str, esporte: str, texto: str) -> dict[str, Any]:
    """Publica um treino planejado no calendário do atleta. Ele sincroniza com o Garmin.

    data: AAAA-MM-DD (hoje até 120 dias à frente).
    esporte: Ride, Run, Swim, WeightTraining etc.
    texto: treino na sintaxe do Intervals.icu, um passo por linha começando com '-'.
    Só chame depois que o usuário aprovar o treino.
    """
    atleta_id = _id_atleta(atleta)
    d = _data_futura(data)
    _validar_conteudo(nome, esporte, texto)
    payload = {
        "category": "WORKOUT",
        "start_date_local": f"{d.isoformat()}T00:00:00",
        "type": esporte,
        "name": nome.strip(),
        "description": texto.strip(),
        "external_id": f"{MARCA}{uuid.uuid4().hex[:16]}",
    }
    if _dry_run():
        return {"dry_run": True, "atleta": atleta, "payload": payload}
    ev = _req("POST", f"/athlete/{atleta_id}/events", json=payload)
    return _com_aviso(_resumo_evento(ev))


@mcp.tool(annotations=ESCRITA)
def atualizar_treino(
    atleta: str,
    evento_id: int,
    data: str | None = None,
    nome: str | None = None,
    esporte: str | None = None,
    texto: str | None = None,
) -> dict[str, Any]:
    """Altera um treino criado por este MCP (data, nome, esporte e/ou texto). Campos omitidos ficam como estão.

    Só chame depois que o usuário aprovar a alteração.
    """
    atleta_id = _id_atleta(atleta)
    _validar_conteudo(nome, esporte, texto)
    nova_data = _data_futura(data) if data else None
    ev = _evento_do_mcp(atleta_id, evento_id)
    payload = {
        "category": "WORKOUT",
        "external_id": ev["external_id"],
        "start_date_local": f"{nova_data.isoformat()}T00:00:00" if nova_data else ev.get("start_date_local"),
        "type": esporte or ev.get("type"),
        "name": (nome or ev.get("name") or "").strip(),
        "description": (texto or ev.get("description") or "").strip(),
    }
    if _dry_run():
        return {"dry_run": True, "atleta": atleta, "evento_id": evento_id, "payload": payload}
    resultado = _req("POST", f"/athlete/{atleta_id}/events/bulk", params={"upsert": "true"}, json=[payload]) or []
    if not resultado:
        raise ErroIntervals("O Intervals não devolveu o treino atualizado.")
    return _com_aviso(_resumo_evento(resultado[0]))


@mcp.tool(annotations=REMOCAO)
def apagar_treino(atleta: str, evento_id: int) -> dict[str, Any]:
    """Apaga UM treino futuro criado por este MCP. Só chame depois que o usuário confirmar."""
    atleta_id = _id_atleta(atleta)
    ev = _evento_do_mcp(atleta_id, evento_id)
    if _data((ev.get("start_date_local") or "")[:10]) < date.today():
        raise ErroValidacao("Esse treino já passou; não apago histórico.")
    if _dry_run():
        return {"dry_run": True, "atleta": atleta, "apagaria": _resumo_evento(ev)}
    n = _req("PUT", f"/athlete/{atleta_id}/events/bulk-delete", json=[{"external_id": ev["external_id"]}])
    return {"apagados": n, "treino": _resumo_evento(ev)}


@mcp.tool(annotations=LEITURA)
def atividades_recentes(atleta: str, dias: int = 14) -> list[dict[str, Any]]:
    """Resumo das atividades concluídas nos últimos N dias (1 a 42): duração, FC, carga, tempo em zonas de FC, RPE.

    Atividades importadas do Strava não aparecem pela API; a atleta precisa sincronizar pelo Garmin.
    """
    atleta_id = _id_atleta(atleta)
    dias = max(1, min(int(dias), DIAS_ATIVIDADES_MAX))
    fim = date.today()
    inicio = fim - timedelta(days=dias)
    atividades = _req("GET", f"/athlete/{atleta_id}/activities", params={
        "oldest": inicio.isoformat(),
        "newest": fim.isoformat(),
        "fields": ",".join(CAMPOS_ATIVIDADE),
    }) or []
    resumo = []
    for a in atividades:
        item = {k: a.get(k) for k in CAMPOS_ATIVIDADE if a.get(k) is not None}
        if "moving_time" in item:
            item["duracao_min"] = round(item.pop("moving_time") / 60)
        if "distance" in item:
            item["distancia_km"] = round(item.pop("distance") / 1000, 2)
        if "start_date_local" in item:
            item["data"] = item.pop("start_date_local")[:10]
        resumo.append(item)
    return resumo


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
