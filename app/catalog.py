import csv
import io
import uuid
import json
import re
import shutil
import tempfile
import asyncio
import urllib.parse
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
import httpx

from .config import DATA_DIR, KNOWN_BRANDS, MAX_UPLOAD_BYTES
from .database import connect, decode_json, encode_json, utc_now
from .unknown import UNKNOWN
from .visual import encode_signature_for_db, image_signature, signature_similarity


REQUIRED_FIELDS = {"brand", "product_name", "category"}
USER_AGENT = "FashionKnowledgeEnginePhase1/0.1 (+manual-catalog-import)"
VISUAL_MATCH_THRESHOLD = 0.88
MAX_CATEGORY_PRODUCTS = 80
MAX_OFFICIAL_IMAGES_PER_PRODUCT = 6
MAX_CATEGORY_TREE_PAGES = 12
CATEGORY_TREE_DELAY_SECONDS = 0.75
BOOTSTRAP_USER_MESSAGE = (
    "Assets can continue to be ingested. Official catalog learning is incomplete, "
    "so product identity confirmation is paused. Provide a brand category URL, "
    "product URL, or official screenshots/product images as candidate references. "
    "Manual CSV/JSON is fallback only."
)


def normalize(value: str) -> str:
    return " ".join(value.lower().replace("-", " ").replace("_", " ").split())


def canonical_brand(value: str) -> str:
    normalized = normalize(value)
    if normalized in KNOWN_BRANDS:
        return KNOWN_BRANDS[normalized]
    compact = normalized.replace(" ", "")
    if compact in KNOWN_BRANDS:
        return KNOWN_BRANDS[compact]
    raise HTTPException(status_code=400, detail=f"Unsupported first-batch brand: {value}")


def catalog_count() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM official_products").fetchone()
        return int(row["count"])


def visual_reference_count() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM official_product_visual_references").fetchone()
        return int(row["count"])


def official_asset_count() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM official_product_assets").fetchone()
        return int(row["count"])


def require_catalog_ready() -> None:
    if catalog_count() == 0:
        raise HTTPException(
            status_code=409,
            detail={
                "result": "Unknown",
                "message": "Official Product Catalog must be imported before user assets.",
            },
        )


async def import_catalog_file(file: UploadFile) -> dict[str, Any]:
    content = await file.read()
    filename = file.filename or ""
    if filename.lower().endswith(".csv"):
        records = parse_csv(content.decode("utf-8-sig"))
    elif filename.lower().endswith(".json"):
        import json

        payload = json.loads(content.decode("utf-8"))
        records = payload["products"] if isinstance(payload, dict) and "products" in payload else payload
        if not isinstance(records, list):
            raise HTTPException(status_code=400, detail="Catalog JSON must be a list or { products: [] }")
    else:
        raise HTTPException(status_code=400, detail="Official catalog importer supports .csv and .json")
    return import_catalog_records(records)


async def import_catalog_url(url: str, brand: str, expected_page_type: str = "catalog_page") -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="A public http(s) URL is required")

    robot_status = await robots_allowed(url)
    if robot_status is not True:
        return needs_manual_import("robots.txt disallows access or could not be verified")

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError:
        return needs_manual_import("page could not be accessed")

    if response.status_code in {401, 403, 429}:
        return needs_manual_import("access denied, login required, or rate limited")
    if response.status_code >= 400:
        return needs_manual_import(f"page returned HTTP {response.status_code}")
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        return needs_manual_import("URL is not a public HTML product/category page")

    extracted = extract_catalog_records_from_html(response.text, brand, str(response.url))
    records = extracted["records"]
    if not records:
        return needs_manual_import("could not extract public product data from this page")
    import_type = determine_import_type(extracted["page_type"], records)
    if expected_page_type == "catalog_page" and import_type == "product_page_import":
        imported = import_catalog_records(records, import_type=import_type)
        imported["result"] = "Needs Catalog Page Import"
        imported["reason"] = "URL appears to be a single product page, not a category catalog page"
        imported["source_url"] = str(response.url)
        imported["import_type"] = import_type
        return imported

    imported = import_catalog_records(records, import_type=import_type)
    downloaded = await hydrate_official_visual_references(records, import_type)
    imported["source_url"] = str(response.url)
    imported["import_type"] = import_type
    imported["visual_references_created"] = downloaded
    if downloaded == 0:
        imported["result"] = "Needs Manual Import"
        imported["reason"] = "catalog data imported but no official visual reference could be created"
    return imported


async def import_catalog_tree_url(url: str, brand: str, max_pages: int = MAX_CATEGORY_TREE_PAGES) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="A public http(s) category URL is required")
    if max_pages < 1 or max_pages > MAX_CATEGORY_TREE_PAGES:
        raise HTTPException(status_code=400, detail=f"max_pages must be between 1 and {MAX_CATEGORY_TREE_PAGES}")

    pages: dict[str, str] = {}
    queue = [url]
    seen: set[str] = set()
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=15,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    ) as client:
        while queue and len(pages) < max_pages:
            current_url = queue.pop(0)
            if current_url in seen:
                continue
            seen.add(current_url)
            robot_status = await robots_allowed(current_url)
            if robot_status is not True:
                if not pages:
                    return needs_manual_import("robots.txt disallows access or could not be verified")
                continue
            try:
                response = await client.get(current_url)
            except httpx.HTTPError:
                if not pages:
                    return needs_manual_import("category page could not be accessed")
                continue
            if response.status_code in {401, 403, 429}:
                if not pages:
                    return needs_manual_import("access denied, login required, or rate limited")
                continue
            if response.status_code >= 400:
                if not pages:
                    return needs_manual_import(f"category page returned HTTP {response.status_code}")
                continue
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                if not pages:
                    return needs_manual_import("URL is not a public HTML category page")
                continue
            final_url = str(response.url)
            pages[final_url] = response.text
            for link in extract_category_links_from_html(response.text, final_url):
                if link not in seen and link not in queue and same_site(url, link):
                    queue.append(link)
            if queue and len(pages) < max_pages:
                await asyncio.sleep(CATEGORY_TREE_DELAY_SECONDS)

    if not pages:
        return needs_manual_import("could not access any public category pages")

    imported = import_catalog_tree_from_html_pages(pages, next(iter(pages)), brand, max_pages=max_pages)
    if imported.get("result") == "Needs Manual Import":
        return imported
    products = products_for_imported_records(imported.get("records", []))
    downloaded = await hydrate_official_visual_references(products, "catalog_tree_import")
    imported["visual_references_created"] = downloaded
    if downloaded == 0:
        imported["result"] = "Needs Manual Import"
        imported["reason"] = "catalog tree imported but no official visual reference could be created"
    return imported


