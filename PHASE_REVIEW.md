# Current Version

Current version: Phase 1 Official Candidate Bootstrap v1.3

Current Commit Hash: pending until commit; see final response for exact hash

Current Branch: master

---

# Added

This iteration added:
- Product-page URL import support for Official Site Learning.
- Positive public acceptance product page for compliant catalog learning tests.
- Official Candidate Bootstrap from uploaded assets.
- `official_candidate_assets` creation for official-like uploaded images.
- `official_candidate_groups` for automatic grouping before human confirmation.
- Human review resolution that can create Official Product, Official Product Asset, and Official Visual Reference automatically.
- Batch-level blocked catalog tracking instead of per-asset review spam.
- Batch progress counts for official-like, human wearing, scene, product, and multi-product images.
- Review UI split into Official Candidate Review, Product Match Review, and Duplicate Review.
- `/api/batches?batch_id=...` filtering for focused acceptance review.

---

# Database Changes

Added tables:
- `official_candidate_assets`
- `official_candidate_groups`

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

Modified interfaces:
- `POST /api/assets/import-zip`
- `POST /api/catalog/learn-site`
- `GET /api/batches`
- `POST /api/review-queue/{review_id}/resolve`

Behavior changes:
- Zip import remains allowed when Official Catalog is missing.
- Missing Official Catalog now updates batch status and asset matching status, but does not create per-asset review items.
- Product-page URL learning can create official catalog records directly.
- Resolving an `official_like_candidate` review can write Official Truth and requeue previously blocked matching jobs.

---

# Architecture Changes

This iteration fixes the Human Review Queue boundary:
- `blocked_missing_official_catalog` is a batch/system blocker, not a human review item.
- Human Review Queue is reserved for true judgment work: duplicate, near duplicate, official-like candidate, product match conflict, low confidence after matching, uncertain identity, and useful low-quality/multi-product cases.
- Official Candidate Bootstrap lets the system learn from uploaded official-like assets instead of forcing users to write CSV.
- Official Catalog can be created from public product pages or from confirmed uploaded official-like candidates.
- Product Matching is unlocked only after Official Catalog is ready or partial usable.

---

# What Works

Actual Test A: Raw Asset Ingestion Without Catalog
- Endpoint: `POST /api/assets/import-zip`
- Result: Zip upload succeeded.
- `batch_id`: `723c4d17-9eb2-4a21-b7fd-8de2f6e45856`
- `raw_asset_ingestion_status`: `allowed`
- `official_catalog_status`: `missing`
- `product_matching_status`: `blocked_missing_official_catalog`
- Batch status had `blocked_missing_official_catalog_count = 1`
- Review Queue had `BLOCKED_CATALOG_REVIEW_ITEMS = 0`

Actual Test B: Official Site Learning Positive Sample
- Endpoint: `POST /api/catalog/learn-site`
- Input: `brand=on`, `url=http://127.0.0.1:8000/acceptance/products/on-cloudrunner-jacket`
- `official_catalog_status`: `ready`
- `product_matching_status`: `ready`
- `fetched_urls_count`: `1`
- `parsed_product_pages_count`: `1`
- `official_products_created`: `1`
- `official_product_assets_created`: `2`
- `official_visual_references_created`: `1`
- `unblocked_jobs`: `910`

Actual Test C: Official Candidate Bootstrap From Uploaded Assets
- Uploaded official-like image: `alo_airlift_blue_jacket_official_white_bg_v2.png`
- `official_like_candidate_count`: `1`
- `official_candidate_assets`: `2` total after test
- `official_candidate_groups`: `2` total after test
- Confirmed candidate via `POST /api/review-queue/{review_id}/resolve`
- Created:
  - `official_product_id`: `d3d85012-7124-4705-b590-36ca26dba9c1`
  - `official_product_asset_id`: `d17c9bb1-b6a9-40e3-a719-32c192bdbff6`
  - `official_visual_reference_id`: `b9331b47-c945-48b0-95ab-4db4f4587016`

Actual Test D: Review Queue Cleanliness
- Missing catalog no longer creates `Unknown: blocked_missing_official_catalog` review rows.
- Related review count for Test A batch: `0`
- Blocked catalog review item count: `0`
- Duplicate and near duplicate still enter Duplicate Review.

Actual Test E: Pending Product Matching Reprocess
- After catalog/candidate confirmation, previously blocked batches changed from `blocked_missing_official_catalog_count > 0` to `0`.
- Those assets moved to `pending_product_matching_count > 0`.
- `POST /api/jobs/process` processed `15` jobs, failed `0`, blocked missing catalog `0`.
- Existing large batch showed matching activity: `matched = 6`, `unknown = 157`, `review_needed = 271`, `vision_calls_used = 100`, `vision_status = paused_budget`.

UI verification:
- In-app browser loaded `http://127.0.0.1:8000/`.
- Sections present: Batch Progress, Official Candidate Review, Product Match Review, Duplicate Review.
- Browser console errors: `0`.

Automated tests:
- `pytest tests/test_phase1.py -q`
- Result: `50 passed, 3 warnings`

---

# Known Limitations

Known limitations:
- Product matching reprocessing is queued/unblocked and can be run through the job processor; it is not yet a continuously running worker daemon.
- Official Site Learning is conservative and still obeys robots/access limits.
- Lululemon homepage remains blocked by robots/access checks in this environment, so the positive sample uses a compliant public acceptance page.
- Official-like classification is still heuristic and should later improve with stronger local visual classifiers.
- Candidate group confirmation currently confirms one representative candidate; batch group confirmation UI can be improved next.

---

# Next Recommended Step

Recommended next step:
- Add a real background worker loop for queued product matching jobs.
- Add official candidate group-level confirm/reject UI.
- Improve official-like candidate grouping by visual similarity and brand/product hints.
- Add more robust local coarse classifiers before Vision Router.

---

# Review Focus

Please重点审查:
- Whether `blocked_missing_official_catalog` is now correctly batch-level only.
- Whether Human Review Queue contains only true human judgment items.
- Whether Official Candidate Bootstrap creates Official Truth without requiring CSV.
- Whether Product Matching remains gated by Official Catalog readiness.
- Whether Truth Layer boundaries remain clean.
