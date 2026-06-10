# Phase 1 Acceptance Review

## Current Version

Current version: Phase 1 Batch Official Site Learning v1.4

Current Commit Hash: pending until commit; see final response for exact hash

Current Branch: `master`

Repository: `https://github.com/guzhiwei390-max/fashion-knowledge-engine-phase-review`

Review zip: `REVIEW_BUNDLE.zip`

## Scope

Phase 1 is still infrastructure only. It does not build AI image generation, try-on, AI models, scene generation, image expansion, or content generation.

This iteration focuses on Official Catalog Learning before large user asset matching:

- Batch Official Site Learning for multiple brand official entrances.
- Lululemon-first compliance test.
- Official Candidate Group Review as the compliant fallback when official pages are blocked.
- Stronger official-like candidate evidence.
- One-click selected-batch processing.

## Database Changes

Added in this phase line:

- `official_candidate_assets`
- `official_candidate_groups`

Still used:

- `official_catalog_import_jobs`
- `official_products`
- `official_product_assets`
- `official_product_visual_references`
- `official_url_candidates`
- `official_parse_events`

Modified:

- `asset_batches`

New batch progress columns:

- `pending_product_matching_count`
- `blocked_missing_official_catalog_count`
- `catalog_status`
- `next_action`
- `official_like_candidate_count`
- `scene_photo_count`
- `human_wearing_count`
- `product_photo_count`
- `multi_product_photo_count`

## API Changes

Added:

- `POST /api/catalog/learn-sites`
- `POST /api/catalog/learn-sites-csv`
- `POST /api/catalog/learn-sites-json`
- `GET /api/official-candidate-groups`
- `POST /api/official-candidate-groups/{group_id}/action`
- `POST /api/batches/{batch_id}/process`

Changed:

- `POST /api/assets/import-zip`
- `POST /api/catalog/learn-site`
- `GET /api/batches`
- `POST /api/review-queue/{review_id}/resolve`

## Actual Test A: Raw Asset Ingestion Without Catalog

Endpoint:

`POST /api/assets/import-zip`

Verified behavior:

```json
{
  "raw_asset_ingestion_status": "allowed",
  "official_catalog_status": "missing",
  "product_matching_status": "blocked_missing_official_catalog"
}
```

Result:

- Zip upload succeeds when Official Catalog is missing.
- Raw ingestion is not blocked.
- Product matching is paused separately.
- Missing catalog does not create per-asset Human Review Queue spam.

## Actual Test B: Batch Official Site Learning, Lululemon First

Endpoint:

`POST /api/catalog/learn-sites-json`

Input:

```json
[
  {
    "brand": "Lululemon",
    "urls": [
      "https://shop.lululemon.com/",
      "https://shop.lululemon.com/c/women-jackets-and-hoodies/_/N-8r6",
      "https://shop.lululemon.com/p/womens-outerwear/Define-Jacket/_/prod5020054"
    ],
    "priority": "high",
    "note": "core brand"
  }
]
```

Actual response summary:

```json
{
  "totals": {
    "entries_received": 1,
    "urls_attempted": 3,
    "ready": 0,
    "partial": 0,
    "blocked": 3,
    "official_products_created": 0,
    "official_product_assets_created": 0,
    "official_visual_references_created": 0
  },
  "unblocked_jobs": 0
}
```

Each Lululemon URL returned:

```json
{
  "brand": "Lululemon",
  "robots_status": {
    "robots_txt_fetched": true,
    "robots_allowed": false,
    "robots_url": "https://shop.lululemon.com/robots.txt"
  },
  "sitemap_found": false,
  "candidate_urls_found": 0,
  "product_pages_parsed": 0,
  "official_products_created": 0,
  "official_product_assets_created": 0,
  "official_visual_references_created": 0,
  "official_catalog_status": "blocked",
  "next_action": "needs_official_candidate_review",
  "blocked_reason": "robots.txt disallows access or could not be verified"
}
```

Result:

- Lululemon was tested first.
- The system obeyed robots/access limits.
- The system did not bypass access controls.
- The system did not pretend catalog learning was complete.
- The system did not create fake official products/assets/references.
- The compliant fallback is official candidate review from accessible official candidates.

## Actual Test C: Batch Official Site Learning Parsers

Verified supported input formats:

- Multi-line text: `Brand, URL`
- CSV entrance list: `brand,url,type,priority,note`
- JSON entrance list: `brand`, `urls`, `priority`

Important distinction:

- These CSV/JSON files are only official site entrance lists.
- They are not manual Product Catalog files.
- The system still owns learning, parsing, candidate creation, and catalog creation.

## Actual Test D: Official Candidate Group Review

Verified group operations:

- Approve group.
- Reject group.
- Merge group.
- Split group.
- Edit product fields once and apply to group through approval payload.

Verified official-like candidate payload fields:

```json
{
  "candidate_type": "official_white_bg_candidate",
  "candidate_confidence": 0.78,
  "why_this_is_official_like": [
    "white or clean background product composition",
    "official marker in filename or source hint"
  ],
  "related_assets": []
}
```

Result:

- Official-like uploaded assets can become reviewable official candidates.
- Human approval can write Official Truth.
- This path is the correct fallback for Lululemon when public site access is blocked.

## Actual Test E: Automated Test Suite

Command:

`pytest tests/test_phase1.py -q`

Result:

```text
55 passed, 3 warnings
```

Warnings:

- Starlette TestClient/httpx deprecation warning.
- FastAPI `on_event` deprecation warning.
- pytest-asyncio default fixture loop scope warning.

## Known Limitations

- Lululemon did not create Official Catalog records because current access checks blocked all tested official URLs.
- This is a compliant blocked result, not a learning success.
- A successful Lululemon bootstrap now requires accessible official candidates, accessible category/product URLs, or user-confirmed official-like uploaded assets.
- Product matching has a stable one-click selected-batch endpoint, but not yet a daemon-style worker.
- Official-like detection is still heuristic and should be improved before 1000-image production pressure testing.

## Acceptance Position

This version proves:

- Official Catalog is prioritized before product matching.
- Lululemon is handled first and conservatively.
- Blocked official site learning does not become fake knowledge.
- CSV/JSON entrance lists do not transfer product catalog maintenance to the user.
- The system can continue via Official Candidate Review when official pages are inaccessible.

This version does not yet prove:

- Successful Lululemon Official Catalog creation from the public website.
- 1000-image production readiness.
- Continuous background processing.
