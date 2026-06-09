# Current Version

Current Version: Phase 1 Foundation Review v0.3

Current Commit Hash: e2c90db

Current Branch: master

---

# Added

- Official Catalog Importer
- Category-tree catalog import reservation and limited implementation
- Official Product Assets
- Official Product Visual Reference Library
- Catalog-first visual matching
- Conditional OpenAI Vision structure analysis
- Product Structure Evidence
- Product DNA
- Garment Validation Rules
- Material DNA, Outfit DNA, Tribe DNA, Scene DNA, Customer Reality DNA, Trend DNA placeholders
- Knowledge Card generation
- Unknown-first mechanism
- Batch upload and zip import
- Batch progress summary
- Human correction API and admin entry
- Pipeline architecture reservation for Internal Upload and External Knowledge
- Source Type registry
- Truth-layer reservation: Official Truth, Reality Truth, Community Truth
- Read-only pipeline design API

---

# Database Changes

新增表：

- assets
- source_type_registry
- ingestion_sources
- pipeline_runs
- external_knowledge_items
- official_products
- official_product_assets
- official_product_visual_references
- product_aliases
- review_queue
- analysis_jobs
- vision_observations
- human_corrections
- knowledge_cards
- dna_records
- retrieval_queries
- knowledge_source_index

修改表：

- assets: added pipeline_type, truth_layer, source_id, external_ref_uri, ingestion_metadata
- official_products: added product_family, variant, truth_layer, truth_locked, official_fields_json, supplemental_fields_json
- official_product_assets: added local_file_uri, visual_signature, source_type, pipeline_type, truth_layer
- official_product_visual_references: added truth_layer
- vision_observations: added product_structure
- knowledge_cards: added source_type, pipeline_type, truth_layer
- dna_records: added source_type, pipeline_type, truth_layer
- retrieval_queries: added pipeline_type, truth_layer

---

# API Changes

新增接口：

- GET /api/health
- GET /
- POST /api/catalog/import
- POST /api/catalog/import-url
- POST /api/catalog/import-tree
- GET /api/catalog
- GET /api/catalog/assets
- GET /api/catalog/visual-references
- POST /api/catalog/visual-reference
- POST /api/upload
- POST /api/import/zip
- POST /api/jobs/process
- GET /api/assets
- GET /api/jobs
- GET /api/batches
- GET /api/observations
- GET /api/knowledge-cards
- GET /api/search
- POST /api/search
- POST /api/corrections
- GET /api/pipelines/design
- GET /api/source-types

修改接口：

- POST /api/upload: blocked until Official Product Catalog exists
- POST /api/import/zip: blocked until Official Product Catalog exists
- POST /api/jobs/process: processes queue and rebuilds knowledge records

---

# Architecture Changes

本次架构调整：

- Reframed system as a Fashion Knowledge Engine instead of an image gallery.
- Official Catalog is the first source of truth.
- Vision is a secondary verification and structure-detail source.
- Unknown remains higher priority than guessing.
- Official Truth, Reality Truth, and Community Truth are independently marked.
- Official Truth is locked and can only be supplemented, not overwritten by user/community data.
- Product Structure fields are stored as evidence objects, not simple placeholders.
- OpenAI Vision cannot create products outside Official Product Catalog.
- External Knowledge Pipeline is reserved only; ingestion is not enabled.
- Internal Upload Pipeline is reserved and currently maps user uploads to Reality Truth.
- Source Type registry seeds future sources without requiring database redesign.

---

# What Works

目前已经可以工作的功能：

- Import official catalog records from CSV or JSON.
- Import a public official product/category URL when accessible and extractable.
- Import a bounded same-site category tree with robots checks and page limits.
- Store official products, official product assets, and official visual references.
- Upload multiple images after catalog is ready.
- Import images from zip after catalog is ready.
- Deduplicate uploaded assets by sha256.
- Match unnamed user images against official visual references.
- Return Unknown when official product match cannot be proven.
- Conditionally call OpenAI Vision only for low confidence, no match, or structure-detail needs.
- Store product structure observations.
- Build Product DNA and Knowledge Cards from evidence-backed observations.
- Return all Phase 1 DNA structures in retrieval.
- Expose batch progress: total, processed, matched, unknown, failed.
- Allow basic human correction on vision observations.
- Expose read-only pipeline design and source type registry.
- Test suite passes.

---

# Known Limitations

目前已知问题：

- Official Catalog Importer is conservative and may return Needs Manual Import for many modern JS-heavy pages.
- Category-tree importer is intentionally bounded and not a full crawler.
- Official visual matching uses lightweight local image signatures, not production-grade embeddings.
- Product Structure Engine depends on available evidence and optional OpenAI Vision; without evidence, fields remain Unknown.
- Human review queue is reserved in database but not fully implemented as a workflow.
- External Knowledge Pipeline is reserved only and not active.
- Community Truth ingestion is not active.
- Official image usage is only for identification reference and does not imply commercial-use rights.
- Admin UI is functional but still minimal.
- GitHub repository does not include local review zip by design.

---

# Next Recommended Step

我建议下一步开发：

- Finish a brand-agnostic Product Structure Engine focused on Collar, Zipper, Logo Position, Stitching, Back Structure, Sleeve Structure, and Material Behavior.
- Add a Confidence Engine that centralizes thresholds and Unknown decisions.
- Add an Evidence Engine that returns matched-because reasons for each product match.
- Implement Human Review Queue for Unknown, Low Confidence, Conflict, and Duplicate.
- Expand Product Alias Engine for Product Name, Aliases, Product Family, Variant, and Color.
- Keep Official Truth locked while allowing Reality Truth and Community Truth to supplement separate knowledge layers.

---

# Review Focus

请重点审查：

- 是否严格遵守 Unknown-first and no-guessing rules.
- 是否禁止 Vision 创建 Official Catalog 中不存在的产品。
- 是否保持 Official Truth > Reality Truth > Community Truth。
- 是否三种 Truth 独立标记，未来可独立检索。
- 是否数据库预留足够，未来扩展 Internal Upload Pipeline 和 External Knowledge Pipeline 时不需要重构。
- 是否 Product Structure Evidence 真的服务于“为什么是这个产品”，而不只是“识别成这个产品”。
- 是否 Official Catalog Importer 的合规边界足够清晰，不构成暴力爬虫。
- 是否 Phase 1 没有引入 AI 生图、换装、模特、场景生成、扩图或内容生成。
