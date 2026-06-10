# Architecture Decisions

## Project North Star

`PROJECT_NORTH_STAR.md` is the highest priority project document.

The final project goal is Reality Image Engine: input Product + Scene, output a photo that feels like a real human-captured moment.

Official Catalog, Product DNA, Structure DNA, Unknown, Confidence, Evidence, and Review Queue remain correct Phase 1 foundations because they prevent product errors before future image generation work exists.

Future features must answer one question before development: does this improve the realism of the final image?

If a feature does not improve final image realism, do not build it.

## Unknown First

If the system cannot prove a product, structure field, material, logo, zipper, category, scene, or source from stored evidence, it must return `Unknown`.

No module may guess, infer creatively, or create a product identity from weak similarity.

## Official Truth Lock

Official catalog data is locked. Official Truth can be supplemented by more official evidence, but it cannot be overwritten by Reality Truth or Community Truth.

Official product identity comes from `official_products`, `official_product_assets`, and `official_product_visual_references`.

## Official Truth > Reality Truth > Community Truth

Truth priority is fixed:

- Official Truth > Reality Truth > Community Truth

The three truth layers must remain independently stored, independently tagged, and independently retrievable. They must not be merged into a single unmarked knowledge source.

## Phase 1 禁止生成

Phase 1 forbids AI image generation, AI try-on, AI models, AI scene generation, image expansion, and content generation.

Phase 1 exists to build the learning system:

- Official Catalog
- Official Visual Reference Library
- Product Structure Engine
- Product DNA
- Knowledge Cards
- Retrieval
- Unknown and Human Review workflow

## Vision 不能创造不存在的产品

Vision is a secondary expert verification and structure-detail source. It cannot create a product that does not exist in the Official Product Catalog.

If Vision sees a possible product but the product cannot be mapped back to `official_products`, the result must be `Unknown`.

Vision can explain evidence, compare candidates, and identify uncertain structure fields, but it cannot invent brands, products, accessories, categories, colors, or variants.

## Vision Provider Adapter

Vision must be provider-agnostic.

Supported provider keys:

- `openai`
- `mimo`
- `qwen_vl`
- `gemini`
- `local`

Required configuration:

- `VISION_PROVIDER`
- `MAX_VISION_CALLS_PER_BATCH`
- `VISION_COST_LIMIT`
- `VISION_REQUIRE_CONFIRM_ABOVE`

Every provider must return the same JSON schema:

- `product_match`
- `product_structure`
- `multi_product`
- `quality`

Provider output is never final truth. It must pass through Confidence Engine, Evidence Engine, and Review Queue routing.

## Vision Gate / Vision Router

OpenAI Vision, MiMo, Qwen-VL, Gemini, or any future model must not be the first layer of recognition.

The processing order is:

1. Local Ingestion
2. Local Deduplication
3. Local Coarse Classification
4. Official Catalog candidate narrowing
5. Vision Router decision
6. Provider-specific Vision call only when allowed
7. Confidence Engine
8. Evidence Engine
9. Review Queue or accepted match

Vision calls are blocked for duplicate, near duplicate, corrupted, low quality, obvious scene-only, and high-confidence local matches that do not need structure detail.

Vision calls are budget-controlled per batch. When the batch exceeds its configured call or cost limit, the batch pauses instead of continuing to call remote models.

## Brand Agnostic

The system must not hard-code Define, Scuba, Align, Wunder Train, Dance Studio, or any single product family as business logic.

The data model and recognition pipeline must work through generic concepts:

- Brand
- Category
- Product
- Product Family
- Variant
- Color
- Material
- Structure
- Evidence
- Confidence

## Visual Matching Role

Lightweight image signatures, color summaries, and hashes are prefilters. They are not the sole source of product truth.

A valid product decision must be routed through the central Confidence Engine and must expose Evidence Engine output.

## Human Review

Unknown, low confidence, conflict, duplicate, and near duplicate items must enter Human Review Queue.

Human correction may add Reality Truth evidence and improve future recognition, but it must not overwrite Official Truth.

## Reserved Future Modules

Success Library, Negative Library, Commercial Score, Trend Timeline, Region Layer, and Learning Feedback Loop are reserved-only in Phase 1.

