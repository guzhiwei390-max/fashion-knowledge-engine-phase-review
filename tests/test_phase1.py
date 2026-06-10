from pathlib import Path
import asyncio
import io
import zipfile

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app import assets, catalog, database
from app.catalog import (
    add_official_visual_reference,
    bootstrap_official_catalog,
    catalog_count,
    candidate_urls_from_sitemap_xml,
    determine_import_type,
    extract_category_links_from_html,
    extract_catalog_records_from_html,
    import_catalog_tree_from_html_pages,
    import_catalog_records,
    match_official_product,
    match_official_product_by_visual_signature,
    needs_manual_import,
    parse_batch_official_site_entries_from_csv,
    parse_batch_official_site_entries_from_json,
    parse_batch_official_site_entries_from_text,
    require_catalog_ready,
)
from app.confidence import HIGH_CONFIDENCE_THRESHOLD, REVIEW_CONFIDENCE_THRESHOLD, evaluate_match_confidence
from app.database import init_db
from app.knowledge import build_knowledge, search_knowledge
from app.pipelines import PIPELINE_EXTERNAL_KNOWLEDGE, PIPELINE_INTERNAL_UPLOAD, PIPELINE_RESERVED_FUTURE, TRUTH_OFFICIAL, TRUTH_REALITY, pipeline_design
from app.structure import STRUCTURE_EVIDENCE_FIELDS
from app.vision import classify_from_evidence, process_pending_jobs
import app.vision as vision
from app.visual import image_signature
from app.openai_vision import parse_openai_response
from app.vision_provider import analyze_image_with_provider, normalize_vision_result
from app.vision_router import vision_route_decision
from app.main import reserved_extensions, source_types as source_types_endpoint, pipelines_design
from app.main import app


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(assets, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path / "data")
    init_db()
    return db_path


def test_upload_gate_requires_official_catalog(isolated_db):
    assert catalog_count() == 0
    with pytest.raises(Exception) as exc:
        require_catalog_ready()
    assert "Official Product Catalog" in str(exc.value)


def test_pipeline_schema_is_reserved_without_enabling_external_ingestion(isolated_db):
    with database.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        asset_columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)").fetchall()}
        product_columns = {row["name"] for row in conn.execute("PRAGMA table_info(official_products)").fetchall()}
        source_types = {
            row["source_type"]: dict(row)
            for row in conn.execute("SELECT * FROM source_type_registry").fetchall()
        }

    assert {
        "source_type_registry",
        "ingestion_sources",
        "pipeline_runs",
        "external_knowledge_items",
        "review_queue",
        "product_aliases",
        "knowledge_source_index",
        "official_catalog_import_jobs",
        "official_url_candidates",
        "official_parse_events",
        "official_candidate_assets",
        "official_candidate_groups",
    }.issubset(tables)
    assert {"pipeline_type", "truth_layer", "source_id", "external_ref_uri", "ingestion_metadata"}.issubset(asset_columns)
    assert {"product_family", "variant", "truth_layer", "truth_locked", "official_fields_json", "supplemental_fields_json"}.issubset(product_columns)
    assert source_types["internal_upload_image"]["pipeline_type"] == PIPELINE_INTERNAL_UPLOAD
    assert source_types["external_knowledge_url"]["pipeline_type"] == PIPELINE_EXTERNAL_KNOWLEDGE
    assert source_types["official_catalog_import"]["truth_layer"] == TRUTH_OFFICIAL
    assert pipeline_design()["rule"] == "Official Truth can be supplemented but not overwritten by Reality Truth or Community Truth."


def test_future_growth_modules_are_schema_and_api_reserved_only(isolated_db):
    expected_tables = {
        "success_library_items",
        "negative_library_items",
        "commercial_score_records",
        "trend_timeline_events",
        "region_layers",
        "learning_feedback_events",
    }
    with database.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        for table in expected_tables:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            assert {"source_type", "pipeline_type", "truth_layer", "status", "reserved_metadata_json"}.issubset(columns)

    design = pipeline_design()
    extensions = reserved_extensions()

    assert expected_tables.issubset(tables)
    assert design["pipelines"][PIPELINE_RESERVED_FUTURE]["may_override"] == []
    assert all(item["status"] == "reserved_only" for item in extensions["modules"].values())
    assert all(item["no_phase1_logic"] is True for item in extensions["modules"].values())
    assert "POST /api/future/commercial-score" in design["api_design"]["reserved_future_endpoints"][PIPELINE_RESERVED_FUTURE]


def test_reality_moment_and_video_structures_are_reserved(isolated_db):
    expected_tables = {
        "material_reality_patterns",
        "human_reality_patterns",
        "scene_reality_patterns",
        "moment_patterns",
        "outfit_reality_patterns",
        "reality_score_schema",
        "video_assets",
        "video_frame_assets",
        "frame_extraction_jobs",
        "asset_product_regions",
        "asset_batches",
    }
    with database.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
    assert expected_tables.issubset(tables)
    design = pipeline_design()
    assert "video_assets" in design["pipelines"][PIPELINE_RESERVED_FUTURE]["writes"]
    assert "material_reality_patterns" in design["pipelines"][PIPELINE_RESERVED_FUTURE]["writes"]


def test_knowledge_tables_have_truth_pipeline_markers(isolated_db):
    with database.connect() as conn:
        dna_columns = {row["name"] for row in conn.execute("PRAGMA table_info(dna_records)").fetchall()}
        card_columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_cards)").fetchall()}
        query_columns = {row["name"] for row in conn.execute("PRAGMA table_info(retrieval_queries)").fetchall()}

    assert {"source_type", "pipeline_type", "truth_layer"}.issubset(dna_columns)
    assert {"source_type", "pipeline_type", "truth_layer"}.issubset(card_columns)
    assert {"pipeline_type", "truth_layer"}.issubset(query_columns)


def test_pipeline_api_design_is_read_only_reservation(isolated_db):
    design = pipelines_design()
    registry = source_types_endpoint()

    assert design["api_design"]["status"] == "reserved_only"
    assert "POST /api/external/sources" in design["api_design"]["reserved_future_endpoints"][PIPELINE_EXTERNAL_KNOWLEDGE]
    assert any(item["source_type"] == "external_social_capture" for item in registry["source_types"])


def test_internal_upload_assets_have_reserved_pipeline_fields(isolated_db, tmp_path):
    image_path = tmp_path / "IMG_0001.png"
    make_solid_image(image_path, (1, 2, 3))

    asset = assets.create_asset_record(
        file_path=image_path,
        original_name="IMG_0001.png",
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="pipeline-batch",
    )

    assert asset["pipeline_type"] == PIPELINE_INTERNAL_UPLOAD
    assert asset["truth_layer"] == TRUTH_REALITY


