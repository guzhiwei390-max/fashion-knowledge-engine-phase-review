import uuid
from collections import defaultdict
from typing import Any

from .database import connect, decode_json, encode_json, utc_now
from .unknown import UNKNOWN, unknown_response


def _known(value: Any) -> bool:
    return value not in (None, "", UNKNOWN)


def build_knowledge() -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT vision_observations.*, assets.id AS asset_id, assets.original_name
            FROM vision_observations
            JOIN assets ON assets.id = vision_observations.asset_id
            ORDER BY vision_observations.created_at ASC
            """
        ).fetchall()

        for row in rows:
            structured = decode_json(row["structured_output"], {})
            brand = structured.get("brand", UNKNOWN)
            product_name = structured.get("product_name", UNKNOWN)
            if _known(brand) and _known(product_name):
                item = dict(row)
                item["structured_output"] = structured
                groups[(brand, product_name)].append(item)

        built = 0
        for (brand, product_name), observations in groups.items():
            product_dna = build_product_dna(brand, product_name, observations)
            dna_suite = build_dna_suite(brand, product_name, observations, product_dna)
            knowledge_card = build_knowledge_card(brand, product_name, observations, dna_suite)
            subject_key = f"{brand}:{product_name}"
            evidence_asset_ids = sorted({item["asset_id"] for item in observations})
            unknown_fields = sorted(set(product_dna["unknown_fields"]) | set(knowledge_card["unknown_fields"]))
            now = utc_now()

            for dna_type, dna_json in dna_suite.items():
                conn.execute(
                    """
                    INSERT INTO dna_records (
                        id, dna_type, subject_key, dna_json, evidence_asset_ids,
                        unknown_fields, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dna_type, subject_key) DO UPDATE SET
                        dna_json = excluded.dna_json,
                        evidence_asset_ids = excluded.evidence_asset_ids,
                        unknown_fields = excluded.unknown_fields,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(uuid.uuid4()),
                        dna_type,
                        subject_key,
                        encode_json(dna_json),
                        encode_json(evidence_asset_ids),
                        encode_json(dna_json.get("unknown_fields", [])),
                        now,
                        now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO knowledge_cards (
                    id, knowledge_type, brand, product_name, card_json,
                    evidence_asset_ids, unknown_fields, created_at, updated_at
                )
                VALUES (?, 'ProductKnowledgeCard', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(knowledge_type, brand, product_name) DO UPDATE SET
                    card_json = excluded.card_json,
                    evidence_asset_ids = excluded.evidence_asset_ids,
                    unknown_fields = excluded.unknown_fields,
                    updated_at = excluded.updated_at
                """,
                (
                    str(uuid.uuid4()),
                    brand,
                    product_name,
                    encode_json(knowledge_card),
                    encode_json(evidence_asset_ids),
                    encode_json(unknown_fields),
                    now,
                    now,
                ),
            )
            built += 1
    return {"built": built}


def build_product_dna(brand: str, product_name: str, observations: list[dict[str, Any]]) -> dict[str, Any]:
    structured_items = [item["structured_output"] for item in observations]
    evidence_asset_ids = sorted({item["asset_id"] for item in observations})
    asset_types = sorted({
        item.get("asset_type")
        for item in structured_items
        if _known(item.get("asset_type"))
    })
    unknown_fields: set[str] = set()

    def verified_when(field: str, predicate: bool) -> str:
        if predicate:
            return "Verified"
        unknown_fields.add(field)
        return UNKNOWN

    structure = merge_product_structure(structured_items)
    product_dna = {
        "brand": brand,
        "product_name": product_name,
        "category": first_known(structured_items, "category", unknown_fields),
        "material": first_known(structured_items, "material", unknown_fields),
        "fit": UNKNOWN,
        "collar": verified_when("collar", "collar_detail" in asset_types),
        "zipper": verified_when("zipper", "zipper_detail" in asset_types),
        "logo": verified_when("logo", "logo_detail" in asset_types),
        "back_structure": verified_when("back_structure", "back" in asset_types),
        "product_structure": structure,
        "must_have": must_have_from_structure(structure),
        "evidence_asset_ids": evidence_asset_ids,
        "evidence_count": len(evidence_asset_ids),
        "unknown_fields": [],
    }

    for field in ("fit",):
        unknown_fields.add(field)
    product_dna["unknown_fields"] = sorted(unknown_fields)
    return product_dna


def must_have_from_structure(structure: dict[str, Any]) -> list[str]:
    must_have: list[str] = []
    for field in ("garment_type", "collar", "zipper", "sleeve", "logo", "back_structure", "material_visual_behavior", "fit"):
        value = structure.get(field)
        if value not in (None, "", UNKNOWN):
            must_have.append(f"{field}: {value}")
    return must_have


def merge_product_structure(structured_items: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "visible_evidence": [],
        "unknown_fields": [],
    }
    fields = ["garment_type", "collar", "zipper", "sleeve", "logo", "back_structure", "material_visual_behavior", "fit"]
    for field in fields:
        merged[field] = UNKNOWN
    for item in structured_items:
        structure = item.get("product_structure") or {}
        for field in fields:
            if merged[field] == UNKNOWN and structure.get(field) not in (None, "", UNKNOWN):
                merged[field] = structure[field]
        merged["visible_evidence"].extend(structure.get("visible_evidence", []))
    merged["visible_evidence"] = sorted(set(str(item) for item in merged["visible_evidence"] if item))
    merged["unknown_fields"] = [field for field in fields if merged[field] == UNKNOWN]
    return merged


def build_dna_suite(
    brand: str,
    product_name: str,
    observations: list[dict[str, Any]],
    product_dna: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    evidence_asset_ids = sorted({item["asset_id"] for item in observations})
    product_unknowns = set(product_dna.get("unknown_fields", []))
    return {
        "ProductDNA": product_dna,
        "GarmentValidationRules": build_garment_validation_rules(product_dna, evidence_asset_ids),
        "MaterialDNA": {
            "brand": brand,
            "product_name": product_name,
            "material": product_dna.get("material", UNKNOWN),
            "reflection": UNKNOWN,
            "texture": UNKNOWN,
            "compression": UNKNOWN,
            "wrinkle_behavior": UNKNOWN,
            "evidence_asset_ids": evidence_asset_ids,
            "unknown_fields": sorted(product_unknowns | {"reflection", "texture", "compression", "wrinkle_behavior"}),
        },
        "OutfitDNA": empty_context_dna(brand, product_name, "OutfitDNA", evidence_asset_ids),
        "TribeDNA": empty_context_dna(brand, product_name, "TribeDNA", evidence_asset_ids),
        "SceneDNA": empty_context_dna(brand, product_name, "SceneDNA", evidence_asset_ids),
        "CustomerRealityDNA": empty_context_dna(brand, product_name, "CustomerRealityDNA", evidence_asset_ids),
        "TrendDNA": empty_context_dna(brand, product_name, "TrendDNA", evidence_asset_ids),
    }


def build_garment_validation_rules(product_dna: dict[str, Any], evidence_asset_ids: list[str]) -> dict[str, Any]:
    structure = product_dna.get("product_structure", {})
    must_have = []
    for field in ("garment_type", "collar", "zipper", "sleeve", "logo", "back_structure", "material_visual_behavior", "fit"):
        value = structure.get(field)
        if value not in (None, "", UNKNOWN):
            must_have.append(f"{field}: {value}")
    for field in ("material", "category"):
        value = product_dna.get(field)
        if value not in (None, "", UNKNOWN):
            must_have.append(f"{field}: {value}")
    unknown_fields = [
        field
        for field in ("collar", "zipper", "logo", "back_structure", "material", "category")
        if product_dna.get(field) in (None, "", UNKNOWN)
    ]
    return {
        "product": product_dna["product_name"],
        "must_have": must_have,
        "must_not_have": [
            "unverified logo",
            "unknown structure",
            "unmatched official product",
            "visual details not supported by evidence",
        ],
        "fatal_errors": [
            "cannot match official product catalog",
            "low visual match confidence",
            "product changed into another garment",
        ],
        "evidence_asset_ids": evidence_asset_ids,
        "unknown_fields": sorted(unknown_fields),
    }


def empty_context_dna(brand: str, product_name: str, dna_type: str, evidence_asset_ids: list[str]) -> dict[str, Any]:
    return {
        "brand": brand,
        "product_name": product_name,
        "dna_type": dna_type,
        "status": "Initial",
        "evidence_asset_ids": evidence_asset_ids,
        "unknown_fields": ["insufficient_phase1_evidence"],
    }


def first_known(items: list[dict[str, Any]], field: str, unknown_fields: set[str]) -> str:
    for item in items:
        value = item.get(field)
        if _known(value):
            return value
    unknown_fields.add(field)
    return UNKNOWN


def build_knowledge_card(
    brand: str,
    product_name: str,
    observations: list[dict[str, Any]],
    dna_suite: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    product_dna = dna_suite["ProductDNA"]
    evidence_asset_ids = sorted({item["asset_id"] for item in observations})
    asset_types = sorted({
        item["structured_output"].get("asset_type")
        for item in observations
        if _known(item["structured_output"].get("asset_type"))
    })
    unknown_fields = sorted(set(product_dna["unknown_fields"]))

    return {
        "brand": brand,
        "product_name": product_name,
        "knowledge_type": "ProductKnowledgeCard",
        "dna": dna_suite,
        "verified_asset_types": asset_types,
        "must_have": dna_suite["GarmentValidationRules"]["must_have"],
        "must_not_have": dna_suite["GarmentValidationRules"]["must_not_have"],
        "evidence_asset_ids": evidence_asset_ids,
        "evidence_count": len(evidence_asset_ids),
        "unknown_fields": unknown_fields,
    }


def search_knowledge(query: dict[str, Any]) -> dict[str, Any]:
    brand = query.get("brand") or UNKNOWN
    product = query.get("product") or query.get("product_name") or UNKNOWN
    with connect() as conn:
        if not _known(brand) or not _known(product):
            result = unknown_response("brand", "product")
            log_query(conn, query, result, True)
            return result

        dna_rows = conn.execute(
            "SELECT * FROM dna_records WHERE subject_key = ?",
            (f"{brand}:{product}",),
        ).fetchall()
        card = conn.execute(
            """
            SELECT * FROM knowledge_cards
            WHERE knowledge_type = 'ProductKnowledgeCard' AND brand = ? AND product_name = ?
            """,
            (brand, product),
        ).fetchone()

        if not dna_rows or not card:
            result = unknown_response("product_dna", "garment_validation_rules", "material_dna", "knowledge_card")
            log_query(conn, query, result, True)
            return result

        dna_map = {
            row["dna_type"]: decode_json(row["dna_json"], {})
            for row in dna_rows
        }
        result = {
            "product_dna": dna_map.get("ProductDNA", {}),
            "garment_validation_rules": dna_map.get("GarmentValidationRules", {}),
            "material_dna": dna_map.get("MaterialDNA", {}),
            "outfit_dna": dna_map.get("OutfitDNA", {}),
            "tribe_dna": dna_map.get("TribeDNA", {}),
            "scene_dna": dna_map.get("SceneDNA", {}),
            "customer_reality_dna": dna_map.get("CustomerRealityDNA", {}),
            "trend_dna": dna_map.get("TrendDNA", {}),
            "knowledge_card": decode_json(card["card_json"], {}),
            "evidence_asset_ids": decode_json(card["evidence_asset_ids"], []),
            "unknown": sorted(
                set().union(*(set(item.get("unknown_fields", [])) for item in dna_map.values()))
                | set(decode_json(card["unknown_fields"], []))
            ),
        }
        log_query(conn, query, result, False)
        return result


def log_query(conn, query: dict[str, Any], result: dict[str, Any], returned_unknown: bool) -> None:
    conn.execute(
        """
        INSERT INTO retrieval_queries (id, query_json, result_json, returned_unknown, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), encode_json(query), encode_json(result), 1 if returned_unknown else 0, utc_now()),
    )


def list_knowledge_cards() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM knowledge_cards ORDER BY updated_at DESC").fetchall()
    cards = []
    for row in rows:
        item = dict(row)
        item["card_json"] = decode_json(item["card_json"], {})
        item["evidence_asset_ids"] = decode_json(item["evidence_asset_ids"], [])
        item["unknown_fields"] = decode_json(item["unknown_fields"], [])
        cards.append(item)
    return cards
