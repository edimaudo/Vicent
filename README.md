# Vincent | Art Concierge

> **Build update — 29 October 2026**
> Signed-out visitors are redirected to sign-in before accessing Dashboard, Concierge, Saved ArtWorks, results, or taste settings. Signing in from the landing page opens Concierge, and the demo profile is named James. PayPal Sandbox uses Vercel credentials plus a built-in CAD $1.00 test checkout; no PayPal catalog variable is required. See `CHANGELOG.md` for details.

A user-centric art concierge that combines broader cultural taste signals from Qloo with artwork research across commercial and institutional sources.

## The five Concierge options

- **Explore art** — discover unexpected directions; optionally enable **Use my saved taste** to build on saved artworks, profile notes, likes/avoids, and recent ideas. Personalised Explore searches only commercial inventory explicitly available to acquire.
- **Find art** — search for a specific artist, artwork, style, subject, or medium.
- **Curate a collection** — assemble a coherent set using commercial works explicitly available to acquire.
- **Buy art** — search public Canadian and U.S. art-shop catalogues for listings with a price, image, and seller page; final availability is reconfirmed at the seller.
- **Learn about art** — generate an educational guide from a required written topic. The image-inspiration uploader is hidden in this mode; enter the artwork title, artist, movement, technique, or idea. Learn does not run artwork catalogue searches or display artwork recommendation cards.

## Concierge intelligence

The concierge is implemented as native Python orchestration rather than a graph framework. Every request begins with a catalogue search based on the written brief and explicit preferences. When images are attached to Explore, Find, or Curate, Vincent completes that written-request search first, then runs required image interpretation and a separate image-directed catalogue search before combining and ranking the candidates. Learn is text-only and ignores image payloads. Image-attached search requests receive a longer API deadline for their sequential phases; image analysis is not optional enrichment in those search modes. Qloo entity/insight enrichment remains best-effort. Artwork selection uses deterministic fit scoring, and hard constraints such as acquisition status and budget are enforced in Python.

Qloo usage follows the hackathon guidance: `/search` resolves known positive cultural references and `/v2/insights` supplies related artist entities and explainability; legacy `/recommendations` and `/recs` are not used. Qloo is a cultural-intelligence layer, not an artwork catalogue: actual works and image URLs always come from the artwork providers below.

To keep personalization meaningful, Vincent does not send “things to avoid” or generic service prompts as positive Qloo interests. Visual subjects such as birds go to the image-analysis and artwork-search path, rather than being treated as unrelated cross-domain culture entities. When the response budget permits, an optional Gemini planning call translates Qloo's associations into visual-art query directions; those directions influence candidate ranking only when the artwork's own title, artist, medium, or description supports the connection. This bridge is best-effort and never delays the catalogue response.

## Architecture

The application uses a **native Python orchestration layer** with explicit tool-like service boundaries. No workflow framework is required.

For Explore, Find, and Curate, the concierge loop is:

1. Search artwork catalogues using the written brief and explicit art preferences.
2. If no inspiration images were attached, rank the written-search pool within the normal text-only deadline.
3. If images were attached, wait for the written search to finish, interpret the images with Gemini, and run a separate search using image-derived terms. If required image analysis cannot complete, return an explicit error rather than text-only recommendations.
4. Resolve positive cultural references using Qloo `/search` followed by `/v2/insights`; where available, use Qloo-derived directions as additional metadata-supported ranking signals.
5. Combine the written and, when attached, image-informed search pools, rank image-bearing works with deterministic fit scoring, enforce budget/acquisition rules, and return up to three visible cards. **Curate a collection, personalized Explore art, and Buy art search commercial seller inventory only** and show only listings explicitly marked available to acquire; they never fill gaps with museum records.

Learn follows a separate path: it requires a written topic, creates a learning guide, and makes no catalogue calls or artwork recommendations. The image-inspiration uploader is hidden in Learn, and direct or stale image payloads are ignored so a vision service error cannot block the educational output.

A slow optional enrichment is skipped rather than blocking the result. The API reports `diagnostics.duration_ms` to make live latency measurable.

