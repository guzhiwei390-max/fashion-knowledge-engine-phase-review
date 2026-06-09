# Fashion Knowledge Engine Phase 1

Phase 1 implements a learning system, not an image gallery.

Required flow:

```text
Official Catalog Importer
→ Official Product Catalog
→ Official Product Assets
→ Official Product Visual Reference Library
→ User Upload
→ Official Catalog Match
→ Conditional Vision Analysis
→ Product DNA
→ Knowledge Card
→ Retrieval API
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
- Admin UI: `Official Visual Reference`

CSV columns:

```csv
brand,product_name,aliases,category,description,colors,material,official_url,official_white_bg,official_model,official_detail,official_fabric
```

The URL importer reads one user-provided public category/product page only. It checks `robots.txt` and returns `Needs Manual Import` if access is blocked, unclear, login-gated, rate-limited, or not extractable.

For category pages, the importer extracts a public product list and marks records as `catalog_page_import`.

For single product pages, the importer marks records as `product_page_import`. It does not pretend that a single product page is a full catalog.

Official product assets must become visual references. The importer attempts to create signatures from public product images. If it cannot create a visual reference, upload official white-bg/model/detail images manually in the admin UI.

Official images are used only as product identification and classification reference. They are not treated as commercial-use rights.

## APIs

- `POST /api/catalog/import`
- `POST /api/catalog/import-url`
- `GET /api/catalog`
- `POST /api/catalog/visual-reference`
- `GET /api/catalog/assets`
- `GET /api/catalog/visual-references`
- `POST /api/upload`
- `POST /api/import/zip`
- `POST /api/jobs/process`
- `GET /api/batches`
- `GET /api/assets`
- `GET /api/jobs`
- `GET /api/knowledge-cards`
- `GET /api/search?brand=Lululemon&product=Define%20Jacket`

## Tests

```powershell
python -m pytest -q
```
