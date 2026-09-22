from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from aurora.api import app, runtime
from aurora.runtime import ConfirmationNotFound, PendingConfirmation, SessionNotFound


class FakeRuntime:
    async def send_message(self, session_id: str, texto: str) -> dict[str, Any]:
        if session_id == "missing":
            raise SessionNotFound
        if session_id == "pending":
            raise PendingConfirmation
        return {"resposta": f"Recebido: {texto}", "confirmacoes_pendentes": []}

    async def answer_confirmation(self, session_id: str, confirmation_id: str, confirmed: bool):
        if session_id == "missing":
            raise SessionNotFound
        if confirmation_id != "valid":
            raise ConfirmationNotFound
        return {"resposta": "Executado" if confirmed else "Negado", "confirmacoes_pendentes": []}

    async def events(self, session_id: str):
        if session_id == "missing":
            raise SessionNotFound
        return [{"id": "event-1"}]


def test_verification_routes_and_validation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AURORA_DOMAIN_DB", str(tmp_path / "domain.db"))
    monkeypatch.setenv("AURORA_SESSION_DB", str(tmp_path / "sessions.db"))
    from aurora.config import get_settings

    get_settings.cache_clear()
    with TestClient(app) as client:
        initial = client.get("/apartamentos/101/reservas")
        assert initial.status_code == 200
        assert initial.json() == [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]
        assert client.get("/apartamentos/999/reservas").status_code == 404
        assert client.post("/sessoes", json={"apartamento": "999"}).status_code == 422
        created = client.post("/sessoes", json={"apartamento": "101"})
        assert created.status_code == 201
        assert created.json()["session_id"]
    get_settings.cache_clear()


def test_conversation_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AURORA_DOMAIN_DB", str(tmp_path / "domain.db"))
    monkeypatch.setenv("AURORA_SESSION_DB", str(tmp_path / "sessions.db"))
    from aurora.config import get_settings

    get_settings.cache_clear()
    app.dependency_overrides[runtime] = lambda: FakeRuntime()
    try:
        with TestClient(app) as client:
            response = client.post("/sessoes/session/mensagens", json={"texto": "oi"})
            assert response.status_code == 200
            assert response.json() == {"resposta": "Recebido: oi", "confirmacoes_pendentes": []}
            assert client.post("/sessoes/pending/mensagens", json={"texto": "oi"}).status_code == 409
            assert client.post(
                "/sessoes/session/confirmacoes", json={"id": "invalid", "confirmado": True}
            ).status_code == 409
            assert client.post(
                "/sessoes/session/confirmacoes", json={"id": "valid", "confirmado": False}
            ).json()["resposta"] == "Negado"
            assert client.get("/sessoes/missing/eventos").status_code == 404
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