The Qloo implementation does not use the unsupported `/recommendations` or `/recs` endpoints.

## Artwork providers

### Canadian-first commercial sources

**Canadian shops:** **ArtMatch** (`ARTMATCH_LISTING_URL`) and **Rookleys Canadian Art Gallery** (`ROOKLEYS_LISTING_URL`) are integrated from their public listing pages. The parser retains entries with a concrete price, usable image, seller page, and no sold/unavailable signal.

**U.S. online art shops:** **UGallery** (`https://www.ugallery.com/collections/all-artworks`) and **Saatchi Art** (`https://www.saatchiart.com/all`) are integrated from their public catalogues. Neither requires a partner API key or private feed. The U.S. selector means the app searches these U.S.-based online storefronts; artwork artists may be international. Seller-side purchases open on the shop website and are tracked as `EXTERNAL_PENDING`; Vincent does not claim that a redirect confirms a completed order. Always reconfirm price, availability, shipping, and currency with the seller.

### Commercial

Vincent's default Buy search uses public Canadian and U.S. shop catalogues, with no paid partner API keys or private gallery feeds. Canada searches **ArtMatch** and **Rookleys Canadian Art Gallery**; United States searches **UGallery** and **Saatchi Art**. Links open the seller's own checkout, and prices and availability must be reconfirmed with the seller.

### Collection management and artwork comparison

- Users can create up to 30 named collections, each holding up to 100 artworks. Add works from recommendation cards or Saved ArtWorks, remove individual works, and delete collections. Collection contents are persisted with registered accounts in the database and locally in IndexedDB for the James demo account.
- Users can select two or three artworks and compare artist, asking price/currency, medium, dimensions, year, seller/source, availability, description, and the original seller listing. Comparison selections are browser-local and are not a separate saved collection.
- Collection totals are shown only when the listed currencies can be combined meaningfully. Mixed-currency collections are labelled rather than incorrectly summed.

## Artwork and seller alerts

- Users can watch an individual commercial artwork for a detected listing price change, or follow one of the four public shops (ArtMatch, Rookleys Canadian Art Gallery, UGallery, or Saatchi Art) for new listings.
- Alert checks are user-triggered from Dashboard → Alerts and query the shop's publicly visible catalogue. The first successful seller check establishes the baseline; it does not alert on the shop's existing catalogue. A missing artwork result is not treated as proof that the work sold.
- Updates are shown in Vincent's in-app Alerts panel. This MVP does not send email/push notifications or run scheduled background checks. It does not claim a shop has shipped an item based on a listing update.
- Demo collections and alerts are stored locally in the current browser. Registered-account collections and alerts are stored in PostgreSQL through `DATABASE_URL`.

## Account and purchasing

Email/password accounts use server-side sessions with an HttpOnly cookie. For an MVP walkthrough, the sign-in page pre-fills a demo account (`demo@vincent.art` / `vincent-demo-2026` by default, with the display name James), which works without a database. Demo profile edits and saved artwork remain in the current browser; they are not private or shared across devices. Configure `DATABASE_URL` for persistent registered accounts, profiles, saved artworks, recent requests, and order history. For production use, configure managed PostgreSQL through `DATABASE_URL`; local SQLite is supported for tests only. Passwords for registered accounts are stored as scrypt hashes. Configure `VINCENT_DEMO_EMAIL`, `VINCENT_DEMO_PASSWORD`, and a strong `VINCENT_DEMO_SESSION_SECRET` before deploying a demo-enabled instance; the signing key is generated per process when the secret is omitted.

Purchasing supports seller redirection, Stripe Checkout catalog entries, and PayPal Orders v2 plus PayPal invoicing. PayPal's REST calls run server-side: the default mode is `sandbox`, with API base `https://api-m.sandbox.paypal.com`; the hosted buyer approval page is on `sandbox.paypal.com`. Configure `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_MODE=sandbox`, `PAYPAL_REGION=CA`, and `PAYPAL_LOCALE=en-CA` in Vercel. Never put the client secret in browser code. The sandbox buyer email/password are used manually on PayPal's hosted approval page and must not be stored in Vercel or sent to Vincent. Set `VINCENT_BASE_URL` to the public HTTPS Vercel deployment URL so PayPal returns to the correct app.

