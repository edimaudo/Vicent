# Changelog

## 2026-10-29 — Canadian Buy inventory correction

- Restricted the active Buy inventory registry to public Canadian shop pages from ArtMatch and Rookleys; removed the Artsy, Artlogic-feed, and Collect24 API-backed provider integrations and their environment-variable setup.
- Fixed generic purchase prompts such as “contemporary art for sale” so the intent words do not filter out every listing by exact title/artist matching.
- Made Buy summaries reflect the actual result count or shop retrieval state instead of always claiming the same search succeeded.
- Added direct shop-browse cards for ArtMatch and Rookleys when users want to browse the current seller catalogue or automated listing retrieval returns no matches.
- Added regression coverage for broad Buy queries, active public-shop sources, removed API configuration, and cache-busted frontend assets.

## 2026-10-28 — PayPal Sandbox configuration and demo checkout

- Standardized Canadian sandbox configuration with `PAYPAL_MODE=sandbox`, `PAYPAL_REGION=CA`, and `PAYPAL_LOCALE=en-CA`; REST requests use the official sandbox API host and order approval uses PayPal's hosted sandbox page.
- Updated PayPal Orders v2 to use the current `payment_source.paypal.experience_context` shape, including the Canadian locale and return/cancel URLs.
- Added a fixed CAD $1.00 Sandbox test checkout on Dashboard → Purchases. It validates order creation, buyer approval, capture, and payment verification without requiring a catalogue environment variable.
- Real third-party artwork still uses the seller's checkout by default; Vincent-managed art charges require explicit seller authorization and fixed pricing in server code.
- Enabled demo-account PayPal order creation/capture without a database using short-lived signed HttpOnly cookies. Registered-account orders remain database-backed; client-side purchase history is updated after server-verified capture.
- Added PayPal mode/region/locale and Sandbox test-checkout readiness to safe diagnostics and documented the Vercel variables. Sandbox buyer login credentials are intentionally not consumed or stored by Vincent; buyers sign in on PayPal's hosted Sandbox page.
- Removed PayPal inventory catalog variables from standard deployment setup. Added regression tests for fixed test-item amount/currency, Sandbox-only enforcement, demo checkout ownership, and purchase verification without a database.

### Verification

- `pytest -q` — 143 passed.
- `python -m compileall -q .` — passed.
- `node --check static/js/app.js` — passed.
- Real PayPal Sandbox transaction remains unverified until deployment credentials are configured in Vercel and a buyer approves the test payment.

## 2026-10-28 — Concierge as the post-login destination and named demo profile

- Changed the sign-in default destination to `/concierge`, including sign-in links from the landing page. Explicit protected-page return destinations are still preserved.
- A landing-page return target (`/`) is normalized to `/concierge` so a successful sign-in does not send the user back to the landing page.
- Changed the demo display name from the placeholder `Demo` to `James`, including fallback behavior when an old `VINCENT_DEMO_NAME=Demo` setting remains configured.
- Added regression coverage for the default destination, landing-page sign-in link, and demo display name.

### Verification

- `pytest -q` — 134 passed.
- `node --check static/js/app.js` — passed.
- `python -m compileall -q .` — passed.

---

## 2026-10-27 — Require authentication for the workspace

- Added server-side access guards for Dashboard, Concierge, Saved ArtWorks, results, and taste settings. Signed-out visitors are redirected to sign-in with their requested destination preserved, including Concierge mode and query parameters.
- Hid primary workspace navigation when no user is signed in and made the Vincent brand link return signed-out users to the public landing page.
- Protected the Concierge API as well as its page; signed-out API requests now receive `401 AuthenticationRequired` rather than being able to run art searches directly.
- Added regression tests for direct-route access, return-path preservation, hidden guest navigation, and API authentication. Updated existing Concierge tests to sign in through the MVP demo account.

### Verification

- `pytest -q` — 134 passed.
- `node --check static/js/app.js` — passed.
- `python -m compileall -q .` — passed.

---

## 2026-10-27 — Sign-in and Concierge layout refinements

- Removed the extra explanatory sentence and demo-access paragraph from the sign-in form so the page shows only its title and sign-in controls.
- Removed the Sign in account link from the sign-in header while keeping the light/dark toggle and text-size selector aligned at the far right.
- Placed **What would you like to do?** and **Where should we look?** in a two-column selector row on wider screens, stacking the controls on narrow screens for readability.
- Added regression coverage for the clean sign-in page, right-aligned header controls, and side-by-side Concierge selectors.

