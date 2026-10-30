from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

import main
from services import database
from services.database import SESSION_COOKIE_NAME, create_session, register_user
from services.art_sources import Artwork

ROOT = Path(__file__).resolve().parent


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(main.config, "DATABASE_URL", f"sqlite:///{tmp_path / 'vincent-features.sqlite3'}")
    database.reset_engine_cache()
    user = register_user("features@example.com", "Correct-Horse-Battery-8!")
    client = TestClient(main.app)
    client.cookies.set(SESSION_COOKIE_NAME, create_session(user["id"]))
    return client, user


def _work(work_id="work-1", title="Blue Garden", artist="Jane Artist", price=125.0):
    return Artwork(
        id=work_id, title=title, artist=artist,
        image_url=f"https://images.example.org/{work_id}.jpg",
        detail_url=f"https://www.ugallery.com/art/{work_id}",
        source="UGallery", source_kind="commercial", availability="available",
        price=price, currency="USD", medium="Oil on canvas", dimensions="50 x 70 cm",
        year="2024", description="A blue abstract landscape", seller_name="UGallery", market_code="US",
    )


def test_registered_user_can_create_and_manage_collections(monkeypatch, tmp_path):
    client, user = _client(monkeypatch, tmp_path)
    collection_id = "collection-test-0000000000000001"
    created = client.post("/api/account/collections", json={"id": collection_id, "name": "Living room", "description": "Large-scale works"})
    assert created.status_code == 200, created.text
    assert created.json()["collections"][0]["name"] == "Living room"

    work = {"id": "work-1", "title": "Blue Garden", "artist": "Jane Artist", "price": 125, "currency": "USD"}
    added = client.post(f"/api/account/collections/{collection_id}/items", json={"artwork": work})
    assert added.status_code == 200, added.text
    collection = added.json()["collections"][0]
    assert collection["artworks"][0]["id"] == "work-1"

    state = client.get("/api/account/state").json()
    assert state["collections"][0]["name"] == "Living room"
    assert state["collections"][0]["artworks"][0]["title"] == "Blue Garden"

    removed = client.delete(f"/api/account/collections/{collection_id}/items/work-1")
    assert removed.status_code == 200
    assert removed.json()["collections"][0]["artworks"] == []

    deleted = client.delete(f"/api/account/collections/{collection_id}")
    assert deleted.status_code == 200
    assert deleted.json()["collections"] == []


def test_registered_user_can_create_artwork_alert_and_detect_price_change(monkeypatch, tmp_path):
    client, user = _client(monkeypatch, tmp_path)
    initial = _work(price=125.0)

    class Provider:
        name = "UGallery"
        async def search(self, query, limit=3):
            assert "Blue Garden" in query
            return [_work(price=99.0)]

    monkeypatch.setattr(main, "commercial_providers", lambda: [Provider()])
    created = client.post("/api/account/alerts", json={
        "id": "alert-price-change-0000000000000001", "type": "artwork",
        "target": {"artwork": main._alert_artwork_payload(initial), "source": "UGallery", "seller_name": "UGallery", "market_code": "US"},
        "baseline": {"price": 125.0, "currency": "USD"},
    })
    assert created.status_code == 200, created.text

    checked = client.post("/api/account/alerts/check", json={})
    assert checked.status_code == 200, checked.text
    assert checked.json()["alerts"][0]["newEvent"]["type"] == "price_change"
    all_alerts = checked.json()["all_alerts"]
    assert all_alerts[0]["events"][0]["message"] == "Blue Garden changed from USD 125.00 to USD 99.00."


def test_seller_alert_uses_baseline_and_detects_new_listing(monkeypatch, tmp_path):
    client, user = _client(monkeypatch, tmp_path)
    class Provider:
        name = "UGallery"
        async def search(self, query, limit=3):
            return [_work("existing", "Existing Work"), _work("new-work", "New Work")]

    monkeypatch.setattr(main, "commercial_providers", lambda: [Provider()])
    created = client.post("/api/account/alerts", json={
        "id": "alert-seller-follow-000000000000001", "type": "seller",
        "target": {"seller_name": "UGallery", "source": "UGallery", "market_code": "US"},
        "baseline": {"known_ids": ["existing"]},
    })
    assert created.status_code == 200, created.text
    checked = client.post("/api/account/alerts/check", json={})
    assert checked.status_code == 200, checked.text
    event = checked.json()["alerts"][0]["newEvent"]
    assert event["type"] == "new_listings"
    assert "1 new listing" in event["message"]
    assert event["data"]["artworks"][0]["title"] == "New Work"


