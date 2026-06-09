# Current Version

当前版本号：Phase 1 Production Ingestion Foundation v0.7

当前 Commit Hash：pending until commit; see final response for exact hash

当前 Branch：master

---

# Added

本次新增功能：

- Brand-agnostic Product Structure Engine fields
- Central Confidence Engine
- Evidence Engine
- Real Human Review Queue workflow foundation
- Exact duplicate and near duplicate detection
- Official product alias, family, variant, color alias storage
- Expanded Official Product Assets for logo, zipper, hardware, stitching
- Architecture decision log
- Reserved database/API architecture for Success Library, Negative Library, Commercial Score, Trend Timeline, Region Layer, and Learning Feedback Loop
- PROJECT_NORTH_STAR.md as the highest priority project goal document
- Production batch ingestion foundation for 10,000+ mixed assets
- Asset batch progress table and retry/pause/resume API
- Ingestion metadata extraction: dimensions, EXIF, thumbnail, file hash, perceptual hash
- Corrupted image handling without batch failure
- Coarse Classification Engine for first-pass asset_type and quality_status
- Multi-product photo region placeholder and review routing
- Reality Pattern and video/frame extraction schema reservations
- Regression tests for Unknown-first, confidence routing, evidence output, review queue, duplicate handling, and architecture rules

---

# Database Changes

新增表：

- success_library_items
- negative_library_items
- commercial_score_records
- trend_timeline_events
- region_layers
- learning_feedback_events
- asset_batches
- asset_product_regions
- material_reality_patterns
- human_reality_patterns
- scene_reality_patterns
- moment_patterns
- outfit_reality_patterns
- reality_score_schema
- video_assets
- video_frame_assets
- frame_extraction_jobs

修改表：

- assets: added visual_signature, duplicate_of_asset_id, duplicate_status
- review_queue: added review_payload, resolution_json, resolved_by, resolved_at
- official_products: official_fields_json now captures official catalog identity fields during import
- product_aliases: now populated during official catalog import for product names, aliases, and colors
- official_product_assets: now supports official_logo, official_zipper, official_hardware, official_stitching
- source_type_registry: added reserved_extension source type
- assets: added ingestion_status, asset_type, quality_status, width, height, exif_json, thumbnail_uri

---

# API Changes

新增接口：

- GET /api/review-queue
- POST /api/review-queue/{review_id}/resolve
- GET /api/extensions/reserved
- GET /api/batches/{batch_id}
- POST /api/batches/{batch_id}/retry
- POST /api/batches/{batch_id}/pause
- POST /api/batches/{batch_id}/resume

修改接口：

- POST /api/upload: now stores visual signatures and duplicate metadata
- POST /api/upload: ordinary user uploads remain Reality Truth even if filenames contain "official"
- POST /api/import/zip: now inherits duplicate, corruption, thumbnail, metadata, and coarse classification handling through asset creation
- POST /api/jobs/process: now auto-enqueues Unknown and Low Confidence observations for human review
- POST /api/catalog/import: now records product family, variant, aliases, color aliases, and expanded official asset types
- GET /: admin UI now shows pending Human Review Queue
- GET /api/pipelines/design: now includes reserved-only future module architecture

---

# Architecture Changes

本次架构调整：

- Product recognition now routes through a central Confidence Engine.
- Low-confidence matches no longer become hard product identities.
- Visual hash/color signature matching is treated as an official visual prefilter and evidence input, not the sole business authority.
- Evidence Engine now returns matched_because, evidence_asset_ids, matched_official_assets, confidence, and uncertain_fields.
- Human Review Queue is active for Unknown, Low Confidence, Duplicate, and Near Duplicate.
- Product Structure Engine is brand agnostic and covers collar, zipper, logo_position, stitching, back_structure, sleeve_structure, hem_shape, fit_shape, pocket, hardware, material_behavior.
- OpenAI Vision remains a secondary verification/detail source and cannot create products outside Official Catalog.
- Architecture rules are now recorded in ARCHITECTURE_DECISIONS.md.
- PROJECT_NORTH_STAR.md now defines the final goal as Reality Image Engine, not Product Recognition Engine.
- Existing Official Catalog, Official Assets, Product DNA, Structure DNA, Unknown, Confidence, Evidence, and Review Queue modules are explicitly preserved as correct Phase 1 foundations.
- Production batch ingestion now treats real mixed material packages as the target input, not curated demo folders.
- Files are preserved before processing; corrupted files enter review/status instead of crashing a batch.
- Coarse classification happens before product identification.
- Multi-product photos are not forced into one product and have region/candidate schema.
- User upload filenames cannot create Official Truth.
- Reality/Moment/Human/Scene and video structures are reserved for future realism learning.
- Success Library, Negative Library, Commercial Score, Trend Timeline, Region Layer, and Learning Feedback Loop are reserved-only extension points.
- Reserved future modules have database structure and API design entries, but no Phase 1 logic, pages, scoring, trend analysis, region algorithms, or feedback automation.

