from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

QLOO_BASE_URL = "https://hackathon.api.qloo.com"
QLOO_API_KEY = os.getenv("QLOO_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
VINCENT_BASE_URL = os.getenv("VINCENT_BASE_URL", os.getenv("ARTCO_BASE_URL", "http://localhost:8000"))
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_APPLICATION_FEE_BPS = int(os.getenv("STRIPE_APPLICATION_FEE_BPS", "0"))
try:
    STRIPE_CHECKOUT_CATALOG = __import__("json").loads(os.getenv("VINCENT_STRIPE_CHECKOUT_CATALOG", os.getenv("ARTCO_STRIPE_CHECKOUT_CATALOG", "{}")))
except Exception:
    STRIPE_CHECKOUT_CATALOG = {}
ARTWORK_RESULT_LIMIT = min(3, max(1, int(os.getenv("ARTWORK_RESULT_LIMIT", "3"))))
REQUEST_TIMEOUT = min(4.5, max(2.0, float(os.getenv("REQUEST_TIMEOUT", "4"))))
QLOO_SEARCH_LIMIT = min(4, max(2, int(os.getenv("QLOO_SEARCH_LIMIT", "3"))))
QLOO_ARTIST_TAKE = min(8, max(4, int(os.getenv("QLOO_ARTIST_TAKE", "8"))))
DATABASE_URL = os.getenv("DATABASE_URL", "")
VINCENT_DEMO_EMAIL = os.getenv("VINCENT_DEMO_EMAIL", "demo@vincent.art").strip().casefold()
VINCENT_DEMO_PASSWORD = os.getenv("VINCENT_DEMO_PASSWORD", "vincent-demo-2026")
_VINCENT_DEMO_NAME = os.getenv("VINCENT_DEMO_NAME", "James").strip()
# Treat the old placeholder label as unset so it never appears as the demo user display name.
VINCENT_DEMO_NAME = "James" if not _VINCENT_DEMO_NAME or _VINCENT_DEMO_NAME.casefold() == "demo" else _VINCENT_DEMO_NAME
VINCENT_DEMO_SESSION_SECRET = os.getenv("VINCENT_DEMO_SESSION_SECRET", "")
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox").strip().casefold()
if PAYPAL_MODE not in {"sandbox", "live"}:
    PAYPAL_MODE = "sandbox"
PAYPAL_API_BASE = "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
PAYPAL_REGION = os.getenv("PAYPAL_REGION", "CA").strip().upper() or "CA"
# PayPal checkout uses a BCP-47 locale to localize its hosted approval experience.
PAYPAL_LOCALE = os.getenv("PAYPAL_LOCALE", f"en-{PAYPAL_REGION}").strip() or f"en-{PAYPAL_REGION}"
PAYPAL_INVOICE_BUSINESS_NAME = os.getenv("PAYPAL_INVOICE_BUSINESS_NAME", "Vincent")
PAYPAL_INVOICE_GIVEN_NAME = os.getenv("PAYPAL_INVOICE_GIVEN_NAME", "Vincent")
PAYPAL_INVOICE_SURNAME = os.getenv("PAYPAL_INVOICE_SURNAME", "Vincent")
PAYPAL_INVOICE_EMAIL = os.getenv("PAYPAL_INVOICE_EMAIL", "")
# Real-art PayPal direct checkout is disabled by default. Seller-authorized entries must be added deliberately in server code.
PAYPAL_CATALOG: dict[str, dict[str, Any]] = {}

ARTMATCH_LISTING_URL = os.getenv("ARTMATCH_LISTING_URL", "https://artmatch.ca/canadian-art-for-sale/")
ROOKLEYS_LISTING_URL = os.getenv("ROOKLEYS_LISTING_URL", "https://rookleys.com/artworks/")