def test_demo_user_can_use_collections_and_alerts_without_database(monkeypatch):
    monkeypatch.setattr(main.config, "DATABASE_URL", "")
    monkeypatch.setattr(main.config, "VINCENT_DEMO_SESSION_SECRET", "demo-feature-secret")
    client = TestClient(main.app)
    login = client.post("/api/auth/login", json={"email": main.config.VINCENT_DEMO_EMAIL, "password": main.config.VINCENT_DEMO_PASSWORD})
    assert login.status_code == 200

    collection = client.post("/api/account/collections", json={"id": "demo-collection-1", "name": "Demo collection"})
    assert collection.status_code == 200 and collection.json()["demo_mode"] is True
    alert = client.post("/api/account/alerts", json={"id": "demo-alert-1", "type": "seller", "target": {"seller_name": "UGallery", "source": "UGallery"}})
    assert alert.status_code == 200 and alert.json()["demo_mode"] is True


def test_dashboard_exposes_collections_alerts_tabs_and_comparison_controls():
    dashboard = (ROOT / "templates/dashboard.html").read_text()
    base = (ROOT / "templates/base.html").read_text()
    js = (ROOT / "static/js/app.js").read_text()
    assert 'id="collectionsTab"' in dashboard and 'id="collectionsPanel"' in dashboard
    assert 'id="alertsTab"' in dashboard and 'id="alertsPanel"' in dashboard
    assert 'id="comparisonModal"' in base and 'id="compareDock"' in base
    assert 'data-compare-id="${id}"' in js
    assert 'data-add-to-collection="${id}"' in js
    assert 'data-watch-artwork="${id}"' in js
    assert 'data-follow-seller=' in js
    assert 'function renderComparison()' in js
    assert 'function renderCollectionsPanel()' in js
    assert 'function renderAlertsPanel()' in js


def test_artwork_alert_check_does_not_treat_missing_listing_as_sold(monkeypatch):
    initial = _work(price=125.0)
    class Provider:
        name = "UGallery"
        async def search(self, query, limit=3):
            return []
    monkeypatch.setattr(main, "commercial_providers", lambda: [Provider()])
    import asyncio
    alert = {"id": "alert-missing", "type": "artwork", "active": True,
             "target": {"artwork": main._alert_artwork_payload(initial), "seller_name": "UGallery", "source": "UGallery"},
             "baseline": {"price": 125.0, "currency": "USD"}}
    result = asyncio.run(main._check_one_artwork_alert(alert))
    assert result.get("newEvent") is None
    assert not result.get("checkError")


def test_first_seller_alert_check_establishes_baseline_without_false_new_listing(monkeypatch):
    class Provider:
        name = "UGallery"
        async def search(self, query, limit=3):
            return [_work("existing-a", "Existing A"), _work("existing-b", "Existing B")]
    monkeypatch.setattr(main, "commercial_providers", lambda: [Provider()])
    import asyncio
    alert = {"id": "alert-baseline", "type": "seller", "active": True,
             "target": {"seller_name": "UGallery", "source": "UGallery"}, "baseline": {"known_ids": []}}
    result = asyncio.run(main._check_one_artwork_alert(alert))
    assert result.get("newEvent") is None
    assert set(result["baseline"]["known_ids"]) == {"existing-a", "existing-b"}
    assert result["baseline"]["baseline_initialized"] is True


