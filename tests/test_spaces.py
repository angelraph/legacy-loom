from fastapi.testclient import TestClient

from app import config, main, store


def test_space_access_rules(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSCODE", "secret")
    client = TestClient(main.app)
    assert client.get("/api/access").json() == {"space": "main", "can_write": False}
    assert client.get("/api/access", headers={"x-family-key": "secret"}).json()["can_write"] is True
    assert client.get("/api/access", headers={"x-space": "try"}).json() == {"space": "try", "can_write": True}
    assert client.get("/api/access", params={"space": "try"}).json()["space"] == "try"


def test_unknown_space_falls_back_to_main():
    assert store.clean_space("anything") == store.MAIN
    assert store.clean_space(None) == store.MAIN
