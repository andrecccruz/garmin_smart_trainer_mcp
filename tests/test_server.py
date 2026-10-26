"""Testes com a API do Intervals.icu simulada (nenhuma chamada de rede real)."""

import json
from datetime import date, timedelta

import httpx
import pytest

from intervals_mcp import server

AMANHA = (date.today() + timedelta(days=1)).isoformat()
ONTEM = (date.today() - timedelta(days=1)).isoformat()


class FakeIntervals:
    def __init__(self):
        self.chamadas = []
        self.eventos = {
            101: {"id": 101, "category": "WORKOUT", "start_date_local": f"{AMANHA}T00:00:00",
                  "type": "Ride", "name": "Z2", "description": "- 60m Z2 HR",
                  "moving_time": 3600, "icu_training_load": 45, "external_id": "tmcp-abc"},
            202: {"id": 202, "category": "WORKOUT", "start_date_local": f"{AMANHA}T00:00:00",
                  "type": "Run", "name": "Criado pela atleta", "description": "- 30m Z2 HR",
                  "moving_time": 1800, "external_id": None},
            303: {"id": 303, "category": "WORKOUT", "start_date_local": f"{ONTEM}T00:00:00",
                  "type": "Ride", "name": "Passado", "description": "- 60m Z2 HR",
                  "moving_time": 3600, "external_id": "tmcp-old"},
            404: {"id": 404, "category": "NOTE", "start_date_local": f"{AMANHA}T00:00:00",
                  "name": "Nota pessoal", "description": "privado"},
        }

    def __call__(self, req: httpx.Request) -> httpx.Response:
        corpo = json.loads(req.content) if req.content else None
        self.chamadas.append((req.method, req.url.path, dict(req.url.params), corpo, req.headers.get("authorization")))
        p = req.url.path.removeprefix("/api/v1")
        if req.method == "GET" and p == "/athlete/i111/events":
            return httpx.Response(200, json=list(self.eventos.values()))
        if req.method == "GET" and p.startswith("/athlete/i111/events/"):
            ev = self.eventos.get(int(p.rsplit("/", 1)[1]))
            return httpx.Response(200, json=ev) if ev else httpx.Response(404, text="not found")
        if req.method == "POST" and p == "/athlete/i111/events":
            parsed = "- " in corpo["description"]
            return httpx.Response(200, json={**corpo, "id": 999, "moving_time": 2700 if parsed else 0})
        if req.method == "POST" and p == "/athlete/i111/events/bulk":
            return httpx.Response(200, json=[{**corpo[0], "id": 101, "moving_time": 3000}])
        if req.method == "PUT" and p == "/athlete/i111/events/bulk-delete":
            return httpx.Response(200, json=1)
        if req.method == "GET" and p == "/athlete/i111/activities":
            return httpx.Response(200, json=[{
                "id": "a1", "start_date_local": f"{ONTEM}T07:00:00", "type": "Ride", "name": "Pedal",
                "moving_time": 5400, "distance": 45000, "average_heartrate": 138,
                "icu_training_load": 70, "streams": "nao deveria aparecer",
            }])
        if p.startswith("/athlete/i222/"):
            return httpx.Response(403, text="Access denied")
        return httpx.Response(500, text=f"rota inesperada {req.method} {p}")


@pytest.fixture
def api(monkeypatch):
    fake = FakeIntervals()
    monkeypatch.setattr(server, "_transport", httpx.MockTransport(fake))
    monkeypatch.setenv("INTERVALS_API_KEY", "chave-teste")
    monkeypatch.setenv("INTERVALS_ATHLETES", "ana=i111,bia=i222")
    monkeypatch.delenv("INTERVALS_DRY_RUN", raising=False)
    return fake


def test_auth_basic_com_usuario_API_KEY(api):
    server.listar_treinos("ana", AMANHA, AMANHA)
    auth = api.chamadas[0][4]
    assert auth.startswith("Basic ")
    import base64
    assert base64.b64decode(auth.split()[1]).decode() == "API_KEY:chave-teste"


def test_atleta_fora_da_allowlist(api):
    with pytest.raises(server.ErroValidacao, match="allowlist"):
        server.listar_treinos("carla", AMANHA, AMANHA)
    assert api.chamadas == []


