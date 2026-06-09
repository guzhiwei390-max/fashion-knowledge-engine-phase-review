import tempfile
import uuid
import json
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .assets import batch_progress, import_zip_file, save_upload_file
from .catalog import (
    add_official_visual_reference,
    import_catalog_file,
    import_catalog_tree_url,
    import_catalog_url,
    list_catalog,
    list_official_assets,
    list_visual_references,
    require_catalog_ready,
)
from .config import DATA_DIR, UPLOAD_DIR
from .database import connect, decode_json, init_db
from .knowledge import build_knowledge, list_knowledge_cards, search_knowledge
from .pipelines import RESERVED_EXTENSION_MODULES, pipeline_design
from .review import list_review_items, resolve_review_item
from .unknown import unknown_response
from .vision import latest_observations, process_pending_jobs

app = FastAPI(title="Fashion Knowledge Engine Phase 1")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
def startup() -> None:
    init_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


@app.get("/", response_class=HTMLResponse)
def admin() -> str:
    return ADMIN_HTML


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "phase": 1, "engine": "Fashion Knowledge Engine"}


@app.get("/api/pipelines/design")
def pipelines_design() -> dict:
    return pipeline_design()


@app.get("/api/extensions/reserved")
def reserved_extensions() -> dict:
    return {
        "status": "reserved_only",
        "phase1_rule": "No Success Library, Negative Library, Commercial Score, Trend Timeline, Region Layer, or Learning Feedback Loop logic is active in Phase 1.",
        "current_priority": [
            "Official Catalog",
            "Official Assets",
            "Product DNA",
            "Product Structure",
            "Confidence",
            "Evidence",
            "Review Queue",
        ],
        "modules": RESERVED_EXTENSION_MODULES,
    }


@app.get("/api/source-types")
def source_types() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM source_type_registry ORDER BY truth_priority DESC, source_type").fetchall()
    return {"source_types": [dict(row) for row in rows]}


@app.post("/api/upload")
async def upload_images(files: Annotated[list[UploadFile], File(...)]) -> dict:
    require_catalog_ready()
    batch_id = str(uuid.uuid4())
    imported = []
    for file in files:
        imported.append(await save_upload_file(file, batch_id))
    return {"batch_id": batch_id, "assets": imported}


@app.post("/api/import/zip")
async def import_zip(file: Annotated[UploadFile, File(...)]) -> dict:
    require_catalog_ready()
    if not file.filename or Path(file.filename).suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="Only .zip files are supported")
    batch_id = str(uuid.uuid4())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=DATA_DIR) as temp:
        temp.write(await file.read())
        temp_path = Path(temp.name)
    try:
        imported = import_zip_file(temp_path, batch_id)
    finally:
        temp_path.unlink(missing_ok=True)
    return {"batch_id": batch_id, "assets": imported}


@app.post("/api/catalog/import")
async def import_catalog(file: Annotated[UploadFile, File(...)]) -> dict:
    return await import_catalog_file(file)


@app.post("/api/catalog/import-url")
async def import_catalog_from_url(
    url: Annotated[str, Form()],
    brand: Annotated[str, Form()],
    expected_page_type: Annotated[str, Form()] = "catalog_page",
) -> dict:
    return await import_catalog_url(url, brand, expected_page_type=expected_page_type)


@app.post("/api/catalog/import-tree")
async def import_catalog_tree(
    url: Annotated[str, Form()],
    brand: Annotated[str, Form()],
    max_pages: Annotated[int, Form()] = 8,
) -> dict:
    return await import_catalog_tree_url(url, brand, max_pages=max_pages)


@app.get("/api/catalog")
def catalog() -> dict:
    return {"products": list_catalog()}


@app.get("/api/catalog/assets")
def catalog_assets() -> dict:
    return {"official_assets": list_official_assets()}


@app.get("/api/catalog/visual-references")
def catalog_visual_references() -> dict:
    return {"visual_references": list_visual_references()}


