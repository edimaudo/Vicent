from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin
from difflib import SequenceMatcher
import re
import time
from io import BytesIO
from html.parser import HTMLParser

import httpx

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from models.schemas import ConciergeRequest, ExistingArtworkReference
from services.concierge import run
from services.payments import PaymentUnavailable, PaymentConfigurationError, CheckoutSessionNotFound, create_checkout_session, retrieve_checkout_session, parse_webhook, verify_webhook_signature
from services.image_proxy import display_image_url, is_allowed_detail_url, is_allowed_image_url
from services.config import REQUEST_TIMEOUT
from services import config
from services.database import (
    SESSION_COOKIE_NAME, DatabaseUnavailable, EmailAlreadyExists, InvalidCredentials,
    register_user, authenticate_user, create_session, get_user_for_session, delete_session,
    get_profile, save_profile, list_saved_artworks, save_artwork, remove_saved_artwork,
    save_recent_search, list_recent_searches, save_order, list_orders, get_order_for_user,
)
from services.paypal_payments import (
    PayPalConfigurationError, PayPalUnavailable, paypal_enabled, invoice_enabled,
    paypal_catalog_entry, create_order as create_paypal_order, capture_order as capture_paypal_order,
    create_and_send_invoice, get_invoice_status,
)
from PIL import Image, UnidentifiedImageError

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
CONCIERGE_RESPONSE_TIMEOUT_SECONDS = 4.6
CONCIERGE_IMAGE_RESPONSE_TIMEOUT_SECONDS = 17.5