def test_listar_filtra_notas_e_marca_origem(api):
    treinos = server.listar_treinos("ANA", AMANHA, AMANHA)
    nomes = {t["nome"]: t for t in treinos}
    assert "Nota pessoal" not in nomes
    assert nomes["Z2"]["criado_pelo_mcp"] is True
    assert nomes["Criado pela atleta"]["criado_pelo_mcp"] is False


def test_criar_treino_payload(api):
    r = server.criar_treino("ana", AMANHA, "Z2 longo", "Ride", "- 10m Z1 HR\n- 45m Z2 HR")
    metodo, caminho, _, corpo, _ = api.chamadas[-1]
    assert (metodo, caminho) == ("POST", "/api/v1/athlete/i111/events")
    assert corpo["category"] == "WORKOUT"
    assert corpo["start_date_local"] == f"{AMANHA}T00:00:00"
    assert corpo["external_id"].startswith("tmcp-")
    assert r["duracao_min"] == 45 and "aviso" not in r


def test_criar_treino_avisa_quando_texto_nao_parseia(api):
    r = server.criar_treino("ana", AMANHA, "Livre", "Ride", "pedala tranquilo")
    assert "aviso" in r


@pytest.mark.parametrize("data", [ONTEM, (date.today() + timedelta(days=200)).isoformat(), "26/10/2026"])
def test_criar_recusa_datas_invalidas(api, data):
    with pytest.raises(server.ErroValidacao):
        server.criar_treino("ana", data, "X", "Ride", "- 10m Z2 HR")
    assert api.chamadas == []


def test_criar_recusa_esporte_desconhecido(api):
    with pytest.raises(server.ErroValidacao, match="esporte"):
        server.criar_treino("ana", AMANHA, "X", "Futebol", "- 10m Z2 HR")


def test_atualizar_so_treino_do_mcp(api):
    with pytest.raises(server.ErroValidacao, match="não foi criado por este MCP"):
        server.atualizar_treino("ana", 202, nome="Hackeado")
    r = server.atualizar_treino("ana", 101, texto="- 50m Z2 HR")
    metodo, caminho, params, corpo, _ = api.chamadas[-1]
    assert (metodo, caminho, params) == ("POST", "/api/v1/athlete/i111/events/bulk", {"upsert": "true"})
    assert corpo[0]["external_id"] == "tmcp-abc"
    assert corpo[0]["name"] == "Z2"  # campo omitido mantido
    assert r["duracao_min"] == 50


def test_apagar_regras(api):
    with pytest.raises(server.ErroValidacao):
        server.apagar_treino("ana", 202)  # não é do MCP
    with pytest.raises(server.ErroValidacao, match="já passou"):
        server.apagar_treino("ana", 303)
    r = server.apagar_treino("ana", 101)
    metodo, caminho, _, corpo, _ = api.chamadas[-1]
    assert (metodo, caminho, corpo) == ("PUT", "/api/v1/athlete/i111/events/bulk-delete", [{"external_id": "tmcp-abc"}])
    assert r["apagados"] == 1


def test_atividades_resumo_e_limite_de_dias(api):
    r = server.atividades_recentes("ana", dias=999)
    _, _, params, _, _ = api.chamadas[-1]
    assert (date.fromisoformat(params["newest"]) - date.fromisoformat(params["oldest"])).days == 42
    assert r[0]["duracao_min"] == 90 and r[0]["distancia_km"] == 45.0
    assert "streams" not in r[0]


def test_dry_run_nao_escreve(api, monkeypatch):
    monkeypatch.setenv("INTERVALS_DRY_RUN", "1")
    r = server.criar_treino("ana", AMANHA, "Z2", "Ride", "- 30m Z2 HR")
    assert r["dry_run"] is True
    assert api.chamadas == []


def test_erro_403_explica_coach(api):
    with pytest.raises(server.ErroIntervals, match="coach"):
        server.listar_treinos("bia", AMANHA, AMANHA)


def test_sem_chave(api, monkeypatch):
    monkeypatch.delenv("INTERVALS_API_KEY")
    with pytest.raises(server.ErroValidacao, match="INTERVALS_API_KEY"):
        server.listar_treinos("ana", AMANHA, AMANHA)