@app.post("/api/catalog/visual-reference")
async def upload_official_visual_reference(
    product_id: Annotated[str, Form()],
    asset_type: Annotated[str, Form()],
    file: Annotated[UploadFile, File(...)],
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Image file is required")
    suffix = Path(file.filename).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=DATA_DIR) as temp:
        temp.write(await file.read())
        temp_path = Path(temp.name)
    try:
        return add_official_visual_reference(
            product_id=product_id,
            image_path=temp_path,
            original_name=file.filename,
            asset_type=asset_type,
            storage_dir=DATA_DIR,
            import_type="manual_visual_reference",
        )
    finally:
        temp_path.unlink(missing_ok=True)


@app.post("/api/jobs/process")
def process_jobs(limit: int = 5000) -> dict:
    job_result = process_pending_jobs(limit=limit)
    knowledge_result = build_knowledge()
    return {"jobs": job_result, "knowledge": knowledge_result}


@app.get("/api/assets")
def list_assets() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM assets ORDER BY created_at DESC").fetchall()
    return {"assets": [dict(row) for row in rows]}


@app.get("/api/jobs")
def list_jobs() -> dict:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT analysis_jobs.*, assets.original_name
            FROM analysis_jobs
            JOIN assets ON assets.id = analysis_jobs.asset_id
            ORDER BY analysis_jobs.created_at DESC
            """
        ).fetchall()
    return {"jobs": [dict(row) for row in rows]}


@app.get("/api/batches")
def list_batches() -> dict:
    return {"batches": batch_progress()}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict:
    rows = batch_progress(batch_id)
    if not rows:
        return unknown_response("batch_id")
    return {"batch": rows[0]}


@app.post("/api/batches/{batch_id}/retry")
def retry_batch(batch_id: str) -> dict:
    with connect() as conn:
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'queued', error_message = NULL
            WHERE asset_id IN (SELECT id FROM assets WHERE upload_batch_id = ?)
              AND status IN ('failed', 'paused')
            """,
            (batch_id,),
        )
        conn.execute("UPDATE asset_batches SET status = 'queued', updated_at = datetime('now') WHERE id = ?", (batch_id,))
    return {"status": "queued", "batch_id": batch_id}


@app.post("/api/batches/{batch_id}/pause")
def pause_batch(batch_id: str) -> dict:
    with connect() as conn:
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'paused'
            WHERE asset_id IN (SELECT id FROM assets WHERE upload_batch_id = ?)
              AND status IN ('queued', 'pending', 'failed')
            """,
            (batch_id,),
        )
        conn.execute("UPDATE asset_batches SET status = 'paused', updated_at = datetime('now') WHERE id = ?", (batch_id,))
    return {"status": "paused", "batch_id": batch_id}


@app.post("/api/batches/{batch_id}/resume")
def resume_batch(batch_id: str) -> dict:
    with connect() as conn:
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'queued'
            WHERE asset_id IN (SELECT id FROM assets WHERE upload_batch_id = ?)
              AND status = 'paused'
            """,
            (batch_id,),
        )
        conn.execute("UPDATE asset_batches SET status = 'queued', updated_at = datetime('now') WHERE id = ?", (batch_id,))
    return {"status": "queued", "batch_id": batch_id}


@app.post("/api/batches/{batch_id}/vision-budget")
async def update_batch_vision_budget(batch_id: str, payload: dict) -> dict:
    max_calls = int(payload.get("max_vision_calls_per_batch", 100))
    cost_limit = float(payload.get("cost_limit", 0.30))
    confirmed = 1 if payload.get("confirmed", False) else 0
    if max_calls < 0 or cost_limit < 0:
        return unknown_response("max_vision_calls_per_batch", "cost_limit")
    with connect() as conn:
        conn.execute(
            """
            UPDATE asset_batches
            SET max_vision_calls_per_batch = ?,
                cost_limit = ?,
                require_manual_confirm_before_large_vision_run = ?,
                vision_status = 'within_budget',
                status = CASE WHEN status = 'paused' THEN 'queued' ELSE status END,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (max_calls, cost_limit, 0 if confirmed else 1, batch_id),
        )
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'queued', error_message = NULL
            WHERE asset_id IN (SELECT id FROM assets WHERE upload_batch_id = ?)
              AND status = 'paused'
              AND error_message LIKE 'Vision budget exhausted%'
            """,
            (batch_id,),
        )
    return {
        "status": "updated",
        "batch_id": batch_id,
        "max_vision_calls_per_batch": max_calls,
        "cost_limit": cost_limit,
        "confirmed": bool(confirmed),
    }