---

# What Works

目前已经可以工作的功能：

- Import official catalog records with product identity fields.
- Store official visual assets for white background, model, detail, fabric, logo, zipper, hardware, and stitching references.
- Store product aliases and color aliases to reduce duplicate product identity drift.
- Upload unnamed images and match them against Official Catalog visual references.
- Upload mixed batches while preserving originals, thumbnails, file hashes, perceptual hashes, dimensions, and EXIF.
- Mark corrupted files without stopping the batch.
- Coarse classify assets into reality_product_photo, human_wearing_photo, scene_photo, outfit_reference, multi_product_photo, low_quality, duplicate, or unknown.
- Keep ordinary user uploads in Reality Truth regardless of filename.
- Create multi-product region placeholders and route uncertain multi-product images to Review Queue.
- Track batch progress with total_files, ingested, duplicated, corrupted, low_quality, coarse_classified, matched, unknown, review_needed, failed, OpenAI Vision calls, and estimated cost.
- Retry, pause, and resume batch jobs.
- Use OpenAI Vision only when matching is missing, low confidence, or structure evidence is needed.
- Return Unknown when no official product match is proven.
- Route low confidence product candidates to Human Review Queue.
- Route exact duplicate and near duplicate uploads to Human Review Queue.
- Generate Product DNA with brand-agnostic structure evidence.
- Generate Garment Validation Rules from evidence-backed Product DNA.
- Retrieve Product DNA, Material DNA, contextual DNA placeholders, and Knowledge Card.
- Inspect reserved future extension architecture through GET /api/extensions/reserved.
- Run automated tests: 34 passing.

---

# Known Limitations

目前已知问题：

- Official Catalog Importer remains conservative and may return Needs Manual Import for JS-heavy or access-restricted brand pages.
- Visual matching still uses lightweight local signatures as a Phase 1 prefilter; production-grade embeddings are not implemented yet.
- OpenAI Vision requires OPENAI_API_KEY; without it, structure fields stay Unknown unless official visual evidence is enough.
- Near duplicate detection is simple and should be upgraded before very large production batches.
- Human review resolution records decisions, but richer correction forms and active learning UI are still minimal.
- Official visual references do not imply commercial usage rights; they are identification references only.
- Community Truth ingestion remains reserved, not active.
- Success Library, Negative Library, Commercial Score, Trend Timeline, Region Layer, and Learning Feedback Loop are schema/API reservations only and intentionally inactive.
- Folder upload is represented in reserved API design, but there is not yet a separate browser folder upload endpoint beyond multi-file upload and zip import.
- Multi-product region detection has schema and review routing, but does not yet crop product regions automatically.
- Batch queue is SQLite-backed and suitable for Phase 1 foundation; a production worker system can replace it later without changing core data contracts.

---

# Next Recommended Step

我建议下一步开发：

- Add product-level visual reference scoring that combines multiple official assets per product.
- Add manual review resolution that can promote corrected Reality Truth into future matching hints without overwriting Official Truth.
- Add richer Official Catalog category-page extraction for common structured product-list patterns.
- Add stronger near-duplicate clustering for large batches.
- Add product variant and color confidence evidence in Product DNA.
- Keep future growth modules inactive until product recognition quality is stable.
- Evaluate every future feature against one question: does it improve final image realism?
- Add stronger worker execution for long-running 10,000+ image batches after ingestion contracts stabilize.

---

# Review Focus

请重点审查：

- 是否严格遵守 Unknown-first，不猜、不脑补、不创造产品。
- Confidence Engine 阈值是否合理，是否所有识别都统一走中央判断。
- Evidence Engine 是否足够解释“为什么是这个产品”。
- Human Review Queue 是否覆盖 Unknown、Low Confidence、Duplicate、Near Duplicate。
- Official Truth Lock 是否没有被 Reality Truth 或 Community Truth 覆盖。
- Product Structure Engine 是否品牌无关，而不是围绕 Define 特化。
- Phase 1 是否没有引入 AI 生图、换装、模特、场景生成、扩图或内容生成。
- Success Library、Negative Library、Commercial Score、Trend Timeline、Region Layer、Learning Feedback Loop 是否只做了预留，没有提前实现逻辑、页面或算法。
- PROJECT_NORTH_STAR.md 是否清楚规定项目最终目标是 Reality Image Engine，而不是 Product Recognition Engine。
- 是否能真实导入 10,000 张混合素材而不因单张失败中断。
- 用户上传文件名是否不会污染 Official Truth。
- 多商品图片是否没有被强行识别为单个产品。
- Batch Progress 是否足够透明，能看到 corrupted、duplicate、low_quality、review_needed、Vision calls 和 estimated cost。
