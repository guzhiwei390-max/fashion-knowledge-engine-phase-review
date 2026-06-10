# Phase 1 Acceptance Review

## Current Version

Current version: Phase 1 Official Candidate Bootstrap v1.3

Current Commit Hash: pending until commit; see final response for exact hash

Current Branch: `master`

Repository: `https://github.com/guzhiwei390-max/fashion-knowledge-engine-phase-review`

Review zip: `fashion-knowledge-engine-phase1-review.zip`

## Scope

Phase 1 is still infrastructure only. It does not build AI image generation, try-on, AI models, scene generation, image expansion, or content generation.

The goal of this iteration was to make these gates real:

- Raw Asset Ingestion is never blocked by missing Official Catalog.
- Missing Official Catalog is batch/system status, not a per-asset Human Review item.
- Official Site Learning can create official products/assets/visual references from a public product page.
- Uploaded official-like images can become official candidates.
- Human confirmation of an official candidate creates Official Truth and unlocks pending product matching.

## Database Changes

Added:

- `official_candidate_assets`
- `official_candidate_groups`

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

Changed:

- `POST /api/assets/import-zip`
- `POST /api/catalog/learn-site`
- `GET /api/batches`
- `POST /api/review-queue/{review_id}/resolve`

Key behavior:

- `/api/assets/import-zip` returns batch-level raw ingestion and product matching states.
- `/api/catalog/learn-site` can create official catalog records from product-page JSON-LD.
- `/api/batches?batch_id=...` now filters to one batch.
- Resolving an `official_like_candidate` review can create `official_products`, `official_product_assets`, and `official_product_visual_references`.

## Actual Test A: Raw Asset Ingestion Without Catalog

Endpoint:

`POST /api/assets/import-zip`

Actual response:

```json
{
  "batch_id": "723c4d17-9eb2-4a21-b7fd-8de2f6e45856",
  "total_received": 1,
  "unsupported_count": 0,
  "status": "queued",
  "raw_asset_ingestion_status": "allowed",
  "official_catalog_status": "missing",
  "product_matching_status": "blocked_missing_official_catalog"
}
```

Batch status:

```json
{
  "pending_product_matching_count": 0,
  "blocked_missing_official_catalog_count": 1,
  "catalog_status": "missing",
  "next_action": "learn_official_site_or_upload_official_candidates",
  "review_needed": 0,
  "official_like_candidate_count": 1
}
```

Review Queue check:

```json
{
  "RELATED_REVIEWS": 0,
  "BLOCKED_CATALOG_REVIEW_ITEMS": 0
}
```

Result:

- Zip upload succeeded.
- Raw ingestion was allowed.
- Product matching was paused.
- Missing Official Catalog did not create per-asset Human Review spam.

## Actual Test B: Official Site Learning Positive Sample

Endpoint:

`POST /api/catalog/learn-site`

Input:

```json
{
  "brand": "on",
  "url": "http://127.0.0.1:8000/acceptance/products/on-cloudrunner-jacket"
}
```

Actual response summary:

```json
{
  "result": "Known",
  "flow": "official_catalog_bootstrap",
  "raw_asset_ingestion_status": "allowed",
  "official_catalog_status": "ready",
  "product_matching_status": "ready",
  "fetched_urls_count": 1,
  "parsed_product_pages_count": 1,
  "official_products_created": 1,
  "official_product_assets_created": 2,
  "official_visual_references_created": 1,
  "unblocked_jobs": 910
}
```

Created product:

```json
{
  "brand": "On",
  "product_name": "Cloudrunner Jacket",
  "category": "Women Jackets",
  "material": "Recycled Polyester",
  "truth_layer": "official_truth",
  "truth_locked": 1
}
```

Result:

- This is a real positive Official Catalog Learning sample.
- It created official product records, official assets, and visual references.
- It did not merely return `partial`.

## Actual Test C: Official Candidate Bootstrap From Uploaded Assets

Uploaded zip:

`test_c_official_candidate_v2.zip`

Uploaded official-like file:

`alo_airlift_blue_jacket_official_white_bg_v2.png`

Upload response:

