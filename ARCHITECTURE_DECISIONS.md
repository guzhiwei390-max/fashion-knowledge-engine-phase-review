# Architecture Decisions

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