Seller listings use their own checkout by default. PayPal credentials alone enable the connection; no custom catalogue variable is required. PayPal's Sandbox does expose shipment-tracking endpoints for captured Orders v2 transactions, but those require Vincent to own the PayPal transaction and the seller to provide shipment details. The current Buy flow redirects to third-party seller checkout, so those seller-owned orders are not visible to Vincent's PayPal account; external purchase tracking is therefore unchanged and is not inferred from the redirect. When `PAYPAL_MODE=sandbox` and credentials are configured, the Dashboard → Purchases section exposes a fixed CAD $1.00 Sandbox test checkout. This lets you verify order creation, PayPal buyer approval, capture, and payment-status reporting without charging for a third-party artwork. Real artwork payments remain disabled unless seller authorization, approved fixed pricing, and fulfilment responsibilities are deliberately configured in server code. The demo account can complete a Sandbox test without a database; short-lived signed HttpOnly cookies bind demo orders to the signed-in session, while registered-user order history requires `DATABASE_URL`. PayPal invoice buttons remain disabled until persistent order storage is configured. Optional PayPal invoices also require `PAYPAL_INVOICE_EMAIL`, which is the merchant/invoicer email, not the Sandbox buyer login. Live transactions require deliberate production credentials and seller agreements.


#### Vercel Sandbox checklist

Add these under **Project → Settings → Environment Variables**, then redeploy:
For new Vincent deployments, use the `VINCENT_*` names below. Previously configured variable aliases are still accepted as a fallback, so existing deployments do not break.


| Variable | Value |
| --- | --- |
| `PAYPAL_CLIENT_ID` | Client ID from the PayPal Developer Dashboard's **Sandbox** app |
| `PAYPAL_CLIENT_SECRET` | Secret for that same Sandbox app; server-side only |
| `PAYPAL_MODE` | `sandbox` |
| `PAYPAL_REGION` | `CA` |
| `PAYPAL_LOCALE` | `en-CA` |
| `VINCENT_BASE_URL` | Public HTTPS URL of this Vincent deployment, without a trailing slash |
| `VINCENT_DEMO_SESSION_SECRET` | Stable, randomly generated secret (keep the same value across serverless invocations) |

No PayPal catalogue variable is required for the Sandbox integration. Use the built-in CAD $1.00 test checkout in Dashboard → Purchases to confirm PayPal is connected. Actual gallery artwork remains on the seller's own checkout unless Vincent is explicitly authorized to collect payment for that seller and the artwork has a server-approved fixed price.

The Sandbox buyer account is different from the Sandbox app's API credentials. At checkout, PayPal opens its hosted Sandbox approval page and the tester signs in there with the test buyer account. Do **not** add the buyer's email/password to Vercel; Vincent does not need those credentials and must never automate a buyer login. The business/seller Sandbox account can be used separately to view the test transaction. `DATABASE_URL` is optional for a James/demo Sandbox checkout; registered-user order history is database-backed. Optional invoice support is separate and needs `PAYPAL_INVOICE_EMAIL` for the business invoicer.

### Institutional

The MVP includes adapters for:

- Art Institute of Chicago (United States)
- The Metropolitan Museum of Art (United States)
- Cleveland Museum of Art (United States)

Institutional works are marked **NOT FOR SALE** and are used for discovery/context rather than purchase inventory.

## UI principles

The Concierge page uses a stable **Your art concierge** heading, a concise page-level instruction, a visible **What would you like to do?** selector, and a separate service-specific description. The selected service is not repeated as a second page title.


