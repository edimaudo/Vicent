from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Protocol
from decimal import Decimal
import re
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin, urlparse

import httpx

from .image_proxy import is_allowed_detail_url, is_allowed_image_url
from .config import (
    ARTSY_PARTNER_ID,
    ARTSY_XAPP_TOKEN,
    ARTLOGIC_FEED_URL,
    ARTWORK_RESULT_LIMIT,
    COLLECT24_API_KEY,
    COLLECT24_BASE_URL,
    REQUEST_TIMEOUT,
    ARTMATCH_LISTING_URL,
    ROOKLEYS_LISTING_URL,
    ARTSY_PARTNER_MARKET,
    ARTLOGIC_MARKET,
)


@dataclass(slots=True)
class Artwork:
    id: str
    title: str
    artist: str
    image_url: str | None
    detail_url: str | None
    source: str
    source_kind: str
    availability: str
    price: float | None
    currency: str | None
    medium: str | None
    dimensions: str | None
    year: str | None
    description: str | None
    matched_direction: str | None = None
    purchase_mode: str = "external"
    seller_name: str | None = None
    market_code: str | None = None


class ArtworkProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int = ARTWORK_RESULT_LIMIT) -> list[Artwork]: ...


class ArtInstituteProvider:
    country_code = "US"
    name = "Art Institute of Chicago"
    base_url = "https://api.artic.edu/api/v1/artworks"

    async def search(self, query: str, limit: int = ARTWORK_RESULT_LIMIT) -> list[Artwork]:
        # Search broadly enough to replace records with no usable reproduction,
        # then return only works with an actual image identifier.
        params = {
            "q": query,
            "limit": min(max(limit * 3, limit), 40),
            "fields": "id,title,artist_display,date_display,medium_display,dimensions,image_id,credit_line,is_public_domain",
            "query[term][is_public_domain]": "true",
        }
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(f"{self.base_url}/search", params=params)
            response.raise_for_status()
            payload = response.json()
        iiif = payload.get("config", {}).get("iiif_url", "https://www.artic.edu/iiif/2")
        output = [self._map(item, iiif) for item in payload.get("data", [])]
        return [work for work in output if _usable_image_url(work.image_url)][:limit]

    def _map(self, item: dict[str, Any], iiif: str) -> Artwork:
        image_id = item.get("image_id")
        image_url = f"{iiif.rstrip('/')}/{image_id}/full/843,/0/default.jpg" if image_id else None
        return Artwork(
            id=f"aic:{item.get('id')}",
            title=item.get("title") or "Untitled",
            artist=item.get("artist_display") or "Unknown artist",
            image_url=image_url,
            detail_url=f"https://www.artic.edu/artworks/{item.get('id')}",
            source=self.name,
            source_kind="institution",
            availability="not_for_sale",
            price=None,
            currency=None,
            medium=item.get("medium_display"),
            dimensions=item.get("dimensions"),
            year=item.get("date_display"),
            description=item.get("credit_line"),
            market_code="US",
        )


class MetProvider:
    country_code = "US"
    name = "The Metropolitan Museum of Art"
    base_url = "https://collectionapi.metmuseum.org/public/collection/v1.1"

    async def search(self, query: str, limit: int = ARTWORK_RESULT_LIMIT) -> list[Artwork]:
        # Keep detail fan-out small; the Met search endpoint returns IDs, while
        # object details carry the image URLs needed by the gallery.
        requested = min(max(limit, 1), 5)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={"q": query, "hasImages": "true", "limit": requested, "offset": 0},
            )
            response.raise_for_status()
            ids = (response.json().get("objectIDs") or [])[:requested]
            details = await asyncio.gather(
                *(client.get(f"{self.base_url}/objects/{object_id}") for object_id in ids),
                return_exceptions=True,
            )
        output: list[Artwork] = []
        for result in details:
            if not isinstance(result, httpx.Response) or result.status_code != 200:
                continue
            try:
                work = self._map(result.json())
            except (TypeError, ValueError):
                continue
            if _usable_image_url(work.image_url):
                output.append(work)
        return output[:limit]

    def _map(self, item: dict[str, Any]) -> Artwork:
        return Artwork(
            id=f"met:{item.get('objectID')}",
            title=item.get("title") or "Untitled",
            artist=item.get("artistDisplayName") or "Unknown artist",
            image_url=_normalise_image_url(item.get("primaryImageSmall") or item.get("primaryImage")),
            detail_url=item.get("objectURL"),
            source=self.name,
            source_kind="institution",
            availability="not_for_sale",
            price=None,
            currency=None,
            medium=item.get("medium"),
            dimensions=item.get("dimensions"),
            year=item.get("objectDate"),
            description=item.get("creditLine"),
            market_code="US",
        )