### Verification

- `pytest -q` — 131 passed.
- `node --check static/js/app.js` — passed.
- `python -m compileall -q .` — passed.

---

## 2026-10-26 — Landing page, sign-in, and MVP demo account

- Added a Buy art card to **What can I help you with?** and updated the landing-page description to explicitly include buying art from connected galleries.
- The **Ask my concierge** call to action routes signed-out visitors to sign-in with a return path to Concierge, while signed-in visitors go directly to Concierge.
- Header account state now displays **Welcome, [user]** and a **Sign out** link after sign-in. Added a non-JavaScript sign-out route as a fallback.
- Simplified the sign-in page: removed the database availability warning and the account-creation prompt, and hid Dashboard, Concierge, and Saved ArtWorks navigation from the auth page.
- Added a prefilled demo account that works without `DATABASE_URL`. Demo session cookies are signed; demo profile and saved-artwork activity remain browser-local and are not shared or private. Registered persistent accounts still require `DATABASE_URL`.
- Aligned the **Where should we look?** label and region dropdown on the same row on desktop and most mobile widths.
- Added regression tests for demo sign-in without a database, authenticated landing-page state, CTA routing, and auth-page simplification.

### Verification

- `pytest -q` — 129 passed.
- `node --check static/js/app.js` — passed.
- `python -m compileall -q .` — passed.

---

## 2026-10-25 — Rebrand to Vincent

- Rebranded visible application copy, page titles, metadata, authentication, dashboard, search errors, image-processing messages, and payment branding from ArtCo to Vincent.
- Updated PayPal order brand and default invoice business/person names to Vincent.
- Changed the browser theme preference key to `vincentThemePreference` with a migration fallback from `artcoThemePreference`.
- Preserved existing deployment variable aliases and database table names for compatibility; the later Vincent-prefixed environment variables are now the documented names for new deployments.

---

## 2026-10-25 — Region label and current integration notes

- The region selector displays **Canada** (without a parenthetical); Canada remains preselected.
- Added a regression assertion that locks in the exact user-facing option label and bumped static asset versions.
- Corrected documentation for the current Learn flow and account-backed order history; clarified that external seller redirects remain pending and delivery/fulfilment are not tracked.

## 2026-10-25 — Region selector label cleanup

- The selected market label is simply **Canada**. Canada remains the default value, but the dropdown no longer shows “(default)” as part of the option label.
- Added a regression assertion and bumped static asset versions so clients refresh the current Concierge UI.

## 2026-10-22 — Artist-aware Learn guide

- Learn about art now generates an artist overview and a text-only list of notable works when an artist is named.
- Known artist profiles provide curated work examples; other artists use structured language-model guide generation when configured.
- Major works render as a concise text list, not artwork recommendation cards. Learn still performs no catalogue search and returns no recommended artwork cards.

## 2026-10-21 — Learn topic handling and Concierge validation recovery

- Hide the image-inspiration uploader when **Learn about art** is selected. Its textarea now directs users to enter an artist, artwork title, movement, technique, or idea, and Learn requires a written topic even when a stale client submits image data.
- Make Learn return its educational guide without catalogue recommendations even if old/direct API requests contain images. Vision configuration or image-analysis failures can no longer prevent a guide from being returned in Learn mode.
- Fix stuck browser validation by clearing stale custom-validity errors as the user types, refreshing the live textarea value at submission time, and keeping request submission under application-level validation control. The selected dropdown option now determines the request mode rather than prompt wording accidentally changing it.
- Added regression tests for the hidden Learn uploader, no-image-analysis learning flow, dropdown-driven intent, and input-validation recovery. Bumped the JavaScript cache-busting version.

### Verification

- `pytest -q` — 84 passed.
- `node --check static/js/app.js` — passed.
- `python -m compileall -q .` — passed.
- ZIP integrity — checked.
- Live Gemini and deployed-browser behavior were not tested in this environment.

---

## 2026-10-21 — Concierge spacing and Saved ArtWorks card hierarchy