- Inter typography
- Light and dark themes
- Small / Medium / Large font scaling
- Responsive layout
- Keyboard-accessible controls
- Semantic form structure
- Editorial, gallery-like presentation rather than a chatbot interface

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set QLOO_API_KEY
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000`.


## Interface

The application is organized as separate screens rather than a single-page intake form:

- `/` — persistent home/workspace
- `/concierge` — conversational request workspace
- `/results` — researched artwork results and refinement
- `/saved` — saved works
- `/taste` — persistent taste references

The frontend keeps taste, saved works, recent requests, the latest result, purchase checkout references, and UI preferences in IndexedDB. Inspiration images are held in memory while composing a request, then passed through a one-time `sessionStorage` handoff and removed as soon as the results page consumes it. Vincent does not use `localStorage` for persistent app data.

## Persistent user relationship

The demo account lets reviewers explore account-aware navigation without configuring a database. In demo mode, Vincent keeps taste references, saved works, and recent requests in browser storage; shared demo credentials are not private and demo activity is not persisted across devices. Configure `DATABASE_URL` to enable persistent registered accounts and cross-device account storage. The dashboard resumes prior requests by restoring the original brief into the concierge workspace.

## Commercial acquisition and payments

Vincent treats commercial availability and payment as separate concerns. Artwork sources can expose a purchase/enquiry URL, which Vincent opens directly for third-party inventory. Vincent Checkout is enabled only for server-configured artwork IDs in `VINCENT_STRIPE_CHECKOUT_CATALOG`.

Stripe Checkout is used for one-time payments. For a third-party gallery or seller, configure a Stripe connected account and map the artwork ID to both a Stripe Price and the seller's connected account. Vincent then creates a destination charge so the customer pays Vincent and Stripe transfers the seller portion to the connected account. This configuration is appropriate only when Vincent is operating the marketplace/merchant flow and has the required seller onboarding and commercial agreements.

Required variables for hosted checkout:

```text
STRIPE_SECRET_KEY=...
STRIPE_WEBHOOK_SECRET=...
VINCENT_BASE_URL=https://your-domain.example
VINCENT_STRIPE_CHECKOUT_CATALOG={"vincent-direct:123":{"price_id":"price_..."}}
```

The `/api/stripe/webhook` endpoint verifies Stripe signatures and recognizes `checkout.session.completed`. Durable order/fulfillment storage should be added when the seller and fulfillment workflow are finalized.


## Product UX structure

Vincent uses the Dashboard as the persistent home for returning users. The user's taste profile is managed inside the Dashboard rather than as a separate product destination. `/taste` redirects to `/dashboard#taste` for backwards compatibility.

Internal navigation is Dashboard, Concierge, and Saved. The landing page intentionally does not display product navigation.

## Service diagnostics

`GET /api/diagnostics` reports whether Qloo, Gemini, commercial artwork sources, and Stripe are configured. It never returns secret values.

Qloo hackathon requests are locked to `https://hackathon.api.qloo.com` so a hackathon API key cannot accidentally be sent to the production or staging base URL.

## Concierge results troubleshooting

The `/results` template must include the `id="resultsPage"` root element because the browser app checks for that root before it submits the pending request to `/api/concierge`. The request is stored before navigation and is only cleared after the page and payload are available.

For deployment troubleshooting:

- `GET /api/diagnostics` reports which keys/providers are configured and the Qloo base URL without revealing secrets.
- `GET /api/diagnostics/live` probes Qloo Search + Insights, Gemini request interpretation, and the public Art Institute, Met, and Cleveland collection APIs. It reports status/counts only.
- Set `GEMINI_MODEL=gemini-3.8-flash` for new deployments. If a `GEMINI_MODEL` override is already set in Vercel, update that environment variable too; changing `.env.example` does not override an existing deployment variable.

## Artwork images, result limits, and responsive search