class ClevelandProvider:
    country_code = "US"
    name = "Cleveland Museum of Art"
    base_url = "https://openaccess-api.clevelandart.org/api/artworks"

    async def search(self, query: str, limit: int = ARTWORK_RESULT_LIMIT) -> list[Artwork]:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(self.base_url, params={"q": query, "limit": min(max(limit * 3, limit), 40)})
            response.raise_for_status()
            payload = response.json()
        output = [self._map(item) for item in (payload.get("data") or []) if isinstance(item, dict)]
        return [work for work in output if _usable_image_url(work.image_url)][:limit]

    def _map(self, item: dict[str, Any]) -> Artwork:
        images = item.get("images") or {}
        image_url = None
        if isinstance(images, dict):
            for key in ("web", "print", "original"):
                value = images.get(key)
                if isinstance(value, dict):
                    image_url = _normalise_image_url(value.get("url") or value.get("href"))
                    if image_url:
                        break
                elif isinstance(value, str):
                    image_url = _normalise_image_url(value)
                    if image_url:
                        break
        return Artwork(
            id=f"cma:{item.get('id') or item.get('accession_number')}",
            title=item.get("title") or "Untitled",
            artist=item.get("artist") or item.get("creator") or "Unknown artist",
            image_url=image_url,
            detail_url=item.get("url"),
            source=self.name,
            source_kind="institution",
            availability="not_for_sale",
            price=None,
            currency=None,
            medium=item.get("technique") or item.get("medium"),
            dimensions=item.get("measurements"),
            year=str(item.get("creation_date")) if item.get("creation_date") else None,
            description=item.get("description"),
            market_code="US",
        )




class ArtsyProvider:
    """Optional Artsy Partner API adapter for published partner/gallery inventory.

    It is disabled unless both an XAPP token and partner ID are configured. The
    public Search API is deliberately avoided because Artsy is retiring that API.
    """

    name = "Artsy"
    base_url = "https://api.artsy.net/api"

    async def search(self, query: str, limit: int = ARTWORK_RESULT_LIMIT) -> list[Artwork]:
        if not ARTSY_XAPP_TOKEN or not ARTSY_PARTNER_ID or ARTSY_PARTNER_MARKET not in {"CA", "US"}:
            return []
        headers = {"X-XAPP-Token": ARTSY_XAPP_TOKEN, "Accept": "application/json"}
        # Use the Partner API's published artwork inventory, not the retiring
        # public Search API. Relevance is ranked locally against the requested brief.
        params = {
            "partner_id": ARTSY_PARTNER_ID,
            "published": "true",
            "size": min(max(limit * 5, limit), 100),
            "page": 1,
        }
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            response = await client.get(f"{self.base_url}/artworks", params=params)
            response.raise_for_status()
            payload = response.json()
        items = (payload.get("_embedded") or {}).get("artworks") or []
        candidates = []
        for item in items:
            if not isinstance(item, dict):
                continue
            work = _map_artsy(item)
            if not _usable_image_url(work.image_url) or work.title == "Untitled":
                continue
            if not _matches_query(work, query):
                continue
            if not any(_same_work_identity(work, existing) for existing in candidates):
                candidates.append(work)
            if len(candidates) >= limit:
                break
        return [replace(work, market_code=ARTSY_PARTNER_MARKET) for work in candidates[:limit]]


