from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from . import config


class PayPalConfigurationError(RuntimeError):
    pass


class PayPalUnavailable(RuntimeError):
    pass


def paypal_enabled() -> bool:
    return bool(config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET)


def invoice_enabled() -> bool:
    return paypal_enabled() and bool(config.PAYPAL_INVOICE_EMAIL)


def paypal_catalog_entry(artwork_id: str, source_url: str | None = None) -> dict[str, Any] | None:
    catalog = config.PAYPAL_CATALOG if isinstance(config.PAYPAL_CATALOG, dict) else {}
    entry = catalog.get(str(artwork_id))
    if not isinstance(entry, dict) and source_url:
        normalized_source = str(source_url).strip().rstrip("/")
        matching = [
            candidate for candidate in catalog.values()
            if isinstance(candidate, dict)
            and str(candidate.get("source_url") or candidate.get("artwork_url") or "").strip().rstrip("/") == normalized_source
        ]
        # If duplicate config entries reference a source URL, choose an explicit
        # seller-approved record rather than letting an earlier unapproved row
        # mask a valid row or authorize a charge implicitly.
        entry = next((candidate for candidate in matching if candidate.get("direct_checkout_approved") is True), None)
    if not isinstance(entry, dict):
        return None
    try:
        amount = Decimal(str(entry.get("amount", ""))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None
    currency = str(entry.get("currency") or "").strip().upper()
    if amount <= 0 or len(currency) != 3 or not currency.isalpha():
        return None
    # Direct Vincent payment is enabled only for explicit merchant-authorized catalog rows.
    if entry.get("direct_checkout_approved") is not True:
        return None
    return {**entry, "amount": str(amount), "currency": currency}


async def _access_token(client: httpx.AsyncClient) -> str:
    if not paypal_enabled():
        raise PayPalUnavailable("PayPal is not configured on the server.")
    response = await client.post(
        f"{config.PAYPAL_API_BASE}/v1/oauth2/token",
        data={"grant_type": "client_credentials"},
        auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
        headers={"Accept": "application/json", "Accept-Language": "en_US"},
    )
    if response.status_code >= 400:
        raise PayPalUnavailable("PayPal authentication failed. Verify the server credentials and mode.")
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise PayPalUnavailable("PayPal did not issue an access token.")
    return str(token)


async def create_order(artwork_id: str, return_url: str, cancel_url: str, source_url: str | None = None) -> dict[str, Any]:
    entry = paypal_catalog_entry(artwork_id, source_url)
    if not entry:
        raise PayPalConfigurationError("This work is not configured for Vincent-managed PayPal checkout. Use the seller's checkout link instead.")
    amount = str(entry["amount"])
    currency = str(entry["currency"])
    title = str(entry.get("title") or artwork_id)[:127]
    description = str(entry.get("description") or f"Artwork purchase: {title}")[:127]
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": str(artwork_id)[:256],
            "custom_id": str(artwork_id)[:127],
            "description": description,
            "items": [{
                "name": title,
                "description": description,
                "quantity": "1",
                "unit_amount": {"currency_code": currency, "value": amount},
                "category": "PHYSICAL_GOODS",
            }],
            "amount": {
                "currency_code": currency,
                "value": amount,
                "breakdown": {"item_total": {"currency_code": currency, "value": amount}},
            },
        }],
        "application_context": {
            "brand_name": "Vincent",
            "user_action": "PAY_NOW",
            "shipping_preference": "GET_FROM_FILE",
            "return_url": return_url,
            "cancel_url": cancel_url,
        },
    }
    async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
        token = await _access_token(client)
        response = await client.post(
            f"{config.PAYPAL_API_BASE}/v2/checkout/orders",
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Prefer": "return=representation"},
        )
        if response.status_code >= 400:
            raise PayPalUnavailable("PayPal could not create the order. Check the item catalog and PayPal account permissions.")
        payload = response.json()
    approval = next((link.get("href") for link in payload.get("links", []) if link.get("rel") in {"approve", "payer-action"}), None)
    if not approval:
        raise PayPalUnavailable("PayPal created an order but did not return an approval URL.")
    return {**payload, "approval_url": approval, "catalog_entry": entry}