- Discover/Find results are filtered to records with a usable HTTPS image URL before they are presented as recommendation cards.
- The Art Institute of Chicago adapter builds image URLs from its documented IIIF `image_id` values and requests public-domain works for image use.
- The Met adapter requests image-bearing objects and resolves their `primaryImageSmall`/`primaryImage` URLs.
- The Cleveland Museum of Art adapter uses the image asset URLs exposed in its open-access response.
- Museum searches are restricted to North America. This build has no validated Canadian provider that consistently returns image-bearing artwork records, so the current fallback uses only US museum collections: Art Institute of Chicago, The Met, and Cleveland Museum of Art. Non-North-American museum sources and Smithsonian have been removed.
- Each enabled provider is queried in parallel within each search phase, with a 3.25-second per-provider ceiling. Text-only requests retain a 4.6-second API deadline. Requests with attached images receive a 17.5-second deadline for the required sequence of written search, Gemini image interpretation, and a separate visual search.
- The current UI and API return at most three visible cards in total, including any **Not for sale** works. The count above the cards reports visible recommendations, not the number of candidates researched. `ARTWORK_RESULT_LIMIT` can lower that limit but cannot raise it above three.
- CSS and JavaScript asset URLs include a version query so deployed browsers do not keep using stale copies of the results cap or layout stylesheet.
- For allowlisted museum and known gallery-CDN hosts, Vincent serves image bytes through a same-origin proxy. If a thumbnail fails, the browser tries the original artwork URL first, then an exact-work recovery search across public museum collections, and only then the official artwork page's image preview so generic Open Graph logos are less likely to replace the artwork. The preview endpoint tests multiple image candidates from the source page rather than stopping at the first failed candidate.
- HTTP thumbnail links are upgraded to HTTPS to avoid browser mixed-content blocking. The image proxy revalidates every redirect, recognizes valid image bytes even when a provider uses a generic binary content type, and caps each image response at 8 MB. Source-page image extraction remains restricted to trusted collection/gallery pages and approved image hosts.
- Artwork images use `object-fit: contain` so the artwork is not cropped to fill the card. Image responses are signature-checked and decoded as raster files before proxying; HTML pages, SVG logos, malformed image payloads, and tiny icon-sized files are rejected. Recommendations are selected only when a concrete image URL is present. Failed thumbnails retry the original source, an exact-work recovery search across public museum collections, and finally the source-page image preview; cards that still have no renderable image are removed rather than displayed without artwork.
- The API returns `diagnostics.duration_ms`; this is the application-side timing metric. End-to-end performance still depends on deployment cold starts and source availability.


## Visual inspiration images

- Users can attach up to five JPEG, PNG, or WebP images to a single Concierge search. The browser compresses them before submission.
- Inspiration images are request-only: they are kept in page memory while composing the brief, passed through a one-time `sessionStorage` handoff for navigation, and consumed by the results page before the API request. The handoff is removed before submission; image bytes are stripped from result history and recent-search data.
- The IndexedDB v3 migration removes the old persistent inspiration-image store and selected-image setting. Images cannot be reused from a gallery or recovered for a later search; they must be uploaded again when needed.
- `/api/concierge` receives only the images attached to that submitted request. With an image attached, Vincent first completes the written-request search, then requires Gemini vision to derive visual descriptions and search terms, and finally performs a separate visual search. If Gemini is unconfigured, fails, times out, or produces no usable visual terms, the request fails explicitly instead of returning text-only recommendations. Remove the image to intentionally run a text-only search.
- Image bytes are sent to the configured model provider for analysis when a request includes them. They are not intentionally retained in Vincent server-side storage by this implementation.

## Learn about art

- Learn requests return an educational guide only. They do not run catalogue searches or show artwork recommendation cards.
- When an artist is named, the guide includes an artist overview and a text-only list of notable works. Recognized artists use curated examples; other artists can use structured generation when Gemini is configured, with curated topic guidance as the fallback.
- The image uploader is hidden in Learn. The user must enter a written topic; stale or direct image payloads are ignored, so image-analysis configuration does not block the learning guide.
- The guide is an educational starting point, not a substitute for a museum or artist-foundation record. Verify specific titles, dates, media, and attributions against an authoritative source.

## Checkout status

