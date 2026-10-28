from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _read_config(overrides: dict[str, str]) -> dict:
    env = os.environ.copy()
    for key in (
        "VINCENT_BASE_URL", "ARTCO_BASE_URL",
        "VINCENT_PAYPAL_CATALOG", "ARTCO_PAYPAL_CATALOG",
        "VINCENT_STRIPE_CHECKOUT_CATALOG", "ARTCO_STRIPE_CHECKOUT_CATALOG",
    ):
        env.pop(key, None)
    env.update(overrides)
    script = (
        "import json; from services import config; "
        "print(json.dumps({'base_url': config.VINCENT_BASE_URL, "
        "'paypal': config.PAYPAL_CATALOG, 'stripe': config.STRIPE_CHECKOUT_CATALOG}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_vincent_environment_names_are_primary() -> None:
    cfg = _read_config({
        "VINCENT_BASE_URL": "https://vincent.example",
        "ARTCO_BASE_URL": "https://legacy.example",
        "VINCENT_PAYPAL_CATALOG": '{"vincent-work": {"amount": "10.00"}}',
        "ARTCO_PAYPAL_CATALOG": '{"legacy-work": {"amount": "20.00"}}',
        "VINCENT_STRIPE_CHECKOUT_CATALOG": '{"vincent-stripe": {"price_id": "price_v"}}',
        "ARTCO_STRIPE_CHECKOUT_CATALOG": '{"legacy-stripe": {"price_id": "price_l"}}',
    })
    assert cfg["base_url"] == "https://vincent.example"
    assert cfg["paypal"] == {}  # a PayPal catalog is not a required deployment variable
    assert "vincent-stripe" in cfg["stripe"]
    assert "legacy-stripe" not in cfg["stripe"]


def test_legacy_base_url_and_stripe_values_remain_compatibility_fallbacks() -> None:
    cfg = _read_config({
        "ARTCO_BASE_URL": "https://previous-deployment.example",
        "ARTCO_PAYPAL_CATALOG": '{"existing-work": {"amount": "10.00"}}',
        "ARTCO_STRIPE_CHECKOUT_CATALOG": '{"existing-stripe": {"price_id": "price_old"}}',
    })
    assert cfg["base_url"] == "https://previous-deployment.example"
    assert cfg["paypal"] == {}
    assert "existing-stripe" in cfg["stripe"]


def test_new_deployment_docs_do_not_require_paypal_catalog_variable() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text()
    example = (PROJECT_ROOT / ".env.example").read_text()
    assert "VINCENT_BASE_URL" in readme and "VINCENT_BASE_URL" in example
    assert "VINCENT_PAYPAL_CATALOG" not in readme and "VINCENT_PAYPAL_CATALOG" not in example
    assert "ARTCO_PAYPAL_CATALOG" not in readme and "ARTCO_PAYPAL_CATALOG" not in example
    assert "Sandbox test checkout" in readme
    assert "PAYPAL_CLIENT_ID" in example and "PAYPAL_CLIENT_SECRET" in example
    assert "VINCENT_STRIPE_CHECKOUT_CATALOG" in readme and "VINCENT_STRIPE_CHECKOUT_CATALOG" in example