async def capture_order(order_id: str) -> dict[str, Any]:
    if not order_id or len(order_id) > 80:
        raise PayPalUnavailable("A valid PayPal order reference is required.")
    async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
        token = await _access_token(client)
        response = await client.post(
            f"{config.PAYPAL_API_BASE}/v2/checkout/orders/{order_id}/capture",
            json={},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Prefer": "return=representation"},
        )
        if response.status_code >= 400:
            # Capture can be retried after the browser returns; retrieve the existing order for idempotent UX.
            if response.status_code in {422, 409}:
                get_response = await client.get(f"{config.PAYPAL_API_BASE}/v2/checkout/orders/{order_id}", headers={"Authorization": f"Bearer {token}"})
                if get_response.status_code == 200:
                    return get_response.json()
            raise PayPalUnavailable("PayPal could not confirm this payment. Check the order status and try again.")
        return response.json()


async def create_and_send_invoice(artwork_id: str, recipient_email: str, invoice_number: str | None = None, source_url: str | None = None) -> dict[str, Any]:
    entry = paypal_catalog_entry(artwork_id, source_url)
    if not invoice_enabled():
        raise PayPalConfigurationError("PayPal invoicing requires PayPal credentials and a configured invoice business email.")
    if not entry:
        raise PayPalConfigurationError("This artwork is not configured for Vincent-managed invoicing. Use the seller's own purchasing options.")
    if not recipient_email or "@" not in recipient_email:
        raise ValueError("A valid recipient email is required for the invoice.")
    currency = str(entry["currency"])
    amount = str(entry["amount"])
    title = str(entry.get("title") or artwork_id)[:127]
    business_name = str(config.PAYPAL_INVOICE_BUSINESS_NAME or "Vincent")[:300]
    body = {
        "detail": {
            "currency_code": currency,
            "note": "Artwork invoice issued by Vincent. Please contact the seller for delivery and fulfilment details.",
            "payment_term": {"term_type": "NET_10"},
        },
        "invoicer": {
            "name": {"given_name": str(config.PAYPAL_INVOICE_GIVEN_NAME or business_name)[:140], "surname": str(config.PAYPAL_INVOICE_SURNAME or "Vincent")[:140]},
            "email_address": str(config.PAYPAL_INVOICE_EMAIL),
        },
        "primary_recipients": [{"billing_info": {"email_address": recipient_email}}],
        "items": [{
            "name": title,
            "description": str(entry.get("description") or title)[:1000],
            "quantity": "1",
            "unit_amount": {"currency_code": currency, "value": amount},
            "unit_of_measure": "QUANTITY",
        }],
        "configuration": {"tax_calculated_after_discount": False, "tax_inclusive": False},
    }
    async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
        token = await _access_token(client)
        response = await client.post(
            f"{config.PAYPAL_API_BASE}/v2/invoicing/invoices",
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Prefer": "return=representation"},
        )
        if response.status_code >= 400:
            raise PayPalUnavailable("PayPal could not create the invoice. Verify invoicing is enabled for the business account.")
        invoice = response.json()
        invoice_id = invoice.get("id")
        if not invoice_id:
            raise PayPalUnavailable("PayPal created no invoice reference.")
        send = await client.post(
            f"{config.PAYPAL_API_BASE}/v2/invoicing/invoices/{invoice_id}/send",
            json={"send_to_invoicer": False, "send_to_recipient": True},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if send.status_code >= 400:
            raise PayPalUnavailable("The invoice was created but PayPal could not send it. Check invoices in the PayPal business account.")
        send_data = send.json() if send.content else {}
        # PayPal may return the payer-facing link only once the invoice is sent.
        links = list(invoice.get("links") or []) + list(send_data.get("links") or [])
        invoice_url = next((link.get("href") for link in links if link.get("rel") in {"payer_view", "detail"}), None)
        if not invoice_url:
            details_response = await client.get(
                f"{config.PAYPAL_API_BASE}/v2/invoicing/invoices/{invoice_id}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            if details_response.status_code == 200:
                details_payload = details_response.json()
                links = list(details_payload.get("links") or [])
                invoice_url = next((link.get("href") for link in links if link.get("rel") in {"payer_view", "detail"}), None)
    return {"id": invoice_id, "status": "SENT", "invoice_url": invoice_url, "currency": currency, "amount": amount, "send": send_data}


async def get_invoice_status(invoice_id: str) -> dict[str, Any]:
    """Retrieve invoice status from PayPal instead of inferring payment from navigation."""
    invoice_id = str(invoice_id or "").strip()
    if not invoice_id or len(invoice_id) > 80:
        raise PayPalUnavailable("A valid PayPal invoice reference is required.")
    async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
        token = await _access_token(client)
        response = await client.get(
            f"{config.PAYPAL_API_BASE}/v2/invoicing/invoices/{invoice_id}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise PayPalUnavailable("PayPal could not retrieve this invoice. Check the invoice in the PayPal business account.")
        return response.json()