app = FastAPI(
    title="Vincent | Your personal art concierge",
    description="Vincent is a personal art concierge for cultural discovery, artwork research, curation and acquisition.",
    version="0.4.0",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _current_user(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not config.DATABASE_URL:
        return None
    try:
        return get_user_for_session(token)
    except DatabaseUnavailable:
        return None


def _require_user(request: Request) -> dict[str, Any] | None:
    return _current_user(request)


def _merge_unique_text(primary: list[str], secondary: list[str], limit: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in [*primary, *secondary]:
        clean = str(value or "").strip()[:500]
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
        if len(output) >= limit:
            break
    return output


def _enrich_request_from_account(payload: ConciergeRequest, user: dict[str, Any] | None) -> ConciergeRequest:
    """Use persisted account taste as a trusted personalization source.

    Profile likes/avoids are merged for signed-in requests. Saved artwork and prior
    ideas are added only when the user explicitly personalizes Explore, so a normal
    Find request is not silently turned into a taste-based search.
    """
    if not user:
        return payload
    try:
        stored_profile = get_profile(user["id"]).get("profile") or {}
        payload.loves = _merge_unique_text(payload.loves, stored_profile.get("loves", []), 12)
        payload.avoid_references = _merge_unique_text(payload.avoid_references, stored_profile.get("avoids", []), 12)
        if payload.personalize_with_saved_taste or payload.intent.value == "taste":
            stored_notes = str(stored_profile.get("notes") or "").strip()
            submitted_notes = str(payload.profile_notes or "").strip()
            if stored_notes and stored_notes.casefold() not in submitted_notes.casefold():
                payload.profile_notes = f"{submitted_notes}\n{stored_notes}".strip()[:1000] or None
            elif submitted_notes:
                payload.profile_notes = submitted_notes[:1000]
            else:
                payload.profile_notes = stored_notes[:1000] or None
            try:
                saved = list_saved_artworks(user["id"], limit=12)
                prior = list_recent_searches(user["id"], limit=8)
            except DatabaseUnavailable:
                saved, prior = [], []
                logger.warning("Persistent taste context could not be loaded for personalized search")
            references: list[ExistingArtworkReference] = list(payload.existing_artworks)
            seen_artworks = {(item.title.casefold(), item.artist.casefold()) for item in references}
            for work in saved:
                if not isinstance(work, dict):
                    continue
                item = ExistingArtworkReference(
                    title=str(work.get("title") or "")[:180],
                    artist=str(work.get("artist") or "")[:140],
                    medium=str(work.get("medium") or "")[:120],
                    description=str(work.get("description") or "")[:300],
                    year=str(work.get("year") or "")[:40],
                )
                identity = (item.title.casefold(), item.artist.casefold())
                if item.title and identity not in seen_artworks:
                    seen_artworks.add(identity)
                    references.append(item)
                if len(references) >= 6:
                    break
            payload.existing_artworks = references[:6]
            current_goal = str(payload.goal or "").strip().casefold()
            prior_ideas = [str(item.get("goal") or "") for item in prior if str(item.get("goal") or "").strip().casefold() != current_goal]
            payload.existing_ideas = _merge_unique_text(payload.existing_ideas, prior_ideas, 6)
            try:
                payload.discovery_level = max(0, min(100, int(stored_profile.get("discoveryLevel", payload.discovery_level))))
            except (TypeError, ValueError):
                pass
    except DatabaseUnavailable:
        logger.warning("Account profile could not be loaded for Concierge personalization")
    return payload


def _cookie_secure(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().casefold()
    return request.url.scheme == "https" or forwarded_proto == "https" or config.ARTCO_BASE_URL.startswith("https://")


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    from services.database import SESSION_LIFETIME_SECONDS
    response.set_cookie(
        key=SESSION_COOKIE_NAME, value=token, httponly=True, secure=_cookie_secure(request),
        samesite="lax", max_age=SESSION_LIFETIME_SECONDS, path="/",
    )


def _order_client_record(order: dict[str, Any]) -> dict[str, Any]:
    amount = order.get("amount")
    try:
        amount_cents = round(float(amount) * 100) if amount is not None else None
    except (TypeError, ValueError):
        amount_cents = None
    created = order.get("created_at")
    metadata = order.get("metadata") if isinstance(order.get("metadata"), dict) else {}
    provider = str(order.get("provider") or "")
    return {
        "sessionId": order.get("provider_order_id"), "artworkId": order.get("artwork_id"),
        "title": order.get("title"), "artist": order.get("artist"),
        "sellerName": order.get("seller_name"), "sourceUrl": order.get("source_url"),
        "provider": provider, "checkoutStatus": str(order.get("status") or "").lower(),
        "paymentStatus": str(order.get("payment_status") or "").lower(),
        "amountTotal": amount_cents, "amountMajor": amount, "currency": order.get("currency"),
        "createdAt": created * 1000 if isinstance(created, (int, float)) else created,
        "updatedAt": order.get("updated_at"), "invoiceUrl": metadata.get("invoice_url"),
        "metadata": metadata,
    }


def _profile_dict(request: Request, user: dict[str, Any]) -> dict[str, Any]:
    profile = get_profile(user["id"]).get("profile")
    return profile if isinstance(profile, dict) else {"loves": [], "avoids": [], "notes": "", "discoveryLevel": 50}


def _safe_next(value: str | None, fallback: str = "/dashboard") -> str:
    target = str(value or "").strip()
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return fallback


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html", context={"page": "home"})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    user = _current_user(request)
    if user:
        return RedirectResponse(url=_safe_next(request.query_params.get("next")), status_code=303)
    return templates.TemplateResponse(request=request, name="auth.html", context={
        "page": "auth", "auth_mode": "login", "next_url": _safe_next(request.query_params.get("next")),
        "database_configured": bool(config.DATABASE_URL),
    })


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="auth.html", context={
        "page": "auth", "auth_mode": "register", "next_url": _safe_next(request.query_params.get("next")),
        "database_configured": bool(config.DATABASE_URL),
    })


@app.get("/api/auth/me")
async def auth_me(request: Request) -> JSONResponse:
    user = _current_user(request)
    return JSONResponse({"authenticated": bool(user), "user": user, "database_configured": bool(config.DATABASE_URL)})


@app.post("/api/auth/register")
async def auth_register(payload: dict[str, Any], request: Request) -> JSONResponse:
    if not config.DATABASE_URL:
        return JSONResponse({"success": False, "detail": "Accounts are not available until persistent database storage is configured."}, status_code=503)
    email = str(payload.get("email") or "").strip().casefold()
    password = str(payload.get("password") or "")
    if "@" not in email or len(email) > 254:
        return JSONResponse({"success": False, "detail": "Enter a valid email address."}, status_code=422)
    try:
        user = register_user(email, password)
        token = create_session(user["id"])
    except EmailAlreadyExists as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=422)
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    response = JSONResponse({"success": True, "user": user})
    _set_session_cookie(response, request, token)
    return response


@app.post("/api/auth/login")
async def auth_login(payload: dict[str, Any], request: Request) -> JSONResponse:
    if not config.DATABASE_URL:
        return JSONResponse({"success": False, "detail": "Accounts are not available until persistent database storage is configured."}, status_code=503)
    try:
        user = authenticate_user(str(payload.get("email") or ""), str(payload.get("password") or ""))
        if not user:
            return JSONResponse({"success": False, "detail": "Email or password is incorrect."}, status_code=401)
        token = create_session(user["id"])
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    response = JSONResponse({"success": True, "user": user})
    _set_session_cookie(response, request, token)
    return response


@app.post("/api/auth/logout")
async def auth_logout(request: Request) -> JSONResponse:
    try:
        delete_session(request.cookies.get(SESSION_COOKIE_NAME))
    except DatabaseUnavailable:
        pass
    response = JSONResponse({"success": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", httponly=True, secure=_cookie_secure(request), samesite="lax")
    return response


@app.get("/api/account/state")
async def account_state(request: Request) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Sign in to access your account library."}, status_code=401)
    try:
        profile_data = get_profile(user["id"])
        saved = list_saved_artworks(user["id"])
        recent = list_recent_searches(user["id"])
        orders = [_order_client_record(order) for order in list_orders(user["id"])]
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    recent_data = [{
        "id": item["id"], "intent": item["intent"], "prompt": item["goal"], "createdAt": item["created_at"] * 1000,
        "request": {"intent": item["intent"], "goal": item["goal"]},
        "data": {"intent": item["intent"], "summary": item["data"].get("summary", ""),
                 "results": item["data"].get("results", []), "not_for_sale": item["data"].get("not_for_sale", [])},
    } for item in recent]
    return JSONResponse({"success": True, "user": user, "profile": profile_data.get("profile"),
                         "profile_updated_at": profile_data.get("updated_at"),
                         "saved": saved, "recent": recent_data, "purchases": orders})


@app.put("/api/account/profile")
async def account_profile_save(payload: dict[str, Any], request: Request) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Sign in to save your taste profile across devices."}, status_code=401)
    try:
        result = save_profile(user["id"], payload)
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    return JSONResponse({"success": True, **result})


@app.post("/api/account/saved")
async def account_saved_action(payload: dict[str, Any], request: Request) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Sign in to sync saved artworks across devices."}, status_code=401)
    action = str(payload.get("action") or "save")
    try:
        if action == "remove":
            remove_saved_artwork(user["id"], str(payload.get("artwork_id") or ""))
        else:
            artwork = payload.get("artwork")
            if not isinstance(artwork, dict):
                return JSONResponse({"success": False, "detail": "Artwork details are required."}, status_code=422)
            save_artwork(user["id"], artwork)
        saved = list_saved_artworks(user["id"])
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    return JSONResponse({"success": True, "saved": saved})


@app.get("/api/orders")
async def account_orders(request: Request) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Sign in to access purchase history."}, status_code=401)
    try:
        orders = [_order_client_record(order) for order in list_orders(user["id"])]
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    return JSONResponse({"success": True, "orders": orders})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"page": "dashboard", "payment_mode": config.PAYPAL_MODE})


@app.get("/concierge", response_class=HTMLResponse)
async def concierge_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="concierge.html",
        context={
            "page": "concierge",
            "inspiration_hidden": request.query_params.get("intent", "discover") == "learn",
        },
    )


@app.get("/results", response_class=HTMLResponse)
async def results_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="results.html", context={"page": "results"})