def test_zip_ingestion_is_allowed_without_official_catalog(isolated_db):
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        image_buffer = io.BytesIO()
        Image.new("RGB", (512, 512), (230, 230, 230)).save(image_buffer, "PNG")
        archive.writestr("IMG_0001.png", image_buffer.getvalue())
    archive_bytes.seek(0)

    client = TestClient(app)
    response = client.post(
        "/api/import/zip",
        files={"file": ("raw-assets.zip", archive_bytes.getvalue(), "application/zip")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_received"] == 1
    assert payload["raw_asset_ingestion_status"] == "allowed"
    assert payload["official_catalog_status"] == "missing"
    assert payload["product_matching_status"] == "blocked_missing_official_catalog"
    assert "官方商品目录" in payload["message"] or "Official Catalog" in payload["message"]
    with database.connect() as conn:
        asset = conn.execute("SELECT * FROM assets WHERE upload_batch_id = ?", (payload["batch_id"],)).fetchone()
        job = conn.execute("SELECT * FROM analysis_jobs WHERE asset_id = ?", (asset["id"],)).fetchone()
        review = conn.execute("SELECT * FROM review_queue WHERE item_id = ?", (asset["id"],)).fetchone()
        batch = conn.execute("SELECT * FROM asset_batches WHERE id = ?", (payload["batch_id"],)).fetchone()
    assert asset["truth_layer"] == TRUTH_REALITY
    assert asset["product_matching_status"] == "blocked_missing_official_catalog"
    assert job["status"] == "blocked_missing_official_catalog"
    assert review is None
    assert batch["blocked_missing_official_catalog_count"] == 1
    assert batch["catalog_status"] == "missing"


def test_assets_import_zip_alias_returns_ingestion_statuses(isolated_db):
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        image_buffer = io.BytesIO()
        Image.new("RGB", (512, 512), (230, 230, 230)).save(image_buffer, "PNG")
        archive.writestr("IMG_0002.png", image_buffer.getvalue())
    archive_bytes.seek(0)

    client = TestClient(app)
    response = client.post(
        "/api/assets/import-zip",
        files={"file": ("raw-assets.zip", archive_bytes.getvalue(), "application/zip")},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["batch_id"]
    assert payload["raw_asset_ingestion_status"] == "allowed"
    assert payload["official_catalog_status"] == "missing"
    assert payload["product_matching_status"] == "blocked_missing_official_catalog"


def test_process_jobs_marks_identity_blocked_without_catalog(isolated_db, tmp_path):
    image_path = tmp_path / "IMG_nomatch.png"
    make_solid_image(image_path, (90, 90, 90))
    asset = assets.create_asset_record(
        file_path=image_path,
        original_name=image_path.name,
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="no-catalog-batch",
    )

    result = process_pending_jobs()

    assert result["blocked_missing_official_catalog"] == 1
    with database.connect() as conn:
        stored = conn.execute("SELECT * FROM assets WHERE id = ?", (asset["id"],)).fetchone()
        review = conn.execute("SELECT * FROM review_queue WHERE item_id = ?", (asset["id"],)).fetchone()
        batch = conn.execute("SELECT * FROM asset_batches WHERE id = 'no-catalog-batch'").fetchone()
    assert stored["product_matching_status"] == "blocked_missing_official_catalog"
    assert review is None
    assert batch["blocked_missing_official_catalog_count"] == 1
    assert batch["next_action"] == "learn_official_site_or_upload_official_candidates"


def test_user_upload_filename_cannot_create_official_truth(isolated_db, tmp_path):
    image_path = tmp_path / "official_white_bg_lululemon.png"
    Image.new("RGB", (512, 512), (12, 12, 12)).save(image_path)

    asset = assets.create_asset_record(
        file_path=image_path,
        original_name="official_white_bg_lululemon.png",
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="truth-clean-batch",
    )

    assert asset["source_type"] == "uploaded"
    assert asset["truth_layer"] == TRUTH_REALITY
    assert asset["asset_type"] != "official_product_image"


def test_official_candidate_review_creates_official_truth(isolated_db, tmp_path):
    image_path = tmp_path / "lululemon_define_official_white_bg.png"
    image = Image.new("RGB", (800, 800), (250, 250, 250))
    for x in range(300, 500):
        for y in range(170, 650):
            image.putpixel((x, y), (35, 35, 35))
    image.save(image_path)

    asset = assets.create_asset_record(
        file_path=image_path,
        original_name=image_path.name,
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="official-candidate-batch",
    )

    with database.connect() as conn:
        candidate = conn.execute("SELECT * FROM official_candidate_assets WHERE asset_id = ?", (asset["id"],)).fetchone()
        group = conn.execute(
            "SELECT * FROM official_candidate_groups WHERE grouping_key = ?",
            (candidate["grouping_key"],),
        ).fetchone()
        review = conn.execute(
            "SELECT * FROM review_queue WHERE item_type = 'official_candidate_asset' AND item_id = ?",
            (candidate["id"],),
        ).fetchone()
        from app.review import resolve_review_item

        result = resolve_review_item(
            conn,
            review_id=review["id"],
            resolution={
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "category": "Women Jackets",
                "asset_type": "official_white_bg",
                "correction_reason": "confirmed official-like white background candidate",
            },
            resolved_by="tester",
        )
        product_count = conn.execute("SELECT COUNT(*) AS count FROM official_products").fetchone()["count"]
        official_asset_count = conn.execute("SELECT COUNT(*) AS count FROM official_product_assets").fetchone()["count"]
        visual_count = conn.execute("SELECT COUNT(*) AS count FROM official_product_visual_references").fetchone()["count"]
        stored_asset = conn.execute("SELECT * FROM assets WHERE id = ?", (asset["id"],)).fetchone()
        updated_group = conn.execute(
            "SELECT * FROM official_candidate_groups WHERE grouping_key = ?",
            (candidate["grouping_key"],),
        ).fetchone()

    assert candidate is not None
    assert group is not None
    assert group["candidate_count"] == 1
    assert review is not None
    assert result["learning_actions"]["official_truth_written"] is True
    assert product_count == 1
    assert official_asset_count == 1
    assert visual_count == 1
    assert stored_asset["product_matching_status"] == "pending"
    assert updated_group["status"] == "confirmed"


def test_official_candidate_payload_includes_evidence_and_group_api_approves(isolated_db, tmp_path):
    image_path = tmp_path / "alo_airlift_official_white_bg.png"
    image = Image.new("RGB", (820, 820), (250, 250, 250))
    for x in range(290, 530):
        for y in range(150, 690):
            image.putpixel((x, y), (24, 48, 96))
    image.save(image_path)

    asset = assets.create_asset_record(
        file_path=image_path,
        original_name=image_path.name,
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="candidate-group-api-batch",
    )
    client = TestClient(app)
    groups = client.get("/api/official-candidate-groups?status=pending").json()["official_candidate_groups"]
    group = next(item for item in groups if item["representative_asset_id"] == asset["id"])
    review = client.get("/api/review-queue?status=pending&reason=official_like_candidate").json()["review_queue"][0]
    payload = review["review_payload"]

    assert payload["candidate_confidence"] > 0
    assert payload["candidate_type"] == "official_white_bg_candidate"
    assert payload["why_this_is_official_like"]
    assert "related_assets" in payload
    assert group["related_assets"]

    result = client.post(
        f"/api/official-candidate-groups/{group['id']}/action",
        json={
            "action": "approve",
            "brand": "Alo",
            "product_name": "Airlift Jacket",
            "category": "Women Jackets",
            "asset_type": "official_white_bg",
            "resolved_by": "tester",
        },
    ).json()

    with database.connect() as conn:
        product_count = conn.execute("SELECT COUNT(*) AS count FROM official_products").fetchone()["count"]
        group_row = conn.execute("SELECT * FROM official_candidate_groups WHERE id = ?", (group["id"],)).fetchone()

    assert result["status"] == "group_approved"
    assert result["confirmed_candidates"] == 1
    assert product_count == 1
    assert group_row["status"] == "confirmed"


def test_official_candidate_group_reject_merge_and_split_actions(isolated_db):
    now = database.utc_now()
    with database.connect() as conn:
        for asset_id in ("asset-a1", "asset-a2", "asset-b1", "asset-c1"):
            conn.execute(
                """
                INSERT INTO assets (
                    id, file_uri, original_name, sha256, content_type, size_bytes,
                    source_type, knowledge_layer, ingestion_metadata, ingestion_status,
                    asset_type, quality_status, visual_signature, duplicate_status,
                    product_matching_status, upload_batch_id, created_at
                )
                VALUES (?, ?, ?, ?, 'image/png', 1, 'uploaded', 'raw_input', '{}', 'ingested',
                        'official_like_candidate', 'usable', '{}', 'unique', 'pending', 'group-action-batch', ?)
                """,
                (asset_id, f"{asset_id}.png", f"{asset_id}.png", f"sha-{asset_id}", now),
            )
        conn.execute(
            """
            INSERT INTO official_candidate_groups (
                id, grouping_key, brand_hint, product_name_hint, candidate_count,
                representative_asset_id, status, signals_json, created_at, updated_at
            )
            VALUES
              ('group-a', 'group-a-key', 'Alo', 'Jacket A', 2, NULL, 'pending_review', '{}', ?, ?),
              ('group-b', 'group-b-key', 'Alo', 'Jacket B', 1, NULL, 'pending_review', '{}', ?, ?),
              ('group-c', 'group-c-key', 'Alo', 'Jacket C', 1, NULL, 'pending_review', '{}', ?, ?)
            """,
            (now, now, now, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO official_candidate_assets (
                id, asset_id, brand_hint, product_name_hint, candidate_type, confidence,
                status, grouping_key, signals_json, created_at, updated_at
            )
            VALUES
              ('candidate-a1', 'asset-a1', 'Alo', 'Jacket A', 'official_white_bg_candidate', 0.8, 'pending_review', 'group-a-key', '{}', ?, ?),
              ('candidate-a2', 'asset-a2', 'Alo', 'Jacket A', 'official_white_bg_candidate', 0.8, 'pending_review', 'group-a-key', '{}', ?, ?),
              ('candidate-b1', 'asset-b1', 'Alo', 'Jacket B', 'official_white_bg_candidate', 0.8, 'pending_review', 'group-b-key', '{}', ?, ?),
              ('candidate-c1', 'asset-c1', 'Alo', 'Jacket C', 'official_white_bg_candidate', 0.8, 'pending_review', 'group-c-key', '{}', ?, ?)
            """,
            (now, now, now, now, now, now, now, now),
        )

    client = TestClient(app)
    split = client.post(
        "/api/official-candidate-groups/group-a/action",
        json={"action": "split", "candidate_ids": ["candidate-a2"], "new_grouping_key": "group-a-split", "resolved_by": "tester"},
    ).json()
    merge = client.post(
        "/api/official-candidate-groups/group-b/action",
        json={"action": "merge", "target_group_id": "group-c", "resolved_by": "tester"},
    ).json()
    reject = client.post(
        "/api/official-candidate-groups/group-c/action",
        json={"action": "reject", "resolved_by": "tester"},
    ).json()

    with database.connect() as conn:
        split_candidate = conn.execute("SELECT * FROM official_candidate_assets WHERE id = 'candidate-a2'").fetchone()
        merged_candidate = conn.execute("SELECT * FROM official_candidate_assets WHERE id = 'candidate-b1'").fetchone()
        rejected = conn.execute("SELECT * FROM official_candidate_groups WHERE id = 'group-c'").fetchone()

    assert split["status"] == "group_split"
    assert split_candidate["grouping_key"] == "group-a-split"
    assert merge["status"] == "group_merged"
    assert merged_candidate["grouping_key"] == "group-c-key"
    assert reject["status"] == "group_rejected"
    assert rejected["status"] == "rejected"


def test_corrupted_image_is_stored_without_blocking_batch(isolated_db, tmp_path):
    bad_image = tmp_path / "broken.png"
    bad_image.write_bytes(b"not a real png")

    asset = assets.create_asset_record(
        file_path=bad_image,
        original_name="broken.png",
        content_type="image/png",
        size_bytes=bad_image.stat().st_size,
        batch_id="corrupt-batch",
    )

    assert asset["ingestion_status"] == "corrupted"
    assert asset["asset_type"] == "unknown"
    with database.connect() as conn:
        job_count = conn.execute("SELECT COUNT(*) AS count FROM analysis_jobs").fetchone()["count"]
        review = conn.execute("SELECT * FROM review_queue WHERE item_id = ?", (asset["id"],)).fetchone()
        batch = conn.execute("SELECT * FROM asset_batches WHERE id = 'corrupt-batch'").fetchone()
    assert job_count == 0
    assert review is not None
    assert batch["corrupted"] == 1
    assert batch["unknown"] == 1


def test_multi_product_photo_creates_region_placeholder_and_review(isolated_db, tmp_path):
    image_path = tmp_path / "desk_multi_product_batch.png"
    Image.new("RGB", (512, 512), (80, 80, 80)).save(image_path)

    asset = assets.create_asset_record(
        file_path=image_path,
        original_name="desk_multi_product_batch.png",
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="multi-batch",
    )

    assert asset["asset_type"] == "multi_product_photo"
    with database.connect() as conn:
        region = conn.execute("SELECT * FROM asset_product_regions WHERE asset_id = ?", (asset["id"],)).fetchone()
        review = conn.execute("SELECT * FROM review_queue WHERE item_id = ?", (asset["id"],)).fetchone()
        batch = conn.execute("SELECT * FROM asset_batches WHERE id = 'multi-batch'").fetchone()
    assert region is not None
    assert review is not None
    assert batch["review_needed"] >= 1


def test_catalog_import_and_match_are_evidence_based(isolated_db):
    import_catalog_records(
        [
            {
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "aliases": ["Define", "Define Jacket Nulu"],
                "category": "Jacket",
                "description": "Official description from source",
                "colors": ["Black"],
                "material": "Nulu",
                "official_url": "https://example.com/official",
            }
        ]
    )

    assert catalog_count() == 1
    assert match_official_product("lululemon define front black")["product_name"] == "Define Jacket"
    assert match_official_product("lululemon similar jacket") is None
    assert match_official_product("lululemon jacket") is None


def test_vision_returns_unknown_without_official_match(isolated_db):
    import_catalog_records(
        [
            {
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "category": "Jacket",
            }
        ]
    )

    result = classify_from_evidence("random-jacket-front.jpg", "uploads/random-jacket-front.jpg", "uploaded")

    assert result["product_name"] == "Unknown"
    assert "official_product_match" in result["unknown_fields"]


def test_catalog_match_builds_product_dna_and_search(isolated_db, tmp_path):
    import_catalog_records(
        [
            {
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "aliases": "Define",
                "category": "Jacket",
                "material": "Nulu",
            }
        ]
    )
    product_id = catalog.list_catalog()[0]["id"]
    official_image = tmp_path / "official.png"
    image_path = tmp_path / "IMG_0007.png"
    make_solid_image(official_image, (18, 18, 18))
    make_solid_image(image_path, (18, 18, 18))
    add_official_visual_reference(
        product_id=product_id,
        image_path=official_image,
        original_name="official.png",
        asset_type="official_white_bg",
        storage_dir=tmp_path / "data",
    )
    asset = assets.create_asset_record(
        file_path=image_path,
        original_name="IMG_0007.png",
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="test-batch",
    )

    assert asset["knowledge_layer"] == "user_reality"
    assert process_pending_jobs()["processed"] == 1
    assert build_knowledge()["built"] == 1

    result = search_knowledge({"brand": "Lululemon", "product": "Define Jacket"})

    assert result["product_dna"]["product_name"] == "Define Jacket"
    assert result["product_dna"]["material"] == "Nulu"
    assert result["evidence_asset_ids"]


def test_search_unknown_when_catalog_product_has_no_knowledge(isolated_db):
    result = search_knowledge({"brand": "Lululemon", "product": "Define Jacket"})
    assert result == {
        "result": "Unknown",
        "unknown": ["product_dna", "garment_validation_rules", "material_dna", "knowledge_card"],
    }


def test_url_importer_extracts_public_json_ld_without_crawling():
    html = """
    <html><head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Lululemon Define Jacket",
        "category": "Jacket",
        "description": "Official public product description",
        "color": ["Black"],
        "material": "Nulu",
        "image": ["https://example.com/define.jpg"],
        "url": "https://example.com/define"
      }
      </script>
    </head></html>
    """

    extracted = extract_catalog_records_from_html(html, "Lululemon", "https://example.com/define")
    records = extracted["records"]

    assert len(records) == 1
    assert determine_import_type(extracted["page_type"], records) == "product_page_import"
    assert records[0]["product_name"] == "Define Jacket"
    assert records[0]["material"] == "Nulu"
    assert records[0]["official_white_bg"] == ["https://example.com/define.jpg"]


def test_needs_manual_import_contract():
    result = needs_manual_import("robots.txt disallows access")
    assert result["result"] == "Needs Manual Import"
    assert "robots.txt" in result["reason"]


def test_category_page_extracts_product_list_not_single_page():
    html = """
    <html><body>
      <script type="application/ld+json">
      [
        {"@type":"Product","name":"Lululemon Define Jacket","category":"Women Jackets","image":"https://example.com/define.jpg","url":"https://example.com/products/define"},
        {"@type":"Product","name":"Lululemon Scuba Hoodie","category":"Women Hoodies","image":"https://example.com/scuba.jpg","url":"https://example.com/products/scuba"}
      ]
      </script>
    </body></html>
    """

    extracted = extract_catalog_records_from_html(html, "Lululemon", "https://example.com/c/women-jackets")

    assert extracted["page_type"] == "catalog_page"
    assert determine_import_type(extracted["page_type"], extracted["records"]) == "catalog_page_import"
    assert [record["product_name"] for record in extracted["records"]] == ["Define Jacket", "Scuba Hoodie"]


def test_category_page_extracts_item_list_products():
    html = """
    <html><body>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
          {"@type":"ListItem","item":{"@type":"Product","name":"Lululemon Define Jacket","category":"Women Jackets","image":"https://example.com/define.jpg","url":"https://example.com/products/define"}},
          {"@type":"ListItem","item":{"@type":"Product","name":"Lululemon Dance Studio Pant","category":"Women Pants","image":"https://example.com/dance.jpg","url":"https://example.com/products/dance-studio"}}
        ]
      }
      </script>
    </body></html>
    """

    extracted = extract_catalog_records_from_html(html, "Lululemon", "https://example.com/c/women")

    assert extracted["page_type"] == "catalog_page"
    assert len(extracted["records"]) == 2
    assert extracted["records"][1]["product_name"] == "Dance Studio Pant"


def test_category_tree_import_collects_same_site_category_pages(isolated_db):
    root_url = "https://shop.example.com/c/women"
    pages = {
        root_url: """
        <html><body>
          <a href="/c/women-jackets">Women Jackets</a>
          <a href="https://other.example.com/c/copy">External Copy</a>
          <script type="application/ld+json">
          {"@type":"ItemList","itemListElement":[
            {"@type":"ListItem","item":{"@type":"Product","name":"Lululemon Align Pant","category":"Women Pants","image":"https://shop.example.com/align.jpg","url":"https://shop.example.com/products/align"}}
          ]}
          </script>
        </body></html>
        """,
        "https://shop.example.com/c/women-jackets": """
        <html><body>
          <script type="application/ld+json">
          [
            {"@type":"Product","name":"Lululemon Define Jacket","category":"Women Jackets","image":"https://shop.example.com/define.jpg","url":"https://shop.example.com/products/define"},
            {"@type":"Product","name":"Lululemon Scuba Hoodie","category":"Women Hoodies","image":"https://shop.example.com/scuba.jpg","url":"https://shop.example.com/products/scuba"}
          ]
          </script>
        </body></html>
        """,
    }

    links = extract_category_links_from_html(pages[root_url], root_url)
    result = import_catalog_tree_from_html_pages(pages, root_url, "Lululemon", max_pages=5)

    assert "https://shop.example.com/c/women-jackets" in links
    assert "https://other.example.com/c/copy" not in links
    assert result["import_type"] == "catalog_tree_import"
    assert result["pages_read"] == 2
    assert result["imported"] == 3
    assert catalog_count() == 3


def test_catalog_import_accepts_public_product_page_url(isolated_db):
    product_url = "https://shop.example.com/products/cloudrunner-jacket"
    pages = {
        product_url: """
        <html><body>
          <script type="application/ld+json">
          {"@type":"Product","name":"On Cloudrunner Jacket","brand":"On","category":"Women Jackets","image":"https://shop.example.com/cloudrunner.jpg","url":"https://shop.example.com/products/cloudrunner-jacket"}
          </script>
        </body></html>
        """,
    }

    result = import_catalog_tree_from_html_pages(pages, product_url, "On", max_pages=1)

    assert result["import_type"] == "catalog_tree_import"
    assert result["imported"] == 1
    assert catalog_count() == 1


def test_sitemap_candidates_prioritize_public_catalog_urls():
    sitemap = """
    <urlset>
      <url><loc>https://shop.example.com/pages/about</loc></url>
      <url><loc>https://shop.example.com/products/define-jacket</loc></url>
      <url><loc>https://shop.example.com/collections/women-jackets</loc></url>
      <url><loc>https://other.example.com/collections/copy</loc></url>
    </urlset>
    """

    candidates = candidate_urls_from_sitemap_xml(sitemap, "https://shop.example.com/")

    assert candidates[0] == "https://shop.example.com/collections/women-jackets"
    assert "https://shop.example.com/products/define-jacket" in candidates
    assert all("other.example.com" not in url for url in candidates)


def test_batch_official_site_entry_parsers_support_text_csv_and_json():
    text_entries = parse_batch_official_site_entries_from_text(
        "Lululemon, https://shop.lululemon.com/\nAlo, https://www.aloyoga.com/"
    )
    csv_entries = parse_batch_official_site_entries_from_csv(
        "brand,url,type,priority,note\nOn,https://www.on.com/,homepage,high,core brand\n"
    )
    json_entries = parse_batch_official_site_entries_from_json(
        '[{"brand":"Arc\\u0027teryx","urls":["https://arcteryx.com/"],"priority":"high"}]'
    )

    assert text_entries[0]["brand"] == "Lululemon"
    assert text_entries[1]["urls"] == ["https://www.aloyoga.com/"]
    assert csv_entries[0]["priority"] == "high"
    assert json_entries[0]["brand"] == "Arc'teryx"


def test_batch_official_site_learning_endpoint_returns_per_brand_status(isolated_db, monkeypatch):
    async def fake_bootstrap(url, brand, max_pages=8, category_url=None, product_url=None):
        return {
            "brand": brand,
            "input_url": url,
            "official_catalog_status": "ready" if brand == "On" else "missing",
            "product_matching_status": "ready" if brand == "On" else "blocked_missing_official_catalog",
            "robots_txt_fetched": True,
            "robots_allowed": True,
            "robots_url": f"{url.rstrip('/')}/robots.txt",
            "sitemap_found": brand == "On",
            "candidate_urls": ["https://www.on.com/products/cloudmonster"] if brand == "On" else [],
            "parsed_product_pages_count": 1 if brand == "On" else 0,
            "official_products_created": 1 if brand == "On" else 0,
            "official_product_assets_created": 1 if brand == "On" else 0,
            "official_visual_references_created": 1 if brand == "On" else 0,
            "blocked_reason": "" if brand == "On" else "robots.txt disallows access or could not be verified",
            "next_best_action": "provide_category_url",
        }

    monkeypatch.setattr(catalog, "bootstrap_official_catalog", fake_bootstrap)
    # main imported the function directly, so patch that binding too.
    import app.main as main_module

    monkeypatch.setattr(main_module, "bootstrap_official_catalog", fake_bootstrap)
    client = TestClient(app)
    response = client.post(
        "/api/catalog/learn-sites",
        data={"entries_text": "On, https://www.on.com/\nLululemon, https://shop.lululemon.com/"},
    )

    payload = response.json()
    rows = payload["batch_official_site_learning"]

    assert response.status_code == 200
    assert payload["totals"]["entries_received"] == 2
    assert payload["totals"]["ready"] == 1
    assert payload["totals"]["blocked"] == 1
    assert rows[0]["brand"] == "On"
    assert rows[0]["official_catalog_status"] == "ready"
    assert rows[0]["next_action"] == "ready"
    assert rows[1]["official_catalog_status"] == "blocked"
    assert rows[1]["next_action"] == "needs_official_candidate_review"


def test_official_catalog_bootstrap_can_use_sitemap_candidate(isolated_db, monkeypatch):
    async def fake_tree(url, brand, max_pages=8):
        if "collections/women-jackets" not in url:
            return needs_manual_import("could not extract public product data from category tree")
        return import_catalog_records(
            [
                {
                    "brand": brand,
                    "product_name": "Define Jacket",
                    "category": "Women Jackets",
                    "description": "Official product description",
                    "material": "Nulu",
                    "official_url": url,
                    "official_white_bg": "https://shop.example.com/define.jpg",
                }
            ],
            import_type="catalog_tree_import",
        )

    async def fake_fetch(url, accept=""):
        return {
            "status": "read",
            "reason": "",
            "text": """
            <urlset>
              <url><loc>https://shop.example.com/collections/women-jackets</loc></url>
            </urlset>
            """,
        }

    async def fake_robots(url):
        return {
            "url": "https://shop.example.com/robots.txt",
            "fetched": True,
            "allowed": True,
            "status": "read",
            "http_status": 200,
            "reason": "",
            "sitemap_urls": ["https://shop.example.com/sitemap.xml"],
        }

    monkeypatch.setattr(catalog, "import_catalog_tree_url", fake_tree)
    monkeypatch.setattr(catalog, "fetch_public_text", fake_fetch)
    monkeypatch.setattr(catalog, "fetch_robots_txt", fake_robots)

    result = asyncio.run(bootstrap_official_catalog("https://shop.example.com/", "Lululemon", max_pages=4))

    assert result["raw_asset_ingestion_status"] == "allowed"
    assert result["official_catalog_status"] == "partial"
    assert result["product_matching_status"] == "blocked_missing_official_catalog"
    assert result["candidate_urls"] == ["https://shop.example.com/collections/women-jackets"]
    assert result["robots_txt_fetched"] is True
    assert result["sitemap_found"] is True
    assert result["category_candidates_found"] == 1
    assert result["official_products_created"] == 1
    assert catalog_count() == 1
    with database.connect() as conn:
        job_count = conn.execute("SELECT COUNT(*) AS count FROM official_catalog_import_jobs").fetchone()["count"]
        event_count = conn.execute("SELECT COUNT(*) AS count FROM official_parse_events").fetchone()["count"]
        candidate_count = conn.execute("SELECT COUNT(*) AS count FROM official_url_candidates").fetchone()["count"]
    assert job_count == 1
    assert event_count >= 2
    assert candidate_count == 1


def test_img_named_upload_matches_official_visual_reference(isolated_db, tmp_path):
    import_catalog_records(
        [
            {
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "aliases": "Define",
                "category": "Jacket",
                "material": "Nulu",
            }
        ],
        import_type="catalog_page_import",
    )
    product_id = catalog.list_catalog()[0]["id"]
    official_image = tmp_path / "official.png"
    user_image = tmp_path / "IMG_1234.png"
    make_solid_image(official_image, (18, 18, 18))
    make_solid_image(user_image, (20, 20, 20))

    add_official_visual_reference(
        product_id=product_id,
        image_path=official_image,
        original_name="official.png",
        asset_type="official_white_bg",
        storage_dir=tmp_path / "data",
    )

    visual_match = match_official_product_by_visual_signature(image_signature(str(user_image)))
    assert visual_match["product_name"] == "Define Jacket"

    result = classify_from_evidence("IMG_1234.png", str(user_image), "uploaded")
    assert result["product_name"] == "Define Jacket"
    assert result["product_match"]["method"] == "visual_reference"


def test_low_confidence_visual_match_returns_unknown(isolated_db, tmp_path):
    import_catalog_records(
        [
            {
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "category": "Jacket",
            }
        ]
    )
    product_id = catalog.list_catalog()[0]["id"]
    official_image = tmp_path / "official.png"
    user_image = tmp_path / "IMG_9999.png"
    make_solid_image(official_image, (0, 0, 0))
    make_solid_image(user_image, (255, 255, 255))
    add_official_visual_reference(
        product_id=product_id,
        image_path=official_image,
        original_name="official.png",
        asset_type="official_white_bg",
        storage_dir=tmp_path / "data",
    )

    result = classify_from_evidence("IMG_9999.png", str(user_image), "uploaded")

    assert result["product_name"] == "Unknown"
    assert "official_product_match" in result["unknown_fields"]


def test_search_returns_all_phase1_dna_structures(isolated_db, tmp_path):
    import_catalog_records(
        [
            {
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "aliases": "Define",
                "category": "Jacket",
                "material": "Nulu",
            }
        ]
    )
    product_id = catalog.list_catalog()[0]["id"]
    official_image = tmp_path / "official.png"
    user_image = tmp_path / "IMG_8888.png"
    make_solid_image(official_image, (30, 30, 30))
    make_solid_image(user_image, (31, 31, 31))
    add_official_visual_reference(
        product_id=product_id,
        image_path=official_image,
        original_name="official.png",
        asset_type="official_white_bg",
        storage_dir=tmp_path / "data",
    )
    asset = assets.create_asset_record(
        file_path=user_image,
        original_name="IMG_8888.png",
        content_type="image/png",
        size_bytes=user_image.stat().st_size,
        batch_id="batch-visual",
    )
    assert asset["id"]
    process_pending_jobs()
    build_knowledge()

    result = search_knowledge({"brand": "Lululemon", "product": "Define Jacket"})

    for key in (
        "product_dna",
        "garment_validation_rules",
        "material_dna",
        "outfit_dna",
        "tribe_dna",
        "scene_dna",
        "customer_reality_dna",
        "trend_dna",
    ):
        assert key in result


def test_high_confidence_catalog_visual_match_does_not_call_openai(isolated_db, tmp_path, monkeypatch):
    calls = {"count": 0}

    def fake_openai(*args, **kwargs):
        calls["count"] += 1
        return {"result": "Unknown", "product_match": {"result": "Unknown"}, "product_structure": {}}

    monkeypatch.setattr(vision, "analyze_image_with_openai", fake_openai)
    import_catalog_records(
        [
            {
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "category": "Jacket",
            }
        ]
    )
    product_id = catalog.list_catalog()[0]["id"]
    official_image = tmp_path / "official.png"
    user_image = tmp_path / "IMG_4242.png"
    make_solid_image(official_image, (12, 12, 12))
    make_solid_image(user_image, (12, 12, 12))
    add_official_visual_reference(
        product_id=product_id,
        image_path=official_image,
        original_name="official.png",
        asset_type="official_white_bg",
        storage_dir=tmp_path / "data",
    )

    result = classify_from_evidence("IMG_4242.png", str(user_image), "uploaded")

    assert result["product_name"] == "Define Jacket"
    assert result["product_match"]["openai_vision_called"] is False
    assert calls["count"] == 0


def test_openai_cannot_create_product_outside_official_catalog(isolated_db, tmp_path, monkeypatch):
    def fake_openai(*args, **kwargs):
        return {
            "result": "Known",
            "product_match": {
                "result": "Known",
                "brand": "Lululemon",
                "product_name": "Imaginary Jacket",
                "confidence": 0.99,
                "why": ["looks like a jacket"],
            },
            "product_structure": {"garment_type": "jacket", "unknown_fields": []},
        }

    monkeypatch.setattr(vision, "analyze_image_with_openai", fake_openai)
    import_catalog_records(
        [
            {
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "category": "Jacket",
            }
        ]
    )
    user_image = tmp_path / "IMG_1111.png"
    make_solid_image(user_image, (200, 10, 10))

    result = classify_from_evidence("IMG_1111.png", str(user_image), "uploaded")

    assert result["product_name"] == "Unknown"
    assert "official_product_match" in result["unknown_fields"]


def test_openai_vision_response_extracts_product_structure():
    payload = {
        "output_text": """
        {
          "product_match": {
            "result": "Known",
            "brand": "Lululemon",
            "product_name": "Define Jacket",
            "confidence": 0.91,
            "why": ["stand collar", "front zipper"]
          },
          "product_structure": {
            "garment_type": "jacket",
            "collar": "stand collar",
            "zipper": "full front zipper",
            "sleeve": "long sleeve",
            "logo": "small chest logo",
            "logo_position": "left chest",
            "back_structure": "curved seam",
            "material_visual_behavior": "low shine",
            "material_behavior": "low shine",
            "fit": "slim",
            "visible_evidence": ["stand collar", "full zipper"]
          }
        }
        """
    }

    parsed = parse_openai_response(payload)

    assert parsed["product_match"]["product_name"] == "Define Jacket"
    assert parsed["product_structure"]["zipper"] == "full front zipper"
    assert parsed["product_structure"]["logo_position"] == "left chest"


def test_product_structure_understanding_becomes_dna_evidence(isolated_db, tmp_path, monkeypatch):
    def fake_openai(*args, **kwargs):
        return {
            "result": "Known",
            "product_match": {
                "result": "Known",
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "confidence": 0.91,
                "why": ["stand collar", "full front zipper", "left chest logo"],
            },
            "product_structure": {
                "garment_type": "jacket",
                "collar": "stand collar",
                "zipper": "full front zipper",
                "logo_position": "left chest",
                "stitching": "curved panel stitching",
                "back_structure": "curved back seam",
                "sleeve_structure": "long fitted sleeves",
                "hem_shape": "straight hip hem",
                "fit_shape": "slim close fit",
                "pocket": "front zip pockets",
                "hardware": "small zipper pull",
                "material_behavior": "low shine, smooth stretch knit",
                "visible_evidence": ["stand collar", "full front zipper", "left chest logo", "curved back seam"],
                "unknown_fields": [],
            },
        }

    monkeypatch.setattr(vision, "analyze_image_with_openai", fake_openai)
    import_catalog_records(
        [
            {
                "brand": "Lululemon",
                "product_name": "Define Jacket",
                "aliases": "Define",
                "category": "Jacket",
                "material": "Nulu",
            }
        ]
    )
    image_path = tmp_path / "IMG_5555.png"
    make_solid_image(image_path, (80, 80, 80))
    asset = assets.create_asset_record(
        file_path=image_path,
        original_name="IMG_5555.png",
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="structure-batch",
    )

    assert asset["id"]
    assert process_pending_jobs()["processed"] == 1
    assert build_knowledge()["built"] == 1
    result = search_knowledge({"brand": "Lululemon", "product": "Define Jacket"})
    product_dna = result["product_dna"]

    assert product_dna["collar"]["value"] == "stand collar"
    assert product_dna["zipper"]["value"] == "full front zipper"
    assert product_dna["logo_position"]["value"] == "left chest"
    assert product_dna["stitching"]["value"] == "curved panel stitching"
    assert product_dna["back_structure"]["source"] == "vision_structure"
    assert product_dna["sleeve_structure"]["value"] == "long fitted sleeves"
    assert product_dna["hem_shape"]["value"] == "straight hip hem"
    assert product_dna["fit_shape"]["value"] == "slim close fit"
    assert product_dna["pocket"]["value"] == "front zip pockets"
    assert product_dna["hardware"]["value"] == "small zipper pull"
    assert product_dna["material_behavior"]["value"] == "low shine, smooth stretch knit"
    assert "collar: stand collar" in product_dna["must_have"]


def test_structure_engine_fields_are_brand_agnostic():
    expected = {
        "collar",
        "zipper",
        "logo_position",
        "stitching",
        "back_structure",
        "sleeve_structure",
        "hem_shape",
        "fit_shape",
        "pocket",
        "hardware",
        "material_behavior",
    }
    assert expected.issubset(set(STRUCTURE_EVIDENCE_FIELDS))


def test_confidence_engine_central_thresholds():
    assert HIGH_CONFIDENCE_THRESHOLD > REVIEW_CONFIDENCE_THRESHOLD
    assert evaluate_match_confidence(confidence=0.91, has_official_match=True).decision == "accepted"
    assert evaluate_match_confidence(confidence=0.72, has_official_match=True).reason == "low_confidence"
    assert evaluate_match_confidence(confidence=0.99, has_official_match=False).decision == "unknown"
    assert evaluate_match_confidence(confidence=0.99, has_official_match=True, conflict=True).reason == "conflict"


def test_vision_provider_adapter_returns_unified_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_PROVIDER", "local")
    image_path = tmp_path / "IMG_provider.png"
    Image.new("RGB", (512, 512), (20, 20, 20)).save(image_path)

    result = analyze_image_with_provider(str(image_path), [])

    assert result["provider"] == "local"
    assert result["product_match"]["candidate_only"] is True
    assert "product_structure" in result
    assert "multi_product" in result
    assert result["product_match"]["result"] == "Unknown"


def test_vision_provider_normalizes_mimo_like_response():
    result = normalize_vision_result(
        {
            "result": "Known",
            "product_match": {
                "result": "Known",
                "brand": "Alo",
                "product_name": "Airbrush Legging",
                "confidence": 0.81,
                "why": ["candidate silhouette match"],
            },
            "product_structure": {"fit": "legging", "visible_evidence": ["legging fit"]},
            "multi_product": {"result": "Unknown", "candidate_regions": [], "needs_region_review": False},
            "quality": {"result": "Known", "issues": []},
        },
        "mimo",
    )

    assert result["provider"] == "mimo"
    assert result["product_match"]["candidate_only"] is True
    assert result["product_match"]["confidence"] == pytest.approx(0.81)
    assert result["product_structure"]["fit_shape"] == "legging"


def test_vision_router_blocks_nonvaluable_first_layer_inputs():
    assert not vision_route_decision(
        asset_type="low_quality",
        quality_status="low_quality",
        duplicate_status="unique",
        has_official_candidate=False,
        match_confidence=0.0,
        needs_structure_detail=True,
    )["allowed"]
    assert not vision_route_decision(
        asset_type="scene_photo",
        quality_status="ok",
        duplicate_status="unique",
        has_official_candidate=False,
        match_confidence=0.0,
        needs_structure_detail=True,
    )["allowed"]
    duplicate_route = vision_route_decision(
        asset_type="reality_product_photo",
        quality_status="ok",
        duplicate_status="near_duplicate",
        has_official_candidate=True,
        match_confidence=0.70,
        needs_structure_detail=True,
    )
    assert duplicate_route["reason"] == "duplicate"


def test_vision_budget_defaults_are_configurable(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_VISION_CALLS_PER_BATCH", "50")
    monkeypatch.setenv("VISION_COST_LIMIT", "0.10")
    image_path = tmp_path / "IMG_budget.png"
    Image.new("RGB", (512, 512), (90, 90, 90)).save(image_path)

    assets.create_asset_record(
        file_path=image_path,
        original_name="IMG_budget.png",
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="budget-batch",
    )

    with database.connect() as conn:
        batch = conn.execute("SELECT * FROM asset_batches WHERE id = 'budget-batch'").fetchone()
    assert batch["max_vision_calls_per_batch"] == 50
    assert batch["cost_limit"] == pytest.approx(0.10)


def test_vision_budget_pauses_batch_before_excess_calls(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_VISION_CALLS_PER_BATCH", "1")
    monkeypatch.setenv("VISION_COST_LIMIT", "1.00")
    calls = {"count": 0}

    def fake_openai(*args, **kwargs):
        calls["count"] += 1
        return {
            "result": "Known",
            "product_match": {
                "result": "Known",
                "brand": "Alo",
                "product_name": "Airbrush Legging",
                "confidence": 0.88,
                "why": ["candidate fit shape"],
            },
            "product_structure": {"fit_shape": "legging", "visible_evidence": ["legging fit"]},
        }

    monkeypatch.setattr(vision, "analyze_image_with_openai", fake_openai)
    import_catalog_records(
        [
            {
                "brand": "Alo",
                "product_name": "Airbrush Legging",
                "product_family": "Airbrush",
                "variant": "Legging",
                "category": "Leggings",
            }
        ]
    )
    first = tmp_path / "IMG_budget_1.png"
    second = tmp_path / "IMG_budget_2.png"
    make_solid_image(first, (30, 40, 50))
    make_solid_image(second, (80, 90, 100))
    for path in [first, second]:
        assets.create_asset_record(
            file_path=path,
            original_name=path.name,
            content_type="image/png",
            size_bytes=path.stat().st_size,
            batch_id="vision-budget-stop",
        )

    result = process_pending_jobs()

    assert calls["count"] == 1
    assert result["processed"] == 1
    assert result["paused"] == 1
    with database.connect() as conn:
        batch = conn.execute("SELECT * FROM asset_batches WHERE id = 'vision-budget-stop'").fetchone()
        paused_job = conn.execute("SELECT * FROM analysis_jobs WHERE status = 'paused'").fetchone()
    assert batch["vision_status"] == "paused_budget"
    assert paused_job is not None


def test_zip_import_streams_and_returns_summary(isolated_db, tmp_path):
    import_catalog_records([{"brand": "Alo", "product_name": "Airbrush Legging", "category": "Leggings"}])
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        image_buffer = io.BytesIO()
        Image.new("RGB", (512, 512), (245, 245, 245)).save(image_buffer, "PNG")
        archive.writestr("IMG_1001.png", image_buffer.getvalue())
        archive.writestr("notes.txt", "not an image")
        archive.writestr("__MACOSX/._hidden", "hidden")
    archive_bytes.seek(0)

    client = TestClient(app)
    response = client.post(
        "/api/import/zip",
        files={"file": ("mixed.zip", archive_bytes.getvalue(), "application/zip")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert {"batch_id", "total_received", "status", "unsupported_count", "message"}.issubset(set(payload))
    assert payload["total_received"] == 1
    assert payload["unsupported_count"] == 2
    with database.connect() as conn:
        batch = conn.execute("SELECT * FROM asset_batches WHERE id = ?", (payload["batch_id"],)).fetchone()
    assert batch["unsupported_count"] == 2


def test_paginated_asset_and_review_endpoints_filter_results(isolated_db, tmp_path):
    import_catalog_records([{"brand": "On", "product_name": "Cloudmonster", "category": "Shoes"}])
    for index in range(3):
        image_path = tmp_path / f"IMG_page_{index}.png"
        make_solid_image(image_path, (index * 20, index * 20, index * 20))
        assets.create_asset_record(
            file_path=image_path,
            original_name=image_path.name,
            content_type="image/png",
            size_bytes=image_path.stat().st_size,
            batch_id="page-batch",
        )

    client = TestClient(app)
    assets_payload = client.get("/api/assets?limit=2&offset=1&batch_id=page-batch").json()

    assert assets_payload["limit"] == 2
    assert assets_payload["offset"] == 1
    assert assets_payload["total"] == 3
    assert len(assets_payload["assets"]) == 2


def test_content_based_coarse_classification_detects_white_bg_and_multi_product(tmp_path):
    white_bg = tmp_path / "IMG_1234.jpg"
    image = Image.new("RGB", (800, 800), (250, 250, 250))
    image.save(white_bg)
    assert assets.inspect_upload_image(white_bg)["content_signals"]["white_background"] is True

    multi = tmp_path / "IMG_5678.jpg"
    image = Image.new("RGB", (800, 800), (250, 250, 250))
    for box in [(70, 220, 230, 600), (320, 210, 480, 600), (570, 230, 720, 610)]:
        for x in range(box[0], box[2]):
            for y in range(box[1], box[3]):
                image.putpixel((x, y), (30, 30, 30))
    image.save(multi)
    signals = assets.inspect_upload_image(multi)["content_signals"]
    classification = assets.classify_with_content(
        original_name="IMG_5678.jpg",
        source_type="uploaded",
        metadata={"width": 800, "height": 800, "ingestion_status": "ingested", "content_signals": signals},
        duplicate_status="unique",
    )
    assert classification["asset_type"] == "multi_product_photo"


def test_review_resolution_updates_asset_and_records_learning(isolated_db, tmp_path):
    import_catalog_records([{"brand": "On", "product_name": "Cloudmonster", "category": "Shoes"}])
    image_path = tmp_path / "IMG_review.png"
    make_solid_image(image_path, (30, 60, 90))
    asset = assets.create_asset_record(
        file_path=image_path,
        original_name=image_path.name,
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="resolve-batch",
    )
    with database.connect() as conn:
        review = conn.execute("SELECT * FROM review_queue WHERE item_id = ?", (asset["id"],)).fetchone()
        if review is None:
            from app.review import enqueue_review_item

            review = enqueue_review_item(conn, item_type="asset", item_id=asset["id"], reason="unknown", payload={})
        from app.review import resolve_review_item

        result = resolve_review_item(
            conn,
            review_id=review["id"],
            resolution={
                "asset_type": "human_wearing_photo",
                "product_name": "Cloudmonster",
                "brand": "On",
                "correction_reason": "manual verified from product view",
            },
            resolved_by="tester",
        )
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset["id"],)).fetchone()
        correction = conn.execute("SELECT * FROM human_corrections WHERE target_id = ?", (asset["id"],)).fetchone()

    assert result["learning_actions"]["asset_updated"] is True
    assert row["asset_type"] == "human_wearing_photo"
    assert correction is not None


def test_low_confidence_match_enters_review_queue(isolated_db, tmp_path, monkeypatch):
    def fake_openai(*args, **kwargs):
        return {
            "result": "Known",
            "product_match": {
                "result": "Known",
                "brand": "On",
                "product_name": "Cloudmonster",
                "confidence": 0.72,
                "why": ["shoe silhouette"],
            },
            "product_structure": {"garment_type": "shoe", "visible_evidence": ["shoe silhouette"]},
        }

    monkeypatch.setattr(vision, "analyze_image_with_openai", fake_openai)
    import_catalog_records(
        [
            {
                "brand": "On",
                "product_name": "Cloudmonster",
                "product_family": "Cloudmonster",
                "variant": "Road Running",
                "aliases": "Cloud Monster",
                "category": "Shoes",
            }
        ]
    )
    image_path = tmp_path / "IMG_4321.png"
    make_solid_image(image_path, (110, 120, 130))
    asset = assets.create_asset_record(
        file_path=image_path,
        original_name="IMG_4321.png",
        content_type="image/png",
        size_bytes=image_path.stat().st_size,
        batch_id="review-batch",
    )

    assert asset["id"]
    assert process_pending_jobs()["processed"] == 1
    observations = vision.latest_observations()["observations"]
    assert observations[0]["structured_output"]["product_name"] == "Unknown"
    assert observations[0]["structured_output"]["product_match"]["decision"] == "review"
    with database.connect() as conn:
        review = conn.execute("SELECT * FROM review_queue WHERE reason = 'low_confidence'").fetchone()
    assert review is not None
    assert review["confidence"] == pytest.approx(0.72)


def test_process_batch_endpoint_only_processes_requested_batch(isolated_db, tmp_path):
    import_catalog_records([{"brand": "On", "product_name": "Cloudmonster", "category": "Shoes"}])
    for batch_id, filename, color in (("batch-one", "IMG_one.png", (80, 90, 100)), ("batch-two", "IMG_two.png", (100, 90, 80))):
        image_path = tmp_path / filename
        make_solid_image(image_path, color)
        assets.create_asset_record(
            file_path=image_path,
            original_name=filename,
            content_type="image/png",
            size_bytes=image_path.stat().st_size,
            batch_id=batch_id,
        )

    client = TestClient(app)
    result = client.post("/api/batches/batch-one/process?limit=10").json()

    with database.connect() as conn:
        batch_one_jobs = conn.execute(
            "SELECT COUNT(*) AS count FROM analysis_jobs JOIN assets ON assets.id = analysis_jobs.asset_id WHERE assets.upload_batch_id = 'batch-one' AND analysis_jobs.status = 'completed'"
        ).fetchone()["count"]
        batch_two_jobs = conn.execute(
            "SELECT COUNT(*) AS count FROM analysis_jobs JOIN assets ON assets.id = analysis_jobs.asset_id WHERE assets.upload_batch_id = 'batch-two' AND analysis_jobs.status IN ('queued', 'pending')"
        ).fetchone()["count"]

    assert result["batch_id"] == "batch-one"
    assert result["jobs"]["processed"] == 1
    assert batch_one_jobs == 1
    assert batch_two_jobs == 1


def test_match_evidence_is_returned_for_accepted_match(isolated_db, tmp_path, monkeypatch):
    def fake_openai(*args, **kwargs):
        return {
            "result": "Known",
            "product_match": {
                "result": "Known",
                "brand": "Arc'teryx",
                "product_name": "Beta Jacket",
                "confidence": 0.93,
                "why": ["hooded shell", "front zipper"],
            },
            "product_structure": {
                "garment_type": "jacket",
                "zipper": "water resistant front zipper",
                "hardware": "zip pull hardware",
                "visible_evidence": ["front zipper", "zip pull hardware"],
            },
        }

    monkeypatch.setattr(vision, "analyze_image_with_openai", fake_openai)
    import_catalog_records(
        [
            {
                "brand": "Arc'teryx",
                "product_name": "Beta Jacket",
                "category": "Jacket",
                "official_logo": "https://example.com/logo.jpg",
                "official_zipper": "https://example.com/zipper.jpg",
            }
        ]
    )
    image_path = tmp_path / "IMG_2468.png"
    make_solid_image(image_path, (60, 80, 90))

    result = classify_from_evidence("IMG_2468.png", str(image_path), "uploaded", asset_id="asset-evidence")
    evidence = result["match_evidence"]

    assert result["product_name"] == "Beta Jacket"
    assert evidence["confidence"] == pytest.approx(0.93)
    assert evidence["matched_because"]
    assert evidence["matched_official_assets"]
    assert "hardware:zip pull hardware" in evidence["matched_because"]
    assert "evidence_asset_ids" in evidence


def test_official_asset_import_supports_structure_reference_types(isolated_db):
    import_catalog_records(
        [
            {
                "brand": "Ralph Lauren",
                "product_name": "Polo Shirt",
                "category": "Shirt",
                "colors": "Navy, White",
                "official_logo": "https://example.com/logo.jpg",
                "official_zipper": "https://example.com/zipper.jpg",
                "official_hardware": "https://example.com/hardware.jpg",
                "official_stitching": "https://example.com/stitching.jpg",
            }
        ]
    )

    asset_types = {item["asset_type"] for item in catalog.list_official_assets()}
    assert {"official_logo", "official_zipper", "official_hardware", "official_stitching"}.issubset(asset_types)
    with database.connect() as conn:
        aliases = {row["alias"] for row in conn.execute("SELECT alias FROM product_aliases").fetchall()}
    assert {"Polo Shirt", "Navy", "White"}.issubset(aliases)


def test_exact_duplicate_enters_review_queue(isolated_db, tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    make_solid_image(first, (8, 8, 8))
    second.write_bytes(first.read_bytes())

    first_asset = assets.create_asset_record(
        file_path=first,
        original_name="IMG_A.png",
        content_type="image/png",
        size_bytes=first.stat().st_size,
        batch_id="dup-batch",
    )
    second_asset = assets.create_asset_record(
        file_path=second,
        original_name="IMG_B.png",
        content_type="image/png",
        size_bytes=second.stat().st_size,
        batch_id="dup-batch",
    )

    assert second_asset["id"] == first_asset["id"]
    with database.connect() as conn:
        review = conn.execute("SELECT * FROM review_queue WHERE reason = 'duplicate'").fetchone()
    assert review is not None
    assert review["item_id"] == first_asset["id"]


def test_architecture_decisions_doc_locks_phase1_rules():
    text = Path("ARCHITECTURE_DECISIONS.md").read_text(encoding="utf-8")
    for phrase in (
        "Unknown First",
        "Official Truth Lock",
        "Official Truth > Reality Truth > Community Truth",
        "Phase 1 forbids",
        "Vision is a secondary expert verification",
        "Vision Provider Adapter",
        "Vision Gate / Vision Router",
        "Brand Agnostic",
    ):
        assert phrase in text


def test_project_north_star_defines_reality_image_engine_goal():
    text = Path("PROJECT_NORTH_STAR.md").read_text(encoding="utf-8")
    for phrase in (
        "Reality Image Engine",
        "Product",
        "Scene",
        "Reality Image Engine",
        "Product Recognition Engine",
        "Product Recognition Engine",
    ):
        assert phrase in text

    architecture = Path("ARCHITECTURE_DECISIONS.md").read_text(encoding="utf-8")
    assert "PROJECT_NORTH_STAR.md" in architecture
    assert "highest priority project document" in architecture
    assert "Official Catalog, Product DNA, Structure DNA, Unknown, Confidence, Evidence, and Review Queue remain correct Phase 1 foundations" in architecture


def make_solid_image(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (512, 512), color).save(path)