@app.get("/api/observations")
def observations() -> dict:
    return {"observations": latest_observations()}


@app.get("/api/review-queue")
def review_queue(status: str | None = None) -> dict:
    with connect() as conn:
        return {"review_queue": list_review_items(conn, status=status)}


@app.post("/api/review-queue/{review_id}/resolve")
async def resolve_review_queue_item(review_id: str, resolution: dict) -> dict:
    with connect() as conn:
        result = resolve_review_item(
            conn,
            review_id=review_id,
            resolution=resolution,
            resolved_by=str(resolution.get("resolved_by", "admin")),
        )
    build_knowledge()
    return result


@app.get("/api/knowledge-cards")
def knowledge_cards() -> dict:
    return {"knowledge_cards": list_knowledge_cards()}


@app.get("/api/search")
def search_get(brand: str | None = None, product: str | None = None, scene: str | None = None) -> dict:
    query = {"brand": brand, "product": product, "scene": scene}
    return search_knowledge(query)


@app.post("/api/search")
async def search_post(query: dict) -> dict:
    return search_knowledge(query)


@app.post("/api/corrections")
def create_correction(
    target_type: Annotated[str, Form()],
    target_id: Annotated[str, Form()],
    field_name: Annotated[str, Form()],
    new_value: Annotated[str, Form()],
    corrected_by: Annotated[str, Form()] = "admin",
) -> dict:
    if not target_type or not target_id or not field_name or not new_value:
        return unknown_response("target_type", "target_id", "field_name", "new_value")
    with connect() as conn:
        old_value = None
        if target_type == "vision_observation":
            row = conn.execute("SELECT structured_output FROM vision_observations WHERE id = ?", (target_id,)).fetchone()
            if row:
                structured = decode_json(row["structured_output"], {})
                old_value = structured.get(field_name)
                structured[field_name] = new_value
                unknown_fields = [field for field in structured.get("unknown_fields", []) if field != field_name]
                structured["unknown_fields"] = unknown_fields
                conn.execute(
                    "UPDATE vision_observations SET structured_output = ?, unknown_fields = ? WHERE id = ?",
                    (
                        json.dumps(structured, ensure_ascii=False, sort_keys=True),
                        json.dumps(unknown_fields, ensure_ascii=False, sort_keys=True),
                        target_id,
                    ),
                )
        conn.execute(
            """
            INSERT INTO human_corrections (
                id, target_type, target_id, field_name, old_value, new_value, corrected_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (str(uuid.uuid4()), target_type, target_id, field_name, old_value, new_value, corrected_by),
        )
    build_knowledge()
    return {"status": "ok"}


ADMIN_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Fashion Knowledge Engine</title>
  <style>
    :root {
      --bg: #f6f4ef;
      --panel: #ffffff;
      --ink: #1d1d1b;
      --muted: #6f6a60;
      --line: #ded8cc;
      --accent: #0f6b5f;
      --danger: #a2342f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    header {
      padding: 28px 36px 18px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: end;
    }
    h1 { margin: 0; font-size: 28px; letter-spacing: 0; }
    .subtitle { margin-top: 8px; color: var(--muted); font-size: 14px; }
    main { padding: 24px 36px 40px; display: grid; gap: 18px; }
    .grid { display: grid; grid-template-columns: 360px 1fr; gap: 18px; align-items: start; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }
    h2 { margin: 0 0 14px; font-size: 16px; }
    form { display: grid; gap: 12px; }
    input, button {
      font: inherit;
      border-radius: 6px;
      border: 1px solid var(--line);
      padding: 10px 12px;
      background: #fff;
    }
    button {
      border-color: var(--accent);
      background: var(--accent);
      color: white;
      cursor: pointer;
      font-weight: 650;
    }
    button.secondary { background: white; color: var(--accent); }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; }
    .cards { display: grid; gap: 12px; }
    .item {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fffdf9;
    }
    .meta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    pre {
      overflow: auto;
      background: #171a18;
      color: #e9f3ee;
      padding: 12px;
      border-radius: 6px;
      font-size: 12px;
      max-height: 340px;
    }
    .unknown { color: var(--danger); font-weight: 700; }
    @media (max-width: 900px) {
      header { display: block; padding: 22px; }
      main { padding: 18px; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Fashion Knowledge Engine</h1>
      <div class="subtitle">Phase 1: Upload → Vision → Structure → DNA → Knowledge Card → Retrieve. Unknown first.</div>
    </div>
    <div class="toolbar">
      <button class="secondary" onclick="processJobs()">Process Queue</button>
      <button class="secondary" onclick="refreshAll()">Refresh</button>
    </div>
  </header>
  <main>
    <div class="grid">
      <section>
        <h2>Official Catalog Import</h2>
        <form id="catalogForm">
          <input type="file" name="file" accept=".csv,.json" />
          <button>Import Official Catalog</button>
        </form>
        <div class="meta" style="margin-top:10px">User uploads are blocked until the official catalog exists.</div>
        <form id="catalogUrlForm" style="margin-top:12px">
          <input name="brand" placeholder="Brand, e.g. Lululemon" />
          <input name="url" placeholder="Official product/category URL" />
          <input name="expected_page_type" value="catalog_page" />
          <button>Import Public URL</button>
        </form>
        <div class="meta" style="margin-top:10px">URL importer reads one public category/product page only, checks robots.txt, and returns Needs Manual Import when access is not clearly allowed.</div>
        <form id="catalogTreeForm" style="margin-top:12px">
          <input name="brand" placeholder="Brand, e.g. Lululemon" />
          <input name="url" placeholder="Official category tree root URL" />
          <input name="max_pages" value="8" />
          <button>Import Category Tree</button>
        </form>
        <div class="meta" style="margin-top:10px">Category-tree import follows only same-site public category links, respects robots.txt, uses low request volume, and marks records as catalog_tree_import.</div>
        <h2 style="margin-top:22px">Official Visual Reference</h2>
        <form id="visualRefForm">
          <input name="product_id" placeholder="Official product id" />
          <input name="asset_type" value="official_white_bg" />
          <input type="file" name="file" accept="image/*" />
          <button>Upload Visual Reference</button>
        </form>
        <h2 style="margin-top:22px">Catalog</h2>
        <div id="catalog" class="cards"></div>
        <h2 style="margin-top:22px">Official Visual Assets</h2>
        <div id="officialAssets" class="cards"></div>
        <h2 style="margin-top:22px">Visual Reference Library</h2>
        <div id="visualReferences" class="cards"></div>
      </section>
      <section>
        <h2>Batch Upload</h2>
        <form id="uploadForm">
          <input type="file" name="files" multiple accept="image/*" />
          <button>Upload Images</button>
        </form>
        <h2 style="margin-top:22px">Zip Import</h2>
        <form id="zipForm">
          <input type="file" name="file" accept=".zip" />
          <button>Import Zip</button>
        </form>
        <h2 style="margin-top:22px">Search</h2>
        <form id="searchForm">
          <input name="brand" placeholder="Brand, e.g. Lululemon" />
          <input name="product" placeholder="Product, e.g. Define Jacket" />
          <input name="scene" placeholder="Scene, optional" />
          <button>Search Knowledge</button>
        </form>
      </section>
      <section>
        <h2>Result</h2>
        <pre id="result">{ "status": "ready" }</pre>
      </section>
    </div>
    <section>
      <h2>Human Review Queue</h2>
      <div id="reviewQueue" class="cards"></div>
    </section>
    <section>
      <h2>Knowledge Cards</h2>
      <div id="cards" class="cards"></div>
    </section>
    <section>
      <h2>Batch Progress</h2>
      <div id="batches" class="cards"></div>
    </section>
    <section>
      <h2>Human Correction</h2>
      <div class="meta">Use this for Unknown or wrong matches. Corrections are audited.</div>
      <div id="observations" class="cards" style="margin-top:12px"></div>
    </section>
    <section>
      <h2>Assets & Jobs</h2>
      <div class="grid">
        <div><h2>Assets</h2><div id="assets" class="cards"></div></div>
        <div><h2>Jobs</h2><div id="jobs" class="cards"></div></div>
      </div>
    </section>
  </main>
  <script>
    const result = document.querySelector("#result");
    const show = data => result.textContent = JSON.stringify(data, null, 2);
    const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    async function postForm(url, form) {
      const res = await fetch(url, { method: "POST", body: new FormData(form) });
      const data = await res.json();
      show(data);
      await refreshAll();
    }
    document.querySelector("#uploadForm").addEventListener("submit", e => {
      e.preventDefault(); postForm("/api/upload", e.currentTarget);
    });
    document.querySelector("#zipForm").addEventListener("submit", e => {
      e.preventDefault(); postForm("/api/import/zip", e.currentTarget);
    });
    document.querySelector("#searchForm").addEventListener("submit", async e => {
      e.preventDefault();
      const params = new URLSearchParams(new FormData(e.currentTarget));
      const res = await fetch(`/api/search?${params}`);
      show(await res.json());
    });
    async function processJobs() {
      const res = await fetch("/api/jobs/process", { method: "POST" });
      show(await res.json());
      await refreshAll();
    }
    async function refreshAll() {
      const [assets, jobs, cards, catalog, officialAssets, visualReferences, observations, batches, reviewQueue] = await Promise.all([
        fetch("/api/assets").then(r => r.json()),
        fetch("/api/jobs").then(r => r.json()),
        fetch("/api/knowledge-cards").then(r => r.json()),
        fetch("/api/catalog").then(r => r.json()),
        fetch("/api/catalog/assets").then(r => r.json()),
        fetch("/api/catalog/visual-references").then(r => r.json()),
        fetch("/api/observations").then(r => r.json()),
        fetch("/api/batches").then(r => r.json()),
        fetch("/api/review-queue?status=pending").then(r => r.json())
      ]);
      renderAssets(assets.assets);
      renderJobs(jobs.jobs);
      renderCards(cards.knowledge_cards);
      renderCatalog(catalog.products);
      renderOfficialAssets(officialAssets.official_assets);
      renderVisualReferences(visualReferences.visual_references);
      renderObservations(observations.observations);
      renderBatches(batches.batches);
      renderReviewQueue(reviewQueue.review_queue);
    }
    document.querySelector("#catalogForm").addEventListener("submit", e => {
      e.preventDefault(); postForm("/api/catalog/import", e.currentTarget);
    });
    document.querySelector("#catalogUrlForm").addEventListener("submit", e => {
      e.preventDefault(); postForm("/api/catalog/import-url", e.currentTarget);
    });
    document.querySelector("#catalogTreeForm").addEventListener("submit", e => {
      e.preventDefault(); postForm("/api/catalog/import-tree", e.currentTarget);
    });
    document.querySelector("#visualRefForm").addEventListener("submit", e => {
      e.preventDefault(); postForm("/api/catalog/visual-reference", e.currentTarget);
    });
    function item(html) { return `<div class="item">${html}</div>`; }
    function renderAssets(rows) {
      document.querySelector("#assets").innerHTML = rows.map(a => item(`<strong>${esc(a.original_name)}</strong><div class="meta">${esc(a.id)}<br>${esc(a.source_type)} / ${esc(a.knowledge_layer)}</div>`)).join("") || "<div class='meta'>No assets</div>";
    }
    function renderJobs(rows) {
      document.querySelector("#jobs").innerHTML = rows.map(j => item(`<strong>${esc(j.status)}</strong> <span class="${j.status === "failed" ? "unknown" : ""}">${esc(j.original_name)}</span><div class="meta">${esc(j.error_message || "")}</div>`)).join("") || "<div class='meta'>No jobs</div>";
    }
    function renderCards(rows) {
      document.querySelector("#cards").innerHTML = rows.map(c => item(`<strong>${esc(c.brand)} / ${esc(c.product_name)}</strong><div class="meta">Evidence: ${c.evidence_asset_ids.length} | Unknown: ${esc(c.unknown_fields.join(", ") || "none")}</div><pre>${esc(JSON.stringify(c.card_json, null, 2))}</pre>`)).join("") || "<div class='meta'>No knowledge cards yet</div>";
    }
    function renderCatalog(rows) {
      document.querySelector("#catalog").innerHTML = rows.map(p => item(`<strong>${esc(p.brand)} / ${esc(p.product_name)}</strong><div class="meta">ID: ${esc(p.id)}<br>${esc(p.category)} | ${esc(p.material)} | ${esc(p.import_type)}<br>Aliases: ${esc(p.aliases.join(", ") || "none")}</div>`)).join("") || "<div class='meta'>Official catalog is empty</div>";
    }
    function renderOfficialAssets(rows) {
      document.querySelector("#officialAssets").innerHTML = rows.map(a => item(`<strong>${esc(a.brand)} / ${esc(a.product_name)}</strong><div class="meta">${esc(a.asset_type)} | ${esc(a.import_type)}<br>visual signature: ${a.visual_signature && a.visual_signature.result !== "Unknown" ? "ready" : "missing"}</div>`)).join("") || "<div class='meta'>No official visual references</div>";
    }
    function renderVisualReferences(rows) {
      document.querySelector("#visualReferences").innerHTML = rows.map(r => item(`<strong>${esc(r.brand)} / ${esc(r.product_name)}</strong><div class="meta">${esc(r.asset_type)}<br>signature: ${r.visual_signature && r.visual_signature.result !== "Unknown" ? "ready" : "missing"}<br>structure: ${Object.keys(r.structure_json || {}).length ? "ready" : "pending"}</div>`)).join("") || "<div class='meta'>No visual reference library entries</div>";
    }
    function renderBatches(rows) {
      document.querySelector("#batches").innerHTML = rows.map(b => item(`<strong>${esc(b.id)}</strong><div class="meta">status ${esc(b.status)} / vision ${esc(b.vision_status || "within_budget")}<br>total ${b.total_files || 0} / ingested ${b.ingested || 0} / duplicated ${b.duplicated || 0} / corrupted ${b.corrupted || 0} / low quality ${b.low_quality || 0}<br>coarse ${b.coarse_classified || 0} / matched ${b.matched || 0} / unknown ${b.unknown || 0} / review ${b.review_needed || 0} / failed ${b.failed || 0}<br>vision calls ${b.vision_calls_used || b.openai_vision_calls_used || 0} / max ${b.max_vision_calls_per_batch || 0} / est. cost ${b.estimated_cost || 0}</div>`)).join("") || "<div class='meta'>No batches</div>";
    }
    function renderReviewQueue(rows) {
      document.querySelector("#reviewQueue").innerHTML = rows.map(r => item(`<strong>${esc(r.reason)}</strong><div class="meta">${esc(r.item_type)} / ${esc(r.item_id)}<br>confidence: ${esc(r.confidence)}<br>${esc(JSON.stringify(r.review_payload || {}))}</div>`)).join("") || "<div class='meta'>No pending review items</div>";
    }
    function renderObservations(rows) {
      document.querySelector("#observations").innerHTML = rows.map(o => {
        const s = o.structured_output || {};
        return item(`<strong>${esc(o.original_name)}</strong><div class="meta">Observation: ${esc(o.id)}<br>Product: ${esc(s.product_name)} | Match: ${esc((s.product_match || {}).method)} ${(s.product_match || {}).confidence || 0}<br>Unknown: ${esc((o.unknown_fields || []).join(", ") || "none")}</div><form onsubmit="submitCorrection(event)"><input name="target_type" value="vision_observation" /><input name="target_id" value="${esc(o.id)}" /><input name="field_name" placeholder="field_name, e.g. product_name" /><input name="new_value" placeholder="correct value" /><input name="corrected_by" value="admin" /><button>Save Correction</button></form>`);
      }).join("") || "<div class='meta'>No observations</div>";
    }
    async function submitCorrection(event) {
      event.preventDefault();
      await postForm("/api/corrections", event.currentTarget);
    }
    refreshAll();
  </script>
</body>
</html>
"""