def test_dashboard_page_renders_new_feature_panels_for_signed_in_account(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    response = client.get("/dashboard")
    assert response.status_code == 200
    for marker in ('id="collectionsTab"', 'id="alertsTab"', 'id="compareTab"', 'id="comparePanel"', 'id="checkAlertsButton"', 'id="comparisonModal"'):
        assert marker in response.text
    assert "20261030-custom-404" in response.text
    assert 'id="collectionCreateForm"' not in response.text
    assert 'id="dashboardComparisonContent"' in response.text


def test_collection_and_alert_limits_and_ids_are_bounded(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    too_short = client.post("/api/account/collections", json={"id": "short", "name": "A"})
    assert too_short.status_code == 422
    bad_alert = client.post("/api/account/alerts", json={"id": "not-valid", "type": "other", "target": {"seller_name": "UGallery"}})
    assert bad_alert.status_code == 422


def test_curated_concierge_results_are_the_dashboard_collection_creation_path():
    results = (ROOT / "templates/results.html").read_text()
    dashboard = (ROOT / "templates/dashboard.html").read_text()
    js = (ROOT / "static/js/app.js").read_text()
    assert 'id="curatedCollectionSection"' in results
    assert 'id="saveCuratedCollectionForm"' in results
    assert "async function saveCuratedResultsAsCollection()" in js
    assert "Curate a collection" in dashboard
    assert 'id="collectionCreateForm"' not in dashboard


def test_dashboard_has_a_visible_artwork_comparison_section():
    dashboard = (ROOT / "templates/dashboard.html").read_text()
    js = (ROOT / "static/js/app.js").read_text()
    assert 'id="compareTab"' in dashboard
    assert 'id="comparePanel"' in dashboard
    assert 'id="dashboardComparisonContent"' in dashboard
    assert "function renderComparisonPanel()" in js
    assert "comparisonTableMarkup(works)" in js


def test_dashboard_purchases_only_shows_the_one_dollar_sandbox_action_without_explanatory_notes():
    dashboard = (ROOT / "templates/dashboard.html").read_text()
    assert 'PayPal Sandbox test · $1.00 CAD' in dashboard
    assert 'Use this one-dollar test transaction' not in dashboard
    assert 'Sandbox mode is selected; transactions are test-only.' not in dashboard


def test_explore_corrects_van_gough_to_van_gogh_catalogue_query():
    from models.schemas import ConciergeRequest, Intent
    from services.agent import _searchable_goal, _fast_search_directions

    request = ConciergeRequest(intent=Intent.discover, goal="Show me art related to van gough.")
    assert _searchable_goal(request) == "Vincent van Gogh"
    assert _fast_search_directions(request)[0] == "Vincent van Gogh"


def test_explore_default_canada_still_searches_north_american_museum_collections(monkeypatch):
    import asyncio
    import services.agent as agent
    from models.schemas import ConciergeRequest, Intent
    from services.art_sources import Artwork

    class Provider:
        def __init__(self, name, country_code, source_kind):
            self.name = name
            self.country_code = country_code
            self.source_kind = source_kind
            self.queries = []

        async def search(self, query, limit=3):
            self.queries.append(query)
            return [Artwork(
                id=f"{self.name}:van-gogh", title="Garden with Cypresses", artist="Vincent van Gogh",
                image_url="https://images.example.org/van-gogh.jpg", detail_url="https://museum.example.org/van-gogh",
                source=self.name, source_kind=self.source_kind, availability="not_for_sale", price=None,
                currency=None, medium="Oil on canvas", dimensions="73 x 92 cm", year="1889",
                description="Museum collection record", market_code=self.country_code,
            )]

    museum = Provider("North American Museum", "US", "institution")
    canadian_shop = Provider("Canadian Shop", "CA", "commercial")
    monkeypatch.setattr(agent, "institutional_providers", lambda: [museum])
    monkeypatch.setattr(agent, "commercial_providers", lambda: [canadian_shop])
    request = ConciergeRequest(intent=Intent.discover, region_scope="CA", goal="Show me art related to van gough.")
    works, diagnostics = asyncio.run(agent._research(["Vincent van Gogh"], request, []))
    assert museum.queries == ["Vincent van Gogh"]
    assert canadian_shop.queries == ["Vincent van Gogh"]
    assert any(work.artist == "Vincent van Gogh" and work.market_code == "US" for work in works)
    assert {item["provider"] for item in diagnostics} == {"North American Museum", "Canadian Shop"}