Phase 1 may reserve database tables, source types, API design entries, and architecture extension points for these modules, but it must not implement their logic, pages, scoring, timeline analysis, region algorithms, or automated learning feedback.

Current priority remains:

- Official Catalog
- Official Assets
- Product DNA
- Product Structure
- Confidence
- Evidence
- Review Queue

The system must recognize products first, then later learn how people wear them, and only after that study commercial or trend behavior.

## Production Batch Ingestion

Phase 1 must handle real production material packages, including 10,000+ mixed images, without assuming clean filenames, clean folders, one product per image, clear images, or successful recognition for every item.

Batch upload and zip import must preserve originals, create a batch_id, keep per-file status, and avoid failing an entire batch because one file is corrupted, duplicated, low quality, or unknown.

Official Catalog must not block Raw Asset Ingestion. Users may upload raw images or zip files before the Official Catalog exists.

When Official Catalog is missing, product identity matching must be paused with `product_matching_status = blocked_missing_official_catalog` or equivalent Unknown identity status. The system must still preserve raw files, thumbnails, metadata, unsupported file records, duplicate status, quality status, and coarse classification.

Official Catalog is a prerequisite for Product Matching and Final Product Identification only.

Zip uploads must be streamed to disk. The API must not read the entire zip into memory in one operation.

Large-batch import endpoints must return small summaries. Asset-level details must be fetched through paginated APIs.

Unsupported files inside zip imports must be recorded in batch progress instead of silently ignored.

## Official Site Learning

The primary Official Catalog creation flow is Official Site Learning.

The user provides one or more of:

- brand name
- official website URL
- category URL
- product URL
- official page/image entry points

The system is responsible for learning public official product data when access is allowed:

- product names
- categories
- colors
- materials
- official white-background images
- official model images
- official detail images
- Official Product Catalog
- Official Product Assets
- Official Product DNA

Official Site Learning is a multi-stage bootstrap flow:

1. Try the provided official homepage, category URL, or product URL.
2. If that is insufficient, try public sitemap URLs.
3. If sitemap data is readable, extract product, category, collection, or catalog candidate URLs.
4. Try the public candidate URLs before asking for manual review.
5. If compliant access is blocked or data remains insufficient, return `official_site_learning_partial` or `needs_manual_review`.

Manual import must not be the first response to incomplete homepage learning.

Official Site Learning must be auditable. Each run records:

- `official_catalog_import_jobs`
- `official_url_candidates`
- `official_parse_events`

If no official products or assets are created, the run must remain `missing`, `blocked`, `partial`, or `needs_manual_review`; it must not be described as complete.

CSV/JSON Manual Import is fallback only. It should be used when official site learning cannot proceed because public access is not available, robots.txt disallows access, the page requires login, the site is region blocked, or anti-bot protections prevent compliant access.

Ordinary user uploads default to Reality Truth. A filename containing words such as "official" must not create Official Truth. Official Truth may only enter through Official Catalog or explicit official visual reference import paths.

Local hashes, thumbnails, EXIF, dimensions, and coarse classification are ingestion infrastructure. They can prefilter and organize work, but they must not become final product identity.

Multi-product photos must not be forced into a single product match. They require region/candidate structures and review when uncertain.

## Pagination Boundary

Large operational endpoints must be paginated and filterable.

The following endpoints must not return the entire database by default:

- `GET /api/assets`
- `GET /api/review-queue`
- `GET /api/observations`
- `GET /api/knowledge-cards`

## DNA Truth Layer Separation

Product knowledge must remain split by truth layer:

- OfficialProductDNA: official catalog and official visual reference evidence
- RealityProductDNA: user uploads, employee photos, buyer photos, real wearing photos, and real details
- CommunityDNA: future public social/community evidence

OfficialProductDNA can be supplemented only by official evidence. RealityProductDNA and CommunityDNA must not overwrite OfficialProductDNA.

## Review Learning Loop

Resolving a Review Queue item must create auditable learning evidence.

At minimum, a resolution should update the target label or observation when provided, record a human correction, store the correction reason, mark that rebuild is required, and keep the source as Reality Truth unless the correction came through an official-only import path.
