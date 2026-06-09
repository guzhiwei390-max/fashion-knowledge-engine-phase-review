# Current Version

Current version: Phase 1 Production Batch Hardening v0.9

Current Commit Hash: pending until commit; see final response for exact hash

Current Branch: master

---

# Added

This iteration added:
- Streaming zip upload to avoid loading the whole archive into memory
- Zip import summary response instead of returning all imported assets
- Unsupported file tracking for zip imports
- Pagination and filters for assets, review queue, observations, and knowledge cards
- Content-based coarse classification signals
- Multi-product detection based on image content signals
- Same-batch-first near duplicate search with larger fallback window
- Review Queue learning actions on resolve
- Human correction records from review resolution
- OfficialProductDNA, RealityProductDNA, and CommunityDNA separation in the DNA suite
- Safer default job process batch size

---

# Database Changes

Added tables:
- None

Modified tables:
- asset_batches: added unsupported_count
- asset_batches: added unsupported_files_json

Existing tables now used more strictly:
- human_corrections records review-based corrections
- assets.ingestion_metadata stores content signals and human review notes
- dna_records now includes OfficialProductDNA, RealityProductDNA, and CommunityDNA records

---

# API Changes

Added interfaces:
- None

Modified interfaces:
- POST /api/import/zip now writes the uploaded zip in chunks and returns only batch_id, total_received, unsupported_count, and status
- POST /api/jobs/process default limit changed from 5000 to 100
- GET /api/assets now supports limit, offset, batch_id, asset_type, quality_status, duplicate_status
- GET /api/review-queue now supports limit, offset, status, reason, item_type
- GET /api/observations now supports limit, offset, batch_id, product_name
- GET /api/knowledge-cards now supports limit, offset, brand, product_name
- POST /api/review-queue/{review_id}/resolve now updates asset labels or observation fields, records human_corrections, and returns learning_actions

---

# Architecture Changes

This iteration changed:
- Zip ingestion is now streaming at the API boundary.
- Zip import no longer returns large asset arrays that would break with 1,000 or 10,000 images.
- Unsupported files are auditable instead of silently ignored.
- Coarse classification no longer depends mainly on filenames.
- Local image content signals now contribute to white-background, multi-subject, detail-like, scene-like, human-like, and low-quality classification.
- Multi-product photos can be detected without filename markers and routed to review.
- Review Queue is now part of the learning loop instead of only a status list.
- Product DNA is now explicitly layered into OfficialProductDNA, RealityProductDNA, and CommunityDNA.
- Official Truth remains locked; ordinary uploads remain Reality Truth.
- Vision remains a second-layer verification path and is still behind Vision Router and budget control.

---

# What Works

Currently working:
- Large zip uploads are written to disk in chunks.
- Zip import response remains small even for large batches.
- Unsupported zip entries are counted and stored.
- Asset lists are paginated and filterable.
- Review Queue is paginated and filterable.
- Observations and knowledge cards are paginated and filterable.
- Local image content signals identify basic white-background and multi-subject cases.
- Multi-product image candidates enter the multi_product_photo path without relying on filenames.
- Review resolution can update asset labels, write Reality Truth correction metadata, and record human_corrections.
- Knowledge build outputs ProductDNA plus OfficialProductDNA, RealityProductDNA, and CommunityDNA.
- process_jobs defaults to smaller chunks for safer large-batch processing.
- Automated tests pass: 43 passed.

---

# Known Limitations

Known limitations:
- Content-based coarse classification is still lightweight heuristic analysis, not a production CV detector.
- Multi-product handling marks candidates and creates review structures but does not yet crop stable product regions automatically.
- Near duplicate grouping is improved for same-batch stability but still not a full embedding-based clustering system.
- Review resolution can update labels and observations, but the review UI is still basic.
- Zip unsupported_files_json stores a capped preview in import summary; full audit remains in the batch row.
- True background worker infrastructure is still SQLite-backed Phase 1 queue logic, not a separate worker service.
- Vision provider A/B reporting is not implemented yet.

---

# Next Recommended Step

Recommended next step:
- Run a 1,000-image mixed zip pressure test and inspect batch counters: total, ingested, unsupported, duplicate, near duplicate, low_quality, multi_product_photo, unknown, review_needed, failed.
- Improve image content classification with a lightweight local detector or embedding prefilter.
- Add a stronger manual review UI for high-volume corrections.
- Add product-region crop candidate generation for multi-product photos.
- Add an A/B evaluation runner for OpenAI vs MiMo on 50-100 selected review-needed images.

---

# Review Focus

Please review:
- Whether zip import is safe for large files and avoids one-shot memory reads.
- Whether large batch APIs avoid returning unbounded arrays.
- Whether pagination is present on assets, review queue, observations, and knowledge cards.
- Whether unsupported zip files are recorded instead of silently ignored.
- Whether filename-based classification has been reduced to an auxiliary signal.
- Whether multi-product photos are no longer forced into one product identity.
- Whether Review Queue resolution actually writes learning evidence.
- Whether OfficialProductDNA, RealityProductDNA, and CommunityDNA stay separate.
- Whether ordinary uploads remain Reality Truth and cannot create Official Truth.
- Whether Vision remains gated, budgeted, and candidate-verification-only.