async def bootstrap_official_catalog(
    url: str,
    brand: str,
    max_pages: int = MAX_CATEGORY_TREE_PAGES,
    category_url: str | None = None,
    product_url: str | None = None,
) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="A public official website URL is required")
    if max_pages < 1 or max_pages > MAX_CATEGORY_TREE_PAGES:
        raise HTTPException(status_code=400, detail=f"max_pages must be between 1 and {MAX_CATEGORY_TREE_PAGES}")

    canonical = canonical_brand(brand)
    job_id = create_catalog_import_job(canonical, url, category_url, product_url)
    stages: list[dict[str, Any]] = []
    before_products = catalog_count()
    before_assets = official_asset_count()
    before_references = visual_reference_count()

    robots = await fetch_robots_txt(url)
    record_parse_event(
        job_id,
        "robots_txt",
        robots["url"],
        robots["status"],
        http_status=robots.get("http_status"),
        reason=str(robots.get("reason", "")),
        metadata={"sitemap_urls": robots.get("sitemap_urls", [])},
    )

    input_urls = [url]
    if category_url:
        input_urls.append(category_url)
    if product_url:
        input_urls.append(product_url)

    for input_candidate in dedupe_urls(input_urls):
        homepage_result = await import_catalog_tree_url(input_candidate, canonical, max_pages=max_pages)
        stage = stage_from_import_result("provided_url", input_candidate, homepage_result)
        stages.append(stage)
        record_stage_event(job_id, stage)
    ready = bootstrap_ready_status(before_products, before_references)
    if ready == "ready":
        result = bootstrap_success_result(stages[-1], stages, ready, job_id=job_id, before_products=before_products, before_assets=before_assets, before_references=before_references)
        add_bootstrap_context(result, canonical, url, category_url, product_url)
        finalize_catalog_import_job(job_id, result)
        return result

    sitemap_urls = dedupe_urls([*robots.get("sitemap_urls", []), *sitemap_entry_urls(url)])
    candidate_urls: list[str] = []
    for sitemap_url in sitemap_urls:
        sitemap = await fetch_public_text(sitemap_url, accept="application/xml,text/xml,text/plain,*/*")
        stages.append(
            {
                "stage": "sitemap",
                "url": sitemap_url,
                "status": sitemap["status"],
                "reason": sitemap.get("reason", ""),
                "http_status": sitemap.get("http_status"),
            }
        )
        record_parse_event(
            job_id,
            "sitemap",
            sitemap_url,
            str(sitemap["status"]),
            http_status=sitemap.get("http_status"),
            reason=str(sitemap.get("reason", "")),
        )
        if sitemap["status"] != "read":
            continue
        for candidate in candidate_urls_from_sitemap_xml(str(sitemap["text"]), url):
            if candidate not in candidate_urls:
                candidate_urls.append(candidate)
                record_url_candidate(job_id, canonical, candidate, candidate_type_from_url(candidate), "sitemap")
        if len(candidate_urls) >= max_pages * 2:
            break

    for candidate_url in candidate_urls[:max_pages]:
        candidate_result = await import_catalog_tree_url(candidate_url, canonical, max_pages=min(4, max_pages))
        stages.append(stage_from_import_result("sitemap_candidate", candidate_url, candidate_result))
        record_stage_event(job_id, stages[-1])
        ready = bootstrap_ready_status(before_products, before_references)
        if ready == "ready":
            result = bootstrap_success_result(
                candidate_result,
                stages,
                ready,
                candidate_urls=candidate_urls,
                job_id=job_id,
                before_products=before_products,
                before_assets=before_assets,
                before_references=before_references,
            )
            add_bootstrap_context(result, canonical, url, category_url, product_url)
            finalize_catalog_import_job(job_id, result)
            return result

    if catalog_count() > before_products:
        result = bootstrap_partial_result(
            "official products were learned, but official visual references are incomplete",
            stages=stages,
            candidate_urls=candidate_urls,
            official_catalog_status="partial",
            job_id=job_id,
            before_products=before_products,
            before_assets=before_assets,
            before_references=before_references,
            robots=robots,
        )
        add_bootstrap_context(result, canonical, url, category_url, product_url)
        finalize_catalog_import_job(job_id, result)
        return result

    if candidate_urls:
        result = bootstrap_partial_result(
            "sitemap candidates were found but no extractable public product data was confirmed",
            stages=stages,
            candidate_urls=candidate_urls,
            official_catalog_status="missing",
            job_id=job_id,
            before_products=before_products,
            before_assets=before_assets,
            before_references=before_references,
            robots=robots,
        )
        add_bootstrap_context(result, canonical, url, category_url, product_url)
        finalize_catalog_import_job(job_id, result)
        return result

    blocked_reasons = [
        str(stage.get("reason", ""))
        for stage in stages
        if stage.get("status") in {"robots_blocked", "access_blocked", "unreadable"}
    ]
    result = "needs_manual_review" if blocked_reasons else "official_site_learning_partial"
    response = bootstrap_partial_result(
        blocked_reasons[0] if blocked_reasons else "homepage and sitemap did not expose an extractable public catalog",
        stages=stages,
        candidate_urls=candidate_urls,
        official_catalog_status="missing",
        result=result,
        job_id=job_id,
        before_products=before_products,
        before_assets=before_assets,
        before_references=before_references,
        robots=robots,
    )
    add_bootstrap_context(response, canonical, url, category_url, product_url)
    finalize_catalog_import_job(job_id, response)
    return response


def add_bootstrap_context(result: dict[str, Any], brand: str, input_url: str, category_url: str | None, product_url: str | None) -> None:
    result["brand"] = brand
    result["input_url"] = input_url
    if category_url:
        result["category_url"] = category_url
    if product_url:
        result["product_url"] = product_url