- Reduced vertical whitespace between the Concierge introduction, service selector, request textarea, inspiration uploader, and submit button so the selected service action remains easier to see.
- Narrowed the visual gap between the page intro and “What would you like to do?”.
- Saved ArtWorks now shows the full collection/source name on a dedicated line immediately after the artist, rather than truncating it in the compressed card footer.
- Balanced saved card content with consistent spacing and bottom-aligned actions.

## 2026-10-21 — Concierge spacing, stable theme, and taste-label alignment

- Reduced vertical spacing around the Concierge request area and the visual-inspiration section; shortened the request textarea slightly and tightened the gap before the action button so the selected service action is easier to reach.
- Replaced system-dependent `light-dark()` palette values with explicit light and dark theme tokens. A small synchronous theme bootstrap runs before the stylesheet, and the app persists only the theme preference in `localStorage`, preventing a transient dark paint when navigating between pages. Concierge request payloads and inspiration images remain out of `localStorage`.
- Applied a stronger shared grid and fixed label dimensions to the Dashboard's “Anything else I should know” and “How adventurous should I be?” controls, with responsive behavior on narrow screens.
- Added regression tests for first-paint theme selection, reduced composer spacing, and horizontal taste-label alignment; incremented CSS/JS cache-busting versions.

### Verification

- `pytest -q` — 79 passed.
- `node --check static/js/app.js` — passed.
- `python -m compileall -q .` — passed.
- ZIP integrity — checked.
- Browser rendering with live user preferences and deployed-site behavior were not independently tested in this environment.

---

## 2026-10-20 — clearer Concierge hierarchy

- Kept the page heading static as **Your art concierge**, with one short explanatory subheading that describes the overall interaction rather than repeating the selected service.
- Made the dropdown label visible and action-oriented (**What would you like to do?**), and moved the service selector before optional resumed conversation context.
- Added a concise, dynamically updated description beneath the selected service. The selector value no longer doubles as the page heading.
- Updated the cache-busting asset version and added a regression test for the hierarchy and visible label.

### Verification

- `pytest -q` — 75 passed.
- `node --check static/js/app.js` — passed.
- `python -m compileall -q main.py models services` — passed.
- Live browser rendering and deployed-site behavior were not independently tested.

---

# Vincent Source Package Changelog

## 2026-10-16 — required sequential image search

- Reordered image-attached concierge requests to run a written-brief catalogue search first, followed by Gemini image interpretation and a separate image-directed catalogue search. Visual analysis is no longer launched as optional enrichment before the first search.
- Added a required-image-search failure path. If Gemini is missing, fails, times out, or yields no usable search terms, Vincent returns an explicit service error rather than silently showing text-only recommendations after an image was attached.
- Extended the image-attached API deadline to 17.5 seconds to support the required sequential phases while retaining the 4.6-second deadline for text-only requests.
- Combined results from both catalogue passes and included per-phase search directions and source diagnostics. Visual terms contribute to final relevance ranking.
- Updated the results UI to display API failure details, revised documentation, and bumped static asset versions.

### Verification

- `pytest -q` — 53 passed.
- `node --check static/js/app.js` — passed.
- Python compilation checks — passed.
- Live Gemini image interpretation, live source catalogue responses, and deployed latency remain unverified in this environment.

---

## 2026-10-13 — storage, image recovery, and layout corrections

- Removed `localStorage` use from the application. Profile, saved work, recent history, last result, preferences, selected inspiration IDs, and image files are persisted in IndexedDB. `sessionStorage` is limited to one-tab request navigation/resume state.
- Expanded artwork image recovery beyond Open Graph metadata: the source-page preview endpoint now also inspects approved public museum pages for image markup (`img src`, `srcset`, lazy-loaded image attributes, and image metadata). Candidate image hosts and redirects remain restricted to the image allowlist.
- Added Artsy collection pages to the trusted detail-page fallback allowlist.
- Fixed Artlogic feed mapping so its published image/thumbnail fields are used instead of always setting `image_url` to `None`. Common flat and nested feed image shapes are supported.
- Limited the main recommendation list to three results and the additional not-for-sale list to three results; desktop grids show three cards per row.
- Reduced the Concierge service selector to a 185px maximum width and tightened its padding for better balance.
- Removed the redundant image-storage and upload-limit messages from the Concierge flow.
- Aligned “Things you love” and “Things to avoid” in a fixed two-line helper-text grid so their input rows line up.
- Changed artwork image error recovery to try the same-origin source-page preview before retrying a direct image URL, and stopped suppressing the HTTP referrer for public museum images that rely on it.