- Vincent's success-return page does not assume that a browser redirect means payment succeeded. It queries `/api/checkout/status`, which retrieves the Checkout Session from Stripe using the server-side secret key.
- The UI distinguishes confirmed payment, checkout completed but unpaid/processing, expired checkout, and unconfirmed payment. It shows only a minimal session/artwork reference, payment status, and total.
- For signed-in users with `DATABASE_URL` configured, Vincent saves checkout/order references, artwork/seller details, totals, and payment status in the account database. The Dashboard's **Purchases** section lists recent orders/invoices and supports refreshing payment status from the corresponding provider.
- Seller-site redirects are recorded as `EXTERNAL_PENDING`; they do not confirm a completed purchase. Vincent does not currently track shipping, delivery, fulfilment, returns, or refunds. These require seller/fulfilment integrations and explicit order-state events.

## Buy source configuration and diagnostics

Curate a collection, Explore art with **Use my saved taste** enabled, and Buy art search **commercial inventory only**. Museum collection APIs can supply records for general Explore and Find, but those records are not acquisition listings and are not queried by purchase-oriented modes. Learn about art generates a written-topic guide without querying collection APIs. The public shop-source readiness and result counts are reported by `GET /api/diagnostics`.

- Stripe checkout also requires a server-side secret and entries in `VINCENT_STRIPE_CHECKOUT_CATALOG` for artworks enabled for Vincent checkout. External “Buy / enquire” links can only be shown when a commercial source provides a seller page.

No commercial inventory provider can be treated as connected simply because museum image APIs are available. Keep provider failures in backend diagnostics/logs; do not expose upstream HTTP errors in the customer-facing Buy panel.


### Buy inventory requirements

Curate a collection, personalised Explore, and Buy art are limited to publicly accessible commercial listings from four no-key sources: **ArtMatch** and **Rookleys Canadian Art Gallery** (Canada), plus **UGallery** and **Saatchi Art** (United States). They do not use institutional records as purchasable inventory and do not require partner API keys. The HTML parsers retain listings with a price, usable image, seller page, and no sold/unavailable signal. Generic Buy prompts are treated as broad browsing requests, not strict title keywords; specific artist/title searches are filtered when matching metadata is available. The Buy page always provides direct shop links: Canada shows ArtMatch and Rookleys, United States shows UGallery and Saatchi Art, and Both shows all four. **The search region refers to the shop catalogue searched, not the artist's nationality.** When the user enables **Use my saved taste** in Explore art, Vincent uses saved artwork metadata, saved profile notes, and recent ideas to guide provider directions and relevance scoring. Older `intent=taste` links remain supported and open Explore art with personalization enabled.

### Learn about art

Learn requires a written topic, never returns artwork recommendation cards, and never searches the artwork catalogues. When a named artist is recognised, the guide includes a short artist overview and a text-only list of notable works. Additional artists are handled through structured guide generation when the Gemini service is configured; curated artist profiles remain available as a fallback. Verify exact titles, dates, and attributions against reliable museum or artist-foundation records.

### Database-backed recommendation context

For signed-in users, Vincent merges the persisted taste profile into current requests. When **Use my saved taste** is enabled, the server also loads saved artwork references and prior search ideas from the database, filters out the current prompt, and uses this context to guide personalization. Saved works, prior requests, user profiles, and purchase history are scoped to the account; account/order persistence requires a configured `DATABASE_URL`.


### Search region

Concierge has a region selector for **Canada**, **United States**, or **Both Canada and the US**. Canada is preselected. The selected market is saved as a lightweight browser preference and applied to provider selection and returned artwork records. Canada searches ArtMatch and Rookleys; the United States searches UGallery and Saatchi Art; Both searches all four shop catalogues. Buy never substitutes museum records for shop inventory. Learn hides the region control because it does not search artwork catalogues.

PayPal defaults to sandbox mode through `PAYPAL_MODE=sandbox`. `GET /api/diagnostics` reports PayPal credential readiness, sandbox/live mode, region/locale, and the number of valid approved catalog rows, without returning secrets. Payment-processor names and sandbox/live status are exposed in the Purchases/payment section and checkout status pages, not general art discovery cards.
