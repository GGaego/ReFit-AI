import os
import json
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import date
from typing import Dict, Any, List, Optional, Iterator

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SMART_CLOSET_DB", os.path.join(BASE_DIR, "closet.db"))
_TMP_DB_PATH = os.path.join(tempfile.gettempdir(), "smart_closet_active.db")

CANONICAL_CATEGORIES = [
    "Tops",
    "Bottoms",
    "Outerwear",
    "Footwear",
    "Accessories",
    "One-Piece / Dresses",
]

ALL_VALID_CATEGORIES = CANONICAL_CATEGORIES + ["Unassigned"]

STANDARD_VIBES = [
    "Streetwear",
    "Casual",
    "Smart Casual",
    "Minimalist",
    "Formal",
    "Athleisure",
    "Workwear",
    "Layered",
]


def normalize_category(raw_category: Optional[str], default: str = "Unassigned") -> str:
    """Normalize any category string to one of the canonical system categories or Unassigned."""
    if not raw_category:
        return default
    cleaned = str(raw_category).strip()
    lower = cleaned.lower()

    if lower in ("unassigned", "unknown", "none", "null", ""):
        return "Unassigned"

    for canon in CANONICAL_CATEGORIES:
        if lower == canon.lower():
            return canon

    if any(k in lower for k in ["one-piece", "one piece", "dress", "jumpsuit", "romper", "overall", "gown"]):
        return "One-Piece / Dresses"
    if any(k in lower for k in ["top", "shirt", "tee", "t-shirt", "hoodie", "sweater", "crewneck", "knit", "blouse", "tank", "polo", "cardigan"]):
        return "Tops"
    if any(k in lower for k in ["bottom", "pant", "jean", "denim", "trouser", "short", "skirt", "chino", "cargo", "sweatpant", "legging"]):
        return "Bottoms"
    if any(k in lower for k in ["outer", "jacket", "coat", "blazer", "parka", "trench", "bomber", "vest", "overshirt", "windbreaker"]):
        return "Outerwear"
    if any(k in lower for k in ["foot", "shoe", "sneaker", "boot", "loafer", "derby", "sandal", "heel", "mule", "flat", "runner"]):
        return "Footwear"
    if any(k in lower for k in ["access", "belt", "bag", "tote", "hat", "cap", "beanie", "scarf", "watch", "jewelry", "glasses", "sunglass", "tie", "sock"]):
        return "Accessories"

    return default


def normalize_vibe(raw_vibe: Optional[str], default: str = "Casual") -> str:
    """Normalize an outfit vibe tag to a standardized styling option."""
    if not raw_vibe:
        return default
    cleaned = str(raw_vibe).strip()
    lower = cleaned.lower()

    for std in STANDARD_VIBES:
        if lower == std.lower():
            return std

    if "street" in lower or "urban" in lower or "grunge" in lower or "skate" in lower:
        return "Streetwear"
    if "smart" in lower or "business casual" in lower or "elevated" in lower or "preppy" in lower:
        return "Smart Casual"
    if "athleisure" in lower or "sport" in lower or "athletic" in lower or "lounge" in lower or "gym" in lower:
        return "Athleisure"
    if "minimal" in lower or "monochrome" in lower or "clean" in lower or "quiet luxury" in lower:
        return "Minimalist"
    if "formal" in lower or "tailor" in lower or "evening" in lower or "dressy" in lower or "sartorial" in lower:
        return "Formal"
    if "workwear" in lower or "utility" in lower or "utilitarian" in lower or "chore" in lower or "heritage" in lower:
        return "Workwear"
    if "layer" in lower or "autumn" in lower or "winter" in lower or "transitional" in lower:
        return "Layered"
    if "casual" in lower or "weekend" in lower or "everyday" in lower or "relaxed" in lower:
        return "Casual"

    return default


