from typing import Any


PIPELINE_INTERNAL_UPLOAD = "internal_upload"
PIPELINE_EXTERNAL_KNOWLEDGE = "external_knowledge"
PIPELINE_OFFICIAL_CATALOG = "official_catalog"

TRUTH_OFFICIAL = "official_truth"
TRUTH_REALITY = "reality_truth"
TRUTH_COMMUNITY = "community_truth"

TRUTH_PRIORITY = {
    TRUTH_OFFICIAL: 100,
    TRUTH_REALITY: 50,
    TRUTH_COMMUNITY: 10,
}

SOURCE_TYPES: dict[str, dict[str, Any]] = {
    "internal_upload_image": {
        "pipeline_type": PIPELINE_INTERNAL_UPLOAD,
        "truth_layer": TRUTH_REALITY,
        "description": "Images uploaded directly by the user.",
    },
    "internal_zip_import": {
        "pipeline_type": PIPELINE_INTERNAL_UPLOAD,
        "truth_layer": TRUTH_REALITY,
        "description": "Images imported from a user-provided zip file.",
    },
    "official_catalog_import": {
        "pipeline_type": PIPELINE_OFFICIAL_CATALOG,
        "truth_layer": TRUTH_OFFICIAL,
        "description": "Official product data imported from approved public catalog sources.",
    },
    "official_visual_reference": {
        "pipeline_type": PIPELINE_OFFICIAL_CATALOG,
        "truth_layer": TRUTH_OFFICIAL,
        "description": "Official product images used only as identification references.",
    },
    "external_knowledge_url": {
        "pipeline_type": PIPELINE_EXTERNAL_KNOWLEDGE,
        "truth_layer": TRUTH_COMMUNITY,
        "description": "Reserved for future public external knowledge URLs.",
    },
    "external_social_capture": {
        "pipeline_type": PIPELINE_EXTERNAL_KNOWLEDGE,
        "truth_layer": TRUTH_COMMUNITY,
        "description": "Reserved for future public social/community evidence.",
    },
    "external_manual_knowledge": {
        "pipeline_type": PIPELINE_EXTERNAL_KNOWLEDGE,
        "truth_layer": TRUTH_COMMUNITY,
        "description": "Reserved for future manually supplied external knowledge.",
    },
}


RESERVED_API_DESIGN: dict[str, Any] = {
    "active_phase1_endpoints": [
        "POST /api/catalog/import",
        "POST /api/catalog/import-url",
        "POST /api/catalog/import-tree",
        "POST /api/catalog/visual-reference",
        "POST /api/upload",
        "POST /api/import/zip",
        "POST /api/jobs/process",
        "GET /api/search",
    ],
    "reserved_future_endpoints": {
        PIPELINE_INTERNAL_UPLOAD: [
            "POST /api/internal/uploads/images",
            "POST /api/internal/uploads/zip",
            "GET /api/internal/reality-truth/search",
        ],
        PIPELINE_EXTERNAL_KNOWLEDGE: [
            "POST /api/external/sources",
            "POST /api/external/runs",
            "GET /api/external/community-truth/search",
        ],
        PIPELINE_OFFICIAL_CATALOG: [
            "POST /api/official/catalog/import",
            "GET /api/official/truth/search",
        ],
    },
    "status": "reserved_only",
}


def pipeline_design() -> dict[str, Any]:
    return {
        "pipelines": {
            PIPELINE_OFFICIAL_CATALOG: {
                "purpose": "Build locked Official Truth before any user or community evidence is learned.",
                "writes": ["official_products", "official_product_assets", "official_product_visual_references"],
                "truth_layer": TRUTH_OFFICIAL,
                "may_override": [],
            },
            PIPELINE_INTERNAL_UPLOAD: {
                "purpose": "Learn Reality Truth from user-owned uploads after Official Catalog matching.",
                "writes": ["assets", "analysis_jobs", "vision_observations", "review_queue"],
                "truth_layer": TRUTH_REALITY,
                "may_override": [],
            },
            PIPELINE_EXTERNAL_KNOWLEDGE: {
                "purpose": "Reserved for future public/community knowledge ingestion without changing Official Truth.",
                "writes": ["external_knowledge_items", "review_queue"],
                "truth_layer": TRUTH_COMMUNITY,
                "may_override": [],
            },
        },
        "truth_priority": TRUTH_PRIORITY,
        "source_types": SOURCE_TYPES,
        "api_design": RESERVED_API_DESIGN,
        "rule": "Official Truth can be supplemented but not overwritten by Reality Truth or Community Truth.",
    }