## Verification

- `pytest -q test_*.py tests_*.py` — 29 passed.
- `node --check static/js/app.js` — passed.
- `python -m compileall -q main.py models services` — passed.
- ZIP integrity checked before delivery. Live museum/CDN image responses, Gemini vision calls, and the deployed Vercel site have not been independently verified in this environment.


## 2026-10-13 — image reliability, fast results, and balanced workspace layout

- Enforced a three-card total across Recommendations and Not for sale in the agent, API response boundary, and frontend. The secondary section only fills unused slots for Buy requests. The count label now reports visible recommendations rather than the larger research pool.
- Started catalogue providers immediately and ran interpretation, optional vision analysis, and Qloo enrichment concurrently. Removed the second research pass and blocking LLM candidate-review round from the result path; bounded provider calls to 3.25 seconds and return partial enrichment rather than making results wait.
- Added `diagnostics.duration_ms`, a 4.5-second agent target, and a 4.6-second API hard deadline. This is a source-level guard; deployed end-to-end latency still needs measurement.
- Expanded the same-origin image proxy allowlist to common gallery image CDNs, including Artlogic-hosted gallery thumbnails, and made HTTP thumbnail URLs prefer HTTPS to avoid browser mixed-content blocking. The proxy still validates redirects and supports common image file signatures/content types.
- Improved image error recovery: try the original artwork image URL if the proxy fails, then parse the official artwork page; also handle image requests that failed before the error handler was attached. Result images load eagerly because the visible list is now limited to three.
- Standardized internal page heading scale, workspace padding, header alignment, section spacing, and card action sizing. Artwork and saved-art thumbnails use `contain` rather than cropping the image.
- Added versioned CSS/JS URLs to prevent browser caches from continuing to use older result limits or layout styles.

## Verification

- `pytest -q test_*.py tests_*.py` — 39 passed.
- `node --check static/js/app.js` — passed.
- `python -m py_compile main.py services/*.py models/*.py` — passed.
- The source enforces the response deadline and result cap in tests. Live image/CDN delivery and deployed Vercel latency remain unverified in this environment.

## 2026-10-14 — saved similar search, image preview fallback, and cultural relevance

- Wired **More like this** on Saved works to create a new Discover request anchored to the selected artwork, then launch the normal results flow. It no longer only updates taste state when the Saved page has no refinement input.
- Changed artwork image recovery to try the allowed source-page preview before the direct thumbnail, and added the Collect24 public page/image hosts to the restricted preview allowlist. Asset URL versions are bumped to avoid stale JS/CSS.
- Tightened Qloo entity resolution to positive cultural references only: avoid-list entries, raw generic service prompts, and visual art subjects are no longer sent as positive cross-domain taste signals.
- Gave uploaded-image analysis up to 1.1 seconds to contribute searchable visual attributes before the initial catalogue queries, while retaining the existing hard response budget.
- Connected Qloo's optional related-entity output to a non-blocking culture-to-art planning task. When it completes in time, translated directions affect ranking only when their terms match the actual artwork metadata. Query planning does not block the primary result path.
- Made the “What guided the search” summary distinguish user-entered cultural interests from visual inspiration, and stopped showing Qloo-related entity names as if the user had explicitly selected them.

## Verification

- `pytest -q test_*.py tests_*.py` — 43 passed.
- `python tests_agent_smoke.py` — passed, including Qloo-to-art direction translation.
- `python tests_discover_images.py` — passed.
- `node --check static/js/app.js` and Python compilation — passed.
- The GitHub connector denied repository writes, and live image/CDN behavior or deployed latency cannot be claimed as verified from this package alone.


## 2026-10-15 — P0/P1 concierge, learning, and checkout status

