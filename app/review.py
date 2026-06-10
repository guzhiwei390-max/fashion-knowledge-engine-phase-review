import uuid
import shutil
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .database import decode_json, encode_json, utc_now
from .unknown import UNKNOWN
from .visual import encode_signature_for_db, image_signature


REVIEW_REASONS = {
    "unknown",
    "low_confidence",
    "conflict",
    "duplicate",
    "near_duplicate",
    "multi_product_uncertain",
    "low_quality_but_possibly_useful",
    "official_like_candidate",
    "official_candidate_review",
    "conflict_after_matching",
    "low_confidence_after_matching",
    "uncertain_product_identity",
}
ASSET_RESOLUTION_FIELDS = {"asset_type", "quality_status", "duplicate_status"}


def enqueue_review_item(
    conn,
    *,
    item_type: str,
    item_id: str,
    reason: str,
    confidence: float = 0.0,
    conflict: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_reason = reason if reason in REVIEW_REASONS else "unknown"
    existing = conn.execute(
        """
        SELECT * FROM review_queue
        WHERE item_type = ? AND item_id = ? AND reason = ? AND status = 'pending'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (item_type, item_id, normalized_reason),
    ).fetchone()
    if existing:
        return dict(existing)

    now = utc_now()
    review_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO review_queue (
            id, item_type, item_id, reason, status, confidence,
            conflict_json, review_payload, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            item_type,
            item_id,
            normalized_reason,
            float(confidence or 0.0),
            encode_json(conflict or {}),
            encode_json(payload or {}),
            now,
            now,
        ),
    )
    return {
        "id": review_id,
        "item_type": item_type,
        "item_id": item_id,
        "reason": normalized_reason,
        "status": "pending",
        "confidence": float(confidence or 0.0),
        "conflict_json": conflict or {},
        "review_payload": payload or {},
        "created_at": now,
        "updated_at": now,
    }


def list_review_items(
    conn,
    status: str | None = None,
    reason: str | None = None,
    item_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    for column, value in (("status", status), ("reason", reason), ("item_type", item_type)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = conn.execute(f"SELECT COUNT(*) AS count FROM review_queue {where}", params).fetchone()["count"]
    rows = conn.execute(
        f"SELECT * FROM review_queue {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["conflict_json"] = decode_json(item.get("conflict_json"), {})
        item["review_payload"] = decode_json(item.get("review_payload"), {})
        item["resolution_json"] = decode_json(item.get("resolution_json"), {})
        items.append(item)
    return {"review_queue": items, "total": total, "limit": limit, "offset": offset}


def list_official_candidate_groups(conn, status: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    if status:
        if status == "pending":
            status = "pending_review"
        clauses.append("official_candidate_groups.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = conn.execute(f"SELECT COUNT(*) AS count FROM official_candidate_groups {where}", params).fetchone()["count"]
    rows = conn.execute(
        f"""
        SELECT official_candidate_groups.*,
               assets.thumbnail_uri AS representative_thumbnail_uri,
               assets.original_name AS representative_original_name
        FROM official_candidate_groups
        LEFT JOIN assets ON assets.id = official_candidate_groups.representative_asset_id
        {where}
        ORDER BY official_candidate_groups.updated_at DESC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    groups = []
    for row in rows:
        item = dict(row)
        item["signals_json"] = decode_json(item.get("signals_json"), {})
        item["related_assets"] = [
            dict(candidate)
            for candidate in conn.execute(
                """
                SELECT official_candidate_assets.*, assets.original_name, assets.thumbnail_uri
                FROM official_candidate_assets
                JOIN assets ON assets.id = official_candidate_assets.asset_id
                WHERE official_candidate_assets.grouping_key = ?
                ORDER BY official_candidate_assets.created_at DESC
                LIMIT 50
                """,
                (row["grouping_key"],),
            ).fetchall()
        ]
        groups.append(item)
    return {"official_candidate_groups": groups, "total": total, "limit": limit, "offset": offset}


def resolve_review_item(
    conn,
    *,
    review_id: str,
    resolution: dict[str, Any],
    resolved_by: str = "admin",
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM review_queue WHERE id = ?", (review_id,)).fetchone()
    if not row:
        return {"result": "Unknown", "unknown": ["review_id"]}
    now = utc_now()
    learning_actions = apply_review_resolution(conn, dict(row), resolution, resolved_by)
    conn.execute(
        """
        UPDATE review_queue
        SET status = 'resolved',
            resolution_json = ?,
            resolved_by = ?,
            resolved_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (encode_json(resolution), resolved_by, now, now, review_id),
    )
    return {"status": "resolved", "review_id": review_id, "resolved_by": resolved_by, "learning_actions": learning_actions}


def resolve_official_candidate_group(conn, group_id: str, payload: dict[str, Any], resolved_by: str = "admin") -> dict[str, Any]:
    group = conn.execute("SELECT * FROM official_candidate_groups WHERE id = ?", (group_id,)).fetchone()
    if not group:
        return {"result": "Unknown", "unknown": ["official_candidate_group"]}
    action = str(payload.get("action") or "").strip().lower()
    if action == "approve":
        return approve_official_candidate_group(conn, dict(group), payload, resolved_by)
    if action == "reject":
        return reject_official_candidate_group(conn, dict(group), payload, resolved_by)
    if action == "merge":
        return merge_official_candidate_group(conn, dict(group), payload, resolved_by)
    if action == "split":
        return split_official_candidate_group(conn, dict(group), payload, resolved_by)
    return {"result": "Unknown", "unknown": ["action"]}


def approve_official_candidate_group(conn, group: dict[str, Any], payload: dict[str, Any], resolved_by: str) -> dict[str, Any]:
    candidates = conn.execute(
        "SELECT * FROM official_candidate_assets WHERE grouping_key = ? AND status != 'rejected' ORDER BY created_at ASC",
        (group["grouping_key"],),
    ).fetchall()
    if not candidates:
        return {"result": "Unknown", "unknown": ["official_candidate_assets"]}
    created = []
    resolution = dict(payload)
    resolution.setdefault("brand", group["brand_hint"])
    resolution.setdefault("product_name", group["product_name_hint"])
    resolution.setdefault("asset_type", "official_white_bg")
    for candidate in candidates:
        result = confirm_official_candidate_asset(conn, candidate["id"], resolution, resolved_by)
        if result.get("status") == "official_truth_created":
            created.append(result)
            conn.execute(
                """
                UPDATE review_queue
                SET status = 'resolved',
                    resolution_json = ?,
                    resolved_by = ?,
                    resolved_at = ?,
                    updated_at = ?
                WHERE item_type = 'official_candidate_asset'
                  AND item_id = ?
                  AND status = 'pending'
                """,
                (encode_json(payload), resolved_by, utc_now(), utc_now(), candidate["id"]),
            )
    return {
        "status": "group_approved",
        "group_id": group["id"],
        "confirmed_candidates": len(created),
        "official_product_id": created[0]["product_id"] if created else None,
        "official_product_asset_ids": [item["official_product_asset_id"] for item in created],
        "official_visual_reference_ids": [item["official_visual_reference_id"] for item in created],
    }


def reject_official_candidate_group(conn, group: dict[str, Any], payload: dict[str, Any], resolved_by: str) -> dict[str, Any]:
    now = utc_now()
    conn.execute(
        "UPDATE official_candidate_groups SET status = 'rejected', updated_at = ? WHERE id = ?",
        (now, group["id"]),
    )
    conn.execute(
        "UPDATE official_candidate_assets SET status = 'rejected', updated_at = ? WHERE grouping_key = ?",
        (now, group["grouping_key"]),
    )
    conn.execute(
        """
        UPDATE review_queue
        SET status = 'resolved',
            resolution_json = ?,
            resolved_by = ?,
            resolved_at = ?,
            updated_at = ?
        WHERE item_type = 'official_candidate_asset'
          AND item_id IN (SELECT id FROM official_candidate_assets WHERE grouping_key = ?)
          AND status = 'pending'
        """,
        (encode_json(payload), resolved_by, now, now, group["grouping_key"]),
    )
    record_human_correction(conn, "official_candidate_group", group["id"], "status", group["status"], "rejected", resolved_by)
    return {"status": "group_rejected", "group_id": group["id"]}


def merge_official_candidate_group(conn, group: dict[str, Any], payload: dict[str, Any], resolved_by: str) -> dict[str, Any]:
    target_group_id = str(payload.get("target_group_id") or "").strip()
    target = conn.execute("SELECT * FROM official_candidate_groups WHERE id = ?", (target_group_id,)).fetchone()
    if not target:
        return {"result": "Unknown", "unknown": ["target_group_id"]}
    now = utc_now()
    conn.execute(
        "UPDATE official_candidate_assets SET grouping_key = ?, updated_at = ? WHERE grouping_key = ?",
        (target["grouping_key"], now, group["grouping_key"]),
    )
    conn.execute(
        "UPDATE official_candidate_groups SET status = 'merged', updated_at = ? WHERE id = ?",
        (now, group["id"]),
    )
    refresh_official_candidate_group_count(conn, target["grouping_key"])
    record_human_correction(conn, "official_candidate_group", group["id"], "merged_into", group["grouping_key"], target["grouping_key"], resolved_by)
    return {"status": "group_merged", "group_id": group["id"], "target_group_id": target["id"]}


def split_official_candidate_group(conn, group: dict[str, Any], payload: dict[str, Any], resolved_by: str) -> dict[str, Any]:
    candidate_ids = [str(item) for item in payload.get("candidate_ids", []) if item]
    if not candidate_ids:
        return {"result": "Unknown", "unknown": ["candidate_ids"]}
    now = utc_now()
    new_grouping_key = str(payload.get("new_grouping_key") or f"{group['grouping_key']}|split|{uuid.uuid4().hex[:8]}")
    new_group_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO official_candidate_groups (
            id, grouping_key, brand_hint, product_name_hint, candidate_count,
            representative_asset_id, status, signals_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 0, NULL, 'pending_review', ?, ?, ?)
        """,
        (
            new_group_id,
            new_grouping_key,
            str(payload.get("brand_hint") or group["brand_hint"]),
            str(payload.get("product_name_hint") or group["product_name_hint"]),
            encode_json({"split_from_group_id": group["id"], "split_reason": payload.get("reason", "")}),
            now,
            now,
        ),
    )
    placeholders = ",".join("?" for _ in candidate_ids)
    conn.execute(
        f"UPDATE official_candidate_assets SET grouping_key = ?, updated_at = ? WHERE id IN ({placeholders})",
        (new_grouping_key, now, *candidate_ids),
    )
    refresh_official_candidate_group_count(conn, group["grouping_key"])
    refresh_official_candidate_group_count(conn, new_grouping_key)
    record_human_correction(conn, "official_candidate_group", group["id"], "split", group["grouping_key"], new_grouping_key, resolved_by)
    return {"status": "group_split", "group_id": group["id"], "new_group_id": new_group_id, "moved_candidates": len(candidate_ids)}


def refresh_official_candidate_group_count(conn, grouping_key: str) -> None:
    row = conn.execute(
        "SELECT COUNT(*) AS count, MIN(asset_id) AS representative_asset_id FROM official_candidate_assets WHERE grouping_key = ?",
        (grouping_key,),
    ).fetchone()
    conn.execute(
        """
        UPDATE official_candidate_groups
        SET candidate_count = ?,
            representative_asset_id = ?,
            updated_at = ?
        WHERE grouping_key = ?
        """,
        (int(row["count"]), row["representative_asset_id"], utc_now(), grouping_key),
    )


def apply_review_resolution(conn, review: dict[str, Any], resolution: dict[str, Any], resolved_by: str) -> dict[str, Any]:
    actions = {
        "asset_updated": False,
        "official_truth_written": False,
        "reality_truth_written": False,
        "correction_recorded": False,
        "rebuild_required": False,
    }
    item_type = review["item_type"]
    item_id = review["item_id"]
    if item_type == "vision_observation":
        observation = conn.execute("SELECT * FROM vision_observations WHERE id = ?", (item_id,)).fetchone()
        if observation:
            structured = decode_json(observation["structured_output"], {})
            changed = False
            for field, value in resolution.items():
                if field in {"resolved_by", "correction_reason"}:
                    continue
                if value in (None, ""):
                    continue
                old_value = structured.get(field)
                if old_value != value:
                    structured[field] = value
                    changed = True
                    record_human_correction(conn, "vision_observation", item_id, field, old_value, value, resolved_by)
            if changed:
                unknown_fields = [field for field in structured.get("unknown_fields", []) if field not in resolution]
                structured["unknown_fields"] = unknown_fields
                conn.execute(
                    "UPDATE vision_observations SET structured_output = ?, unknown_fields = ? WHERE id = ?",
                    (encode_json(structured), encode_json(unknown_fields), item_id),
                )
                actions["reality_truth_written"] = True
                actions["correction_recorded"] = True
                actions["rebuild_required"] = True
    if item_type == "asset":
        asset = conn.execute("SELECT * FROM assets WHERE id = ?", (item_id,)).fetchone()
        if asset:
            metadata = decode_json(asset["ingestion_metadata"], {})
            metadata.setdefault("human_review", []).append(
                {
                    "resolved_by": resolved_by,
                    "reason": resolution.get("correction_reason", ""),
                    "resolution": resolution,
                    "resolved_at": utc_now(),
                    "truth_layer": "reality_truth",
                }
            )
            assignments = []
            params: list[Any] = []
            for field in ASSET_RESOLUTION_FIELDS:
                if resolution.get(field):
                    assignments.append(f"{field} = ?")
                    params.append(str(resolution[field]))
                    record_human_correction(conn, "asset", item_id, field, asset[field], str(resolution[field]), resolved_by)
            assignments.append("ingestion_metadata = ?")
            params.append(encode_json(metadata))
            params.append(item_id)
            conn.execute(f"UPDATE assets SET {', '.join(assignments)} WHERE id = ?", params)
            actions["asset_updated"] = bool(assignments)
            actions["reality_truth_written"] = True
            actions["correction_recorded"] = True
            actions["rebuild_required"] = True
    if item_type == "official_candidate_asset":
        official_result = confirm_official_candidate_asset(conn, item_id, resolution, resolved_by)
        if official_result.get("status") == "official_truth_created":
            actions["official_truth_written"] = True
            actions["correction_recorded"] = True
            actions["rebuild_required"] = True
            actions["official_product_id"] = official_result["product_id"]
            actions["official_product_asset_id"] = official_result["official_product_asset_id"]
            actions["official_visual_reference_id"] = official_result["official_visual_reference_id"]
    return actions


def confirm_official_candidate_asset(conn, candidate_id: str, resolution: dict[str, Any], resolved_by: str) -> dict[str, Any]:
    candidate = conn.execute("SELECT * FROM official_candidate_assets WHERE id = ?", (candidate_id,)).fetchone()
    if not candidate:
        return {"result": "Unknown", "unknown": ["official_candidate_asset"]}
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (candidate["asset_id"],)).fetchone()
    if not asset:
        return {"result": "Unknown", "unknown": ["asset"]}

    brand = str(resolution.get("brand") or candidate["brand_hint"] or UNKNOWN).strip() or UNKNOWN
    product_name = str(resolution.get("product_name") or candidate["product_name_hint"] or UNKNOWN).strip() or UNKNOWN
    category = str(resolution.get("category") or "Unknown").strip() or UNKNOWN
    if brand == UNKNOWN or product_name == UNKNOWN:
        return {"result": "Unknown", "unknown": ["brand", "product_name"]}

    now = utc_now()
    existing = conn.execute(
        "SELECT * FROM official_products WHERE brand = ? AND product_name = ?",
        (brand, product_name),
    ).fetchone()
    product_id = existing["id"] if existing else str(uuid.uuid4())
    aliases = [product_name]
    official_fields = {
        "brand": brand,
        "product_name": product_name,
        "category": category,
        "source": "official_candidate_review",
        "reviewed_by": resolved_by,
        "candidate_asset_id": candidate_id,
    }
    conn.execute(
        """
        INSERT INTO official_products (
            id, brand, product_name, product_family, variant, aliases, category, description,
            colors, material, official_url, import_type, official_fields_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'official_candidate_review', ?, ?, ?)
        ON CONFLICT(brand, product_name) DO UPDATE SET
            category = excluded.category,
            import_type = excluded.import_type,
            official_fields_json = excluded.official_fields_json,
            updated_at = excluded.updated_at
        """,
        (
            product_id,
            brand,
            product_name,
            str(resolution.get("product_family", UNKNOWN) or UNKNOWN),
            str(resolution.get("variant", UNKNOWN) or UNKNOWN),
            encode_json(aliases),
            category,
            str(resolution.get("description", UNKNOWN) or UNKNOWN),
            encode_json([value for value in [resolution.get("color")] if value]),
            str(resolution.get("material", UNKNOWN) or UNKNOWN),
            str(resolution.get("official_url", asset["file_uri"]) or asset["file_uri"]),
            encode_json(official_fields),
            now,
            now,
        ),
    )
    asset_type = str(resolution.get("asset_type") or "official_white_bg")
    reference_id = str(uuid.uuid4())
    source_path = Path(asset["file_uri"])
    suffix = source_path.suffix or ".jpg"
    destination = DATA_DIR / "official_refs" / product_id / f"{reference_id}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    signature = image_signature(str(destination))
    conn.execute(
        """
        INSERT INTO official_product_assets (
            id, product_id, asset_type, uri, local_file_uri, visual_signature, import_type, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'official_candidate_review', ?)
        """,
        (
            reference_id,
            product_id,
            asset_type,
            asset["file_uri"],
            str(destination),
            encode_signature_for_db(signature),
            now,
        ),
    )
    visual_reference_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO official_product_visual_references (
            id, product_id, official_product_asset_id, asset_type,
            local_file_uri, visual_signature, structure_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, '{}', ?)
        """,
        (
            visual_reference_id,
            product_id,
            reference_id,
            asset_type,
            str(destination),
            encode_signature_for_db(signature),
            now,
        ),
    )
    conn.execute(
        """
        UPDATE official_candidate_assets
        SET status = 'confirmed',
            confirmed_product_id = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (product_id, now, candidate_id),
    )
    conn.execute(
        """
        UPDATE official_candidate_groups
        SET status = 'confirmed',
            confirmed_product_id = ?,
            updated_at = ?
        WHERE grouping_key = ?
        """,
        (product_id, now, candidate["grouping_key"]),
    )
    conn.execute(
        """
        UPDATE assets
        SET product_matching_status = 'pending'
        WHERE product_matching_status = 'blocked_missing_official_catalog'
        """
    )
    conn.execute(
        """
        UPDATE analysis_jobs
        SET status = 'queued', error_message = NULL
        WHERE status = 'blocked_missing_official_catalog'
        """
    )
    record_human_correction(conn, "official_candidate_asset", candidate_id, "official_product", UNKNOWN, f"{brand} / {product_name}", resolved_by)
    return {
        "status": "official_truth_created",
        "product_id": product_id,
        "official_product_asset_id": reference_id,
        "official_visual_reference_id": visual_reference_id,
    }


def record_human_correction(
    conn,
    target_type: str,
    target_id: str,
    field_name: str,
    old_value: Any,
    new_value: Any,
    corrected_by: str,
) -> None:
    conn.execute(
        """
        INSERT INTO human_corrections (
            id, target_type, target_id, field_name, old_value, new_value, corrected_by, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            target_type,
            target_id,
            field_name,
            None if old_value is None else str(old_value),
            str(new_value),
            corrected_by,
            utc_now(),
        ),
    )