class Collect24Provider:
    """Optional commercial artwork registry adapter.

    Collect24 exposes public artwork records with explicit availability and
    public price fields. Access requires a server-side API key. The provider
    only returns works published as for-sale so the Buy experience never has
    to infer purchase status from museum data.
    """

    name = "Collect24"

    async def search(self, query: str, limit: int = ARTWORK_RESULT_LIMIT) -> list[Artwork]:
        if not COLLECT24_API_KEY:
            return []
        headers = {
            "Authorization": f"Bearer {COLLECT24_API_KEY}",
            "Accept": "application/json",
        }
        params = {
            "q": query,
            "availability": "for_sale",
            "limit": min(limit, 100),
        }
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            response = await client.get(f"{COLLECT24_BASE_URL}/artworks", params=params)
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("data") or payload.get("results") or []
        output = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            seller = row.get("seller") if isinstance(row.get("seller"), dict) else {}
            market = str(seller.get("country_code") or seller.get("country") or row.get("country_code") or "").upper()
            if market not in {"CA", "CANADA", "US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
                continue
            work = self._map(row)
            output.append(replace(work, market_code="CA" if market in {"CA", "CANADA"} else "US"))
            if len(output) >= limit:
                break
        return output

    def _map(self, item: dict[str, Any]) -> Artwork:
        artist_data = item.get("artist") or {}
        price_data = item.get("price") or {}
        dimensions = item.get("dimensions_cm") or {}
        dimension_parts = []
        if isinstance(dimensions, dict):
            for key, label in (("height", "H"), ("width", "W"), ("depth", "D")):
                value = dimensions.get(key)
                if isinstance(value, (int, float)):
                    dimension_parts.append(f"{label} {value:g} cm")
        image_data = item.get("images") or {}
        image_url = _normalise_image_url(image_data.get("cover_url") or image_data.get("thumbnail_url")) if isinstance(image_data, dict) else None
        price_value = price_data.get("amount") if isinstance(price_data, dict) else None
        try:
            price_value = float(price_value) if price_value is not None else None
        except (TypeError, ValueError):
            price_value = None
        return Artwork(
            id=f"collect24:{item.get('collect24_id') or item.get('id')}",
            title=item.get("title") or "Untitled",
            artist=(artist_data.get("name") if isinstance(artist_data, dict) else None) or "Unknown artist",
            image_url=image_url,
            detail_url=(item.get("links") or {}).get("public_page") if isinstance(item.get("links"), dict) else None,
            source=self.name,
            source_kind="commercial",
            availability="available" if item.get("availability") == "for_sale" else "not_for_sale",
            price=price_value,
            currency=price_data.get("currency") if isinstance(price_data, dict) else None,
            medium=item.get("medium"),
            dimensions=" · ".join(dimension_parts) if dimension_parts else None,
            year=item.get("year_created"),
            description=item.get("provenance_summary"),
            purchase_mode="external",
            seller_name=(item.get("seller") or {}).get("name") if isinstance(item.get("seller"), dict) else None,
        )



class CanadianGalleryHtmlProvider:
    """Base parser for public Canadian gallery inventory with explicit sale signals."""

    country_code = "CA"
    name = "Canadian gallery"
    listing_url = ""
    image_source_prefix = ""

    async def search(self, query: str, limit: int = ARTWORK_RESULT_LIMIT) -> list[Artwork]:
        headers = {"User-Agent": "Vincent/1.0 (+public art discovery)", "Accept": "text/html,application/xhtml+xml"}
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True, headers=headers) as client:
            response = await client.get(self.listing_url)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        candidates = _parse_canadian_gallery_cards(soup, self.name, self.listing_url, self.country_code)
        normalized_query = str(query or "").casefold()
        query_terms = [term for term in re.findall(r"[a-z0-9]+", normalized_query) if len(term) > 2 and term not in {"art", "artwork", "painting", "original", "canadian", "available", "purchase", "buy", "find", "search", "works", "work"}]
        if query_terms:
            scored = []
            for work in candidates:
                haystack = " ".join([work.title, work.artist, work.medium or "", work.description or ""]).casefold()
                score = sum(1 for term in query_terms if term in haystack)
                if score:
                    scored.append((score, work))
            scored.sort(key=lambda item: item[0], reverse=True)
            candidates = [work for _, work in scored]
        unique: list[Artwork] = []
        for work in candidates:
            if work.image_url and work.availability == "available" and not any(_same_work_identity(work, existing) for existing in unique):
                unique.append(work)
            if len(unique) >= limit:
                break
        return unique[:limit]


class ArtMatchProvider(CanadianGalleryHtmlProvider):
    """Public ArtMatch catalogue for Canadian original art; seller handles checkout."""
    name = "ArtMatch"
    listing_url = ARTMATCH_LISTING_URL


class RookleysProvider(CanadianGalleryHtmlProvider):
    """Rookleys Canadian Art Gallery inventory, presented by its Artlogic storefront."""
    name = "Rookleys Canadian Art Gallery"
    listing_url = ROOKLEYS_LISTING_URL