def bootstrap_ready_status(before_products: int, before_references: int) -> str | None:
    products_added = catalog_count() > before_products
    references_added = visual_reference_count() > before_references
    if products_added and references_added:
        return "ready"
    if products_added:
        return "partial"
    return None


def bootstrap_success_result(
    imported: dict[str, Any],
    stages: list[dict[str, Any]],
    status: str,
    candidate_urls: list[str] | None = None,
    job_id: str | None = None,
    before_products: int = 0,
    before_assets: int = 0,
    before_references: int = 0,
    robots: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product_matching_status = "ready" if status == "ready" else "blocked_missing_official_catalog"
    message = (
        "Official Catalog and official visual references were learned. Product matching can resume."
        if status == "ready"
        else BOOTSTRAP_USER_MESSAGE
    )
    return {
        **imported,
        "result": "Known" if status == "ready" else "official_site_learning_partial",
        "flow": "official_catalog_bootstrap",
        "job_id": job_id,
        "raw_asset_ingestion_status": "allowed",
        "official_catalog_status": status,
        "product_matching_status": product_matching_status,
        **bootstrap_metrics(stages, candidate_urls or [], before_products, before_assets, before_references, robots or {}),
        "stages": stages,
        "candidate_urls": (candidate_urls or [])[:MAX_CATEGORY_TREE_PAGES],
        "message": message,
    }


def bootstrap_partial_result(
    reason: str,
    *,
    stages: list[dict[str, Any]],
    candidate_urls: list[str],
    official_catalog_status: str,
    result: str = "official_site_learning_partial",
    job_id: str | None = None,
    before_products: int = 0,
    before_assets: int = 0,
    before_references: int = 0,
    robots: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "result": result,
        "flow": "official_catalog_bootstrap",
        "job_id": job_id,
        "reason": reason,
        "raw_asset_ingestion_status": "allowed",
        "official_catalog_status": official_catalog_status,
        "product_matching_status": "blocked_missing_official_catalog",
        **bootstrap_metrics(stages, candidate_urls, before_products, before_assets, before_references, robots or {}),
        "candidate_urls": candidate_urls[:MAX_CATEGORY_TREE_PAGES],
        "stages": stages,
        "message": BOOTSTRAP_USER_MESSAGE,
        "partial_reason": reason if result == "official_site_learning_partial" else "",
        "blocked_reason": reason if result == "needs_manual_review" else "",
        "next_best_action": next_best_action(official_catalog_status, candidate_urls, result),
        "fallback": "Manual CSV/JSON is final fallback only after public official URLs, candidate references, and human review cannot be used.",
    }


def bootstrap_metrics(
    stages: list[dict[str, Any]],
    candidate_urls: list[str],
    before_products: int = 0,
    before_assets: int = 0,
    before_references: int = 0,
    robots: dict[str, Any] | None = None,
) -> dict[str, int | bool | str]:
    robots = robots or {}
    sitemap_stages = [stage for stage in stages if stage.get("stage") == "sitemap"]
    fetched_stages = [stage for stage in stages if stage.get("status") == "read"]
    parsed_product_pages = [
        stage
        for stage in stages
        if int(stage.get("imported", 0) or 0) > 0
    ]
    products_created = max(0, catalog_count() - before_products)
    assets_created = max(0, official_asset_count() - before_assets)
    references_created = max(0, visual_reference_count() - before_references)
    return {
        "robots_txt_fetched": bool(robots.get("fetched", False)),
        "robots_allowed": bool(robots.get("allowed", False)),
        "robots_url": str(robots.get("url", "")),
        "sitemap_found": bool(robots.get("sitemap_urls")) or any(stage.get("status") == "read" for stage in sitemap_stages),
        "sitemap_url": str((robots.get("sitemap_urls") or [""])[0]),
        "sitemap_discovered": bool(robots.get("sitemap_urls")) or any(stage.get("status") == "read" for stage in sitemap_stages),
        "sitemap_urls_attempted": len(sitemap_stages),
        "sitemap_urls_found": len(set([*robots.get("sitemap_urls", []), *[str(stage.get("url", "")) for stage in sitemap_stages if stage.get("status") == "read"]])),
        "category_candidates_found": sum(1 for url in candidate_urls if looks_like_category_url(url) or looks_like_collection_or_catalog_url(url)),
        "product_candidates_found": sum(1 for url in candidate_urls if looks_like_product_url(url)),
        "fetched_urls_count": len(fetched_stages),
        "parsed_product_pages_count": len(parsed_product_pages),
        "official_products_created": products_created,
        "official_product_assets_created": assets_created,
        "official_visual_references_created": references_created,
    }


def next_best_action(official_catalog_status: str, candidate_urls: list[str], result: str) -> str:
    if official_catalog_status == "partial":
        return "review_candidates"
    if candidate_urls:
        return "review_candidates"
    if result == "needs_manual_review":
        return "provide_category_url"
    return "provide_product_url"


def create_catalog_import_job(brand: str, input_url: str, category_url: str | None, product_url: str | None) -> str:
    job_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO official_catalog_import_jobs (
                id, brand, input_url, optional_category_url, optional_product_url,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'learning', ?, ?)
            """,
            (job_id, brand, input_url, category_url, product_url, now, now),
        )
    return job_id


def finalize_catalog_import_job(job_id: str, result: dict[str, Any]) -> None:
    now = utc_now()
    status = "completed" if result.get("official_catalog_status") == "ready" else str(result.get("result", "partial"))
    with connect() as conn:
        conn.execute(
            """
            UPDATE official_catalog_import_jobs
            SET status = ?,
                raw_asset_ingestion_status = ?,
                official_catalog_status = ?,
                product_matching_status = ?,
                robots_txt_fetched = ?,
                robots_allowed = ?,
                sitemap_found = ?,
                sitemap_urls_found = ?,
                category_candidates_found = ?,
                product_candidates_found = ?,
                fetched_urls_count = ?,
                parsed_product_pages_count = ?,
                official_products_created = ?,
                official_product_assets_created = ?,
                official_visual_references_created = ?,
                partial_reason = ?,
                blocked_reason = ?,
                next_best_action = ?,
                result_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                result.get("raw_asset_ingestion_status", "allowed"),
                result.get("official_catalog_status", "missing"),
                result.get("product_matching_status", "blocked_missing_official_catalog"),
                1 if result.get("robots_txt_fetched") else 0,
                1 if result.get("robots_allowed") else 0,
                1 if result.get("sitemap_found") else 0,
                int(result.get("sitemap_urls_found", 0) or 0),
                int(result.get("category_candidates_found", 0) or 0),
                int(result.get("product_candidates_found", 0) or 0),
                int(result.get("fetched_urls_count", 0) or 0),
                int(result.get("parsed_product_pages_count", 0) or 0),
                int(result.get("official_products_created", 0) or 0),
                int(result.get("official_product_assets_created", 0) or 0),
                int(result.get("official_visual_references_created", 0) or 0),
                str(result.get("partial_reason", "")),
                str(result.get("blocked_reason", "")),
                str(result.get("next_best_action", "")),
                encode_json(result),
                now,
                job_id,
            ),
        )


