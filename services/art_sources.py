from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from decimal import Decimal
import re
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin, urlparse

import httpx

from .image_proxy import is_allowed_detail_url, is_allowed_image_url
from .config import (
    ARTWORK_RESULT_LIMIT,
    REQUEST_TIMEOUT,
    ARTMATCH_LISTING_URL,
    ROOKLEYS_LISTING_URL,
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




class CanadianGalleryHtmlProvider:
    """Base parser for public online-gallery inventory with public sale signals."""

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
        # Generic Buy prompts describe the task, not necessarily the words sellers
        # use in artwork titles. Treat those terms as intent words, not strict tags.
        stop_words = {
            "art", "arts", "artwork", "artworks", "painting", "paintings", "original",
            "canadian", "available", "availability", "purchase", "purchasable", "buy",
            "buying", "find", "search", "works", "work", "contemporary", "modern",
            "sale", "sold", "for", "the", "and", "with", "from", "under", "over",
            "limited", "edition", "print", "prints", "gallery", "shop", "shops",
            "fine", "artist", "artists", "artistic", "please", "show", "me", "some",
        }
        query_terms = [
            term for term in re.findall(r"[a-z0-9]+", normalized_query)
            if len(term) > 2 and term not in stop_words
        ]
        if query_terms:
            scored = []
            for work in candidates:
                haystack = " ".join([work.title, work.artist, work.medium or "", work.description or ""]).casefold()
                score = sum(1 for term in query_terms if term in haystack)
                if score:
                    scored.append((score, work))
            # For a specific artist/title, do not silently replace zero matches with
            # unrelated artwork. The Buy screen still offers direct shop links.
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
    """Rookleys Canadian Art Gallery's public catalogue; seller handles checkout."""
    name = "Rookleys Canadian Art Gallery"
    listing_url = ROOKLEYS_LISTING_URL


class USGalleryHtmlProvider(CanadianGalleryHtmlProvider):
    """Base class for public U.S. online art marketplace catalogues."""
    country_code = "US"


class UGalleryProvider(USGalleryHtmlProvider):
    """Public UGallery catalogue; no partner key or private feed required."""
    name = "UGallery"
    listing_url = "https://www.ugallery.com/collections/all-artworks"


class SaatchiArtProvider(USGalleryHtmlProvider):
    """Public Saatchi Art catalogue; no partner key or private feed required."""
    name = "Saatchi Art"
    listing_url = "https://www.saatchiart.com/all"


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


def _parse_price(text: str, market: str = "CA") -> tuple[float | None, str | None]:
    # Gallery prices are scraped only from the listing card; no prices are inferred.
    match = re.search(r"(?:(CAD|CAN\$|C\$|USD|US\$|\$)\s*)(\d[\d,]*(?:\.\d{1,2})?)", text, re.I)
    if not match:
        return None, None
    try:
        price = float(Decimal(match.group(2).replace(",", "")))
    except Exception:
        return None, None
    currency_text = (match.group(1) or "").upper()
    currency = "CAD" if currency_text in {"CAD", "CAN$", "C$"} else ("USD" if currency_text in {"USD", "US$"} else ("USD" if market == "US" else "CAD"))
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
        price, currency = _parse_price(card_text, market)
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
        elif source in {"UGallery", "Saatchi Art"}:
            # These are public for-sale catalogue pages: a current price and a linked
            # artwork detail page are the listing-level sale signals. Final status is
            # reconfirmed with the seller at checkout.
            available = price is not None and not sold
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
    """Public Canadian and U.S. art shop catalogues; no partner keys or private feeds required."""
    return [ArtMatchProvider(), RookleysProvider(), UGalleryProvider(), SaatchiArtProvider()]


def institutional_providers() -> list[ArtworkProvider]:
    """Return the supported North American museum sources.

    This build has no validated Canadian provider that consistently returns
    image-bearing artwork records. Until one is integrated, use US museums as
    the fallback; never query museums outside Canada/United States.
    """
    # No Canadian connector is enabled until it can reliably return a direct,
    # image-bearing artwork result. Use US museums as the approved fallback.
    return [ArtInstituteProvider(), MetProvider(), ClevelandProvider()]
