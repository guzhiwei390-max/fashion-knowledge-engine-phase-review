from pathlib import Path

import pytest
from PIL import Image

from app import assets, catalog, database
from app.catalog import (
    add_official_visual_reference,
    catalog_count,
    determine_import_type,
    extract_category_links_from_html,
    extract_catalog_records_from_html,
    import_catalog_tree_from_html_pages,
    import_catalog_records,
    match_official_product,
    match_official_product_by_visual_signature,
    needs_manual_import,
    require_catalog_ready,
)
from app.database import init_db
from app.knowledge import build_knowledge, search_knowledge
from app.pipelines import PIPELINE_EXTERNAL_KNOWLEDGE, PIPELINE_INTERNAL_UPLOAD, TRUTH_OFFICIAL, TRUTH_REALITY, pipeline_design
from app.vision import classify_from_evidence, process_pending_jobs
import app.vision as vision
from app.visual import image_signature
from app.openai_vision import parse_openai_response
from app.main import source_types as source_types_endpoint, pipelines_design


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
    }.issubset(tables)
    assert {"pipeline_type", "truth_layer", "source_id", "external_ref_uri", "ingestion_metadata"}.issubset(asset_columns)
    assert {"product_family", "variant", "truth_layer", "truth_locked", "official_fields_json", "supplemental_fields_json"}.issubset(product_columns)
    assert source_types["internal_upload_image"]["pipeline_type"] == PIPELINE_INTERNAL_UPLOAD
    assert source_types["external_knowledge_url"]["pipeline_type"] == PIPELINE_EXTERNAL_KNOWLEDGE
    assert source_types["official_catalog_import"]["truth_layer"] == TRUTH_OFFICIAL
    assert pipeline_design()["rule"] == "Official Truth can be supplemented but not overwritten by Reality Truth or Community Truth."


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
    image_path = tmp_path / "lululemon-define-front.jpg"
    image_path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c4944415408d763f8ffff3f0005fe02fea73581e50000000049454e44ae426082"
        )
    )
    asset = assets.create_asset_record(
        file_path=image_path,
        original_name="lululemon-define-front.jpg",
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
                "back_structure": "curved back seam",
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
    assert product_dna["back_structure"]["source"] == "openai_vision_structure"
    assert product_dna["material_behavior"]["value"] == "low shine, smooth stretch knit"
    assert "collar: stand collar" in product_dna["must_have"]


def make_solid_image(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (64, 64), color).save(path)