- Made **Not for me** a new search action with server-side hard exclusions for the selected artwork ID and avoided artist. Added regression coverage to ensure an excluded work or artist cannot return in the new shortlist.
- Changed inspiration-image handling to request-only. The UI no longer presents a persistent image gallery; the IndexedDB v3 migration removes the old stored-image object store and selected-image setting. Uploaded images are transferred only for the request that uses them and stripped from recent-search and result persistence.
- Improved deterministic relevance scoring so that the user's actual goal and artwork title/medium/description metadata contribute to fit. Added additional intent-specific ranking signals while keeping Buy's purchasability constraints and Curate's artist variety.
- Added a **Learn about art** guide to the existing results screen. It includes a concise overview, key ideas, ways to look closely, terms, questions, and examples drawn from the searched artwork records. Topic-specific content is available for selected artists and art movements; unknown topics use a cautious general framework.
- Changed the Stripe success return page to verify the actual Checkout Session through a new `/api/checkout/status` endpoint before showing payment confirmation. The endpoint returns a minimal status payload and does not expose customer details.
- Added regression tests for exclusions, scoring relevance, the connected learning guide, request-only inspiration storage, Stripe verification, and checkout return status.

### Verification

- `python -m compileall -q .` — passed.
- `node --check static/js/app.js` — passed.
- `python -m pytest -q --disable-warnings --maxfail=1` — 48 passed.
- Tests mock the Stripe Checkout Session request. Live credentials, external provider behavior, and a deployed site were not tested. Durable purchase/fulfilment tracking remains out of scope until a persistent order store and fulfilment source are configured.

## 2026-10-16 — Integrated browser-local purchase tracking

- Built on the full `Vincent_P0_P1_Updated_2026-10-15.zip` source tree, not the earlier updater-only package.
- Records the Stripe Checkout Session and related artwork label in the existing IndexedDB app state before redirecting away from Vincent.
- On the verified Stripe return, updates that same record with checkout status, payment status, total, and currency using the existing `/api/checkout/status` endpoint.
- Added a separate **Purchase tracking** tab on Dashboard with recent checkout references, current payment status, totals when available, and an explicit **Check payment** action that refreshes status directly from Stripe.
- Updated keyboard tab navigation, static asset cache versions, and tests for the six intent-specific ranking/selection paths.
- This is browser-local payment-status tracking, not a cross-device server order ledger. Shipping and fulfilment status are not represented.

### Verification

- `python -m pytest -q --disable-warnings --maxfail=1` — 50 passed.
- `node --check static/js/app.js` — passed.
- `python -m compileall -q .` — passed.
- Stripe calls are mocked in tests. Live Stripe credentials, gallery purchase fulfilment, and deployment behavior remain unverified.


## 2026-10-17 — Purchases, education-only Learn, image recovery, and unsaved taste

- Renamed **Purchase tracking** to **Purchases**, moved it after **Your taste**, and removed browser/Stripe/shipping implementation copy from the customer UI.
- Changed **Learn about art** to return an educational guide only, without artwork catalogue recommendations. If an image is attached, interpretation is required and incorporated into the guide.
- Tightened recommendation eligibility to works with concrete image URLs. Preview recovery now iterates multiple source-page image candidates and can look for the exact artwork in public museum collections before removing a card whose image cannot be loaded.
- Changed the taste editor to maintain an unsaved draft until the user saves. Leaving the editor opens a Save and continue / Discard changes / Keep editing modal, and the text and adventure controls now use equal-width columns with aligned heights.

## 2026-10-17 — Buy-source separation and fresh result rendering

- Buy searches commercial inventory providers only. Museum APIs are excluded from Buy searches and no longer generate irrelevant Met 403 diagnostics in the acquisition path.
- Customer-facing empty states no longer expose connection/configuration errors; provider state remains available from `/api/diagnostics` and server logs.
- `/api/diagnostics` identifies the server-side configuration required for Artsy, Artlogic, Collect24, and Stripe checkout catalog readiness without returning credentials.
- New results runs no longer render the cached last search first. The view clears the previous cards, learning content, taste summary, visual-inspiration details, and secondary list before showing the current request's progress. Missing/invalid request handoffs display a recoverable empty state.


## 2026-10-18 — Buyable inventory and fresh Concierge searches

- Buy results now include only commercial works explicitly marked available to purchase; museum collection works and inquiry-only listings are excluded. Buy results are never padded with non-purchasable works.
- Artlogic feed records must carry an explicit purchase-availability value before they qualify for Buy. Artsy inquiry-only records are not purchase inventory.
- Buy empty states distinguish between no seller inventory configured and no matching purchasable listings.
- New Concierge runs use unique result URLs, clear the previous result from in-memory state, and restart a pending run when a cached Results page is restored with a new request waiting.
- Aligned the two lower Your Taste field labels and their control tops using a fixed shared label row.


