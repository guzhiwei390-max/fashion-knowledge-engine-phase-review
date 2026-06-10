# Current Version

Current version: Phase 1 Raw Ingestion / Official Site Learning Correction v1.0

Current Commit Hash: pending until commit; see final response for exact hash

Current Branch: master

---

# Added

This iteration added:
- Raw Asset Ingestion no longer requires Official Catalog
- product_matching_status on assets
- blocked_missing_official_catalog state for product identity matching
- Official Site Learning endpoint: POST /api/catalog/learn-site
- Automatic unblocking of previously paused product matching jobs after Official Catalog is built
- Admin UI Official Site Learning form
- Regression tests for zip ingestion without Official Catalog
- Regression tests for product matching blocked state without Official Catalog

---

# Database Changes

Added tables:
- None

Modified tables:
- assets: added product_matching_status

State values:
- pending
- blocked_missing_official_catalog

---

# API Changes

Added interfaces:
- POST /api/catalog/learn-site

Modified interfaces:
- POST /api/upload now allows raw asset ingestion without Official Catalog
- POST /api/import/zip now allows raw zip ingestion without Official Catalog
- POST /api/jobs/process marks matching as blocked_missing_official_catalog when no official catalog exists
- POST /api/catalog/import, /api/catalog/import-url, /api/catalog/import-tree, /api/catalog/learn-site unblock paused matching jobs after catalog creation
- POST /api/batches/{batch_id}/retry can requeue jobs blocked by missing Official Catalog

---

# Architecture Changes

This iteration corrected a major rule:
- Official Catalog is not a prerequisite for Raw Asset Ingestion.
- Official Catalog is a prerequisite only for Product Matching and Final Product Identification.

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
- Zip upload succeeds even when Official Catalog is empty
- Raw assets are stored with Reality Truth by default
- Batch id, metadata, thumbnail, unsupported detection, corrupted detection, low quality detection, dedup, and coarse classification still run without Official Catalog
- Assets created before Official Catalog exists are marked blocked_missing_official_catalog for product matching
- Jobs blocked by missing catalog can be unblocked after catalog import or official site learning
- Admin UI has Official Site Learning as the primary catalog creation path
- CSV/JSON import remains available as manual fallback
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
- Test the flow with a real mixed zip before catalog exists.
- Provide a brand official category URL and verify the blocked_missing_official_catalog jobs are requeued after catalog import.
- Improve Official Site Learning extraction for common product-list structured data patterns.
- Add clearer batch progress counters for blocked_missing_official_catalog.

---

# Review Focus

Please review:
- Whether Raw Asset Ingestion is fully decoupled from Official Catalog.
- Whether Official Catalog only blocks Product Matching / Final Product Identification.
- Whether product_matching_status communicates blocked_missing_official_catalog clearly.
- Whether Official Site Learning is the primary path and CSV/JSON is only fallback.
- Whether ordinary uploads remain Reality Truth.
- Whether Vision still does not run as a first-layer classifier.
- Whether no module guesses product identity when Official Catalog is missing.
