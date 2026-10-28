"""Shared test helpers for authenticated MVP flows."""
from __future__ import annotations


def sign_in_demo(client):
    """Authenticate a TestClient with Vincent's configured demo account."""
    from services import config

    current = client.get("/api/auth/me")
    if current.status_code == 200 and current.json().get("authenticated"):
        return current.json().get("user")
    response = client.post("/api/auth/login", json={
        "email": config.VINCENT_DEMO_EMAIL,
        "password": config.VINCENT_DEMO_PASSWORD,
    })
    assert response.status_code == 200, response.text
    return response.json()["user"]
