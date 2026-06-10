# Current Version

Current version: Phase 1 User-Facing Workflow Cleanup v1.1

Current Commit Hash: pending until commit; see final response for exact hash

Current Branch: master

---

# Added

This iteration added:
- User-facing Start Here workflow
- Primary Step 1: Upload Zip
- Primary Step 2: Learn Official Catalog from brand website URL
- CSV/JSON moved into Manual fallback only
- Internal fields hidden from the main UI
- Official Assets display changed to user-facing language

---

# Database Changes

Added tables:
- None

Modified tables:
- None in this UI-only iteration

State values:
- pending
- blocked_missing_official_catalog

---

# API Changes

Added interfaces:
- POST /api/catalog/learn-site

Modified interfaces:
- No API contract changes in this UI-only iteration

---

# Architecture Changes

This iteration corrected a major rule:
- The page no longer asks users to fill internal catalog_page, product_id, asset_type, official_white_bg, category tree, or visual reference fields.
- The user-facing flow is now Upload Zip first, then provide brand name and official site/category/product URL.
- Manual CSV/JSON import is visually demoted to fallback only.
- Internal API compatibility remains, but internal pipeline fields are not part of the main user workflow.

Correct flow:
- User uploads zip or images
- System saves originals, creates batch_id, extracts metadata, creates thumbnails, detects corruption, unsupported files, low quality, duplicates, and coarse asset type
- If Official Catalog is missing, product identity matching is paused with product_matching_status = blocked_missing_official_catalog
- User provides brand official website, category URL, or product URL
- System performs Official Site Learning from public allowed pages
- System builds Official Product Catalog and Official Product Assets
- Previously paused matching jobs are requeued

Manual CSV/JSON import is now documented as fallback only when Official Site Learning returns Needs Manual Import.

---

# What Works

Currently working:
- Page presents a simple two-step workflow: upload material package, then learn official catalog from brand website.
- Zip upload remains available before Official Catalog exists.
- Official Site Learning is the primary catalog creation action.
- CSV/JSON import is available only inside Manual fallback.
- Official product ids, visual reference types, expected_page_type, and category-tree internals are not exposed in the main UI.
- Automated tests pass: 45 passed

---

# Known Limitations

Known limitations:
- Official Site Learning remains conservative and only reads public allowed pages.
- JS-heavy, login-required, region-blocked, robots-disallowed, or anti-bot-protected pages still return Needs Manual Import.
- Official Product DNA generation from official site data is still basic and depends on what the public page exposes.
- Matching requeue happens after catalog creation, but there is not yet a long-running worker service.
- Admin UI is functional but still minimal for high-volume operations.

---

# Next Recommended Step

Recommended next step:
- Use the simplified page to upload the real mixed zip.
- Provide one brand official category URL and verify Official Site Learning can build the catalog.
- Improve Official Site Learning extraction for common product-list structured data patterns.
- Add clearer batch progress counters for blocked_missing_official_catalog.

---

# Review Focus

Please review:
- Whether the page is now user-facing rather than pipeline-facing.
- Whether users can follow Upload Zip -> Learn Official Catalog without understanding database fields.
- Whether CSV/JSON is clearly fallback only.
- Whether internal catalog fields are no longer exposed as primary UI inputs.
