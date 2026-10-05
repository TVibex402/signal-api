"""End to end through the real FastAPI app. One TestClient for the whole file: the MCP session manager can start only once."""
import httpx
import pytest
from fastapi.testclient import TestClient

import main
from helpers import World

TOK = "Tok" + "x" * 41
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def test_health_has_version_and_security_headers(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["version"] == main.VERSION
    assert r.headers["x-api-version"] == main.VERSION
    assert r.headers["x-content-type-options"] == "nosniff"


def test_invalid_mint_is_rejected(client):
    main._hits.clear()
    r = client.get("/signal?mint=nope")
    assert r.status_code == 400 and "detail" in r.json()


def test_public_pages_exist(client):
    main._hits.clear()
    assert client.get("/").status_code == 200
    assert "TVibex402" in client.get("/llms.txt").text
    assert client.get("/methodology").json()["verdict_rules"]["avoid_score_threshold"] == 50
    assert client.get("/accuracy").status_code == 200


def test_signal_end_to_end_with_fake_upstreams(client):
    w = World()
    w.add(TOK)
    main.app.state.client = w.client()
    main._cache.clear()
    main._hits.clear()
    r = client.get("/signal?mint=" + TOK)
    assert r.status_code == 200 and r.headers["x-cache"] == "MISS"
    body = r.json()
    assert body["verdict"] == "ok" and body["decision"]["verdict"] == "ok"
    batch = client.get(f"/signals?mints={TOK}&view=compact")
    assert batch.status_code == 200 and batch.json()["count"] == 1


def test_mcp_server_answers(client):
    if main.mcp_server is None:
        pytest.skip("mcp package not installed: " + str(main.mcp_error))
    init = client.post("/mcp", headers=MCP_HEADERS, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "ci", "version": "0"}}})
    assert init.status_code == 200 and "TVibex402" in init.text
    tools = client.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert tools.status_code == 200 and "check_token_risk" in tools.text
