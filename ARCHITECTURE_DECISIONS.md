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

OpenAI Vision is a secondary verification and structure-detail source. It cannot create a product that does not exist in the Official Product Catalog.

If Vision sees a possible product but the product cannot be mapped back to `official_products`, the result must be `Unknown`.

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