def infer_vibe_from_outfit_text(outfit_name: str, styling_notes: str, idx: int = 0) -> str:
    """Infer a standardized vibe from outfit title and styling notes when migrating legacy rows."""
    combined = f"{outfit_name} {styling_notes}".lower()
    if any(w in combined for w in ["urban", "street", "hoodie", "sneaker", "shadow"]):
        return "Streetwear"
    if any(w in combined for w in ["sweatpant", "lounge", "fleece", "athletic", "sport"]):
        return "Athleisure"
    if any(w in combined for w in ["minimal", "monochrome", "clean", "muse", "tonal"]):
        return "Minimalist"
    if any(w in combined for w in ["formal", "tailor", "evening", "sartorial", "polished"]):
        return "Formal"
    if any(w in combined for w in ["smart", "chic", "derby", "poplin", "loafer", "elevated"]):
        return "Smart Casual"
    if any(w in combined for w in ["workwear", "utility", "cargo", "canvas"]):
        return "Workwear"
    if any(w in combined for w in ["casual", "desert", "weekend", "relaxed", "sun-kissed"]):
        return "Casual"
    fallback_cycle = ["Streetwear", "Casual", "Smart Casual", "Minimalist", "Formal", "Athleisure"]
    return fallback_cycle[idx % len(fallback_cycle)]


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """
    Open a SQLite connection synced with DB_PATH.
    Uses a local temp working copy when DB_PATH resides on a FUSE/virtual mount
    so SQLite transactions never fail with disk I/O lock errors, syncing back on close.
    """
    if os.path.exists(DB_PATH):
        if not os.path.exists(_TMP_DB_PATH) or os.path.getmtime(DB_PATH) > os.path.getmtime(_TMP_DB_PATH):
            shutil.copy2(DB_PATH, _TMP_DB_PATH)
    elif os.path.exists(_TMP_DB_PATH):
        try:
            os.remove(_TMP_DB_PATH)
        except OSError:
            pass

    conn = sqlite3.connect(_TMP_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        total_changes = conn.total_changes
        conn.close()
        if total_changes > 0 or not os.path.exists(DB_PATH):
            try:
                shutil.copy2(_TMP_DB_PATH, DB_PATH)
            except OSError:
                pass


def init_db() -> None:
    """Create the clothes and outfits SQLite tables if they do not exist and normalize legacy records."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clothes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_url TEXT NOT NULL,
                type TEXT NOT NULL,
                category TEXT NOT NULL,
                color TEXT NOT NULL,
                fit TEXT NOT NULL,
                size TEXT,
                description TEXT DEFAULT '',
                last_worn TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outfits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                outfit_name TEXT NOT NULL,
                item_ids TEXT NOT NULL,
                styling_notes TEXT DEFAULT '',
                vibe TEXT DEFAULT 'Casual',
                is_saved INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Ensure `is_saved` column exists on legacy `outfits` tables
        outfit_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(outfits)").fetchall()
        }
        if "is_saved" not in outfit_cols:
            conn.execute("ALTER TABLE outfits ADD COLUMN is_saved INTEGER DEFAULT 0")

        # Issue 1: Remove any programmatically generated studio capsule SVG records on initialization
        conn.execute(
            "DELETE FROM clothes WHERE image_url LIKE '%/demo_%.svg' OR image_url LIKE 'demo_%.svg'"
        )

        clothes_rows = conn.execute("SELECT id, category FROM clothes").fetchall()
        for r in clothes_rows:
            norm_cat = normalize_category(r["category"], default="Tops")
            if norm_cat != r["category"]:
                conn.execute("UPDATE clothes SET category = ? WHERE id = ?", (norm_cat, r["id"]))

        outfit_rows = conn.execute("SELECT id, outfit_name, styling_notes, vibe, is_saved FROM outfits").fetchall()
        for idx, r in enumerate(outfit_rows):
            current_vibe = (r["vibe"] or "").strip()
            if not current_vibe or current_vibe.lower() == "editorial mix":
                inferred = infer_vibe_from_outfit_text(r["outfit_name"] or "", r["styling_notes"] or "", idx)
                conn.execute("UPDATE outfits SET vibe = ? WHERE id = ?", (inferred, r["id"]))
            else:
                norm_v = normalize_vibe(current_vibe, default="Casual")
                if norm_v != current_vibe:
                    conn.execute("UPDATE outfits SET vibe = ? WHERE id = ?", (norm_v, r["id"]))
            if r["is_saved"] is None:
                conn.execute("UPDATE outfits SET is_saved = 0 WHERE id = ?", (r["id"],))

        conn.commit()


def compute_last_worn_status(last_worn: Optional[str]) -> Dict[str, Any]:
    """Compute human-friendly label and badge state from YYYY-MM-DD date string."""
    if not last_worn or str(last_worn).strip().lower() in ("unassigned", "none", "null", ""):
        return {"label": "Never worn", "state": "never", "days_ago": None}

    try:
        worn_date = date.fromisoformat(str(last_worn)[:10])
        delta_days = (date.today() - worn_date).days
    except ValueError:
        return {"label": f"Worn {last_worn}", "state": "normal", "days_ago": None}

    if delta_days <= 0:
        return {"label": "Worn today", "state": "today", "days_ago": 0}
    if delta_days == 1:
        return {"label": "Worn yesterday", "state": "recent", "days_ago": 1}
    if delta_days <= 7:
        return {"label": f"Worn {delta_days}d ago", "state": "recent", "days_ago": delta_days}
    if delta_days <= 30:
        return {"label": f"Worn {delta_days}d ago", "state": "normal", "days_ago": delta_days}
    return {"label": f"Worn {delta_days}d ago", "state": "stale", "days_ago": delta_days}


def serialize_cloth_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["category"] = normalize_category(item.get("category"), default="Unassigned")
    item["name"] = item.get("type") or "Clothing Item"
    item["style_notes"] = item.get("description") if item.get("description") is not None else "Unassigned"
    item["last_worn_status"] = compute_last_worn_status(item.get("last_worn"))
    return item


def get_all_clothes(
    category: str = "all",
    search_query: str = "",
    sort_by: str = "newest",
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM clothes WHERE 1=1"
    params: List[Any] = []

    if category and category.strip().lower() != "all":
        norm_cat = normalize_category(category, default=category.strip())
        query += " AND LOWER(category) = LOWER(?)"
        params.append(norm_cat)

    if search_query and search_query.strip():
        q_like = f"%{search_query.strip().lower()}%"
        query += (
            " AND ("
            "LOWER(type) LIKE ? OR "
            "LOWER(category) LIKE ? OR "
            "LOWER(color) LIKE ? OR "
            "LOWER(fit) LIKE ? OR "
            "LOWER(COALESCE(size, '')) LIKE ? OR "
            "LOWER(COALESCE(description, '')) LIKE ?"
            ")"
        )
        params.extend([q_like] * 6)

    sort_map = {
        "newest": "id DESC",
        "never_worn": "CASE WHEN last_worn IS NULL OR last_worn = '' THEN 0 ELSE 1 END ASC, last_worn ASC, id DESC",
        "recently_worn": "CASE WHEN last_worn IS NULL OR last_worn = '' THEN 1 ELSE 0 END ASC, last_worn DESC, id DESC",
        "category": "LOWER(category) ASC, LOWER(type) ASC, id DESC",
        "color": "LOWER(color) ASC, LOWER(type) ASC, id DESC",
    }
    order_clause = sort_map.get(sort_by, "id DESC")
    query += f" ORDER BY {order_clause}"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [serialize_cloth_row(r) for r in rows]


def get_cloth_by_id(item_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM clothes WHERE id = ?", (item_id,)).fetchone()
    return serialize_cloth_row(row) if row else None


def insert_cloth(
    image_url: str,
    garment_type: str,
    category: str,
    color: str,
    fit: str,
    size: Optional[str] = None,
    description: str = "",
    last_worn: Optional[str] = None,
) -> Dict[str, Any]:
    norm_cat = normalize_category(category, default="Unassigned")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO clothes (image_url, type, category, color, fit, size, description, last_worn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (image_url, garment_type, norm_cat, color, fit, size, description, last_worn),
        )
        conn.commit()
        new_id = cursor.lastrowid
    return get_cloth_by_id(int(new_id))  # type: ignore[arg-type]


def update_cloth(item_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    normalized_payload: Dict[str, Any] = dict(payload)

    if "name" in normalized_payload and "type" not in normalized_payload:
        normalized_payload["type"] = normalized_payload["name"]
    if "style_notes" in normalized_payload and "description" not in normalized_payload:
        normalized_payload["description"] = normalized_payload["style_notes"]

    if "category" in normalized_payload and normalized_payload["category"] is not None:
        normalized_payload["category"] = normalize_category(
            normalized_payload["category"], default="Unassigned"
        )

    allowed_fields = {"type", "category", "color", "fit", "size", "description", "last_worn"}
    updates = []
    values: List[Any] = []

    for field in allowed_fields:
        if field in normalized_payload:
            updates.append(f"{field} = ?")
            values.append(normalized_payload[field])

    if updates:
        values.append(item_id)
        with get_connection() as conn:
            conn.execute(
                f"UPDATE clothes SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            conn.commit()

    return get_cloth_by_id(item_id)


def mark_cloth_worn_today(item_id: int) -> Optional[Dict[str, Any]]:
    today_iso = date.today().isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE clothes SET last_worn = ? WHERE id = ?",
            (today_iso, item_id),
        )
        conn.commit()
    return get_cloth_by_id(item_id)


def delete_cloth(item_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM clothes WHERE id = ?", (item_id,))
        conn.commit()
    return cursor.rowcount > 0


def get_hydrated_outfits() -> List[Dict[str, Any]]:
    clothes_map = {item["id"]: item for item in get_all_clothes()}
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM outfits ORDER BY id DESC").fetchall()

    hydrated: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        record = dict(row)
        try:
            raw_ids = json.loads(record.get("item_ids") or "[]")
            item_ids = []
            for raw_id in raw_ids:
                if isinstance(raw_id, str) and raw_id.startswith("item_"):
                    raw_id = raw_id.replace("item_", "")
                item_ids.append(int(raw_id))
        except (ValueError, TypeError, json.JSONDecodeError):
            item_ids = []

        items = [clothes_map[i] for i in item_ids if i in clothes_map]
        if items:
            raw_vibe = record.get("vibe") or ""
            if not raw_vibe or raw_vibe.lower() == "editorial mix":
                outfit_vibe = infer_vibe_from_outfit_text(
                    record.get("outfit_name", ""), record.get("styling_notes", ""), idx
                )
            else:
                outfit_vibe = normalize_vibe(raw_vibe, default="Casual")

            hydrated.append(
                {
                    "id": record["id"],
                    "outfit_name": record["outfit_name"],
                    "item_ids": [item["id"] for item in items],
                    "items": items,
                    "styling_notes": record.get("styling_notes", ""),
                    "vibe": outfit_vibe,
                    "is_saved": bool(record.get("is_saved", 0)),
                    "created_at": record.get("created_at"),
                }
            )
    return hydrated


def save_generated_outfits(
    generated_list: List[Dict[str, Any]],
    vibe: str = "All",
    replace_existing: bool = True,
) -> List[Dict[str, Any]]:
    default_vibe = (
        normalize_vibe(vibe, default="Casual")
        if vibe and vibe.lower() not in ("all", "editorial mix")
        else "Casual"
    )
    with get_connection() as conn:
        if replace_existing:
            conn.execute("DELETE FROM outfits WHERE is_saved = 0 OR is_saved IS NULL")
        for idx, outfit in enumerate(generated_list):
            item_vibe_raw = outfit.get("vibe")
            if item_vibe_raw and str(item_vibe_raw).lower() not in ("all", "editorial mix"):
                outfit_vibe = normalize_vibe(str(item_vibe_raw), default=default_vibe)
            elif vibe and vibe.lower() not in ("all", "editorial mix"):
                outfit_vibe = default_vibe
            else:
                outfit_vibe = infer_vibe_from_outfit_text(
                    outfit.get("outfit_name", ""), outfit.get("styling_notes", ""), idx
                )

            conn.execute(
                """
                INSERT INTO outfits (outfit_name, item_ids, styling_notes, vibe, is_saved)
                VALUES (?, ?, ?, ?, 0)
                """,
                (
                    outfit.get("outfit_name", "Curated Archival Look"),
                    json.dumps(outfit.get("item_ids", [])),
                    outfit.get("styling_notes", ""),
                    outfit_vibe,
                ),
            )
        conn.commit()
    return get_hydrated_outfits()


def toggle_save_outfit(outfit_id: int) -> Optional[Dict[str, Any]]:
    """Toggle the `is_saved` bookmark flag (0 <-> 1) for a specific outfit and return the hydrated record."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE outfits SET is_saved = 1 - COALESCE(is_saved, 0) WHERE id = ?",
            (outfit_id,),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM outfits WHERE id = ?", (outfit_id,)).fetchone()

    hydrated = get_hydrated_outfits()
    matched = next((o for o in hydrated if o["id"] == outfit_id), None)
    if matched is not None:
        return matched

    if row is not None:
        record = dict(row)
        return {
            "id": record["id"],
            "outfit_name": record["outfit_name"],
            "item_ids": json.loads(record.get("item_ids") or "[]"),
            "items": [],
            "styling_notes": record.get("styling_notes", ""),
            "vibe": normalize_vibe(record.get("vibe"), default="Casual"),
            "is_saved": bool(record.get("is_saved", 0)),
            "created_at": record.get("created_at"),
        }
    return None


def delete_outfit(outfit_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM outfits WHERE id = ?", (outfit_id,))
        conn.commit()
    return cursor.rowcount > 0
