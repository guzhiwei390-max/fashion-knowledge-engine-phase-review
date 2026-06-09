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

Zip uploads must be streamed to disk. The API must not read the entire zip into memory in one operation.

Large-batch import endpoints must return small summaries. Asset-level details must be fetched through paginated APIs.

Unsupported files inside zip imports must be recorded in batch progress instead of silently ignored.

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
