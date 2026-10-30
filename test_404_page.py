from fastapi.testclient import TestClient

import main


def test_unknown_browser_route_uses_branded_vincent_404_page():
    response = TestClient(main.app).get("/this-page-does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "This page isn't in our collection." in response.text
    assert "Ask my concierge" in response.text
    assert 'href="/login?next=%2Fconcierge"' in response.text
    assert "Vincent" in response.text


def test_unknown_api_route_keeps_json_404_response():
    response = TestClient(main.app).get("/api/this-endpoint-does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json().get("detail") == "Not Found"


def test_404_template_has_authenticated_concierge_destination():
    template = (main.BASE_DIR / "templates" / "404.html").read_text()

    assert "{% if current_user %}" in template
    assert 'href="/concierge"' in template
    assert 'href="/login?next=%2Fconcierge"' in template
