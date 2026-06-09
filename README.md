# Fashion Knowledge Engine Phase 1

Phase 1 implements a learning system, not an image gallery.

Required flow:

```text
Official Catalog Importer
-> Official Product Catalog
-> Official Product Assets
-> Official Product Visual Reference Library
-> User Upload
-> Official Catalog Match
-> Conditional Vision Analysis
-> Product Structure Evidence
-> Product DNA
-> Knowledge Card
-> Retrieval API
```

If a user image cannot match the Official Product Catalog, the system returns `Unknown`.

Data source priority:

```text
Official Catalog = first source of truth
Vision = second source, used for verification and structure details
Unknown = higher priority than guessing
```

OpenAI Vision is not called for every image. It is called only when:

- official visual reference matching fails
- match confidence is below threshold
- structure details are needed and are not already supported by official visual references

OpenAI Vision cannot create products, accessories, categories, or catalog facts outside Official Product Catalog.

## Run

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Official Catalog Import

Before uploading user assets, import official products via:

- Admin UI: `Official Catalog Import`
- API: `POST /api/catalog/import`
- API: `POST /api/catalog/import-url`
- API: `POST /api/catalog/import-tree`
- Admin UI: `Official Visual Reference`

CSV columns:

```csv
brand,product_name,aliases,category,description,colors,material,official_url,official_white_bg,official_model,official_detail,official_fabric
```

The URL importer reads one user-provided public category/product page only. It checks `robots.txt` and returns `Needs Manual Import` if access is blocked, unclear, login-gated, rate-limited, or not extractable.

For category pages, the importer extracts a public product list and marks records as `catalog_page_import`.

For category trees, the importer starts from one user-provided official category root URL, follows only same-site public category links, caps page count, waits between requests, checks `robots.txt` for every page, and marks records as `catalog_tree_import`.

For single product pages, the importer marks records as `product_page_import`. It does not pretend that a single product page is a full catalog.

Official product assets must become visual references. The importer attempts to create signatures from public product images. If it cannot create a visual reference, upload official white-bg/model/detail images manually in the admin UI.

Official images are used only as product identification and classification reference. They are not treated as commercial-use rights.

## Product Structure Understanding

Phase 1 stores structure as evidence, not placeholders. Product DNA includes:

- `collar`
- `zipper`
- `logo_position`
- `back_structure`
- `material_behavior`

Each field is an evidence object with `result`, `value`, `confidence`, `source`, `evidence_asset_ids`, and `visible_evidence`.

If structure cannot be proven from official visual reference, OpenAI Vision structure analysis, or human correction, the field remains `Unknown`.

## Reserved Pipelines

The database is reserved for two future ingestion paths without enabling External Knowledge ingestion yet:

- `internal_upload`: user-owned image uploads and zip imports
- `external_knowledge`: future public/community knowledge ingestion

Reserved source tables:

- `source_type_registry`
- `ingestion_sources`
- `pipeline_runs`
- `external_knowledge_items`
- `review_queue`
- `product_aliases`
- `knowledge_source_index`

Truth priority is fixed:

```text
Official Truth > Reality Truth > Community Truth
```

Official Truth is locked in `official_products` with `truth_layer`, `truth_locked`, `official_fields_json`, and `supplemental_fields_json`. Future Reality or Community evidence may supplement records, but must not overwrite official fields.

Knowledge outputs are also reserved with `source_type`, `pipeline_type`, and `truth_layer` on `dna_records`, `knowledge_cards`, and `retrieval_queries`. This keeps Official Truth, Reality Truth, and Community Truth independently marked for future independent retrieval.

Read-only reservation APIs:

- `GET /api/pipelines/design`
- `GET /api/source-types`

These endpoints expose the future API contract and source registry only. They do not enable external website, social, or community ingestion.

## APIs

- `POST /api/catalog/import`
- `POST /api/catalog/import-url`
- `POST /api/catalog/import-tree`
- `GET /api/catalog`
- `POST /api/catalog/visual-reference`
- `GET /api/catalog/assets`
- `GET /api/catalog/visual-references`
- `POST /api/upload`
- `POST /api/import/zip`
- `POST /api/jobs/process`
- `GET /api/pipelines/design`
- `GET /api/source-types`
- `GET /api/batches`
- `GET /api/assets`
- `GET /api/jobs`
- `GET /api/knowledge-cards`
- `GET /api/search?brand=Lululemon&product=Define%20Jacket`

## Tests

```powershell
python -m pytest -q
```