def record_url_candidate(job_id: str, brand: str, candidate_url: str, candidate_type: str, source: str, status: str = "candidate", reason: str = "") -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO official_url_candidates (
                id, job_id, brand, candidate_url, candidate_type, source, status, reason, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, candidate_url) DO UPDATE SET
                candidate_type = excluded.candidate_type,
                source = excluded.source,
                status = excluded.status,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            """,
            (str(uuid.uuid4()), job_id, brand, candidate_url, candidate_type, source, status, reason, now, now),
        )


def record_parse_event(
    job_id: str,
    stage: str,
    url: str,
    status: str,
    *,
    http_status: int | None = None,
    reason: str = "",
    parsed_records: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO official_parse_events (
                id, job_id, stage, url, status, http_status, reason, parsed_records, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                job_id,
                stage,
                url,
                status,
                http_status,
                reason,
                parsed_records,
                encode_json(metadata or {}),
                utc_now(),
            ),
        )


def record_stage_event(job_id: str, stage: dict[str, Any]) -> None:
    record_parse_event(
        job_id,
        str(stage.get("stage", "unknown")),
        str(stage.get("url", "")),
        str(stage.get("status", "")),
        http_status=stage.get("http_status"),
        reason=str(stage.get("reason", "")),
        parsed_records=int(stage.get("imported", 0) or 0),
        metadata={"result": stage.get("result", ""), "visual_references_created": stage.get("visual_references_created", 0)},
    )


def dedupe_urls(urls: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def candidate_type_from_url(url: str) -> str:
    if looks_like_product_url(url):
        return "product"
    if looks_like_category_url(url) or looks_like_collection_or_catalog_url(url):
        return "category"
    return "unknown"


def stage_from_import_result(stage: str, url: str, result: dict[str, Any]) -> dict[str, Any]:
    status = "read"
    if result.get("result") == "Needs Manual Import":
        status = "partial"
    if "robots" in str(result.get("reason", "")).lower():
        status = "robots_blocked"
    if any(marker in str(result.get("reason", "")).lower() for marker in ("login", "rate limited", "access denied")):
        status = "access_blocked"
    return {
        "stage": stage,
        "url": url,
        "status": status,
        "result": result.get("result", result.get("status", "")),
        "reason": result.get("reason", ""),
        "imported": result.get("imported", 0),
        "visual_references_created": result.get("visual_references_created", 0),
    }


def sitemap_entry_urls(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(url)
    base = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    candidates = [
        "/sitemap.xml",
        "/sitemap_index.xml",
        "/sitemap-product.xml",
        "/sitemap-products.xml",
        "/sitemap_products_1.xml",
        "/sitemap-collections.xml",
        "/sitemap_collections_1.xml",
        "/sitemap-categories.xml",
        "/sitemap_categories_1.xml",
    ]
    return [urllib.parse.urljoin(base, path) for path in candidates]


async def fetch_robots_txt(url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"}) as client:
            response = await client.get(robots_url)
    except httpx.HTTPError as error:
        return {
            "url": robots_url,
            "fetched": False,
            "allowed": False,
            "status": "unreadable",
            "http_status": None,
            "reason": str(error),
            "sitemap_urls": [],
        }
    sitemap_urls = sitemap_urls_from_robots(response.text if response.status_code < 500 else "")
    allowed = response.status_code < 400
    return {
        "url": robots_url,
        "fetched": True,
        "allowed": allowed,
        "status": "read" if allowed else "blocked",
        "http_status": response.status_code,
        "reason": "" if allowed else f"robots.txt returned HTTP {response.status_code}",
        "sitemap_urls": sitemap_urls,
    }


def sitemap_urls_from_robots(text: str) -> list[str]:
    urls: list[str] = []
    for line in text.splitlines():
        if line.lower().startswith("sitemap:"):
            candidate = line.split(":", 1)[1].strip()
            if candidate.startswith(("http://", "https://")) and candidate not in urls:
                urls.append(candidate)
    return urls


async def fetch_public_text(url: str, accept: str = "text/html,application/xhtml+xml") -> dict[str, Any]:
    robot_status = await robots_allowed(url)
    if robot_status is not True:
        return {"status": "robots_blocked", "reason": "robots.txt disallows access or could not be verified", "text": "", "http_status": None}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": USER_AGENT, "Accept": accept},
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError:
        return {"status": "unreadable", "reason": "page could not be accessed", "text": "", "http_status": None}
    if response.status_code in {401, 403, 429}:
        return {"status": "access_blocked", "reason": "access denied, login required, or rate limited", "text": "", "http_status": response.status_code}
    if response.status_code >= 400:
        return {"status": "unreadable", "reason": f"page returned HTTP {response.status_code}", "text": "", "http_status": response.status_code}
    return {"status": "read", "reason": "", "text": response.text, "url": str(response.url), "http_status": response.status_code}


def candidate_urls_from_sitemap_xml(xml_text: str, root_url: str, limit: int = MAX_CATEGORY_TREE_PAGES * 3) -> list[str]:
    urls = sitemap_locs_from_xml(xml_text)
    candidates: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not same_site(root_url, url):
            continue
        if not (looks_like_product_url(url) or looks_like_category_url(url) or looks_like_collection_or_catalog_url(url)):
            continue
        if url in seen:
            continue
        seen.add(url)
        candidates.append(url)
        if len(candidates) >= limit:
            break
    candidates.sort(key=sitemap_candidate_rank)
    return candidates[:limit]


def sitemap_locs_from_xml(xml_text: str) -> list[str]:
    locs: list[str] = []
    try:
        root = ET.fromstring(xml_text)
        for element in root.iter():
            if element.tag.lower().endswith("loc") and element.text:
                locs.append(element.text.strip())
    except ET.ParseError:
        locs.extend(re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml_text, flags=re.IGNORECASE))
    return [loc for loc in locs if loc.startswith(("http://", "https://"))]


def sitemap_candidate_rank(url: str) -> tuple[int, str]:
    lowered = url.lower()
    if looks_like_category_url(url) or looks_like_collection_or_catalog_url(url):
        return (0, lowered)
    if looks_like_product_url(url):
        return (1, lowered)
    return (2, lowered)


def looks_like_collection_or_catalog_url(href: str) -> bool:
    lowered = href.lower()
    return any(marker in lowered for marker in ("/collection/", "/collections/", "/catalog/", "/shop/"))


def import_catalog_tree_from_html_pages(
    pages: dict[str, str],
    root_url: str,
    brand: str,
    max_pages: int = MAX_CATEGORY_TREE_PAGES,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    visited = 0
    for page_url, html in list(pages.items())[:max_pages]:
        if not same_site(root_url, page_url):
            continue
        extracted = extract_catalog_records_from_html(html, brand, page_url)
        if extracted["page_type"] == "catalog_page" or looks_like_category_url(page_url):
            records.extend(extracted["records"])
        visited += 1
    records = dedupe_catalog_records(records)
    if not records:
        return needs_manual_import("could not extract public product data from category tree")
    result = import_catalog_records(records, import_type="catalog_tree_import")
    result["result"] = "Known"
    result["import_type"] = "catalog_tree_import"
    result["pages_read"] = visited
    result["source_url"] = root_url
    result["records"] = records
    return result


def products_for_imported_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    products = []
    for record in records:
        product = find_official_product(str(record.get("brand", "")), str(record.get("product_name", "")))
        if product:
            merged = dict(record)
            merged["brand"] = product["brand"]
            merged["product_name"] = product["product_name"]
            products.append(merged)
    return products


def dedupe_catalog_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        key = (
            normalize(str(record.get("brand", ""))),
            normalize(str(record.get("product_name", ""))),
            str(record.get("official_url", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped[:MAX_CATEGORY_PRODUCTS]


async def robots_allowed(url: str) -> bool | None:
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = urllib.robotparser.RobotFileParser()
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(robots_url)
        if response.status_code >= 400:
            return None
        parser.parse(response.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except httpx.HTTPError:
        return None


async def hydrate_official_visual_references(records: list[dict[str, Any]], import_type: str) -> int:
    created = 0
    for record in records:
        product = find_official_product(str(record.get("brand", "")), str(record.get("product_name", "")))
        if not product:
            continue
        image_candidates = official_image_candidates(record)[:MAX_OFFICIAL_IMAGES_PER_PRODUCT]
        for image_url, asset_type in image_candidates:
            downloaded = await download_public_image_reference(image_url)
            if not downloaded:
                continue
            try:
                add_official_visual_reference(
                    product_id=product["id"],
                    image_path=downloaded,
                    original_name=Path(urllib.parse.urlparse(image_url).path).name or f"{asset_type}.jpg",
                    asset_type=asset_type,
                    storage_dir=DATA_DIR,
                    import_type=import_type,
                    source_uri=image_url,
                )
                created += 1
            finally:
                downloaded.unlink(missing_ok=True)
    return created


def find_official_product(brand: str, product_name: str) -> dict[str, Any] | None:
    if not brand or not product_name:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM official_products WHERE brand = ? AND product_name = ?",
            (canonical_brand(brand), product_name),
        ).fetchone()
    return dict(row) if row else None


def official_image_candidates(record: dict[str, Any]) -> list[tuple[str, str]]:
    fields = {
        "official_white_bg": "official_white_bg",
        "official_model": "official_model",
        "official_detail": "official_detail",
        "official_fabric": "official_fabric",
        "official_logo": "official_logo",
        "official_zipper": "official_zipper",
        "official_hardware": "official_hardware",
        "official_stitching": "official_stitching",
    }
    candidates: list[tuple[str, str]] = []
    for field, asset_type in fields.items():
        for uri in parse_list(record.get(field, [])):
            if uri.startswith("http://") or uri.startswith("https://"):
                candidates.append((uri, asset_type))
    return candidates


async def download_public_image_reference(url: str) -> Path | None:
    allowed = await robots_allowed(url)
    if allowed is not True:
        return None
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15,
            headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*"},
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError:
        return None
    if response.status_code in {401, 403, 429} or response.status_code >= 400:
        return None
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        return None
    content = response.content
    if len(content) > MAX_UPLOAD_BYTES:
        return None
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".jpg"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=DATA_DIR)
    try:
        handle.write(content)
        return Path(handle.name)
    finally:
        handle.close()


def needs_manual_import(reason: str) -> dict[str, Any]:
    return {
        "result": "Needs Manual Import",
        "reason": reason,
        "message": "Import an official catalog CSV/JSON or provide a different public product/category page.",
    }


def parse_csv(text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def determine_import_type(page_type: str, records: list[dict[str, Any]]) -> str:
    if page_type == "catalog_page" and len(records) > 1:
        return "catalog_page_import"
    return "product_page_import"


def extract_catalog_records_from_html(html: str, brand: str, source_url: str) -> dict[str, Any]:
    parser = ProductHTMLParser()
    parser.feed(html)
    records = []
    records.extend(records_from_json_ld(parser.json_ld_blocks, brand, source_url))
    if records:
        page_type = "catalog_page" if len(records) > 1 else "product_page"
        return {"page_type": page_type, "records": records[:MAX_CATEGORY_PRODUCTS]}

    records.extend(records_from_embedded_product_scripts(parser.script_blocks, brand, parser.canonical_url or source_url))
    if records:
        page_type = "catalog_page" if len(records) > 1 else "product_page"
        return {"page_type": page_type, "records": records[:MAX_CATEGORY_PRODUCTS]}

    card_records = records_from_product_cards(parser.product_cards, brand, source_url)
    if card_records:
        return {"page_type": "catalog_page", "records": card_records[:MAX_CATEGORY_PRODUCTS]}

    title = parser.meta.get("og:title") or parser.title
    description = parser.meta.get("og:description") or parser.meta.get("description") or UNKNOWN
    image = parser.meta.get("og:image", "")
    if title:
        records.append(
            {
                "brand": brand,
                "product_name": clean_product_title(title, brand),
                "category": UNKNOWN,
                "description": description,
                "official_url": parser.canonical_url or source_url,
                "official_white_bg": image,
            }
        )
    return {"page_type": "product_page", "records": records}


def extract_category_links_from_html(html: str, source_url: str) -> list[str]:
    parser = ProductHTMLParser()
    parser.feed(html)
    links: list[str] = []
    seen: set[str] = set()
    for href in parser.category_links:
        absolute = absolutize_url(href, source_url)
        if not absolute or not same_site(source_url, absolute) or not looks_like_category_url(absolute):
            continue
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links[:MAX_CATEGORY_TREE_PAGES]


class ProductHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.canonical_url = ""
        self.json_ld_blocks: list[str] = []
        self.script_blocks: list[str] = []
        self._in_json_ld = False
        self._in_script = False
        self._json_ld_parts: list[str] = []
        self._script_parts: list[str] = []
        self.product_cards: list[dict[str, str]] = []
        self.category_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "meta":
            key = attrs_dict.get("property") or attrs_dict.get("name")
            content = attrs_dict.get("content")
            if key and content:
                self.meta[key.lower()] = content
        if tag.lower() == "script" and attrs_dict.get("type", "").lower() == "application/ld+json":
            self._in_json_ld = True
            self._json_ld_parts = []
        elif tag.lower() == "script":
            self._in_script = True
            self._script_parts = []
        if tag.lower() == "link" and attrs_dict.get("rel", "").lower() == "canonical":
            self.canonical_url = attrs_dict.get("href", "")
        if tag.lower() == "a":
            href = attrs_dict.get("href", "")
            label = attrs_dict.get("aria-label") or attrs_dict.get("title") or attrs_dict.get("data-product-name") or ""
            if looks_like_product_url(href):
                self.product_cards.append({"href": href, "name": label, "image": ""})
            elif looks_like_category_url(href):
                self.category_links.append(href)
        if tag.lower() == "img":
            src = attrs_dict.get("src") or attrs_dict.get("data-src") or attrs_dict.get("data-image") or ""
            alt = attrs_dict.get("alt") or attrs_dict.get("title") or ""
            if src and (alt or looks_like_product_image(src)):
                if self.product_cards and not self.product_cards[-1].get("image"):
                    self.product_cards[-1]["image"] = src
                    if alt and not self.product_cards[-1].get("name"):
                        self.product_cards[-1]["name"] = alt
                else:
                    self.product_cards.append({"href": "", "name": alt, "image": src})

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        if tag.lower() == "script" and self._in_json_ld:
            self._in_json_ld = False
            self.json_ld_blocks.append("".join(self._json_ld_parts).strip())
        elif tag.lower() == "script" and self._in_script:
            self._in_script = False
            text = "".join(self._script_parts).strip()
            if "product" in text.lower() and len(text) < 500000:
                self.script_blocks.append(text)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()
        if self._in_json_ld:
            self._json_ld_parts.append(data)
        elif self._in_script:
            self._script_parts.append(data)


def records_from_json_ld(blocks: list[str], brand: str, source_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for block in blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        for product in iter_json_ld_products(payload):
            name = product.get("name")
            if not name:
                continue
            image = product.get("image", [])
            if isinstance(image, str):
                image = [image]
            image = dedupe_urls([str(item) for item in image])
            records.append(
                {
                    "brand": brand,
                    "product_name": clean_product_title(str(name), brand),
                    "aliases": [str(name)],
                    "category": str(product.get("category", UNKNOWN) or UNKNOWN),
                    "description": str(product.get("description", UNKNOWN) or UNKNOWN),
                    "colors": extract_colors(product),
                    "material": str(product.get("material", UNKNOWN) or UNKNOWN),
                    "official_url": str(product.get("url", source_url) or source_url),
                    "official_white_bg": image[:1],
                    "official_model": image[1:3],
                    "official_detail": image[3:],
                }
            )
    return records


def records_from_embedded_product_scripts(blocks: list[str], brand: str, source_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in blocks[:8]:
        images = re.findall(r"https?://[^\"'\\\s]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\"'\\\s]*)?", block, flags=re.IGNORECASE)
        urls = re.findall(r"https?://[^\"'\\\s]+/(?:products?|p)/[^\"'\\\s]+", block, flags=re.IGNORECASE)
        names = []
        for pattern in (
            r'"(?:productName|product_name|name|title)"\s*:\s*"([^"]{3,140})"',
            r"'(?:productName|product_name|name|title)'\s*:\s*'([^']{3,140})'",
        ):
            names.extend(re.findall(pattern, block, flags=re.IGNORECASE))
        for raw_name in names[:MAX_CATEGORY_PRODUCTS]:
            product_name = clean_product_title(raw_name, brand)
            if product_name == UNKNOWN or product_name in seen:
                continue
            seen.add(product_name)
            image_list = dedupe_urls(images)[:MAX_OFFICIAL_IMAGES_PER_PRODUCT]
            product_url = urls[0] if urls else source_url
            records.append(
                {
                    "brand": brand,
                    "product_name": product_name,
                    "aliases": [raw_name],
                    "category": category_from_url(product_url),
                    "description": extract_script_value(block, "description") or UNKNOWN,
                    "colors": parse_list(extract_script_value(block, "color")),
                    "material": extract_script_value(block, "material") or UNKNOWN,
                    "official_url": product_url,
                    "official_white_bg": image_list[:1],
                    "official_model": image_list[1:3],
                    "official_detail": image_list[3:],
                }
            )
    return dedupe_catalog_records(records)


def extract_script_value(text: str, key: str) -> str:
    patterns = (
        rf'"{re.escape(key)}"\s*:\s*"([^"]{{1,240}})"',
        rf"'{re.escape(key)}'\s*:\s*'([^']{{1,240}})'",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def records_from_product_cards(cards: list[dict[str, str]], brand: str, source_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for card in cards:
        name = clean_product_title(card.get("name", ""), brand)
        product_url = absolutize_url(card.get("href", ""), source_url)
        image_url = absolutize_url(card.get("image", ""), source_url)
        if name == UNKNOWN:
            name = product_name_from_url(product_url, brand)
        if name == UNKNOWN or (name, product_url) in seen:
            continue
        seen.add((name, product_url))
        records.append(
            {
                "brand": brand,
                "product_name": name,
                "aliases": [name],
                "category": category_from_url(source_url),
                "description": UNKNOWN,
                "colors": [],
                "material": UNKNOWN,
                "official_url": product_url or source_url,
                "official_white_bg": image_url,
            }
        )
    return records


def looks_like_product_url(href: str) -> bool:
    lowered = href.lower()
    path = urllib.parse.urlparse(href).path.lower()
    return any(marker in lowered or marker in path for marker in ("/products/", "/product/", "/p/", "/prod/", "product", "prod"))


def looks_like_category_url(href: str) -> bool:
    lowered = href.lower()
    path = urllib.parse.urlparse(href).path.lower()
    if looks_like_product_url(href):
        return False
    return any(
        marker in lowered or marker in path
        for marker in (
            "/c/",
            "/category/",
            "/categories/",
            "/collections/",
            "/shop/",
            "/women",
            "/men",
            "jackets",
            "hoodies",
            "pants",
            "leggings",
            "shirts",
            "shoes",
            "clothing",
            "bags",
            "accessories",
        )
    )


def looks_like_product_image(src: str) -> bool:
    lowered = src.lower()
    return any(ext in lowered for ext in (".jpg", ".jpeg", ".png", ".webp", "image"))


def absolutize_url(value: str, source_url: str) -> str:
    if not value:
        return ""
    return urllib.parse.urljoin(source_url, value)


def same_site(root_url: str, candidate_url: str) -> bool:
    root = urllib.parse.urlparse(root_url)
    candidate = urllib.parse.urlparse(candidate_url)
    return root.scheme in {"http", "https"} and candidate.scheme in {"http", "https"} and root.netloc == candidate.netloc


def product_name_from_url(url: str, brand: str) -> str:
    if not url:
        return UNKNOWN
    slug = Path(urllib.parse.urlparse(url).path).stem or Path(urllib.parse.urlparse(url).path).name
    cleaned = slug.replace("-", " ").replace("_", " ").strip()
    return clean_product_title(cleaned.title(), brand)


def category_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    pieces = [piece.replace("-", " ").replace("_", " ").title() for piece in path.split("/") if piece]
    return pieces[-1] if pieces else UNKNOWN


def iter_json_ld_products(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from iter_json_ld_products(item)
    elif isinstance(payload, dict):
        graph = payload.get("@graph")
        if graph:
            yield from iter_json_ld_products(graph)
        if payload.get("@type") == "ItemList" or (isinstance(payload.get("@type"), list) and "ItemList" in payload.get("@type")):
            yield from iter_json_ld_products(payload.get("itemListElement", []))
        if "item" in payload:
            yield from iter_json_ld_products(payload["item"])
        item_type = payload.get("@type")
        if item_type == "Product" or (isinstance(item_type, list) and "Product" in item_type):
            yield payload


def extract_colors(product: dict[str, Any]) -> list[str]:
    color = product.get("color")
    if isinstance(color, list):
        return [str(item) for item in color]
    if isinstance(color, str) and color.strip():
        return [color.strip()]
    return []


def clean_product_title(title: str, brand: str) -> str:
    cleaned = re.sub(r"\s+", " ", title).strip()
    cleaned = re.sub(re.escape(brand), "", cleaned, flags=re.IGNORECASE).strip(" |-")
    return cleaned or UNKNOWN


def import_catalog_records(records: list[dict[str, Any]], import_type: str = "manual_import") -> dict[str, Any]:
    imported = 0
    skipped = 0
    with connect() as conn:
        for record in records:
            validate_record(record)
            brand = canonical_brand(str(record["brand"]))
            product_name = str(record["product_name"]).strip()
            now = utc_now()
            aliases = parse_list(record.get("aliases", []))
            if product_name not in aliases:
                aliases.append(product_name)
            colors = parse_list(record.get("colors", []))
            product_family = str(record.get("product_family", UNKNOWN)).strip() or UNKNOWN
            variant = str(record.get("variant", UNKNOWN)).strip() or UNKNOWN
            official_fields = {
                "brand": brand,
                "product_name": product_name,
                "product_family": product_family,
                "variant": variant,
                "aliases": aliases,
                "category": str(record.get("category", UNKNOWN)).strip() or UNKNOWN,
                "colors": colors,
                "material": str(record.get("material", UNKNOWN)).strip() or UNKNOWN,
                "source": "official_catalog",
            }
            product_id = str(uuid.uuid4())
            existing = conn.execute(
                "SELECT id FROM official_products WHERE brand = ? AND product_name = ?",
                (brand, product_name),
            ).fetchone()
            if existing:
                product_id = existing["id"]
            conn.execute(
                """
                INSERT INTO official_products (
                    id, brand, product_name, product_family, variant, aliases, category, description,
                    colors, material, official_url, import_type, official_fields_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(brand, product_name) DO UPDATE SET
                    product_family = excluded.product_family,
                    variant = excluded.variant,
                    aliases = excluded.aliases,
                    category = excluded.category,
                    description = excluded.description,
                    colors = excluded.colors,
                    material = excluded.material,
                    official_url = excluded.official_url,
                    import_type = excluded.import_type,
                    official_fields_json = excluded.official_fields_json,
                    updated_at = excluded.updated_at
                """,
                (
                    product_id,
                    brand,
                    product_name,
                    product_family,
                    variant,
                    encode_json(aliases),
                    str(record.get("category", UNKNOWN)).strip() or UNKNOWN,
                    str(record.get("description", UNKNOWN)).strip() or UNKNOWN,
                    encode_json(colors),
                    str(record.get("material", UNKNOWN)).strip() or UNKNOWN,
                    str(record.get("official_url", "")).strip(),
                    import_type,
                    encode_json(official_fields),
                    now,
                    now,
                ),
            )
            import_product_aliases(conn, product_id, product_name, aliases, colors)
            import_official_assets(conn, product_id, record, import_type=import_type)
            imported += 1
    return {"imported": imported, "skipped": skipped, "catalog_count": catalog_count()}


def validate_record(record: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_FIELDS if not str(record.get(field, "")).strip()]
    if missing:
        raise HTTPException(status_code=400, detail=f"Official catalog record missing fields: {', '.join(missing)}")


def parse_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.replace("|", ",").split(",") if item.strip()]


def import_official_assets(conn, product_id: str, record: dict[str, Any], import_type: str = "manual_import") -> None:
    asset_fields = {
        "official_white_bg": "official_white_bg",
        "official_model": "official_model",
        "official_detail": "official_detail",
        "official_fabric": "official_fabric",
        "official_logo": "official_logo",
        "official_zipper": "official_zipper",
        "official_hardware": "official_hardware",
        "official_stitching": "official_stitching",
    }
    now = utc_now()
    for field, asset_type in asset_fields.items():
        for uri in parse_list(record.get(field, [])):
            conn.execute(
                """
                INSERT INTO official_product_assets (id, product_id, asset_type, uri, local_file_uri, visual_signature, import_type, created_at)
                VALUES (?, ?, ?, ?, NULL, '{}', ?, ?)
                """,
                (str(uuid.uuid4()), product_id, asset_type, uri, import_type, now),
            )


def import_product_aliases(conn, product_id: str, product_name: str, aliases: list[str], colors: list[str]) -> None:
    now = utc_now()
    values: list[tuple[str, str]] = [(product_name, "official_name")]
    values.extend((alias, "name_alias") for alias in aliases)
    values.extend((color, "color_alias") for color in colors)
    for alias, alias_type in values:
        cleaned = str(alias).strip()
        if not cleaned:
            continue
        conn.execute(
            """
            INSERT INTO product_aliases (id, product_id, alias, alias_type, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(product_id, alias, alias_type) DO NOTHING
            """,
            (str(uuid.uuid4()), product_id, cleaned, alias_type, now),
        )


def list_catalog() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM official_products ORDER BY brand, product_name").fetchall()
    products = []
    for row in rows:
        product = dict(row)
        product["aliases"] = decode_json(product["aliases"], [])
        product["colors"] = decode_json(product["colors"], [])
        products.append(product)
    return products


def add_official_visual_reference(
    *,
    product_id: str,
    image_path: Path,
    original_name: str,
    asset_type: str,
    storage_dir: Path,
    import_type: str = "manual_import",
    source_uri: str | None = None,
) -> dict[str, Any]:
    with connect() as conn:
        product = conn.execute("SELECT * FROM official_products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Official product not found")
        reference_id = str(uuid.uuid4())
        suffix = Path(original_name).suffix.lower()
        destination = storage_dir / "official_refs" / product_id / f"{reference_id}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(image_path), destination)
        signature = image_signature(str(destination))
        conn.execute(
            """
            INSERT INTO official_product_assets (
                id, product_id, asset_type, uri, local_file_uri, visual_signature, import_type, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reference_id,
                product_id,
                asset_type,
                source_uri or str(destination),
                str(destination),
                encode_signature_for_db(signature),
                import_type,
                utc_now(),
            ),
        )
        conn.execute(
            """
            INSERT INTO official_product_visual_references (
                id, product_id, official_product_asset_id, asset_type,
                local_file_uri, visual_signature, structure_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '{}', ?)
            """,
            (
                str(uuid.uuid4()),
                product_id,
                reference_id,
                asset_type,
                str(destination),
                encode_signature_for_db(signature),
                utc_now(),
            ),
        )
    return {
        "id": reference_id,
        "product_id": product_id,
        "asset_type": asset_type,
        "local_file_uri": str(destination),
        "visual_signature": signature,
    }


def list_official_assets() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT official_product_assets.*, official_products.brand, official_products.product_name
            FROM official_product_assets
            JOIN official_products ON official_products.id = official_product_assets.product_id
            ORDER BY official_product_assets.created_at DESC
            """
        ).fetchall()
    assets = []
    for row in rows:
        item = dict(row)
        item["visual_signature"] = decode_json(item["visual_signature"], {})
        assets.append(item)
    return assets


def list_visual_references() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT official_product_visual_references.*, official_products.brand, official_products.product_name
            FROM official_product_visual_references
            JOIN official_products ON official_products.id = official_product_visual_references.product_id
            ORDER BY official_product_visual_references.created_at DESC
            """
        ).fetchall()
    references = []
    for row in rows:
        item = dict(row)
        item["visual_signature"] = decode_json(item["visual_signature"], {})
        item["structure_json"] = decode_json(item["structure_json"], {})
        references.append(item)
    return references