@app.get("/saved", response_class=HTMLResponse)
async def saved_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="saved.html", context={"page": "saved"})


@app.get("/taste")
async def taste_page(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/dashboard#taste", status_code=307)


@app.get("/checkout/success", response_class=HTMLResponse)
async def checkout_success(request: Request) -> HTMLResponse:
    # A redirect to this URL proves only that the browser returned from Stripe.
    # The page must verify the Checkout Session before telling the buyer payment succeeded.
    return templates.TemplateResponse(request=request, name="checkout_status.html", context={
        "page": "checkout", "status": "verify",
        "session_id": request.query_params.get("session_id", ""),
    })


@app.get("/checkout/cancel", response_class=HTMLResponse)
async def checkout_cancel(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="checkout_status.html", context={"page": "checkout", "status": "cancel"})


@app.post("/api/checkout/session")
async def create_checkout(payload: dict[str, Any], request: Request) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Sign in before starting checkout so Vincent can keep your order history."}, status_code=401)
    artwork_id = str(payload.get("artwork_id") or "").strip()
    if not artwork_id:
        return JSONResponse({"success": False, "detail": "Artwork ID is required."}, status_code=400)
    try:
        session = await create_checkout_session(artwork_id, user_id=user["id"])
        entry = config.STRIPE_CHECKOUT_CATALOG.get(artwork_id, {})
        save_order(user["id"], provider="stripe", provider_order_id=str(session.get("id") or ""),
                   artwork_id=artwork_id, title=str(entry.get("title") or artwork_id),
                   artist=str(entry.get("artist") or ""), seller_name=str(entry.get("seller_name") or ""),
                   source_url=str(entry.get("source_url") or ""), status="OPEN", payment_status="UNPAID",
                   currency=str(entry.get("currency") or ""), metadata={"checkout_url": session.get("url")})
    except PaymentUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=409)
    except PaymentConfigurationError as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=422)
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    except Exception:
        logger.exception("Could not start Stripe checkout")
        return JSONResponse({"success": False, "detail": "Stripe Checkout could not be started."}, status_code=502)
    return JSONResponse({"success": True, "checkout_url": session.get("url"), "session_id": session.get("id")})


@app.get("/api/checkout/status")
async def checkout_status(request: Request, session_id: str = "") -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Sign in to verify and view your purchase."}, status_code=401)
    try:
        owned_order = get_order_for_user(user["id"], session_id, provider="stripe")
        if not owned_order:
            return JSONResponse({"success": False, "detail": "This checkout is not linked to your signed-in account."}, status_code=404)
        session = await retrieve_checkout_session(session_id)
        amount = session.get("amount_total")
        save_order(user["id"], provider="stripe", provider_order_id=session_id,
                   artwork_id=str(session.get("artwork_id") or owned_order["artwork_id"]),
                   title=owned_order["title"], artist=owned_order["artist"], seller_name=owned_order["seller_name"],
                   source_url=owned_order["source_url"], status=str(session.get("status") or "OPEN").upper(),
                   payment_status=str(session.get("payment_status") or "UNPAID").upper(),
                   amount=(float(amount) / 100) if amount is not None else owned_order.get("amount"),
                   currency=str(session.get("currency") or owned_order.get("currency") or "").upper())
    except CheckoutSessionNotFound as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=404)
    except PaymentUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=409)
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    except Exception:
        logger.exception("Could not verify Stripe Checkout Session")
        return JSONResponse({"success": False, "detail": "Stripe could not verify this checkout right now. Try again shortly."}, status_code=502)
    return JSONResponse({"success": True, **session})


@app.post("/api/paypal/orders")
async def paypal_create(payload: dict[str, Any], request: Request) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Sign in before starting checkout so Vincent can keep your order history."}, status_code=401)
    artwork_id = str(payload.get("artwork_id") or "").strip()
    source_url = str(payload.get("source_url") or "").strip()
    entry = paypal_catalog_entry(artwork_id, source_url)
    if not entry:
        return JSONResponse({"success": False, "detail": "This listing uses the seller's checkout unless it has been explicitly approved for Vincent-managed PayPal payment."}, status_code=409)
    base_url = config.ARTCO_BASE_URL.rstrip("/")
    try:
        order = await create_paypal_order(artwork_id, f"{base_url}/checkout/paypal/return", f"{base_url}/checkout/paypal/cancel", source_url=source_url)
        save_order(user["id"], provider="paypal", provider_order_id=str(order["id"]), artwork_id=artwork_id,
                   title=str(entry.get("title") or payload.get("title") or artwork_id),
                   artist=str(entry.get("artist") or payload.get("artist") or ""),
                   seller_name=str(entry.get("seller_name") or payload.get("seller_name") or "Vincent-approved seller"),
                   source_url=str(entry.get("source_url") or source_url), status="CREATED", payment_status="APPROVAL_PENDING",
                   amount=float(entry["amount"]), currency=str(entry["currency"]), metadata={"approval_url": order["approval_url"]})
    except PayPalConfigurationError as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=409)
    except PayPalUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=502)
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    return JSONResponse({"success": True, "order_id": order["id"], "checkout_url": order["approval_url"]})


