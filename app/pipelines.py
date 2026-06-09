from typing import Any


PIPELINE_INTERNAL_UPLOAD = "internal_upload"
PIPELINE_EXTERNAL_KNOWLEDGE = "external_knowledge"
PIPELINE_OFFICIAL_CATALOG = "official_catalog"
PIPELINE_RESERVED_FUTURE = "reserved_future"

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
    "uploaded_video": {
        "pipeline_type": PIPELINE_INTERNAL_UPLOAD,
        "truth_layer": TRUTH_REALITY,
        "description": "Reserved for future user-uploaded videos and extracted frames.",
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
    "reserved_extension": {
        "pipeline_type": PIPELINE_RESERVED_FUTURE,
        "truth_layer": TRUTH_COMMUNITY,
        "description": "Reserved-only source type for future Success Library, Negative Library, Commercial Score, Trend Timeline, Region Layer, and Learning Feedback Loop.",
    },
}


RESERVED_EXTENSION_MODULES: dict[str, dict[str, Any]] = {
    "success_library": {
        "table": "success_library_items",
        "status": "reserved_only",
        "purpose": "Future storage for proven successful product/community examples after product identity is reliable.",
        "no_phase1_logic": True,
    },
    "negative_library": {
        "table": "negative_library_items",
        "status": "reserved_only",
        "purpose": "Future storage for failed or disallowed examples without contaminating Product DNA.",
        "no_phase1_logic": True,
    },
    "commercial_score": {
        "table": "commercial_score_records",
        "status": "reserved_only",
        "purpose": "Future scoring records for commercial potential after evidence and trend systems exist.",
        "no_phase1_logic": True,
    },
    "trend_timeline": {
        "table": "trend_timeline_events",
        "status": "reserved_only",
        "purpose": "Future time-series trend evidence separated from product identity truth.",
        "no_phase1_logic": True,
    },
    "region_layer": {
        "table": "region_layers",
        "status": "reserved_only",
        "purpose": "Future region/localization layer for market-specific evidence.",
        "no_phase1_logic": True,
    },
    "learning_feedback_loop": {
        "table": "learning_feedback_events",
        "status": "reserved_only",
        "purpose": "Future feedback events from human review and model improvement without direct Phase 1 automation.",
        "no_phase1_logic": True,
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
        "GET /api/batches",
        "GET /api/batches/{batch_id}",
        "POST /api/batches/{batch_id}/retry",
        "POST /api/batches/{batch_id}/pause",
        "POST /api/batches/{batch_id}/resume",
        "POST /api/batches/{batch_id}/vision-budget",
    ],
    "reserved_future_endpoints": {
        PIPELINE_INTERNAL_UPLOAD: [
            "POST /api/internal/uploads/images",
            "POST /api/internal/uploads/zip",
            "GET /api/internal/reality-truth/search",
            "POST /api/internal/uploads/folder",
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
        PIPELINE_RESERVED_FUTURE: [
            "GET /api/extensions/reserved",
            "POST /api/future/success-library",
            "POST /api/future/negative-library",
            "POST /api/future/commercial-score",
            "POST /api/future/trend-timeline",
            "POST /api/future/region-layer",
            "POST /api/future/learning-feedback",
            "POST /api/future/video-assets",
            "POST /api/future/frame-extraction-jobs",
        ],
    },
    "vision_provider_adapter": {
        "providers": ["openai", "mimo", "qwen_vl", "gemini", "local"],
        "environment": [
            "VISION_PROVIDER",
            "MAX_VISION_CALLS_PER_BATCH",
            "VISION_COST_LIMIT",
            "VISION_REQUIRE_CONFIRM_ABOVE",
        ],
        "unified_schema": [
            "product_match",
            "product_structure",
            "multi_product",
            "quality",
        ],
        "rule": "Vision providers are expert verification adapters, not first-layer classifiers.",
        "ab_test_ready": True,
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
                "ingestion_order": [
                    "file type check",
                    "corruption check",
                    "original preservation",
                    "metadata extraction",
                    "thumbnail generation",
                    "file hash",
                    "perceptual hash",
                    "coarse classification",
                    "queue analysis job",
                ],
                "truth_layer": TRUTH_REALITY,
                "may_override": [],
            },
            PIPELINE_EXTERNAL_KNOWLEDGE: {
                "purpose": "Reserved for future public/community knowledge ingestion without changing Official Truth.",
                "writes": ["external_knowledge_items", "review_queue"],
                "truth_layer": TRUTH_COMMUNITY,
                "may_override": [],
            },
            PIPELINE_RESERVED_FUTURE: {
                "purpose": "Reserved-only extension slots. No Phase 1 logic, UI, scoring, trend, region, or feedback automation is enabled.",
                "writes": [
                    "success_library_items",
                    "negative_library_items",
                    "commercial_score_records",
                    "trend_timeline_events",
                    "region_layers",
                    "learning_feedback_events",
                    "material_reality_patterns",
                    "human_reality_patterns",
                    "scene_reality_patterns",
                    "moment_patterns",
                    "outfit_reality_patterns",
                    "reality_score_schema",
                    "video_assets",
                    "video_frame_assets",
                    "frame_extraction_jobs",
                ],
                "truth_layer": TRUTH_COMMUNITY,
                "may_override": [],
            },
        },
        "truth_priority": TRUTH_PRIORITY,
        "source_types": SOURCE_TYPES,
        "reserved_extension_modules": RESERVED_EXTENSION_MODULES,
        "api_design": RESERVED_API_DESIGN,
        "vision_provider_adapter": RESERVED_API_DESIGN["vision_provider_adapter"],
        "rule": "Official Truth can be supplemented but not overwritten by Reality Truth or Community Truth.",
    }