## 2026-10-19 — Purchase-only recommendations and taste references

- Changed **Curate**, **Develop my taste**, and **Buy** to query commercial artwork providers only. The agent filters for records explicitly marked available to acquire, and the frontend independently blocks institutional or unavailable records from rendering in these modes. No museum results are used to pad short lists.
- Updated empty states and result metadata for all three purchase-oriented services. If no seller inventory is configured or no purchasable work matches, the app now says so rather than suggesting museum alternatives.
- **Develop my taste** now passes saved artwork metadata, saved profile notes, and recent search ideas as context. That context guides provider queries and contributes to relevance scoring; references are sent only for taste-development requests.
- Added regression tests for provider exclusion, sellable-only selection across all three intents, frontend result guards, and saved-work/idea influence.

### Verification

- `pytest -q` — 71 passed.
- `node --check static/js/app.js` — passed.
- `python -m compileall -q .` — passed.
- Live commercial source credentials and seller inventory were not available for an end-to-end provider check.


## 2026-10-20 — Simplified Concierge service choices

- Consolidated the six visible service choices into five: **Explore art**, **Find art**, **Curate a collection**, **Buy art**, and **Learn about art**. Updated the landing-page cards and service-specific prompts to use the same labels.
- Moved taste development into an opt-in **Use my saved taste** setting under Explore art. When enabled, the request uses saved artworks, profile notes, likes/avoids, and recent ideas to steer search queries and ranking.
- Personalized Explore requests are restricted to commercial works explicitly available to acquire. General Explore remains broader discovery.
- Preserved legacy `intent=taste` links by mapping them to Explore art with personalization enabled.
- Changed the Learn about art prompt guidance so it requests a learning guide, not recommendations.

## 2026-10-21 — Dashboard, Saved ArtWorks, and Concierge refinements

- Hid the Purchases tab and panel from the dashboard while retaining checkout and purchase tracking logic for future reintroduction.
- Renamed the saved navigation/page label to Saved ArtWorks.
- Removed Buy art from the landing service cards and Concierge dropdown, leaving Explore art, Find art, Curate a collection, and Learn about art.
- Put the four landing choices in a four-column desktop layout with responsive tablet and mobile layouts.
- Changed the Concierge submit button label to the first word of the currently selected service and retained its arrow.
- Moved “Add as much or as little detail as you like” into the text area's placeholder and simplified the inspiration-upload helper text.
- Added spacing between the Concierge introduction, service selector, and request composer.
- Kept source names compact in recommendation-card footers, removed duplicate provider names from metadata, and standardized action buttons in an equal two-column grid. Saved ArtWorks now displays the complete source name below the artist without truncation.
- Aligned the two lower taste controls with matching label heights and control dimensions.


## 2026-10-21 — Museum coverage, six recommendations, duplicate and image fixes

- Added National Gallery, London public collection search and credential-gated Smithsonian Open Access search.
- Updated Artsy to use the partner search API and resolve artwork records when valid partner credentials are configured.
- Raised the visible recommendation count from three to six across API, agent and frontend.
- Deduplicated artwork across providers using normalized title and artist identity, not just provider-specific record IDs.
- Reordered image recovery to prefer the source artwork URL and exact-work museum search before source-page metadata.
- Added image payload validation to reject SVG/HTML placeholders and malformed or icon-sized raster responses.


## 2026-10-21 — North American museum scope and three-result cap

- Removed Smithsonian Open Access, National Gallery, London, and Rijksmuseum from active museum search, fallback image recovery, diagnostics, and image-host allowlists.
- Restricted active institutional search to US sources (Art Institute of Chicago, The Met, and Cleveland Museum of Art) because no Canadian source currently passes the image-bearing integration requirements. The policy explicitly records Canada/United States as the only allowed regions and uses US sources as the fallback until a validated Canadian image source is integrated.
- Restored the visible recommendation limit to three across configuration, schemas, agent selection, UI request defaults, API enforcement, and documentation.


## 2026-10-25 — Buy option, accounts, PayPal, and Canadian gallery acquisition