@app.post("/api/paypal/orders/{order_id}/capture")
async def paypal_capture(order_id: str, request: Request) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Sign in to verify this PayPal payment."}, status_code=401)
    try:
        owned = get_order_for_user(user["id"], order_id, provider="paypal")
        if not owned:
            return JSONResponse({"success": False, "detail": "This PayPal order is not linked to your account."}, status_code=404)
        result = await capture_paypal_order(order_id)
        status = str(result.get("status") or "UNKNOWN").upper()
        units = result.get("purchase_units") or []
        payments = (units[0].get("payments") or {}) if units else {}
        captures = payments.get("captures") or []
        capture = captures[0] if captures else {}
        money = capture.get("amount") or ((units[0].get("amount") or {}) if units else {})
        amount = money.get("value") if isinstance(money, dict) else None
        currency = money.get("currency_code") if isinstance(money, dict) else None
        payment_status = "PAID" if status == "COMPLETED" or str(capture.get("status") or "").upper() == "COMPLETED" else status
        save_order(user["id"], provider="paypal", provider_order_id=order_id, artwork_id=owned["artwork_id"],
                   title=owned["title"], artist=owned["artist"], seller_name=owned["seller_name"], source_url=owned["source_url"],
                   status=status, payment_status=payment_status,
                   amount=float(amount) if amount is not None else owned.get("amount"),
                   currency=str(currency or owned.get("currency") or ""), metadata=owned.get("metadata") or {})
    except PayPalUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=502)
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    return JSONResponse({"success": True, "provider": "paypal", "session_id": order_id, "order_id": order_id,
                         "artwork_id": owned["artwork_id"], "status": status.lower(), "payment_status": payment_status.lower(),
                         "amount_total": round(float(amount) * 100) if amount is not None else None,
                         "currency": str(currency or owned.get("currency") or "").lower()})


@app.post("/api/paypal/invoices")
async def paypal_invoice(payload: dict[str, Any], request: Request) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Sign in to request a PayPal invoice."}, status_code=401)
    artwork_id = str(payload.get("artwork_id") or "").strip()
    source_url = str(payload.get("source_url") or "").strip()
    entry = paypal_catalog_entry(artwork_id, source_url)
    if not entry or not invoice_enabled():
        return JSONResponse({"success": False, "detail": "PayPal invoicing is not enabled for this seller-approved listing."}, status_code=409)
    try:
        invoice = await create_and_send_invoice(artwork_id, user["email"], source_url=source_url)
        save_order(user["id"], provider="paypal_invoice", provider_order_id=str(invoice["id"]), artwork_id=artwork_id,
                   title=str(entry.get("title") or payload.get("title") or artwork_id), artist=str(entry.get("artist") or payload.get("artist") or ""),
                   seller_name=str(entry.get("seller_name") or "Vincent-approved seller"), source_url=str(entry.get("source_url") or source_url),
                   status="SENT", payment_status="UNPAID", amount=float(invoice["amount"]), currency=str(invoice["currency"]),
                   metadata={"invoice_url": invoice.get("invoice_url")})
    except (PayPalUnavailable, PayPalConfigurationError, ValueError) as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=502)
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    return JSONResponse({"success": True, "invoice_id": invoice["id"], "invoice_url": invoice.get("invoice_url"),
                         "detail": "The PayPal invoice was sent to your account email address."})


@app.get("/api/paypal/invoices/{invoice_id}/status")
async def paypal_invoice_status(invoice_id: str, request: Request) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Sign in to verify this invoice."}, status_code=401)
    try:
        owned = get_order_for_user(user["id"], invoice_id, provider="paypal_invoice")
        if not owned:
            return JSONResponse({"success": False, "detail": "This invoice is not linked to your account."}, status_code=404)
        invoice = await get_invoice_status(invoice_id)
        status = str(invoice.get("status") or owned.get("status") or "UNKNOWN").upper()
        payment_status = "PAID" if status in {"PAID", "MARKED_AS_PAID"} else status
        metadata = dict(owned.get("metadata") or {})
        links = invoice.get("links") or []
        invoice_url = next((item.get("href") for item in links if item.get("rel") in {"payer_view", "detail"}), None)
        if invoice_url:
            metadata["invoice_url"] = invoice_url
        save_order(user["id"], provider="paypal_invoice", provider_order_id=invoice_id,
                   artwork_id=owned["artwork_id"], title=owned["title"], artist=owned["artist"],
                   seller_name=owned["seller_name"], source_url=owned["source_url"], status=status,
                   payment_status=payment_status, amount=owned.get("amount"), currency=str(owned.get("currency") or ""),
                   metadata=metadata)
    except PayPalUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=502)
    except DatabaseUnavailable as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=503)
    return JSONResponse({"success": True, "provider": "paypal_invoice", "session_id": invoice_id,
                         "artwork_id": owned["artwork_id"], "status": status.casefold(),
                         "payment_status": payment_status.casefold(), "currency": str(owned.get("currency") or "").casefold()})


@app.get("/checkout/paypal/return", response_class=HTMLResponse)
async def paypal_return(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="checkout_status.html", context={
        "page": "checkout", "status": "paypal_verify", "payment_provider": "paypal",
        "session_id": request.query_params.get("token", ""),
    })


@app.get("/checkout/paypal/cancel", response_class=HTMLResponse)
async def paypal_cancel(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="checkout_status.html", context={"page": "checkout", "status": "cancel", "payment_provider": "paypal"})


