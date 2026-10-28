"""Regression tests for accounts, Canadian gallery acquisition, and PayPal checkout."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from bs4 import BeautifulSoup

import main
from services import config, database
from services.database import SESSION_COOKIE_NAME, get_order_for_user, list_orders, reset_engine_cache
from services.art_sources import (
    ArtMatchProvider,
    RookleysProvider,
    _parse_canadian_gallery_cards,
    commercial_providers,
)
from services.paypal_payments import paypal_catalog_entry

ROOT = Path(__file__).resolve().parent
PASSWORD = "Canvas-Blue-Tree-2030!"


def _account_client(monkeypatch, tmp_path, request, email="buyer@example.com"):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'artco-account-test.sqlite3'}")
    reset_engine_cache()
    request.addfinalizer(reset_engine_cache)
    client = TestClient(main.app)
    response = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert SESSION_COOKIE_NAME in client.cookies
    return client, response.json()["user"]


def test_register_login_profile_saved_artwork_purchase_history_and_logout(monkeypatch, tmp_path, request):
    client, user = _account_client(monkeypatch, tmp_path, request)

    me = client.get("/api/auth/me").json()
    assert me["authenticated"] is True
    assert me["user"]["id"] == user["id"]

    profile = client.put("/api/account/profile", json={
        "loves": ["Yayoi Kusama", "Geometric abstraction"],
        "avoids": ["Gory imagery"],
        "notes": "Prefer vivid colour and bold patterns.",
        "discoveryLevel": 72,
    })
    assert profile.status_code == 200
    assert profile.json()["profile"]["discoveryLevel"] == 72

    saved = client.post("/api/account/saved", json={
        "action": "save",
        "artwork": {
            "id": "artmatch:test-work", "title": "Blue Garden", "artist": "Jane Artist",
            "source": "ArtMatch", "source_kind": "commercial", "availability": "available",
            "market_code": "CA", "detail_url": "https://artmatch.ca/canadian-art-for-sale/blue-garden",
        },
    })
    assert saved.status_code == 200
    assert saved.json()["saved"][0]["market_code"] == "CA"

    redirect = client.get("/api/acquisition/redirect", params={
        "url": "https://artmatch.ca/canadian-art-for-sale/blue-garden",
        "artwork_id": "artmatch:test-work", "title": "Blue Garden", "artist": "Jane Artist",
        "seller": "ArtMatch", "amount": "500", "currency": "CAD", "market": "CA",
    }, follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "https://artmatch.ca/canadian-art-for-sale/blue-garden"

    state = client.get("/api/account/state")
    assert state.status_code == 200
    body = state.json()
    assert body["profile"]["loves"] == ["Yayoi Kusama", "Geometric abstraction"]
    assert body["saved"][0]["id"] == "artmatch:test-work"
    assert body["purchases"][0]["provider"] == "seller_redirect"
    assert body["purchases"][0]["paymentStatus"] == "external_pending"

    orders = client.get("/api/orders")
    assert orders.status_code == 200
    assert orders.json()["orders"][0]["provider"] == "seller_redirect"

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/auth/me").json()["authenticated"] is False
    assert client.get("/api/account/state").status_code == 401

    login = client.post("/api/auth/login", json={"email": "BUYER@example.com", "password": PASSWORD})
    assert login.status_code == 200
    assert client.get("/api/account/state").json()["saved"][0]["id"] == "artmatch:test-work"
    client.close()


def test_auth_routes_fail_closed_when_persistent_database_is_not_configured(monkeypatch):
    reset_engine_cache()
    monkeypatch.setattr(config, "DATABASE_URL", "")
    client = TestClient(main.app)
    response = client.post("/api/auth/register", json={"email": "buyer@example.com", "password": PASSWORD})
    assert response.status_code == 503
    assert "DATABASE_URL" not in response.text or "persistent database" in response.text


def test_paypal_catalog_requires_explicit_seller_authorization(monkeypatch):
    source_url = "https://artmatch.ca/canadian-art-for-sale/blue-garden"
    monkeypatch.setattr(config, "PAYPAL_CATALOG", {
        "unapproved": {"source_url": source_url, "amount": "500.00", "currency": "CAD"},
        "approved": {"source_url": source_url, "amount": "500.00", "currency": "CAD", "direct_checkout_approved": True},
    })
    entry = paypal_catalog_entry("work-not-found", source_url)
    assert entry is not None
    assert entry["direct_checkout_approved"] is True
    assert entry["currency"] == "CAD"
    # A different source URL with no approved row never becomes a direct charge.
    assert paypal_catalog_entry("work-not-found", "https://rookleys.com/artworks/unapproved") is None


def test_paypal_order_creates_account_owned_order_history(monkeypatch, tmp_path, request):
    client, user = _account_client(monkeypatch, tmp_path, request, email="paypal-buyer@example.com")
    source_url = "https://artmatch.ca/canadian-art-for-sale/blue-garden"
    monkeypatch.setattr(config, "PAYPAL_CATALOG", {
        "approved-artmatch-work": {
            "source_url": source_url, "amount": "500.00", "currency": "CAD",
            "title": "Blue Garden", "artist": "Jane Artist", "seller_name": "ArtMatch",
            "direct_checkout_approved": True,
        }
    })

    async def fake_create(artwork_id, return_url, cancel_url, source_url=None):
        assert artwork_id == "artmatch:blue-garden"
        assert source_url == "https://artmatch.ca/canadian-art-for-sale/blue-garden"
        return {"id": "PAYPAL-ORDER-TEST-1", "approval_url": "https://www.sandbox.paypal.com/checkoutnow?token=PAYPAL-ORDER-TEST-1"}

    monkeypatch.setattr(main, "create_paypal_order", fake_create)
    response = client.post("/api/paypal/orders", json={
        "artwork_id": "artmatch:blue-garden", "source_url": source_url,
        "title": "Blue Garden", "artist": "Jane Artist", "seller_name": "ArtMatch",
    })
    assert response.status_code == 200, response.text
    assert response.json()["checkout_url"].startswith("https://www.sandbox.paypal.com/")

    state = client.get("/api/account/state").json()
    purchase = state["purchases"][0]
    assert purchase["provider"] == "paypal"
    assert purchase["sessionId"] == "PAYPAL-ORDER-TEST-1"
    assert purchase["paymentStatus"] == "approval_pending"
    assert purchase["currency"] == "CAD"



def test_paypal_sandbox_test_flow_needs_no_catalog_variable(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "sandbox-client-id")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "sandbox-client-secret")
    monkeypatch.setattr(config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setattr(config, "PAYPAL_CATALOG", {})
    monkeypatch.setattr(config, "VINCENT_DEMO_SESSION_SECRET", "test-demo-secret-for-paypal")

    async def fake_create(return_url, cancel_url):
        assert return_url.endswith("/checkout/paypal/return")
        assert cancel_url.endswith("/checkout/paypal/cancel")
        return {"id": "SANDBOX-TEST-ORDER-1", "approval_url": "https://www.sandbox.paypal.com/checkoutnow?token=SANDBOX-TEST-ORDER-1"}

    async def fake_capture(order_id):
        assert order_id == "SANDBOX-TEST-ORDER-1"
        return {"id": order_id, "status": "COMPLETED", "purchase_units": [{
            "amount": {"currency_code": "CAD", "value": "1.00"},
            "payments": {"captures": [{"status": "COMPLETED", "amount": {"currency_code": "CAD", "value": "1.00"}}]},
        }]}

    monkeypatch.setattr(main, "create_paypal_sandbox_test_order", fake_create)
    monkeypatch.setattr(main, "capture_paypal_order", fake_capture)
    client = TestClient(main.app)
    signin = client.post("/api/auth/login", json={
        "email": config.VINCENT_DEMO_EMAIL, "password": config.VINCENT_DEMO_PASSWORD,
    })
    assert signin.status_code == 200, signin.text

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert 'id="paypalSandboxTestButton"' in dashboard.text

    started = client.post("/api/paypal/sandbox-test/orders")
    assert started.status_code == 200, started.text
    assert started.json()["title"] == "Vincent PayPal Sandbox Test"
    assert started.json()["currency"] == "cad"
    assert started.json()["amount_total"] == 100
    assert "SANDBOX-TEST-ORDER-1" in started.json()["checkout_url"]

    captured = client.post("/api/paypal/orders/SANDBOX-TEST-ORDER-1/capture")
    assert captured.status_code == 200, captured.text
    assert captured.json()["payment_status"] == "paid"
    assert captured.json()["currency"] == "cad"


def test_dashboard_hides_paypal_sandbox_test_button_without_credentials(monkeypatch):
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "")
    monkeypatch.setattr(config, "PAYPAL_MODE", "sandbox")
    client = TestClient(main.app)
    signin = client.post("/api/auth/login", json={
        "email": config.VINCENT_DEMO_EMAIL, "password": config.VINCENT_DEMO_PASSWORD,
    })
    assert signin.status_code == 200, signin.text
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert 'id="paypalSandboxTestButton"' not in response.text


def test_paypal_sandbox_test_route_rejects_live_mode(monkeypatch):
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "sandbox-client-id")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "sandbox-client-secret")
    monkeypatch.setattr(config, "PAYPAL_MODE", "live")
    client = TestClient(main.app)
    signin = client.post("/api/auth/login", json={
        "email": config.VINCENT_DEMO_EMAIL, "password": config.VINCENT_DEMO_PASSWORD,
    })
    assert signin.status_code == 200, signin.text
    response = client.post("/api/paypal/sandbox-test/orders")
    assert response.status_code == 409
    assert "only when PAYPAL_MODE=sandbox" in response.json()["detail"]

def test_paypal_invoice_requires_authorized_listing_and_records_invoice(monkeypatch, tmp_path, request):
    client, user = _account_client(monkeypatch, tmp_path, request, email="invoice-buyer@example.com")
    source_url = "https://artmatch.ca/canadian-art-for-sale/blue-garden"
    monkeypatch.setattr(config, "PAYPAL_CATALOG", {
        "approved-artmatch-work": {
            "source_url": source_url, "amount": "500.00", "currency": "CAD",
            "title": "Blue Garden", "artist": "Jane Artist", "seller_name": "ArtMatch",
            "direct_checkout_approved": True,
        }
    })
    monkeypatch.setattr(main, "invoice_enabled", lambda: True)

    async def fake_invoice(artwork_id, recipient_email, invoice_number=None, source_url=None):
        assert recipient_email == "invoice-buyer@example.com"
        return {"id": "INV-TEST-1", "invoice_url": "https://www.sandbox.paypal.com/invoice/p/#INV-TEST-1", "amount": "500.00", "currency": "CAD"}

    monkeypatch.setattr(main, "create_and_send_invoice", fake_invoice)
    response = client.post("/api/paypal/invoices", json={
        "artwork_id": "artmatch:blue-garden", "source_url": source_url,
        "title": "Blue Garden", "artist": "Jane Artist", "seller_name": "ArtMatch",
    })
    assert response.status_code == 200, response.text
    state = client.get("/api/account/state").json()
    purchase = state["purchases"][0]
    assert purchase["provider"] == "paypal_invoice"
    assert purchase["invoiceUrl"].endswith("INV-TEST-1")
    assert purchase["paymentStatus"] == "unpaid"


def test_buy_sources_include_canadian_galleries_before_optional_marketplaces():
    names = [provider.name for provider in commercial_providers()]
    assert names[:2] == ["ArtMatch", "Rookleys Canadian Art Gallery"]
    assert {"Artsy", "Artlogic", "Collect24"}.issubset(names)
    assert ArtMatchProvider.country_code == "CA"
    assert RookleysProvider.country_code == "CA"


@pytest.mark.parametrize("provider_cls", [ArtMatchProvider, RookleysProvider])
def test_canadian_gallery_parsers_require_price_and_available_status(provider_cls):
    provider = provider_cls()
    image_host = "https://artmatch-wp-media.s3.us-west-1.amazonaws.com/blue-garden.jpg" if provider_cls is ArtMatchProvider else "https://www.rookleys.com/images/blue-garden.jpg"
    html = f'''<article class="artwork-card">
      <a href="/artworks/blue-garden"><img src="{image_host}" alt="Jane Artist, Blue Garden"></a>
      <h2 class="artwork-title">Blue Garden</h2><div class="artist-name">Jane Artist</div>
      <div class="availability">Available</div><p>CAD $500</p></article>'''
    works = _parse_canadian_gallery_cards(BeautifulSoup(html, "html.parser"), provider.name, provider.listing_url, provider.country_code)
    assert len(works) == 1
    work = works[0]
    assert work.title == "Blue Garden"
    assert work.artist == "Jane Artist"
    assert work.availability == "available"
    assert work.market_code == "CA"
    assert work.purchase_mode == "external"
    assert work.price == 500

    sold_html = html.replace("Available", "Sold")
    sold = _parse_canadian_gallery_cards(BeautifulSoup(sold_html, "html.parser"), provider.name, provider.listing_url, provider.country_code)
    assert sold == []


def test_buy_option_and_account_purchase_history_are_exposed_in_ui():
    concierge = (ROOT / "templates/concierge.html").read_text()
    base = (ROOT / "templates/base.html").read_text()
    dashboard = (ROOT / "templates/dashboard.html").read_text()
    js = (ROOT / "static/js/app.js").read_text()
    assert '<option value="buy">Buy art</option>' in concierge
    assert 'id="authLink"' in base and 'id="logoutButton"' in base
    assert 'id="purchasesTab"' in dashboard and 'id="purchasesPanel"' in dashboard
    assert "await accountFetch('/api/account/state')" in js
    assert "data-provider=\"seller_redirect\"" in js or "provider === 'seller_redirect'" in js
    assert "paypal_invoice_available" in js


def test_guest_seller_checkout_redirects_through_login_to_preserve_order_history():
    client = TestClient(main.app)
    response = client.get("/api/acquisition/redirect", params={
        "url": "https://artmatch.ca/canadian-art-for-sale/blue-garden",
        "artwork_id": "artmatch:test-work", "title": "Blue Garden",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")


def test_paypal_invoice_status_is_verified_and_saved_to_account(monkeypatch, tmp_path, request):
    client, user = _account_client(monkeypatch, tmp_path, request, email="invoice-status@example.com")
    from services.database import save_order
    save_order(user["id"], provider="paypal_invoice", provider_order_id="INV2-TEST-1",
               artwork_id="artmatch:work", title="Blue Garden", artist="Jane Artist",
               seller_name="ArtMatch", source_url="https://artmatch.ca/canadian-art-for-sale/blue-garden",
               status="SENT", payment_status="UNPAID", amount=500, currency="CAD",
               metadata={"invoice_url": "https://www.sandbox.paypal.com/invoice/INV2-TEST-1"})

    async def fake_invoice_status(invoice_id):
        assert invoice_id == "INV2-TEST-1"
        return {"id": invoice_id, "status": "PAID", "links": [{"rel": "payer_view", "href": "https://www.sandbox.paypal.com/invoice/INV2-TEST-1"}]}

    monkeypatch.setattr(main, "get_invoice_status", fake_invoice_status)
    response = client.get("/api/paypal/invoices/INV2-TEST-1/status")
    assert response.status_code == 200
    assert response.json()["payment_status"] == "paid"
    purchase = client.get("/api/account/state").json()["purchases"][0]
    assert purchase["paymentStatus"] == "paid"
    assert purchase["checkoutStatus"] == "paid"


def test_paypal_invoice_creation_uses_the_documented_payment_term_and_sends_invoice(monkeypatch):
    import asyncio
    import httpx
    import services.paypal_payments as paypal

    source_url = "https://artmatch.ca/canadian-art-for-sale/blue-garden"
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "client-id")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setattr(config, "PAYPAL_INVOICE_EMAIL", "invoices@example.com")
    monkeypatch.setattr(config, "PAYPAL_CATALOG", {
        "approved-work": {"source_url": source_url, "amount": "500.00", "currency": "CAD", "title": "Blue Garden", "direct_checkout_approved": True}
    })
    calls = []
    class FakeResponse:
        def __init__(self, status_code, payload=None, content=b"json"):
            self.status_code = status_code
            self._payload = payload or {}
            self.content = content
            self.headers = {}
        def json(self): return self._payload
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            if url.endswith("/v1/oauth2/token"):
                return FakeResponse(200, {"access_token": "token"})
            if url.endswith("/v2/invoicing/invoices"):
                return FakeResponse(201, {"id": "INV2-TEST-2", "links": [{"rel": "self", "href": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-TEST-2"}]})
            if url.endswith("/INV2-TEST-2/send"):
                return FakeResponse(200, {"id": "INV2-TEST-2", "links": []})
            raise AssertionError(url)
        async def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return FakeResponse(200, {"id": "INV2-TEST-2", "status": "SENT", "links": [{"rel": "payer_view", "href": "https://www.sandbox.paypal.com/invoice/INV2-TEST-2"}]})
    monkeypatch.setattr(paypal.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(paypal.create_and_send_invoice("work-not-found", "buyer@example.com", source_url=source_url))
    assert result["id"] == "INV2-TEST-2"
    assert result["invoice_url"] == "https://www.sandbox.paypal.com/invoice/INV2-TEST-2"
    create_call = next(call for call in calls if call[0] == "POST" and call[1].endswith("/v2/invoicing/invoices"))
    body = create_call[2]["json"]
    assert body["detail"]["payment_term"]["term_type"] == "NET_10"
    assert all(call[1].endswith("/INV2-TEST-2/send") for call in calls if call[0] == "POST" and "/send" in call[1])


def test_database_backed_taste_context_enriches_personalized_explore(monkeypatch, tmp_path, request):
    client, user = _account_client(monkeypatch, tmp_path, request, email="taste-context@example.com")
    from services.database import save_profile, save_artwork, save_recent_search
    save_profile(user["id"], {
        "loves": ["Yayoi Kusama", "Geometric abstraction"],
        "avoids": ["Gory imagery"],
        "notes": "Prefer vivid colours and bold patterns.",
        "discoveryLevel": 74,
    })
    save_artwork(user["id"], {
        "id": "artmatch:saved-blue", "title": "Blue Garden", "artist": "Jane Artist",
        "medium": "Acrylic", "description": "Bold blue geometric forms", "year": "2024",
        "source": "ArtMatch", "source_kind": "commercial", "availability": "available",
    })
    save_recent_search(user["id"], "find", "Find blue abstract art", {"summary": "Blue abstract art"})
    seen = {}

    async def fake_run(payload):
        seen["loves"] = list(payload.loves)
        seen["avoids"] = list(payload.avoid_references)
        seen["notes"] = payload.profile_notes
        seen["ideas"] = list(payload.existing_ideas)
        seen["artworks"] = [item.model_dump() for item in payload.existing_artworks]
        seen["discovery_level"] = payload.discovery_level
        return {"success": True, "intent": "discover", "results": [], "not_for_sale": []}

    monkeypatch.setattr(main, "run", fake_run)
    response = client.post("/api/concierge", json={
        "intent": "discover", "goal": "Find something new with vivid colour",
        "personalize_with_saved_taste": True, "number_of_works": 3,
    })
    assert response.status_code == 200, response.text
    assert "Yayoi Kusama" in seen["loves"]
    assert "Gory imagery" in seen["avoids"]
    assert "Prefer vivid colours and bold patterns." in seen["notes"]
    assert "Find blue abstract art" in seen["ideas"]
    assert seen["artworks"][0]["title"] == "Blue Garden"
    assert seen["discovery_level"] == 74


def test_region_scope_defaults_to_canada_and_only_accepts_supported_choices():
    from models.schemas import ConciergeRequest
    assert ConciergeRequest(intent="buy", goal="Find original art").region_scope == "CA"
    assert ConciergeRequest(intent="discover", goal="Find abstract art", region_scope="US").region_scope == "US"
    assert ConciergeRequest(intent="discover", goal="Find abstract art", region_scope="BOTH").region_scope == "BOTH"
    with pytest.raises(Exception):
        ConciergeRequest(intent="discover", goal="Find art", region_scope="UK")


def test_region_filter_routes_only_to_configured_country_sources():
    from services.agent import _provider_in_region
    from services.art_sources import ArtMatchProvider, RookleysProvider, ArtInstituteProvider, MetProvider, ClevelandProvider, ArtsyProvider, ArtlogicProvider, Collect24Provider
    assert _provider_in_region(ArtMatchProvider(), "CA")
    assert _provider_in_region(RookleysProvider(), "CA")
    assert not _provider_in_region(ArtMatchProvider(), "US")
    assert not _provider_in_region(ArtInstituteProvider(), "CA")
    assert _provider_in_region(ArtInstituteProvider(), "US")
    assert _provider_in_region(MetProvider(), "BOTH")
    assert _provider_in_region(Collect24Provider(), "CA")
    assert _provider_in_region(Collect24Provider(), "US")


def test_concierge_region_selector_defaults_to_canada_and_hides_in_learn():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    from test_helpers import sign_in_demo
    sign_in_demo(client)
    response = client.get("/concierge")
    assert response.status_code == 200
    assert '<option value="CA" selected>Canada</option>' in response.text
    region_control = response.text[response.text.index('id="marketScope"'):response.text.index('</select>', response.text.index('id="marketScope"'))]
    assert 'Canada (default)' not in region_control
    assert '<option value="US">United States</option>' in response.text
    assert '<option value="BOTH">Both Canada and the US</option>' in response.text
    js = (Path(__file__).resolve().parent / "static/js/app.js").read_text()
    assert "region_scope: ['CA', 'US', 'BOTH'].includes(state.preferences.regionScope) ? state.preferences.regionScope : 'CA'" in js
    assert "if (marketScopeControl) marketScopeControl.hidden = state.intent === 'learn';" in js
    css = (Path(__file__).resolve().parent / "static/css/style.css").read_text()
    assert "grid-template-columns: minmax(150px, max-content) minmax(0, 270px);" in css
    assert ".concierge-service-control .market-scope-control label" in css


def test_general_art_cards_do_not_name_the_payment_processor():
    js = (Path(__file__).resolve().parent / "static/js/app.js").read_text()
    cards = js[js.index('const paypalButton ='):js.index('const link = `${checkoutButton}')]
    assert 'Pay with PayPal' not in cards
    assert 'Request PayPal invoice' not in cards
    assert '>Buy securely</button>' in cards
    assert '>Request invoice</button>' in cards
    assert "Starting PayPal checkout" not in js
    assert "PayPal checkout could not be started" not in js


def test_payments_section_is_the_only_general_ui_section_that_announces_sandbox_mode():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    from test_helpers import sign_in_demo
    sign_in_demo(client)
    dashboard = client.get('/dashboard').text
    assert 'Sandbox mode is selected; transactions are test-only.' in dashboard
    assert 'PayPal and Stripe payments, invoices' not in dashboard
    concierge = client.get('/concierge').text
    assert 'PayPal' not in concierge


def test_demo_signin_works_without_database_and_shows_authenticated_header(monkeypatch):
    reset_engine_cache()
    monkeypatch.setattr(config, "DATABASE_URL", "")
    client = TestClient(main.app)

    landing = client.get("/")
    landing_soup = BeautifulSoup(landing.text, "html.parser")
    cta = landing_soup.select_one("#homeConciergeCta")
    assert cta is not None
    assert cta["href"] == "/login?next=%2Fconcierge"
    auth_link = landing_soup.select_one("#authLink")
    assert auth_link is not None
    assert auth_link["href"] == "/login?next=%2Fconcierge"
    assert landing_soup.select_one('a[href="/concierge?intent=buy&new=1"]') is not None
    assert "buy art from connected galleries" in landing.text.lower()

    login_page = client.get("/login?next=%2Fconcierge")
    login_soup = BeautifulSoup(login_page.text, "html.parser")
    assert login_soup.select_one("#authForm") is not None
    assert "Account access is not available yet" not in login_page.text
    assert "New to Vincent?" not in login_page.text
    assert "Sign in to continue to your art concierge." not in login_page.text
    assert "Demo access is prefilled." not in login_page.text
    assert login_soup.select_one("#authLink") is None
    assert login_soup.select_one("#logoutButton") is None
    assert "topbar--auth" in login_soup.select_one("header.topbar")["class"]
    assert login_soup.select_one("#themeToggle") is not None
    assert login_soup.select_one("#fontScale") is not None
    assert login_soup.select_one("nav.primary-nav") is None
    assert login_soup.select_one("#authEmail")["value"] == config.VINCENT_DEMO_EMAIL
    assert login_soup.select_one("#authPassword")["value"] == config.VINCENT_DEMO_PASSWORD
    assert 'window.ART_AUTH_NEXT = "/concierge"' in login_page.text
    root_target_login = client.get("/login?next=%2F")
    assert 'window.ART_AUTH_NEXT = "/concierge"' in root_target_login.text
    assert config.VINCENT_DEMO_NAME == "James"

    signin = client.post("/api/auth/login", json={
        "email": config.VINCENT_DEMO_EMAIL,
        "password": config.VINCENT_DEMO_PASSWORD,
    })
    assert signin.status_code == 200
    assert signin.json()["demo_mode"] is True
    session = client.get("/api/auth/me").json()
    assert session["authenticated"] is True
    assert session["user"]["name"] == config.VINCENT_DEMO_NAME
    account_state = client.get("/api/account/state")
    assert account_state.status_code == 200
    assert account_state.json()["demo_mode"] is True

    home_signed_in = client.get("/")
    home_soup = BeautifulSoup(home_signed_in.text, "html.parser")
    assert "Welcome, James" in home_signed_in.text
    assert home_soup.select_one("#logoutButton:not([hidden])") is not None
    assert home_soup.select_one("#homeConciergeCta")["href"] == "/concierge"

    redirected = client.get("/login?next=%2Fconcierge", follow_redirects=False)
    assert redirected.status_code == 303
    assert redirected.headers["location"] == "/concierge"
    redirected_from_landing = client.get("/login?next=%2F", follow_redirects=False)
    assert redirected_from_landing.status_code == 303
    assert redirected_from_landing.headers["location"] == "/concierge"

    signed_out = client.get("/logout", follow_redirects=False)
    assert signed_out.status_code == 303
    assert signed_out.headers["location"] == "/"
    assert client.get("/api/auth/me").json()["authenticated"] is False
    client.close()


def test_demo_account_mutations_do_not_require_database(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    client = TestClient(main.app)
    signin = client.post("/api/auth/login", json={
        "email": config.VINCENT_DEMO_EMAIL,
        "password": config.VINCENT_DEMO_PASSWORD,
    })
    assert signin.status_code == 200
    profile = client.put("/api/account/profile", json={"loves": ["colour"]})
    assert profile.status_code == 200
    saved = client.post("/api/account/saved", json={"action": "save", "artwork": {"id": "demo-1"}})
    assert saved.status_code == 200
    orders = client.get("/api/orders")
    assert orders.status_code == 200
    assert orders.json()["orders"] == []
    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/auth/me").json()["authenticated"] is False
    client.close()


def test_paypal_orders_use_canadian_sandbox_payment_source_context(monkeypatch):
    import asyncio
    import services.paypal_payments as paypal

    source_url = "https://artmatch.ca/canadian-art-for-sale/blue-garden"
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "sandbox-client-id")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.setattr(config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setattr(config, "PAYPAL_API_BASE", "https://api-m.sandbox.paypal.com")
    monkeypatch.setattr(config, "PAYPAL_REGION", "CA")
    monkeypatch.setattr(config, "PAYPAL_LOCALE", "en-CA")
    monkeypatch.setattr(config, "PAYPAL_CATALOG", {
        "approved-work": {
            "source_url": source_url, "amount": "500.00", "currency": "CAD",
            "title": "Blue Garden", "direct_checkout_approved": True,
        }
    })
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.content = b"json"
        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/v1/oauth2/token"):
                return FakeResponse(200, {"access_token": "sandbox-access-token"})
            if url.endswith("/v2/checkout/orders"):
                return FakeResponse(201, {
                    "id": "SANDBOX-ORDER-CA-1",
                    "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=SANDBOX-ORDER-CA-1"}],
                })
            raise AssertionError(f"Unexpected PayPal endpoint: {url}")

    monkeypatch.setattr(paypal.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(paypal.create_order(
        "artmatch:blue-garden", "https://vincent.example/checkout/paypal/return",
        "https://vincent.example/checkout/paypal/cancel", source_url=source_url,
    ))

    assert result["id"] == "SANDBOX-ORDER-CA-1"
    assert result["approval_url"].startswith("https://www.sandbox.paypal.com/")
    assert calls[0][0] == "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    assert calls[1][0] == "https://api-m.sandbox.paypal.com/v2/checkout/orders"
    body = calls[1][1]["json"]
    context = body["payment_source"]["paypal"]["experience_context"]
    assert body["purchase_units"][0]["amount"]["currency_code"] == "CAD"
    assert context["locale"] == "en-CA"
    assert context["return_url"] == "https://vincent.example/checkout/paypal/return"
    assert context["cancel_url"] == "https://vincent.example/checkout/paypal/cancel"
    assert "application_context" not in body

    monkeypatch.setattr(config, "PAYPAL_CATALOG", {})
    test_order = asyncio.run(paypal.create_sandbox_test_order(
        "https://vincent.example/checkout/paypal/return", "https://vincent.example/checkout/paypal/cancel",
    ))
    assert test_order["id"] == "SANDBOX-ORDER-CA-1"
    test_body = calls[-1][1]["json"]
    test_unit = test_body["purchase_units"][0]
    assert test_unit["amount"] == {"currency_code": "CAD", "value": "1.00", "breakdown": {"item_total": {"currency_code": "CAD", "value": "1.00"}}}
    assert test_unit["items"][0]["name"] == "Vincent PayPal Sandbox Test"
    test_context = test_body["payment_source"]["paypal"]["experience_context"]
    assert test_context["shipping_preference"] == "NO_SHIPPING"
    assert test_unit["items"][0]["category"] == "DIGITAL_GOODS"


def test_demo_paypal_checkout_and_capture_work_without_database(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "sandbox-client-id")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "sandbox-secret")
    source_url = "https://artmatch.ca/canadian-art-for-sale/blue-garden"
    monkeypatch.setattr(config, "PAYPAL_CATALOG", {
        "approved-artmatch-work": {
            "source_url": source_url, "amount": "500.00", "currency": "CAD",
            "title": "Blue Garden", "artist": "Jane Artist", "seller_name": "ArtMatch",
            "direct_checkout_approved": True,
        }
    })

    async def fake_create(artwork_id, return_url, cancel_url, source_url=None):
        assert return_url == "https://vincent-test.vercel.app/checkout/paypal/return"
        assert cancel_url == "https://vincent-test.vercel.app/checkout/paypal/cancel"
        return {
            "id": "SANDBOX-DEMO-ORDER-1",
            "approval_url": "https://www.sandbox.paypal.com/checkoutnow?token=SANDBOX-DEMO-ORDER-1",
        }

    async def fake_capture(order_id):
        assert order_id == "SANDBOX-DEMO-ORDER-1"
        return {
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [{
                "amount": {"currency_code": "CAD", "value": "500.00"},
                "payments": {"captures": [{
                    "status": "COMPLETED",
                    "amount": {"currency_code": "CAD", "value": "500.00"},
                }]},
            }],
        }

    monkeypatch.setattr(main, "create_paypal_order", fake_create)
    monkeypatch.setattr(main, "capture_paypal_order", fake_capture)
    client = TestClient(main.app, base_url="https://vincent-test.vercel.app")
    login = client.post("/api/auth/login", json={
        "email": config.VINCENT_DEMO_EMAIL, "password": config.VINCENT_DEMO_PASSWORD,
    })
    assert login.status_code == 200
    assert login.json()["demo_mode"] is True

    create = client.post("/api/paypal/orders", headers={
        "x-forwarded-host": "vincent-test.vercel.app", "x-forwarded-proto": "https",
    }, json={
        "artwork_id": "artmatch:blue-garden", "source_url": source_url,
        "title": "Blue Garden", "artist": "Jane Artist", "seller_name": "ArtMatch",
    })
    assert create.status_code == 200, create.text
    assert create.json()["checkout_url"].startswith("https://www.sandbox.paypal.com/")
    assert any(name.startswith("vincent_demo_pp_SANDBOX-DEMO-ORDER-1") for name in client.cookies.keys())

    capture = client.post("/api/paypal/orders/SANDBOX-DEMO-ORDER-1/capture")
    assert capture.status_code == 200, capture.text
    assert capture.json()["payment_status"] == "paid"
    assert capture.json()["title"] == "Blue Garden"

    state = client.get("/api/account/state")
    assert state.status_code == 200
    purchases = state.json()["purchases"]
    assert len(purchases) == 1
    assert purchases[0]["sessionId"] == "SANDBOX-DEMO-ORDER-1"
    assert purchases[0]["paymentStatus"] == "paid"
    assert purchases[0]["currency"] == "CAD"
    client.close()


def test_paypal_diagnostics_report_safe_sandbox_settings(monkeypatch):
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "safe-to-report-as-configured")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "never-return-this-secret")
    monkeypatch.setattr(config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setattr(config, "PAYPAL_REGION", "CA")
    monkeypatch.setattr(config, "PAYPAL_LOCALE", "en-CA")
    monkeypatch.setattr(config, "PAYPAL_CATALOG", {})
    response = TestClient(main.app).get("/api/diagnostics")
    assert response.status_code == 200
    body = response.json()["payments"]
    assert body["paypal_configured"] is True
    assert body["paypal_mode"] == "sandbox"
    assert body["paypal_region"] == "CA"
    assert body["paypal_locale"] == "en-CA"
    assert body["paypal_sandbox_test_checkout_available"] is True
    assert "never-return-this-secret" not in response.text