```json
{
  "batch_id": "251f7662-7aa0-486d-9710-cec603d832e2",
  "total_received": 1,
  "raw_asset_ingestion_status": "allowed",
  "official_catalog_status": "ready",
  "product_matching_status": "ready"
}
```

Official candidate review created:

```json
{
  "item_type": "official_candidate_asset",
  "reason": "official_like_candidate",
  "confidence": 0.85,
  "review_payload": {
    "brand_hint": "Alo",
    "product_name_hint": "Alo Airlift Blue Jacket V2",
    "grouping_key": "Alo|Alo Airlift Blue Jacket V2|111111111100"
  }
}
```

Resolve response:

```json
{
  "status": "resolved",
  "learning_actions": {
    "official_truth_written": true,
    "correction_recorded": true,
    "rebuild_required": true,
    "official_product_id": "d3d85012-7124-4705-b590-36ca26dba9c1",
    "official_product_asset_id": "d17c9bb1-b6a9-40e3-a719-32c192bdbff6",
    "official_visual_reference_id": "b9331b47-c945-48b0-95ab-4db4f4587016"
  }
}
```

Database counts after confirmation:

```json
{
  "official_products": 2,
  "official_product_assets": 3,
  "official_product_visual_references": 2,
  "official_candidate_assets": 2,
  "official_candidate_groups": 2
}
```

Result:

- The system found official-like candidates from uploaded assets.
- The system grouped candidates.
- The user only needed to confirm/edit/reject, not create CSV.
- Confirmation wrote Official Truth automatically.

## Actual Test D: Review Queue Cleanliness

Result:

```json
{
  "blocked_missing_official_catalog_review_items": 0,
  "duplicate_review_items_exist": true,
  "near_duplicate_review_items_exist": true,
  "official_like_candidate_review_items_exist": true
}
```

Interpretation:

- Missing Official Catalog is not polluting Human Review Queue.
- Duplicate and official candidate cases still enter review correctly.

## Actual Test E: Pending Product Matching Reprocess

After Official Catalog was created:

```json
{
  "blocked_missing_official_catalog_count": 0,
  "pending_product_matching_count": 1,
  "catalog_status": "ready",
  "next_action": "run_product_matching"
}
```

Job processing:

```json
{
  "jobs": {
    "processed": 15,
    "failed": 0,
    "paused": 85,
    "blocked_missing_official_catalog": 0
  },
  "knowledge": {
    "built": 2
  }
}
```

Large batch evidence after processing:

```json
{
  "matched": 6,
  "unknown": 157,
  "review_needed": 271,
  "vision_calls_used": 100,
  "vision_status": "paused_budget",
  "blocked_missing_official_catalog_count": 0
}
```

Result:

- Previously blocked items were unblocked into pending matching.
- Processing no longer reports missing catalog blockers.
- Vision budget protection paused further expensive processing at the configured limit.

## UI Verification

In-app browser verification:

```json
{
  "title": "Fashion Knowledge Engine",
  "hasBatchProgress": true,
  "hasOfficialCandidateReview": true,
  "hasProductMatchReview": true,
  "hasDuplicateReview": true,
  "consoleErrors": []
}
```

## Automated Tests

Command:

`pytest tests/test_phase1.py -q`

Result:

`50 passed, 3 warnings`

## Known Limitations

- Job reprocessing is queued and can be triggered with `/api/jobs/process`; it is not yet a dedicated always-on worker process.
- Official-like candidate detection is heuristic and should be upgraded with stronger local visual classifiers.
- Group confirmation exists at the data layer; the UI still needs a more polished batch confirm/reject workflow.
- Lululemon homepage remains blocked by robots/access checks in this environment, so the positive learning sample uses the compliant local public acceptance page.

## Review Focus

Please focus review on:

- Whether `blocked_missing_official_catalog` is now correctly batch-level only.
- Whether Human Review Queue is clean.
- Whether Official Candidate Bootstrap is real enough for first 1000-image test.
- Whether Official Truth is only written through official learning or human-confirmed official candidates.
- Whether Product Matching remains gated and Unknown-first.