@app.get("/api/acquisition/redirect")
async def acquisition_redirect(request: Request) -> Response:
    raw_url = str(request.query_params.get("url") or "").strip()
    if not is_allowed_detail_url(raw_url):
        return JSONResponse({"success": False, "detail": "The seller link is not an approved North American art source."}, status_code=400)
    user = _current_user(request)
    if not user:
        # Keep seller hand-offs attached to a buyer account so the Purchases
        # dashboard can record the outbound acquisition event.
        local_target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(url=f"/login?next={quote(local_target, safe='')}", status_code=303)
    if user:
        try:
            save_order(user["id"], provider="seller_redirect", provider_order_id=__import__("uuid").uuid4().hex,
                       artwork_id=str(request.query_params.get("artwork_id") or "")[:180],
                       title=str(request.query_params.get("title") or "Artwork listing")[:240],
                       artist=str(request.query_params.get("artist") or "")[:180],
                       seller_name=str(request.query_params.get("seller") or "Seller")[:180], source_url=raw_url,
                       status="REDIRECTED", payment_status="EXTERNAL_PENDING",
                       amount=float(request.query_params.get("amount")) if request.query_params.get("amount") else None,
                       currency=str(request.query_params.get("currency") or "")[:8])
        except (ValueError, DatabaseUnavailable):
            logger.warning("Could not persist seller redirect order for signed-in user")
    return RedirectResponse(raw_url, status_code=303)


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request) -> JSONResponse:
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    if not verify_webhook_signature(payload, signature):
        return JSONResponse({"success": False, "detail": "Invalid Stripe webhook signature."}, status_code=400)
    event = parse_webhook(payload)
    event_type = event.get("type", "")
    if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded", "checkout.session.expired"}:
        session = (event.get("data") or {}).get("object") or {}
        metadata = session.get("metadata") or {}
        request.app.state.last_completed_checkout = {
            "session_id": session.get("id"),
            "artwork_id": metadata.get("artwork_id"),
            "completed_at": session.get("created"),
        }
        user_id = metadata.get("user_id")
        session_id = session.get("id")
        if user_id and session_id and config.DATABASE_URL:
            try:
                owned = get_order_for_user(str(user_id), str(session_id), provider="stripe")
                if owned:
                    payment_status = str(session.get("payment_status") or "unpaid").upper()
                    checkout_status = str(session.get("status") or "OPEN").upper()
                    amount_total = session.get("amount_total")
                    save_order(str(user_id), provider="stripe", provider_order_id=str(session_id),
                               artwork_id=str(metadata.get("artwork_id") or owned["artwork_id"]),
                               title=owned["title"], artist=owned["artist"], seller_name=owned["seller_name"],
                               source_url=owned["source_url"], status=checkout_status, payment_status=payment_status,
                               amount=(float(amount_total) / 100) if amount_total is not None else owned.get("amount"),
                               currency=str(session.get("currency") or owned.get("currency") or "").upper())
            except Exception as exc:
                logger.warning("Could not update order from Stripe webhook (%s)", type(exc).__name__)
    return JSONResponse({"success": True, "received": True})


MAX_PROXY_IMAGE_BYTES = 8 * 1024 * 1024
_PROXY_REDIRECT_CODES = {301, 302, 303, 307, 308}