def _visible_text(node: Tag) -> str:
    clone = BeautifulSoup(str(node), "html.parser")
    for hidden in clone.select("[hidden], .hidden, .sold_out_dynamic.hidden, script, style, noscript, svg"):
        hidden.decompose()
    return " ".join(clone.stripped_strings)


def _first_text(card: Tag, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = card.select_one(selector)
        if node:
            value = " ".join(node.stripped_strings).strip()
            if value and value.casefold() not in {"add to wishlist", "wishlist", "sold", "available"}:
                return value[:240]
    return ""


def _parse_price(text: str) -> tuple[float | None, str | None]:
    # Gallery prices are scraped only from the listing card; no prices are inferred.
    match = re.search(r"(?:(CAD|CAN\$|C\$|USD|US\$|\$)\s*)(\d[\d,]*(?:\.\d{1,2})?)", text, re.I)
    if not match:
        return None, None
    try:
        price = float(Decimal(match.group(2).replace(",", "")))
    except Exception:
        return None, None
    currency_text = (match.group(1) or "").upper()
    currency = "CAD" if currency_text in {"CAD", "CAN$", "C$", "$"} else "USD"
    return price, currency


def _parse_artist_title(raw: str) -> tuple[str, str]:
    value = re.sub(r"^(?:image:\s*)", "", raw or "", flags=re.I).strip(" \t,–—-")
    # Gallery image alt text commonly follows Artist, Title, year.
    match = re.match(r"^([^,]{2,100}),\s*(.+)$", value)
    if match:
        artist = match.group(1).strip()
        title = re.sub(r",?\s*(?:c\.?\s*)?(?:18|19|20)\d{2}.*$", "", match.group(2)).strip(" ,")
        if title and artist.casefold() not in {"image", "artwork", "photo"}:
            return artist, title[:240]
    return "Unknown artist", value[:240] or "Untitled"


def _parse_canadian_gallery_cards(soup: BeautifulSoup, source: str, listing_url: str, market: str) -> list[Artwork]:
    output: list[Artwork] = []
    base_host = (urlparse(listing_url).hostname or "").lower().removeprefix("www.")
    image_seen: set[str] = set()
    for img in soup.find_all("img"):
        raw_image = img.get("data-src") or img.get("data-lazy-src") or img.get("data-original") or img.get("src")
        if not raw_image or str(raw_image).startswith("data:"):
            continue
        image_url = _normalise_image_url(str(raw_image))
        if not image_url or image_url in image_seen or not is_allowed_image_url(image_url):
            continue
        # Find the nearest likely listing tile, not a wrapper containing the full grid.
        card = None
        for ancestor in img.parents:
            if not isinstance(ancestor, Tag):
                continue
            attrs = " ".join([str(x) for x in ancestor.get("class", [])]) + " " + str(ancestor.get("id") or "")
            text = _visible_text(ancestor)
            looks_like_tile = re.search(r"store[_-]?item|artwork[-_ ]?(?:card|item|tile)|product[-_ ]?(?:card|item|tile)|woocommerce-loop-product|portfolio-item", attrs, re.I)
            if looks_like_tile and len(text) < 1800 and ancestor.find("a", href=True):
                card = ancestor
                break
            if ancestor.name in {"article", "li"} and len(text) < 1800 and ancestor.find("a", href=True):
                card = ancestor
                break
        if card is None:
            parent = img.find_parent("a", href=True)
            if not parent:
                continue
            card = parent.parent if isinstance(parent.parent, Tag) else parent
        card_text = _visible_text(card)
        links = [anchor for anchor in card.find_all("a", href=True) if anchor.get("href")]
        detail_url = None
        title_from_link = ""
        for anchor in links:
            href = str(anchor.get("href"))
            abs_url = urljoin(listing_url, href)
            if (urlparse(abs_url).hostname or "").lower().removeprefix("www.") != base_host:
                continue
            image_link = anchor.find("img")
            link_text = " ".join(anchor.stripped_strings).strip()
            if not link_text and image_link is not None:
                link_text = str(image_link.get("alt") or "").strip()
            if link_text.casefold() in {"add to wishlist", "wishlist", "quick view", "reserve", "purchase", "add to cart"}:
                continue
            # Gallery listing tiles often wrap only the image in the product link;
            # accept that anchor and derive title/artist from alt text or card fields.
            if abs_url.rstrip("/") != listing_url.rstrip("/") and (len(link_text) > 2 or image_link is not None):
                detail_url, title_from_link = abs_url, link_text[:240]
                if image_link is not None or re.search(r"artwork|painting|product", abs_url, re.I):
                    break
        if not detail_url:
            continue
        artist = _first_text(card, (".artist", ".artist-name", ".artwork-artist", ".product-artist", ".store_item_artist", "[itemprop='creator']"))
        title = _first_text(card, (".artwork-title", ".product-title", ".woocommerce-loop-product__title", ".store_item_title", "[itemprop='name']", "h2", "h3")) or title_from_link
        alt_artist, alt_title = _parse_artist_title(str(img.get("alt") or ""))
        if not artist:
            artist = alt_artist if alt_artist != "Unknown artist" else "Unknown artist"
        if not title or title.casefold() == artist.casefold():
            title = alt_title
        if title.casefold().startswith("image:"):
            _, title = _parse_artist_title(title)
        if artist == "Unknown artist" and title_from_link:
            # Many gallery tiles list artist and title in adjacent links; use alt text if available.
            alt_artist, alt_title = _parse_artist_title(str(img.get("alt") or title_from_link))
            artist = alt_artist
            if title == title_from_link and alt_title:
                title = alt_title
        if not title or title.casefold() in {"view artwork", "view details", "read more"}:
            continue
        price, currency = _parse_price(card_text)
        if price is None:
            continue
        lower_text = card_text.casefold()
        status_el = card.select_one(".store_item_dynamic_status, .availability, .stock-status, .product-stock")
        status_text = _visible_text(status_el).casefold() if status_el else ""
        sold = bool(re.search(r"\b(sold|sold out|unavailable|reserved|out of stock)\b", status_text))
        if not status_text:
            # ArtMatch labels sold items in visible card content; other entries on its
            # dedicated for-sale listing have a current price and a product purchase page.
            sold = bool(re.search(r"\b(sold|sold out|unavailable|out of stock)\b", lower_text))
        availability_text = f"{status_text} {lower_text}"
        explicit_available = bool(re.search(r"\b(available|in stock|add to cart|purchase)\b", availability_text))
        if source == "Rookleys Canadian Art Gallery":
            available = explicit_available and not sold
        else:
            available = price is not None and not sold and "canadian-art-for-sale" in listing_url
        if not available:
            continue
        medium = _first_text(card, (".medium", ".artwork-medium", "[itemprop='artMedium']")) or None
        year_match = re.search(r"\b(?:c\.?\s*)?(?:18|19|20)\d{2}\b", card_text)
        year = year_match.group(0) if year_match else None
        description = f"Listing shown as available on the {source} website when retrieved. Price and status should be reconfirmed at checkout."
        identifier = re.sub(r"[^a-z0-9]+", "-", detail_url.casefold()).strip("-")[-120:]
        output.append(Artwork(
            id=f"{source.casefold().replace(' ', '-')}:" + identifier,
            title=title, artist=artist, image_url=image_url, detail_url=detail_url,
            source=source, source_kind="commercial", availability="available", price=price,
            currency=currency or ("CAD" if market == "CA" else "USD"), medium=medium,
            dimensions=None, year=year, description=description, purchase_mode="external",
            seller_name=source, market_code=market,
        ))
        image_seen.add(image_url)
    return output

def _flag_is_true(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y"}
    return bool(value)


def _map_artsy(item: dict[str, Any]) -> Artwork:
    artists = item.get("_embedded", {}).get("artists") or []
    artist = artists[0].get("name") if artists else "Unknown artist"
    # Buy must only surface works the marketplace explicitly marks as
    # purchasable. Inquiry-only listings are not purchase inventory.
    can_acquire = _flag_is_true(item.get("can_acquire"))
    sold = _flag_is_true(item.get("sold"))
    availability = "available" if can_acquire and not sold else "not_for_sale"
    price = None
    price_data = item.get("price") or item.get("list_price")
    if isinstance(price_data, (int, float)):
        price = float(price_data)
    elif isinstance(price_data, dict):
        amount = price_data.get("amount")
        if isinstance(amount, (int, float)):
            price = float(amount)
    if price is None and isinstance(item.get("price_cents"), (int, float)):
        price = float(item["price_cents"]) / 100.0
    thumbnail_url = (item.get("_links", {}).get("thumbnail", {}) or {}).get("href")
    if isinstance(thumbnail_url, str) and "missing_image" in thumbnail_url.casefold():
        thumbnail_url = None
    elif isinstance(thumbnail_url, str):
        thumbnail_url = thumbnail_url.replace("/square.jpg", "/large.jpg")
    return Artwork(
        id=f"artsy:{item.get('id')}",
        title=item.get("title") or "Untitled",
        artist=artist or "Unknown artist",
        image_url=_normalise_image_url(thumbnail_url),
        detail_url=(item.get("_links", {}).get("permalink", {}) or {}).get("href"),
        source="Artsy",
        source_kind="commercial",
        availability=availability,
        price=price,
        currency=item.get("currency") or (price_data.get("currency") if isinstance(price_data, dict) else None),
        medium=item.get("medium"),
        dimensions=(item.get("dimensions") or {}).get("cm", {}).get("text") if isinstance(item.get("dimensions"), dict) else item.get("dimensions"),
        year=str(item.get("date")) if item.get("date") else None,
        description=item.get("blurb"),
        purchase_mode="external",
        seller_name=None,
    )


class ArtlogicProvider:
    """Optional server-side Artlogic feed adapter.

    Artlogic feeds are configured per gallery/account and are not treated as a general public search API.
    Configure ARTLOGIC_FEED_URL only when the gallery has granted access.
    """

    name = "Artlogic"

    async def search(self, query: str, limit: int = ARTWORK_RESULT_LIMIT) -> list[Artwork]:
        if not ARTLOGIC_FEED_URL or ARTLOGIC_MARKET not in {"CA", "US"}:
            return []
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(ARTLOGIC_FEED_URL)
            response.raise_for_status()
            payload = response.json()
        return [replace(work, market_code=ARTLOGIC_MARKET) for work in _map_artlogic(payload)
                if _matches_query(work, query) and work.image_url and work.availability == "available"][:limit]


def _map_artlogic(payload: Any) -> list[Artwork]:
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    output: list[Artwork] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_status = row.get("availability") or row.get("sale_status") or row.get("status") or ""
        available = str(raw_status).strip().casefold().replace("-", "_")
        # A general gallery-feed status such as "published" or "active" does
        # not prove that a work is currently for sale. Accept only explicit
        # sale-availability values and honour explicit sold/reserved flags.
        availability_values = {
            "available", "for sale", "for_sale", "in stock", "in_stock",
            "available for purchase", "available_for_purchase",
            "available to purchase", "available_to_purchase",
        }
        status_value = str(row.get("status") or "").strip().casefold().replace("-", "_")
        sold_values = {"sold", "reserved", "unavailable", "not_for_sale", "not for sale", "withdrawn", "archived"}
        sold = (
            _flag_is_true(row.get("sold"))
            or _flag_is_true(row.get("is_sold"))
            or _flag_is_true(row.get("reserved"))
            or available in sold_values
            or status_value in sold_values
        )
        is_available = available in availability_values and not sold
        price = row.get("retail_price") or row.get("price")
        try:
            price_value = float(price) if price not in (None, "") else None
        except (TypeError, ValueError):
            price_value = None
        output.append(
            Artwork(
                id=f"artlogic:{row.get('id') or row.get('artwork_id')}",
                title=row.get("title") or "Untitled",
                artist=row.get("artist") or row.get("artist_name") or "Unknown artist",
                # Prefer the feed's published thumbnail/image URL when present.
                # The same-origin proxy is used only for explicitly allowlisted hosts;
                # other HTTPS image URLs are loaded directly by the browser.
                image_url=_extract_image_url(row),
                detail_url=row.get("url") or row.get("permalink"),
                source="Artlogic",
                source_kind="commercial",
                availability="available" if is_available else "not_for_sale",
                price=price_value,
                currency=row.get("retail_currency") or row.get("currency"),
                medium=row.get("medium") or row.get("artwork_type"),
                dimensions=row.get("dimensions"),
                year=str(row.get("year")) if row.get("year") else None,
                description=None,
                purchase_mode="external",
                seller_name=row.get("gallery") or row.get("gallery_name"),
            )
        )
    return output



def _extract_image_url(item: Any) -> str | None:
    """Extract a likely image URL from common gallery-feed field shapes."""
    if not isinstance(item, dict):
        return None

    direct_keys = (
        "image_url", "imageUrl", "thumbnail_url", "thumbnailUrl", "thumbnail",
        "primary_image", "primaryImage", "cover_image", "coverImage", "image",
        "image_uri", "imageUri", "preview_image", "previewImage",
    )
    nested_keys = ("url", "href", "src", "uri", "image_url", "thumbnail_url", "original_url", "large_url", "small_url")

    def from_value(value: Any, depth: int = 0) -> str | None:
        if depth > 3:
            return None
        if isinstance(value, str):
            return _normalise_image_url(value)
        if isinstance(value, dict):
            for key in nested_keys:
                candidate = value.get(key)
                if candidate:
                    result = from_value(candidate, depth + 1)
                    if result:
                        return result
            for key in ("web", "primary", "thumbnail", "small", "preview", "cover", "original", "large", "image"):
                candidate = value.get(key)
                if candidate:
                    result = from_value(candidate, depth + 1)
                    if result:
                        return result
        if isinstance(value, list):
            for candidate in value:
                result = from_value(candidate, depth + 1)
                if result:
                    return result
        return None

    for key in direct_keys:
        if item.get(key):
            result = from_value(item[key])
            if result:
                return result

    # Gallery feeds vary: images can be a keyed object, a list of image records,
    # or a nested media object. Prioritise rendition names that usually work in cards.
    images = item.get("images") or item.get("media") or item.get("artwork_images")
    result = from_value(images)
    if result:
        return result

    for key in ("assets", "files", "photos", "picture"):
        result = from_value(item.get(key))
        if result:
            return result
    return None



def _normalise_image_url(value: Any) -> str | None:
    """Convert provider image templates into browser-ready HTTPS URLs."""
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    lowered_url = url.casefold()
    if any(marker in lowered_url for marker in ("missing_image.png", "no-image", "no_image", "placeholder.svg", "default-placeholder")):
        return None
    if url.startswith("//"):
        url = "https:" + url
    url = url.replace("{?width,height}", "?width=900&height=1125")
    url = url.replace("{?width}", "?width=900")
    url = url.replace("{?height}", "?height=1125")
    # Some APIs return templates with brace placeholders that need removal.
    url = url.replace("{width}", "900").replace("{height}", "1125")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    # HTTPS pages cannot reliably show HTTP thumbnails (browsers block mixed
    # content). Prefer the HTTPS form for every public catalogue image URL; the
    # browser or same-origin proxy will fail cleanly if that host has no TLS.
    if parsed.scheme == "http":
        return parsed._replace(scheme="https").geturl()
    return url


def _usable_image_url(value: Any) -> bool:
    return bool(_normalise_image_url(value))


def _work_identity(work: Artwork) -> tuple[str, str, str]:
    import re
    title = re.sub(r"[^a-z0-9]+", " ", str(work.title or "").casefold()).strip()
    artist = re.sub(r"[^a-z0-9]+", " ", str(work.artist or "").casefold()).strip()
    if artist in {"", "unknown artist", "anonymous artist", "various artists"}:
        artist = ""
    generic_title = title in {"", "untitled", "untitled work", "unknown", "no title"}
    image_key = _normalise_image_url(work.image_url) or str(work.id) if generic_title else ""
    return title, artist, image_key


def _same_work_identity(left: Artwork, right: Artwork) -> bool:
    left_title, left_artist, left_image = _work_identity(left)
    right_title, right_artist, right_image = _work_identity(right)
    return bool(left_title and left_title == right_title and left_artist == right_artist and left_image == right_image)


def _matches_query(work: Artwork, query: str) -> bool:
    haystack = " ".join(part for part in [work.title, work.artist, work.medium, work.description] if part).lower()
    tokens = [token for token in query.lower().split() if len(token) > 3 and token not in {"more:pagemap:metatags-og_type:artwork"}]
    return not tokens or any(token in haystack for token in tokens)


def commercial_providers() -> list[ArtworkProvider]:
    # Artsy is used only with current partner credentials; Artlogic is feed-based.
    return [ArtMatchProvider(), RookleysProvider(), ArtsyProvider(), ArtlogicProvider(), Collect24Provider()]


def institutional_providers() -> list[ArtworkProvider]:
    """Return the supported North American museum sources.

    This build has no validated Canadian provider that consistently returns
    image-bearing artwork records. Until one is integrated, use US museums as
    the fallback; never query museums outside Canada/United States.
    """
    # No Canadian connector is enabled until it can reliably return a direct,
    # image-bearing artwork result. Use US museums as the approved fallback.
    return [ArtInstituteProvider(), MetProvider(), ClevelandProvider()]
