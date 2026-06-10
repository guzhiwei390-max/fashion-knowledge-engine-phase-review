# Current Version

Current version: Phase 1 Batch Official Site Learning v1.4

Current Commit Hash: pending until commit; see final response for exact hash

Current Branch: master

---

# Added

This iteration added:
- Batch Official Site Learning for multiple brand entrance URLs.
- Multi-line official entrance input: `Brand, URL`.
- CSV official entrance input with `brand,url,type,priority,note`.
- JSON official entrance input with `brand`, `urls`, and `priority`.
- Per-brand/per-URL learning result rows.
- Lululemon-first official site learning test.
- Sitemap index child parsing for compliant sitemap discovery.
- Rich official-like candidate evidence: `candidate_type`, `candidate_confidence`, `why_this_is_official_like`, and `related_assets`.
- Official Candidate Group Review actions: approve group, reject group, merge group, split group.
- One-click `POST /api/batches/{batch_id}/process` endpoint for stable selected-batch processing.

---

# Database Changes

Added tables in this phase line:
- `official_candidate_assets`
- `official_candidate_groups`

Previously added and still used:
- `official_catalog_import_jobs`
- `official_products`
- `official_product_assets`
- `official_product_visual_references`
- `official_url_candidates`
- `official_parse_events`

Modified tables:
- `asset_batches`

Added `asset_batches` columns:
- `pending_product_matching_count`
- `blocked_missing_official_catalog_count`
- `catalog_status`
- `next_action`
- `official_like_candidate_count`
- `scene_photo_count`
- `human_wearing_count`
- `product_photo_count`
- `multi_product_photo_count`

---

# API Changes

Added interfaces:
- `POST /api/catalog/learn-sites`
- `POST /api/catalog/learn-sites-csv`
- `POST /api/catalog/learn-sites-json`
- `GET /api/official-candidate-groups`
- `POST /api/official-candidate-groups/{group_id}/action`
- `POST /api/batches/{batch_id}/process`

Modified interfaces:
- `POST /api/assets/import-zip`
- `POST /api/catalog/learn-site`
- `GET /api/batches`
- `POST /api/review-queue/{review_id}/resolve`

Behavior changes:
- Batch Official Site Learning creates one learning attempt per submitted URL.
- If `official_products_created = 0`, the catalog cannot be marked `ready`.
- Lululemon blocked/access-limited results remain `blocked`, not fake `ready`.
- Zip import remains allowed when Official Catalog is missing.
- Missing Official Catalog updates batch status and asset matching status, but does not create per-asset review items.
- Official Candidate Group Review can write Official Truth after human approval.

---

# Architecture Changes

This iteration reinforces:
- Official Catalog Learning should happen before large user asset matching when possible.
- CSV/JSON entrance files are only official site entrance lists, not manual product catalog creation.
- Manual Product CSV remains final fallback, not the default path.
- Lululemon is handled conservatively: if robots/access checks block public learning, the system moves to official candidate review instead of guessing.
- Official-like assets from user uploads are a bootstrap supplement, not a replacement for Official Truth.
- Product Matching remains locked until Official Catalog is ready or partial usable.

---

# What Works

Automated tests:
- Command: `pytest tests/test_phase1.py -q`
- Result: `55 passed, 3 warnings`

Actual Lululemon Batch Official Site Learning test:
- Endpoint: `POST /api/catalog/learn-sites-json`
- Input URLs:
  - `https://shop.lululemon.com/`
  - `https://shop.lululemon.com/c/women-jackets-and-hoodies/_/N-8r6`
  - `https://shop.lululemon.com/p/womens-outerwear/Define-Jacket/_/prod5020054`
- `entries_received`: `1`
- `urls_attempted`: `3`
- `ready`: `0`
- `partial`: `0`
- `blocked`: `3`
- `official_products_created`: `0`
- `official_product_assets_created`: `0`
- `official_visual_references_created`: `0`
- Per URL:
  - `robots_txt_fetched`: `true`
  - `robots_allowed`: `false`
  - `sitemap_found`: `false`
  - `candidate_urls_found`: `0`
  - `product_pages_parsed`: `0`
  - `official_catalog_status`: `blocked`
  - `next_action`: `needs_official_candidate_review`

What this proves:
- The system prioritizes Lululemon.
- The system obeys robots/access constraints.
- The system does not mark blocked learning as `ready`.
- The system does not create fake official products.
- The system routes blocked official learning to official candidate review.

Other verified behavior:
- Raw Asset Ingestion is allowed without Official Catalog.
- `blocked_missing_official_catalog` is tracked at batch/system level, not as per-asset review spam.
- Official-like candidate detection emits reviewable evidence.
- Official Candidate Group Review supports approve, reject, merge, and split.
- Selected-batch processing does not process unrelated batches.

---

# Known Limitations

Known limitations:
- Lululemon public site learning is blocked in this environment by robots/access checks; compliant code cannot bypass this.
- Official Candidate Review is therefore the correct Lululemon bootstrap path unless the user provides accessible category/product URLs or official-like candidate assets.
- Product matching reprocessing has a stable one-click endpoint, but not yet a continuously running worker daemon.
- Official-like classification is still heuristic and should later improve with stronger local visual classifiers.
- Group split/merge APIs exist; the UI can still become more polished for high-volume review work.

---

# Next Recommended Step

Recommended next step:
- For Lululemon, upload official-like candidate images or accessible official page URLs, then use Official Candidate Group Review to confirm Official Truth.
- Run Batch Official Site Learning for Alo/On/Arc'teryx/Ralph Lauren and collect per-brand status rows.
- Add a real background worker loop for queued product matching jobs.
- Improve local official-like classification before Vision Router.

---

# Review Focus

Please focus review on:
- Whether Lululemon blocked results are handled honestly and compliantly.
- Whether Batch Official Site Learning supports multi-line, CSV entrance, and JSON entrance flows.
- Whether the system avoids manual Product CSV as the default path.
- Whether official candidates can be reviewed at group level.
- Whether Product Matching remains gated by real Official Catalog readiness.