def _sniff_image_media_type(declared_type: str, payload: bytes) -> str | None:
    """Validate raster image bytes instead of trusting a potentially misleading MIME header.

    Page errors and generic SVG logos are a common source of square placeholders. The
    artwork UI expects photographic/raster collection images, so HTML and SVG are not
    accepted as artwork thumbnails even if an upstream server labels them image/*.
    """
    if not payload or len(payload) < 64:
        return None
    leading = payload.lstrip()[:512].lower()
    if b"<html" in leading or b"<!doctype html" in leading or b"<svg" in leading:
        return None
    if leading.startswith(b"<?xml") and b"<svg" in leading:
        return None

    magic_type = None
    if payload.startswith(b"\xff\xd8\xff"):
        magic_type = "image/jpeg"
    elif payload.startswith(b"\x89PNG\r\n\x1a\n"):
        magic_type = "image/png"
    elif payload.startswith((b"GIF87a", b"GIF89a")):
        magic_type = "image/gif"
    elif len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        magic_type = "image/webp"
    elif len(payload) >= 12 and payload[4:8] == b"ftyp" and payload[8:12] in {b"avif", b"avis"}:
        # AVIF decoding is not available in every Pillow build; the BMFF signature
        # is specific enough to preserve valid AVIF collection imagery.
        return "image/avif"
    if not magic_type:
        return None
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
        with Image.open(BytesIO(payload)) as image:
            width, height = image.size
            if width < 64 or height < 64:
                return None
            decoded_format = (image.format or "").lower()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    format_type = {"jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(decoded_format)
    return format_type if format_type == magic_type else None





class _ArtworkImageMetadataParser(HTMLParser):
    """Extract likely artwork images from metadata and rendered HTML markup."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[str] = []
        self.inline_candidates: list[tuple[int, str]] = []

    @staticmethod
    def _srcset_urls(value: str) -> list[str]:
        urls: list[str] = []
        for entry in value.split(","):
            parts = entry.strip().split()
            if parts:
                urls.append(parts[0])
        return urls

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value for key, value in attrs if key and value}
        tag_name = tag.lower()
        if tag_name == "meta":
            key = (attributes.get("property") or attributes.get("name") or attributes.get("itemprop") or "").lower()
            if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src", "image"}:
                content = attributes.get("content")
                if content:
                    self.candidates.append(content.strip())
        elif tag_name == "link":
            rel = (attributes.get("rel") or "").lower().split()
            if "image_src" in rel or ("image" in rel and attributes.get("itemprop") == "image"):
                href = attributes.get("href")
                if href:
                    self.candidates.append(href.strip())
        elif tag_name == "img":
            image_values = []
            for key in ("data-full-image", "data-zoom-image", "data-original", "data-lazy-src", "data-src", "data-original-src", "data-image", "src"):
                value = attributes.get(key)
                if value:
                    image_values.append(value.strip())
            image_values.extend(self._srcset_urls(attributes.get("srcset", "")))
            context = " ".join(attributes.get(key, "") for key in ("alt", "title", "class", "id")).lower()
            score = 20
            if any(word in context for word in ("artwork", "object-image", "primary-image", "collection-image", "art-image", "painting", "sculpture")):
                score += 40
            if any(word in context for word in ("logo", "avatar", "icon", "profile", "social")):
                score -= 80
            width = attributes.get("width", "")
            height = attributes.get("height", "")
            try:
                if width.isdigit() and height.isdigit():
                    score += 20 if int(width) >= 250 and int(height) >= 250 else -20 if int(width) < 80 or int(height) < 80 else 0
            except (TypeError, ValueError):
                pass
            for value in image_values:
                self.inline_candidates.append((score, value))


async def _get_allowed_redirected_url(
    client: httpx.AsyncClient,
    url: str,
    allow_url: Any,
    headers: dict[str, str],
) -> httpx.Response:
    current_url = url
    for _ in range(4):
        if not allow_url(current_url):
            raise HTTPException(status_code=400, detail="This artwork source is not supported by Vincent.")
        response = await client.get(current_url, headers=headers, follow_redirects=False)
        if response.status_code in _PROXY_REDIRECT_CODES:
            location = response.headers.get("location")
            if not location:
                raise HTTPException(status_code=502, detail="The art source returned an invalid redirect.")
            current_url = urljoin(current_url, location)
            continue
        return response
    raise HTTPException(status_code=502, detail="The art source redirected too many times.")


@app.get("/api/artwork-preview")
async def artwork_preview_from_source(url: str) -> Response:
    """Resolve a source page's artwork image, trying every safe candidate before failing."""
    if not is_allowed_detail_url(url):
        raise HTTPException(status_code=400, detail="This artwork source page is not supported by Vincent.")
    headers = {
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "User-Agent": "Mozilla/5.0 (compatible; VincentArtworkPreview/1.0)",
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
            page = await _get_allowed_redirected_url(client, url, is_allowed_detail_url, headers)
            if page.status_code != 200:
                raise HTTPException(status_code=502, detail="The artwork source page could not be read.")
            page_type = page.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if page_type not in {"text/html", "application/xhtml+xml"}:
                raise HTTPException(status_code=502, detail="The artwork source did not return a collection page.")
            if len(page.content) > 2 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="The artwork source page is too large to inspect.")
            parser = _ArtworkImageMetadataParser()
            parser.feed(page.text)
            page_base = str(page.url or url)
            ordered_candidates = [
                candidate for _, candidate in sorted(parser.inline_candidates, key=lambda item: item[0], reverse=True)
            ] + list(parser.candidates)
            seen: set[str] = set()
            for candidate in ordered_candidates:
                candidate_url = urljoin(page_base, candidate.strip())
                if candidate_url.lower().startswith("http://"):
                    secure_candidate = "https://" + candidate_url[len("http://"):]
                    if is_allowed_image_url(secure_candidate):
                        candidate_url = secure_candidate
                if candidate_url in seen or not is_allowed_image_url(candidate_url):
                    continue
                seen.add(candidate_url)
                try:
                    image_response = await _get_allowed_redirected_url(
                        client,
                        candidate_url,
                        is_allowed_image_url,
                        {"Accept": "image/avif,image/webp,image/*,*/*;q=0.8", "User-Agent": "VincentArtworkImageProxy/1.0"},
                    )
                except (HTTPException, httpx.HTTPError, TimeoutError) as exc:
                    logger.debug("Skipping failed artwork preview candidate (%s)", type(exc).__name__)
                    continue
                if image_response.status_code != 200 or len(image_response.content) > MAX_PROXY_IMAGE_BYTES:
                    continue
                content_type = _sniff_image_media_type(
                    image_response.headers.get("content-type", ""), image_response.content
                )
                if not content_type:
                    continue
                return Response(
                    content=image_response.content,
                    media_type=content_type,
                    headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"},
                )
            raise HTTPException(status_code=404, detail="No retrievable artwork image was found on the source page.")
    except HTTPException:
        raise
    except (httpx.HTTPError, TimeoutError) as exc:
        logger.info("Artwork preview fallback failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=502, detail="The artwork preview is temporarily unavailable.") from exc


@app.get("/api/artwork-image")
async def proxy_artwork_image(url: str) -> Response:
    """Serve approved public art thumbnails same-origin when browser hotlinking fails.

    Every redirect is revalidated against an allowlist. Arbitrary hosts, non-HTTPS
    URLs, non-image responses, and oversized responses are rejected.
    """
    if not is_allowed_image_url(url):
        raise HTTPException(status_code=400, detail="This image source is not supported by the Vincent image proxy.")

    current_url = url
    headers = {
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "User-Agent": "VincentArtworkImageProxy/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
            for _ in range(4):
                async with client.stream("GET", current_url, headers=headers) as upstream:
                    if upstream.status_code in _PROXY_REDIRECT_CODES:
                        location = upstream.headers.get("location")
                        if not location:
                            raise HTTPException(status_code=502, detail="The image provider returned an invalid redirect.")
                        next_url = urljoin(current_url, location)
                        if not is_allowed_image_url(next_url):
                            raise HTTPException(status_code=502, detail="The image provider redirected to an unsupported host.")
                        current_url = next_url
                        continue
                    if upstream.status_code != 200:
                        raise HTTPException(status_code=502, detail="The image provider could not return this artwork image.")
                    declared_content_type = upstream.headers.get("content-type", "")
                    declared_size = upstream.headers.get("content-length")
                    if declared_size and declared_size.isdigit() and int(declared_size) > MAX_PROXY_IMAGE_BYTES:
                        raise HTTPException(status_code=413, detail="The artwork image is too large to display.")
                    chunks: list[bytes] = []
                    total_size = 0
                    async for chunk in upstream.aiter_bytes():
                        total_size += len(chunk)
                        if total_size > MAX_PROXY_IMAGE_BYTES:
                            raise HTTPException(status_code=413, detail="The artwork image is too large to display.")
                        chunks.append(chunk)
                    image_payload = b"".join(chunks)
                    content_type = _sniff_image_media_type(declared_content_type, image_payload)
                    if not content_type:
                        raise HTTPException(status_code=502, detail="The image provider returned a non-image response.")
                    return Response(
                        content=image_payload,
                        media_type=content_type,
                        headers={
                            "Cache-Control": "public, max-age=86400",
                            "X-Content-Type-Options": "nosniff",
                            "Content-Disposition": "inline",
                        },
                    )
    except HTTPException:
        raise
    except (httpx.HTTPError, TimeoutError) as exc:
        logger.info("Artwork image proxy failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=502, detail="The artwork image is temporarily unavailable.") from exc
    raise HTTPException(status_code=502, detail="The image provider redirected too many times.")


_ARTWORK_IMAGE_FALLBACK_CACHE: dict[tuple[str, str], tuple[float, str]] = {}


def _normalised_match_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _artwork_match_score(target_title: str, target_artist: str, candidate_title: str, candidate_artist: str) -> float:
    target_title_n = _normalised_match_text(target_title)
    candidate_title_n = _normalised_match_text(candidate_title)
    if not target_title_n or not candidate_title_n:
        return 0.0
    title_ratio = SequenceMatcher(None, target_title_n, candidate_title_n).ratio()
    target_tokens = set(target_title_n.split())
    candidate_tokens = set(candidate_title_n.split())
    overlap = len(target_tokens & candidate_tokens) / max(1, len(target_tokens | candidate_tokens))
    title_score = max(title_ratio, overlap)
    target_artist_n = _normalised_match_text(target_artist)
    candidate_artist_n = _normalised_match_text(candidate_artist)
    if target_artist_n and candidate_artist_n and not target_artist_n.startswith("unknown"):
        artist_ratio = SequenceMatcher(None, target_artist_n, candidate_artist_n).ratio()
        artist_tokens = set(target_artist_n.split())
        candidate_artist_tokens = set(candidate_artist_n.split())
        artist_overlap = len(artist_tokens & candidate_artist_tokens) / max(1, len(artist_tokens | candidate_artist_tokens))
        artist_score = max(artist_ratio, artist_overlap)
        if title_score < 0.92 and artist_score < 0.5:
            return 0.0
        return title_score * 0.72 + artist_score * 0.28
    return title_score


@app.get("/api/artwork-image-fallback")
async def artwork_image_fallback(title: str = "", artist: str = "") -> Response:
    """Recover a missing thumbnail by finding the same work in public museum collections."""
    clean_title = " ".join(title.split())[:180]
    clean_artist = " ".join(artist.split())[:140]
    if len(clean_title) < 2:
        raise HTTPException(status_code=400, detail="An artwork title is required to recover its image.")
    key = (_normalised_match_text(clean_title), _normalised_match_text(clean_artist))
    cached = _ARTWORK_IMAGE_FALLBACK_CACHE.get(key)
    image_url = cached[1] if cached and cached[0] > time.time() else ""
    if not image_url:
        from services.art_sources import (
            ArtInstituteProvider, MetProvider, ClevelandProvider,
        )
        query = " ".join(part for part in (clean_title, clean_artist) if part and not part.casefold().startswith("unknown"))
        providers = [ArtInstituteProvider(), MetProvider(), ClevelandProvider()]
        results = await asyncio.gather(
            *(asyncio.wait_for(provider.search(query, limit=3), timeout=6.0) for provider in providers),
            return_exceptions=True,
        )
        candidates = []
        for batch in results:
            if isinstance(batch, Exception):
                continue
            for work in batch:
                if not work.image_url or not is_allowed_image_url(work.image_url):
                    continue
                score = _artwork_match_score(clean_title, clean_artist, work.title, work.artist)
                if score >= 0.68:
                    candidates.append((score, work.image_url))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            image_url = candidates[0][1]
            _ARTWORK_IMAGE_FALLBACK_CACHE[key] = (time.time() + 6 * 60 * 60, image_url)
    if not image_url:
        raise HTTPException(status_code=404, detail="No matching public artwork image could be recovered.")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=display_image_url(image_url) or image_url, status_code=307, headers={"Cache-Control": "public, max-age=21600"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/api/diagnostics")
async def diagnostics() -> dict[str, Any]:
    from services import config
    return {
        "qloo": {
            "configured": bool(config.QLOO_API_KEY),
            "base_url": config.QLOO_BASE_URL,
            "hackathon_environment": config.QLOO_BASE_URL == "https://hackathon.api.qloo.com",
        },
        "gemini": {
            "configured": bool(config.GEMINI_API_KEY),
            "model": config.GEMINI_MODEL,
        },
        "accounts": {"database_configured": bool(config.DATABASE_URL)},
        "payments": {"stripe_configured": bool(config.STRIPE_SECRET_KEY), "paypal_configured": paypal_enabled(), "paypal_invoices_configured": invoice_enabled()},
        "institutional_sources": {
            "art_institute_of_chicago": {"configured": True, "requires": []},
            "metropolitan_museum_of_art": {"configured": True, "requires": []},
            "cleveland_museum_of_art": {"configured": True, "requires": []},
            "geographic_policy": {
                "allowed_regions": ["Canada", "United States"],
                "canadian_image_sources_configured": False,
                "active_fallback_region": "United States",
            },
        },
        "commercial_sources": {
            "artmatch": {"configured": bool(config.ARTMATCH_LISTING_URL), "requires": []},
            "rookleys": {"configured": bool(config.ROOKLEYS_LISTING_URL), "requires": []},
            "artsy": {
                "configured": bool(config.ARTSY_XAPP_TOKEN and config.ARTSY_PARTNER_ID),
                "requires": ["ARTSY_XAPP_TOKEN", "ARTSY_PARTNER_ID"],
            },
            "artlogic": {
                "configured": bool(config.ARTLOGIC_FEED_URL),
                "requires": ["ARTLOGIC_FEED_URL"],
            },
            "collect24": {
                "configured": bool(config.COLLECT24_API_KEY),
                "requires": ["COLLECT24_API_KEY"],
            },
            "purchase_search_ready": bool(config.ARTMATCH_LISTING_URL or config.ROOKLEYS_LISTING_URL or
                (config.ARTSY_XAPP_TOKEN and config.ARTSY_PARTNER_ID) or config.ARTLOGIC_FEED_URL or config.COLLECT24_API_KEY),
        },
        "stripe": {
            "configured": bool(config.STRIPE_SECRET_KEY),
            "checkout_catalog_items": len(config.STRIPE_CHECKOUT_CATALOG),
        },
    }


@app.get("/api/diagnostics/live")
async def diagnostics_live() -> dict[str, Any]:
    """Safe live smoke test for the configured model APIs and open art sources.

    This endpoint returns configuration/health metadata and counts only; it never
    returns credentials or raw upstream response bodies.
    """
    from services import config
    from services.qloo import search_entities, get_artist_insights
    from services.llm import extract_request_signals
    from services.art_sources import ArtInstituteProvider, MetProvider, ClevelandProvider

    async def probe(label: str, operation, count_field: str | None = None) -> dict[str, Any]:
        try:
            result = await asyncio.wait_for(operation(), timeout=18)
            count = len(result) if hasattr(result, "__len__") else None
            value = {"name": label, "ok": True}
            if count_field:
                value[count_field] = count
            return value
        except Exception as exc:
            logger.warning("Live diagnostics probe %s failed (%s)", label, type(exc).__name__)
            return {"name": label, "ok": False, "error": type(exc).__name__}

    async def qloo_probe() -> dict[str, Any]:
        try:
            entities = await asyncio.wait_for(search_entities("Radiohead"), timeout=18)
            entity_ids = [str(item.get("id")) for item in entities if item.get("id")][:5]
            insights = await asyncio.wait_for(get_artist_insights(entity_ids, 50), timeout=18) if entity_ids else []
            return {
                "name": "Qloo Search + Insights",
                "ok": bool(entities) and (bool(insights) or not entity_ids),
                "entities_found": len(entities),
                "insights_found": len(insights),
            }
        except Exception as exc:
            logger.warning("Live Qloo probe failed (%s)", type(exc).__name__)
            return {"name": "Qloo Search + Insights", "ok": False, "error": type(exc).__name__}

    gemini_task = probe(
        "Gemini request interpretation",
        lambda: extract_request_signals("Find contemporary art inspired by Radiohead", [], []),
        None,
    )
    aic_task = probe("Art Institute of Chicago", lambda: ArtInstituteProvider().search("contemporary art", limit=2), "works_found")
    met_task = probe("The Met", lambda: MetProvider().search("contemporary art", limit=2), "works_found")
    cleveland_task = probe("Cleveland Museum of Art", lambda: ClevelandProvider().search("contemporary art", limit=2), "works_found")

    qloo_result, gemini_result, aic_result, met_result, cleveland_result = await asyncio.gather(
        qloo_probe(), gemini_task, aic_task, met_task, cleveland_task
    )
    return {
        "qloo_base_url": config.QLOO_BASE_URL,
        "qloo_configured": bool(config.QLOO_API_KEY),
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "probes": [qloo_result, gemini_result, aic_result, met_result, cleveland_result],
        "hint": "If a probe is not configured or fails, review the matching environment variable and deployment logs.",
    }


@app.post("/api/concierge")
async def concierge(payload: ConciergeRequest, request: Request) -> JSONResponse:
    # Enforce this at the API boundary as well as the browser UI. Images may
    # support a request, but every Concierge service must receive written input.
    if not (payload.goal or "").strip():
        return JSONResponse(
            {
                "success": False,
                "detail": "Enter a short written description. Inspiration images can support your request, but cannot replace the text.",
                "error_type": "WrittenPromptRequired",
            },
            status_code=422,
        )
    payload.goal = payload.goal.strip()
    # Image-attached Explore/Find/Curate requests require sequential work: complete
    # the written search, interpret the image, then run a separate visual search.
    # Learn is text-topic driven and does not process inspiration-image payloads.
    user = _current_user(request)
    payload = _enrich_request_from_account(payload, user)
    timeout_seconds = (
        CONCIERGE_IMAGE_RESPONSE_TIMEOUT_SECONDS
        if payload.inspiration_images and payload.intent.value != "learn"
        else CONCIERGE_RESPONSE_TIMEOUT_SECONDS
    )
    try:
        result: dict[str, Any] = await asyncio.wait_for(
            run(payload), timeout=timeout_seconds
        )
        if result.get("success") is False:
            return JSONResponse(result, status_code=503)
        # Enforce the three-card visible limit at the API boundary as well as
        # in the agent and UI so stale/alternate responses cannot over-render.
        primary = list(result.get("results") or [])[:3]
        remaining = max(0, 3 - len(primary))
        secondary = list(result.get("not_for_sale") or [])[:remaining] if payload.intent.value == "buy" else []
        result["results"] = primary
        result["not_for_sale"] = secondary
        if user:
            try:
                save_recent_search(user["id"], payload.intent.value, payload.goal, result)
            except Exception as exc:
                logger.warning("Could not persist account search history (%s)", type(exc).__name__)
        return JSONResponse(result)
    except asyncio.TimeoutError:
        logger.warning("Concierge request exceeded the %.1f second API deadline", timeout_seconds)
        if payload.inspiration_images and payload.intent.value != "learn":
            detail = (
                "The written-request search started, but Vincent could not finish the required image interpretation and visual search "
                "within the image-search deadline. No text-only results were returned. Retry with a clearer/smaller image, or remove it for a text-only search."
            )
        else:
            detail = "This search exceeded Vincent’s response-time limit. Try a more focused request."
        return JSONResponse(
            {
                "success": False,
                "detail": detail,
                "error_type": "ResponseDeadlineExceeded",
                "diagnostics": {"response_target_ms": int(timeout_seconds * 1000)},
            },
            status_code=504,
        )
    except Exception as exc:
        logger.exception("Concierge request failed")
        # Preserve a useful, non-sensitive API response instead of returning an
        # unexplained framework 500 page to the frontend.
        return JSONResponse(
            {
                "success": False,
                "detail": "The concierge could not complete this search. Check the service configuration and try again.",
                "error_type": type(exc).__name__,
            },
            status_code=502,
        )