- Added Buy art back to the Concierge dropdown and made it a purchase-only search across ArtMatch, Rookleys, Artsy Partner inventory, authorized Artlogic feeds, and Collect24 when configured.
- Added email/password registration, login/logout, secure server sessions, account-scoped taste profile/saved artworks/recent searches, and persistent purchase history through SQLAlchemy.
- Added PayPal Orders v2 checkout, capture/status verification, and PayPal invoicing for explicitly approved server-side catalog listings. Unapproved gallery listings continue to redirect to the seller checkout, with the outbound acquisition event recorded as external/pending.
- Restored the Purchases dashboard tab after Your Taste, with account-backed orders and provider-specific status actions.
- Updated purchase diagnostics to count ArtMatch/Rookleys public seller sources as configured while keeping optional partner sources credential-gated.
- Fixed account state persistence and checkout ownership checks; corrected the Concierge request route to receive the current Request object.


## 2026-10-25 — Database-backed recommendation personalization

- Merge the signed-in user's stored likes and avoids into Concierge requests.
- For personalized Explore, load saved artwork references, prior search ideas, profile notes, and discovery level server-side from the account database rather than depending solely on browser state.
- Update the PayPal approved-catalog example with `source_url` matching and seller authorization requirements.


## 2026-10-25 — Market selector and payment-provider-neutral art UX

- Added Canada / United States / Both region selection to Concierge, defaulting to Canada. The chosen scope is persisted in the browser preference and included in each request.
- Filtered providers and individual results to the requested market: Canadian galleries (ArtMatch and Rookleys), configured CA/US Artsy/Artlogic feeds, US museums, and market-tagged Collect24 results.
- Hid market selection for Learn because that service produces a text learning guide without artwork search.
- Removed processor-specific wording from artwork cards and general checkout feedback. Provider names remain in the Purchases/payment section and checkout-status pages.
- Surfaced sandbox-vs-live mode only within the Purchases/payment section; sandbox remains the default. Region choice is stored with browser preferences and is sent to the planner as well as the provider filter.


## 2026-10-28 — Vincent environment variable names

- Made `VINCENT_BASE_URL`, `VINCENT_PAYPAL_CATALOG`, and `VINCENT_STRIPE_CHECKOUT_CATALOG` the documented environment variable names for new deployments.
- Kept previously used environment variable names as backend-only fallback aliases for deployment compatibility; no old-brand variables need to be configured for a new Vincent deployment.
- Updated PayPal and Stripe setup documentation and the `.env.example` file to use Vincent names.


## 29 October 2026 — U.S. art-shop discovery

- Added public U.S. catalogue search for UGallery and Saatchi Art, without partner APIs or new environment variables.
- The Buy shop panel now follows the Concierge region: Canada shows ArtMatch and Rookleys, United States shows UGallery and Saatchi Art, and Both shows all four.
- U.S. marketplace prices marked with `$` are interpreted as USD; source results are labelled as public listings and buyers must reconfirm seller-side price, availability, shipping, and currency.
- Updated Buy summaries, diagnostics, docs, and regression tests to cover region-aware marketplace selection.


## 29 October 2026 — Collections, artwork comparison, and alerts

- Added named artwork collections with create, add, remove, and delete workflows. Registered accounts persist collections in PostgreSQL; the James demo account persists them in browser IndexedDB.
- Added side-by-side comparison for two or three artworks, including artist, asking price/currency, medium, dimensions, year, seller/source, availability, description, and seller listing link. Comparison selections persist locally across Vincent pages.
- Added Watch artwork and Follow seller actions for the supported commercial shops. Artwork alerts detect observed price changes; seller alerts detect new public catalogue listings against a baseline. Missing listings are not treated as sold.
- Added Dashboard → Collections and Dashboard → Alerts panels with in-app event history and a user-triggered Check for updates action. No background schedule or email notification service was added.
- Kept structured search filters in the user's Concierge prompt; no separate filter form was introduced.
- Kept the existing Buy flow for third-party gallery checkout hand-offs. PayPal Sandbox supports tracking details on captured PayPal transactions, but it cannot expose transactions completed through external sellers' own checkout accounts. Shipment details must be supplied by the seller.
- Added database tables for collections, collection items, artwork alerts, and alert events. Existing databases initialize the new tables through the existing SQLAlchemy metadata initialization.