def match_official_product(evidence_text: str) -> dict[str, Any] | None:
    normalized_evidence = normalize(evidence_text)
    with connect() as conn:
        rows = conn.execute("SELECT * FROM official_products").fetchall()
    matches: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        product = dict(row)
        aliases = decode_json(product["aliases"], [])
        candidates = [product["product_name"], *aliases]
        score = 0
        product_matched = False
        for candidate in candidates:
            normalized_candidate = normalize(candidate)
            if normalized_candidate and normalized_candidate in normalized_evidence:
                score = max(score, len(normalized_candidate))
                product_matched = True
        if not product_matched:
            continue
        if normalize(product["brand"]) in normalized_evidence:
            score += 10
        if score > 0:
            product["aliases"] = aliases
            product["colors"] = decode_json(product["colors"], [])
            matches.append((score, product))

    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None
    return matches[0][1]


def match_official_product_by_visual_signature(signature: dict[str, Any]) -> dict[str, Any] | None:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT official_product_assets.visual_signature, official_products.*
            FROM official_product_assets
            JOIN official_products ON official_products.id = official_product_assets.product_id
            WHERE official_product_assets.visual_signature IS NOT NULL
              AND official_product_assets.visual_signature != '{}'
            """
        ).fetchall()

    matches: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        score = signature_similarity(signature, row["visual_signature"])
        if score >= VISUAL_MATCH_THRESHOLD:
            product = dict(row)
            product["aliases"] = decode_json(product["aliases"], [])
            product["colors"] = decode_json(product["colors"], [])
            product["visual_match_confidence"] = score
            matches.append((score, product))

    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1 and abs(matches[0][0] - matches[1][0]) < 0.03:
        return None
    return matches[0][1]
