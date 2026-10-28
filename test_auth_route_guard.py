from __future__ import annotations
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import main


def test_workspace_pages_redirect_signed_out_visitors_to_sign_in_and_preserve_destination():
    client = TestClient(main.app, follow_redirects=False)
    protected = [
        ("/dashboard", "/dashboard"),
        ("/concierge", "/concierge"),
        ("/concierge?intent=buy&new=1", "/concierge?intent=buy&new=1"),
        ("/saved", "/saved"),
        ("/results?run=1", "/results?run=1"),
        ("/taste", "/taste"),
    ]
    for route, expected_next in protected:
        response = client.get(route)
        assert response.status_code == 303, (route, response.status_code)
        parsed = urlparse(response.headers["location"])
        assert parsed.path == "/login"
        assert parse_qs(parsed.query).get("next") == [expected_next]


def test_signed_out_header_hides_private_workspace_navigation():
    client = TestClient(main.app)
    landing = client.get("/").text
    assert 'nav class="primary-nav"' not in landing
    assert 'href="/dashboard" data-page="dashboard"' not in landing
    assert 'href="/concierge" data-page="concierge"' not in landing
    assert 'href="/saved" data-page="saved"' not in landing

    signin = client.get("/login").text
    assert 'nav class="primary-nav"' not in signin


def test_concierge_api_rejects_signed_out_requests_and_accepts_demo_sign_in():
    client = TestClient(main.app)
    payload = {"intent": "discover", "goal": "Find modern Canadian art"}
    denied = client.post("/api/concierge", json=payload)
    assert denied.status_code == 401
    assert denied.json()["error_type"] == "AuthenticationRequired"

    from test_helpers import sign_in_demo
    sign_in_demo(client)
    assert client.get("/concierge", follow_redirects=False).status_code == 200
