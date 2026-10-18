import os
import re
import json
import uuid
import base64
import hashlib
import mimetypes
from datetime import date
from typing import Dict, Any, List, Optional, Tuple

from flask import (
    Flask,
    Response,
    request,
    jsonify,
    render_template,
    send_from_directory,
)
from jinja2 import ChoiceLoader, FileSystemLoader
from werkzeug.utils import secure_filename

import database

# Optional PIL import for dominant color inspection in offline purchase evaluator
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# Attempt to import modern google-genai SDK first, with fallback to google-generativeai
GENAI_SDK = None
try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_SDK = "google-genai"
except ImportError:
    try:
        import google.generativeai as legacy_genai
        GENAI_SDK = "google-generativeai"
    except ImportError:
        GENAI_SDK = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "svg"}
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

PLACEHOLDER_GARMENT_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 375" width="300" height="375">
  <rect width="300" height="375" fill="#F3F4F6"/>
  <rect x="18" y="18" width="264" height="339" rx="6" fill="none" stroke="#E4E4E7" stroke-width="1.5" stroke-dasharray="4 4"/>
  <g transform="translate(150, 165)" fill="none" stroke="#8E8E98" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M0 -38 C8 -38 12 -32 12 -26 C12 -20 6 -16 0 -13 L-58 20 C-64 23 -62 32 -54 32 L54 32 C62 32 64 23 58 20 L0 -13"/>
    <path d="M-38 32 L-38 62 L38 62 L38 32"/>
  </g>
  <text x="150" y="268" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="11" fill="#52525B" letter-spacing="1.5">ARCHIVE GARMENT</text>
  <text x="150" y="286" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="9" fill="#8E8E98" letter-spacing="1">IMAGE PLACEHOLDER</text>
</svg>"""

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

# Support both standard `templates/` directory and flat single-folder layout
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(os.path.join(BASE_DIR, "templates")),
    FileSystemLoader(BASE_DIR),
])


def cleanup_programmatic_demo_files() -> None:
    """Remove any leftover programmatically generated demo SVG files on initialization."""
    if not os.path.isdir(UPLOAD_FOLDER):
        return
    known_demos = [
        "demo_work_jacket.svg",
        "demo_ecru_tee.svg",
        "demo_poplin_shirt.svg",
        "demo_charcoal_knit.svg",
        "demo_selvedge_jeans.svg",
        "demo_olive_cargos.svg",
        "demo_penny_loafers.svg",
        "demo_leather_belt.svg",
    ]
    candidates = set(known_demos)
    try:
        for fname in os.listdir(UPLOAD_FOLDER):
            if fname.startswith("demo_") and fname.lower().endswith(".svg"):
                candidates.add(fname)
    except OSError:
        pass
    for fname in candidates:
        fpath = os.path.join(UPLOAD_FOLDER, fname)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass


# Ensure SQLite tables exist, legacy records are normalized, and demo SVGs are removed at startup
database.init_db()
cleanup_programmatic_demo_files()

# ---------------------------------------------------------------------------
# Gemini System Prompts (Strict Schemas)
# ---------------------------------------------------------------------------

VISION_SYSTEM_PROMPT = """You are a precise fashion archivist and garment analyzer. 
Analyze the provided clothing image and extract its properties into a strict JSON object. 
Never hallucinate exact sizing (set size to null). Be specific with color nuances and silhouettes.
The category MUST be one of the canonical categories:
"Tops", "Bottoms", "Outerwear", "Footwear", "Accessories", or "One-Piece / Dresses".

Output JSON Format:
{
  "name": "string (e.g., jeans, button-up, hoodie, crewneck, sweatpants, bomber jacket, trench coat, sneakers, loafers, midi dress, etc.)",
  "type": "string (same as name)",
  "category": "string (Tops | Bottoms | Outerwear | Footwear | Accessories | One-Piece / Dresses)",
  "color": "string (e.g., light blue wash, deep navy, maroon, olive drab, charcoal, ecru)",
  "fit": "string (e.g., oversized, boxy, relaxed, slim, skinny, straight-leg, cropped)",
  "size": null,
  "style_notes": "string (Details on specific textures, graphics, branding, distressing, hardware, collars, stitching, or distinct aesthetic vibes like vintage workwear, streetwear, grunge, minimalist, preppy)",
  "description": "string (same as style_notes)"
}"""

STYLIST_SYSTEM_PROMPT = """You are an avant-garde personal wardrobe stylist and sustainability advocate. 
Given a user's closet inventory (list of garment objects with IDs), your job is to build practical, aesthetically cohesive outfits.

Guidelines:
1. Every outfit MUST consist of at least:
   - 1 Top + 1 Bottom (or 1 full-body piece from "One-Piece / Dresses")
   - Optional: Outerwear (jackets, coats), Footwear, and Accessories if available in the closet.
2. When a specific vibe is requested, verify whether the closet inventory contains items that credibly form at least one outfit in that aesthetic (minimum 1 Top + 1 Bottom, or 1 One-Piece). If not feasible, set `"possible": false`, return `"outfits": []`, and specify what pieces are missing in `"message"`.
3. Ensure silhouette harmony (e.g., pairing slim tops with baggy pants, or balanced boxy proportions).
4. Ensure color coordination (monochromatic, complementary, earth tones, tonal matching).
5. Do not reuse the exact same combination of top + bottom across multiple outfits unless layered distinctly.
6. Provide a witty, creative title for each outfit.
7. Every outfit MUST include an explicit `vibe` tag matching one of the standardized styling options:
   "Streetwear", "Casual", "Smart Casual", "Minimalist", "Formal", "Athleisure", "Workwear", or "Layered".

Output JSON Format (when outfits are possible):
{
  "possible": true,
  "message": "Successfully curated outfits for Streetwear.",
  "outfits": [
    {
      "outfit_name": "string (creative, memorable outfit name)",
      "vibe": "string (Streetwear | Casual | Smart Casual | Minimalist | Formal | Athleisure | Workwear | Layered)",
      "item_ids": [1, 2],
      "styling_notes": "string (1-2 sentences on why this works and how to wear it)"
    }
  ]
}

Or if the closet cannot support that style:
{
  "possible": false,
  "message": "Insufficient wardrobe foundation: Unable to synthesize a Streetwear outfit. Missing relaxed tops or complementary bottoms.",
  "outfits": []
}"""

PURCHASE_EVALUATOR_SYSTEM_PROMPT = """You are an expert personal wardrobe strategist and smart shopping advisor.
A user is in-store considering buying a candidate clothing item (shown in the attached image).
You are given the candidate item image and the user's current closet catalog (JSON list of owned garments).

Your task:
1. Extract metadata for the candidate item:
   - `category`: Must be one of "Tops", "Bottoms", "Outerwear", "Footwear", "Accessories", or "One-Piece / Dresses"
   - `color`: Specific color nuance (e.g., "olive green", "washed black", "ecru")
   - `fit`: Silhouette/cut (e.g., "relaxed", "oversized", "boxy", "straight-leg", "tailored")
   - `aesthetic`: Style direction (e.g., "military utility", "minimalist tailoring", "vintage streetwear")
2. Compare the candidate item with the user's current closet catalog:
   - **Redundancy / Overlap:** Check if the candidate item is too similar in category, color, and vibe to already-owned pieces (e.g., "You already own 2 black oversized bomber jackets").
   - **Versatility:** Calculate how many viable complete outfits (`total_outfit_count`) this piece can form with the user's current closet items.
   - **Decision:** Recommend `"BUY"` if high versatility and low redundancy, `"THINK TWICE"` if moderate overlap or niche styling utility, or `"PASS"` if redundant or incompatible with the current closet.
3. Build the **Top 3 Outfits** (`top_outfits`) combining this candidate piece with specific existing items from the user's catalog (referencing their IDs formatted as `"item_<id>"`, e.g., `"item_12"`).

Return ONLY a strict JSON object matching this exact schema:
{
  "candidate_item": {
    "category": "Outerwear",
    "color": "olive green",
    "fit": "relaxed",
    "aesthetic": "military utility"
  },
  "decision": "BUY",
  "decision_summary": "Versatile layering piece that pairs with 70% of your bottoms and fills an outerwear gap.",
  "is_redundant": false,
  "redundancy_notes": null,
  "total_outfit_count": 8,
  "top_outfits": [
    {
      "title": "Casual Utility Fall",
      "vibe": "Streetwear",
      "existing_item_ids": ["item_12", "item_45"],
      "styling_tips": "Pair with your straight-leg light denim and cream crewneck."
    }
  ]
}"""


# ---------------------------------------------------------------------------
# Utility, Duplicate Detection & AI Helper Functions
# ---------------------------------------------------------------------------

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def compute_bytes_sha256(data: bytes) -> str:
    """Compute SHA-256 hex digest for raw image bytes."""
    return hashlib.sha256(data).hexdigest()


def find_duplicate_closet_image(image_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Issue 3 / Feat 3: Check whether the exact image file is already present in the closet.
    Compares SHA-256 digest and raw byte equality against:
    1. All existing closet items in SQLite (`database.get_all_clothes()`).
    2. Any existing garment image files in `UPLOAD_FOLDER` (excluding `.gitkeep` and temporary `eval_` files).
    Returns a dict with `item` and `filename` if an exact match exists, or None if unique.
    """
    if not image_bytes:
        return None

    candidate_hash = compute_bytes_sha256(image_bytes)
    upload_dir = app.config["UPLOAD_FOLDER"]
    checked_paths = set()

    # 1. Check all image files referenced by existing closet items
    for item in database.get_all_clothes():
        img_url = item.get("image_url") or ""
        fname = os.path.basename(img_url)
        if not fname:
            continue
        existing_path = os.path.join(upload_dir, fname)
        if os.path.isfile(existing_path):
            checked_paths.add(os.path.abspath(existing_path))
            try:
                with open(existing_path, "rb") as f:
                    existing_bytes = f.read()
                if compute_bytes_sha256(existing_bytes) == candidate_hash and existing_bytes == image_bytes:
                    return {"item": item, "filename": fname}
            except OSError:
                continue

    # 2. Check any other non-temporary garment files in UPLOAD_FOLDER
    if os.path.isdir(upload_dir):
        for fname in os.listdir(upload_dir):
            if fname.startswith(".") or fname.startswith("eval_") or fname.startswith("demo_"):
                continue
            if not allowed_file(fname):
                continue
            existing_path = os.path.join(upload_dir, fname)
            abs_path = os.path.abspath(existing_path)
            if abs_path in checked_paths or not os.path.isfile(existing_path):
                continue
            try:
                with open(existing_path, "rb") as f:
                    existing_bytes = f.read()
                if compute_bytes_sha256(existing_bytes) == candidate_hash and existing_bytes == image_bytes:
                    return {"item": None, "filename": fname}
            except OSError:
                continue

    return None


def get_active_api_key() -> Optional[str]:
    """Retrieve Gemini API key from request header or environment variables."""
    header_key = request.headers.get("X-Gemini-Api-Key", "").strip()
    if header_key and header_key.lower() not in ("null", "none", "undefined"):
        return header_key
    env_key = (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )
    if env_key and env_key.lower() not in ("null", "none", "undefined"):
        return env_key
    return None


def extract_json_payload(raw_text: str) -> Dict[str, Any]:
    """Robustly parse JSON from LLM output, stripping markdown code blocks if present."""
    if not raw_text:
        raise ValueError("Empty response from AI model.")

    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            return json.loads(match.group(0))
        raise


def fallback_unassigned_garment() -> Dict[str, Any]:
    """
    Graceful Offline & Missing API Key Fallback Defaults.
    When the API key is missing/invalid or the vision model request throws an error,
    return safe 'Unassigned' defaults so the upload never crashes or blocks.
    """
    return {
        "name": "Clothing Item",
        "type": "Clothing Item",
        "category": "Unassigned",
        "color": "Unassigned",
        "fit": "Unassigned",
        "size": None,
        "style_notes": "Unassigned",
        "description": "Unassigned",
    }


def normalize_garment_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure all required keys exist and adhere to the canonical category schema."""
    raw_cat = data.get("category")
    category = database.normalize_category(str(raw_cat) if raw_cat else "Unassigned", default="Unassigned")

    garment_name = str(data.get("name") or data.get("type") or "Clothing Item").strip()
    style_notes = str(
        data.get("style_notes")
        or data.get("description")
        or "Unassigned"
    ).strip()
    color = str(data.get("color") or "Unassigned").strip()
    fit = str(data.get("fit") or "Unassigned").strip()
    size_val = data.get("size")
    if size_val is not None and str(size_val).strip().lower() in ("null", "none", ""):
        size_val = None

    return {
        "name": garment_name,
        "type": garment_name,
        "category": category,
        "color": color,
        "fit": fit,
        "size": size_val,
        "style_notes": style_notes,
        "description": style_notes,
    }


def analyze_garment_with_gemini(
    filepath: str,
    original_filename: str = "",
    api_key: Optional[str] = None
) -> Tuple[Dict[str, Any], str, bool]:
    """
    Analyze an uploaded garment photo using Gemini 2.5 Flash Vision.
    Returns (metadata_dict, source_engine_str, fallback_used_bool).
    If API key is missing, empty, or invalid, or if the vision request throws an error,
    returns the Unassigned fallback defaults.
    """
    if not api_key or not GENAI_SDK or filepath.lower().endswith(".svg"):
        return fallback_unassigned_garment(), "fallback-unassigned", True

    mime_type, _ = mimetypes.guess_type(filepath)
    if not mime_type or mime_type == "image/svg+xml":
        mime_type = "image/jpeg"

    try:
        with open(filepath, "rb") as f:
            image_bytes = f.read()

        if GENAI_SDK == "google-genai":
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=GEMINI_MODEL_NAME,
                contents=[
                    genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    "Analyze this garment image and return the strict JSON metadata object."
                ],
                config=genai_types.GenerateContentConfig(
                    system_instruction=VISION_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            parsed = extract_json_payload(response.text)
            return normalize_garment_metadata(parsed), f"gemini ({GEMINI_MODEL_NAME})", False

        elif GENAI_SDK == "google-generativeai":
            legacy_genai.configure(api_key=api_key)
            model = legacy_genai.GenerativeModel(
                model_name=GEMINI_MODEL_NAME,
                system_instruction=VISION_SYSTEM_PROMPT,
                generation_config={"response_mime_type": "application/json", "temperature": 0.2},
            )
            response = model.generate_content(
                [
                    {"mime_type": mime_type, "data": image_bytes},
                    "Analyze this garment image and return the strict JSON metadata object."
                ]
            )
            parsed = extract_json_payload(response.text)
            return normalize_garment_metadata(parsed), f"gemini ({GEMINI_MODEL_NAME})", False
    except Exception as exc:
        app.logger.warning(f"Gemini Vision API call failed, using Unassigned fallback defaults: {exc}")

    return fallback_unassigned_garment(), "fallback-unassigned", True


# ---------------------------------------------------------------------------
# Outfit Generation, Vibe Insufficiency Detection & Vibe Tagging
# ---------------------------------------------------------------------------

def _garment_blob(item: Dict[str, Any]) -> str:
    """Combine garment metadata fields into a single lowercase searchable text string."""
    return " ".join(
        str(item.get(k) or "")
        for k in ("type", "name", "category", "color", "fit", "description", "style_notes")
    ).lower()


def evaluate_vibe_sufficiency(
    inventory: List[Dict[str, Any]],
    vibe: str = "All",
) -> Tuple[bool, str, List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Verify whether the current closet inventory can credibly satisfy the requested vibe.
    Requires at least (1 Top + 1 Bottom) or (1 One-Piece / Dresses), plus aesthetic
    compatibility when a specific vibe is requested.
    Returns (possible, explanation_message, compatible_tops, compatible_bottoms, compatible_one_pieces).
    """
    tops = [i for i in inventory if database.normalize_category(i.get("category")) == "Tops"]
    bottoms = [i for i in inventory if database.normalize_category(i.get("category")) == "Bottoms"]
    one_pieces = [i for i in inventory if database.normalize_category(i.get("category")) == "One-Piece / Dresses"]
    outerwear = [i for i in inventory if database.normalize_category(i.get("category")) == "Outerwear"]

    is_specific = bool(vibe and vibe.strip().lower() not in ("all", "editorial mix"))
    target_vibe = database.normalize_vibe(vibe, default="Casual") if is_specific else None
    vibe_label = target_vibe or "cohesive"

    # 1. Foundational piece check: minimum (1 Top + 1 Bottom) or (1 One-Piece)
    if (not tops or not bottoms) and not one_pieces:
        if not tops and not bottoms:
            missing_desc = "tops and bottoms (or a one-piece dress)"
        elif not tops:
            missing_desc = "tops (or a one-piece dress) to pair with your bottoms"
        else:
            missing_desc = "bottoms (or a one-piece dress) to pair with your tops"

        if target_vibe:
            msg = (
                f"Insufficient wardrobe foundation: Unable to synthesize a {target_vibe} outfit. "
                f"Missing {missing_desc}."
            )
        else:
            msg = (
                f"Insufficient wardrobe foundation: Unable to synthesize outfits. "
                f"Missing {missing_desc}."
            )
        return False, msg, [], [], []

    if not target_vibe:
        return True, "Successfully curated lookbook combinations.", tops, bottoms, one_pieces

    # 2. Vibe-specific compatibility rules
    formal_kw = [
        "formal", "tailor", "suit", "blazer", "tuxedo", "dress shirt", "poplin", "oxford",
        "button-up", "button up", "button-down", "button down", "trouser", "slack", "pleat",
        "gown", "evening", "silk", "satin", "sartorial", "structured", "loafer", "derby",
        "heel", "slip dress", "midi dress", "trench", "overcoat", "wool coat", "refined", "polished",
    ]
    strictly_casual_sport_kw = [
        "hoodie", "sweatpant", "jogger", "gym", "athletic", "cargo", "graphic tee",
        "tank top", "boardshort", "fleece", "distressed", "ripped", "track", "sweatshirt",
    ]
    athleisure_kw = [
        "athleisure", "athletic", "sport", "lounge", "gym", "hoodie", "crewneck", "sweatpant",
        "jogger", "legging", "fleece", "track", "runner", "sneaker", "windbreaker", "zip",
        "tee", "t-shirt", "tank", "short", "active", "jersey", "sweatshirt",
    ]
    strictly_formal_kw = [
        "tuxedo", "evening gown", "ball gown", "three-piece suit", "tailcoat", "stiletto",
    ]
    workwear_kw = [
        "work", "chore", "utility", "utilitarian", "canvas", "denim", "jean", "selvedge",
        "cargo", "chino", "carpenter", "twill", "flannel", "overshirt", "jacket", "coat",
        "boot", "heavyweight", "rugged", "heritage", "olive", "tobacco", "brown", "khaki",
        "tan", "charcoal", "boxy",
    ]
    streetwear_kw = [
        "street", "urban", "skate", "grunge", "vintage", "archive", "oversized", "boxy",
        "relaxed", "baggy", "wide", "straight", "hoodie", "crewneck", "tee", "t-shirt",
        "sweater", "knit", "bomber", "work jacket", "jacket", "cargo", "jean", "denim",
        "sweatpant", "jogger", "sneaker", "loafer", "boot", "cap", "beanie",
    ]
    smart_casual_kw = [
        "smart", "elevated", "preppy", "tailor", "button", "shirt", "poplin", "oxford",
        "knit", "sweater", "cardigan", "polo", "blazer", "coat", "trench", "jacket",
        "trouser", "chino", "jean", "denim", "selvedge", "dress", "skirt", "loafer",
        "derby", "boot", "clean", "minimal", "structured",
    ]

    if target_vibe == "Formal":
        compat_tops = [
            t for t in tops
            if any(k in _garment_blob(t) for k in formal_kw)
            or (
                not any(k in _garment_blob(t) for k in strictly_casual_sport_kw)
                and any(k in _garment_blob(t) for k in ["shirt", "blouse", "knit", "sweater", "turtleneck", "polo"])
            )
        ]
        compat_bottoms = [
            b for b in bottoms
            if any(k in _garment_blob(b) for k in formal_kw)
            or (
                not any(k in _garment_blob(b) for k in strictly_casual_sport_kw + ["jean", "denim", "short"])
                and any(k in _garment_blob(b) for k in ["trouser", "pant", "skirt", "chino", "tailor", "slim", "straight"])
            )
        ]
        compat_op = [
            op for op in one_pieces
            if not any(k in _garment_blob(op) for k in strictly_casual_sport_kw)
        ]
        has_formal_anchor = any(
            any(k in _garment_blob(it) for k in formal_kw)
            for it in (compat_tops + compat_bottoms + compat_op + outerwear)
        )
        if ((not compat_tops or not compat_bottoms) and not compat_op) or not has_formal_anchor:
            missing_parts = []
            if not compat_tops and not compat_op:
                missing_parts.append("formal tops (e.g., button-up shirts, silk blouses, or fine knits)")
            if not compat_bottoms and not compat_op:
                missing_parts.append("tailored bottoms (e.g., trousers or dress slacks)")
            if not missing_parts:
                missing_parts.append("formal tops and tailored bottoms")
            return (
                False,
                f"Cannot synthesize a Formal look: Your closet lacks {' and '.join(missing_parts)}.",
                [],
                [],
                [],
            )
        return True, "Successfully curated outfits for Formal.", compat_tops, compat_bottoms, compat_op

    if target_vibe == "Athleisure":
        compat_tops = [
            t for t in tops
            if any(k in _garment_blob(t) for k in athleisure_kw)
            or not any(k in _garment_blob(t) for k in strictly_formal_kw + ["dress shirt", "poplin", "oxford", "silk"])
        ]
        compat_bottoms = [
            b for b in bottoms
            if any(k in _garment_blob(b) for k in athleisure_kw)
        ]
        compat_op = [
            op for op in one_pieces
            if any(k in _garment_blob(op) for k in athleisure_kw)
        ]
        has_athleisure_anchor = any(
            any(k in _garment_blob(it) for k in athleisure_kw)
            for it in inventory
        )
        if ((not compat_tops or not compat_bottoms) and not compat_op) or not has_athleisure_anchor:
            missing_parts = []
            if not compat_tops:
                missing_parts.append("sporty or relaxed tops (hoodies, crewnecks, tees)")
            if not compat_bottoms:
                missing_parts.append("athletic or lounge bottoms (sweatpants, joggers, shorts, leggings)")
            if not missing_parts:
                missing_parts.append("sporty tops and lounge-ready bottoms")
            return (
                False,
                f"Insufficient wardrobe foundation: Unable to synthesize an Athleisure outfit. Missing {' and '.join(missing_parts)}.",
                [],
                [],
                [],
            )
        return True, "Successfully curated outfits for Athleisure.", compat_tops, compat_bottoms, compat_op

    if target_vibe == "Layered":
        layering_tops = [
            t for t in tops
            if any(k in _garment_blob(t) for k in ["knit", "sweater", "cardigan", "hoodie", "crewneck", "overshirt", "flannel", "vest", "shirt"])
        ]
        can_layer = len(outerwear) >= 1 or (len(tops) >= 2 and len(layering_tops) >= 1)
        if not can_layer:
            return (
                False,
                "Insufficient wardrobe foundation: Unable to synthesize a Layered outfit. Missing outerwear (jackets, coats) or layerable mid-layers.",
                [],
                [],
                [],
            )
        return True, "Successfully curated outfits for Layered.", tops, bottoms, one_pieces

    if target_vibe == "Workwear":
        compat_tops = [
            t for t in tops
            if not any(k in _garment_blob(t) for k in strictly_formal_kw)
        ]
        compat_bottoms = [
            b for b in bottoms
            if any(k in _garment_blob(b) for k in ["jean", "denim", "cargo", "chino", "carpenter", "twill", "work", "pant", "trouser"])
            and not any(k in _garment_blob(b) for k in strictly_formal_kw + ["legging", "gym short"])
        ]
        compat_op = [
            op for op in one_pieces
            if any(k in _garment_blob(op) for k in ["jumpsuit", "overall", "work", "denim", "canvas", "utility"])
        ]
        has_workwear_piece = any(
            any(k in _garment_blob(it) for k in workwear_kw)
            for it in (compat_tops + compat_bottoms + compat_op + outerwear)
        )
        if ((not compat_tops or not compat_bottoms) and not compat_op) or not has_workwear_piece:
            return (
                False,
                "Insufficient wardrobe foundation: Unable to synthesize a Workwear outfit. Missing utility outerwear, rugged tops, or structured workwear bottoms (denim, cargos, chinos).",
                [],
                [],
                [],
            )
        return True, "Successfully curated outfits for Workwear.", compat_tops, compat_bottoms, compat_op

    if target_vibe == "Streetwear":
        compat_tops = [
            t for t in tops
            if any(k in _garment_blob(t) for k in streetwear_kw)
            or not any(k in _garment_blob(t) for k in strictly_formal_kw)
        ]
        compat_bottoms = [
            b for b in bottoms
            if any(k in _garment_blob(b) for k in streetwear_kw)
            or not any(k in _garment_blob(b) for k in strictly_formal_kw)
        ]
        has_street_anchor = any(
            any(k in _garment_blob(it) for k in streetwear_kw)
            for it in inventory
        )
        if not compat_tops or not compat_bottoms or not has_street_anchor:
            return (
                False,
                "Insufficient wardrobe foundation: Unable to synthesize a Streetwear outfit. Missing relaxed tops or complementary streetwear bottoms.",
                [],
                [],
                [],
            )
        return True, "Successfully curated outfits for Streetwear.", compat_tops, compat_bottoms, one_pieces

    if target_vibe == "Smart Casual":
        compat_tops = [
            t for t in tops
            if any(k in _garment_blob(t) for k in smart_casual_kw)
            or not any(k in _garment_blob(t) for k in ["gym", "graphic tee", "tank top"])
        ]
        compat_bottoms = [
            b for b in bottoms
            if any(k in _garment_blob(b) for k in smart_casual_kw)
            and not any(k in _garment_blob(b) for k in ["sweatpant", "jogger", "gym", "athletic"])
        ]
        compat_op = [
            op for op in one_pieces
            if not any(k in _garment_blob(op) for k in ["gym", "athletic"])
        ]
        has_smart_anchor = any(
            any(k in _garment_blob(it) for k in smart_casual_kw)
            for it in (compat_tops + compat_bottoms + compat_op + outerwear)
        )
        if ((not compat_tops or not compat_bottoms) and not compat_op) or not has_smart_anchor:
            return (
                False,
                "Insufficient wardrobe foundation: Unable to synthesize a Smart Casual outfit. Missing elevated tops or tailored/denim bottoms.",
                [],
                [],
                [],
            )
        return True, "Successfully curated outfits for Smart Casual.", compat_tops, compat_bottoms, compat_op

    # Minimalist / Casual
    return True, f"Successfully curated outfits for {vibe_label}.", tops, bottoms, one_pieces


def generate_outfits_with_gemini(
    inventory: List[Dict[str, Any]],
    vibe: str = "All",
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send the user's wardrobe catalog JSON to Gemini 2.5 Flash to synthesize cohesive outfits.
    Returns a dict:
    {
      "possible": bool,
      "message": str,
      "outfits": List[Dict[str, Any]],
      "engine": str
    }
    """
    is_specific_vibe = bool(vibe and vibe.strip().lower() not in ("all", "editorial mix"))
    normalized_requested_vibe = database.normalize_vibe(vibe, default="Casual") if is_specific_vibe else None

    # Pre-check foundational sufficiency (1 Top + 1 Bottom, or 1 One-Piece)
    tops = [i for i in inventory if database.normalize_category(i.get("category")) == "Tops"]
    bottoms = [i for i in inventory if database.normalize_category(i.get("category")) == "Bottoms"]
    one_pieces = [i for i in inventory if database.normalize_category(i.get("category")) == "One-Piece / Dresses"]

    if (not tops or not bottoms) and not one_pieces:
        fallback_res = algorithmic_stylist_fallback(inventory, vibe)
        fallback_res["engine"] = "algorithmic-stylist"
        return fallback_res

    compact_inventory = [
        {
            "id": item["id"],
            "type": item["type"],
            "category": item["category"],
            "color": item["color"],
            "fit": item["fit"],
            "description": item.get("description", ""),
        }
        for item in inventory
    ]

    if is_specific_vibe:
        vibe_instruction = (
            f"\nRequested Styling Direction / Vibe: {normalized_requested_vibe}. "
            f"First verify whether the closet inventory contains items that credibly form at least one "
            f"'{normalized_requested_vibe}' outfit (minimum 1 Top + 1 Bottom, or 1 One-Piece). "
            f"If NOT feasible, set `\"possible\": false`, `\"outfits\": []`, and explain what pieces are missing in `\"message\"`. "
            f"If feasible, set `\"possible\": true` and tailor the outfit combinations, layering choices, and titles toward '{normalized_requested_vibe}' "
            f"with `vibe` set to '{normalized_requested_vibe}'."
        )
    else:
        vibe_instruction = (
            "\nCreate a versatile range of 4 to 6 outfits across distinct standardized vibes "
            "(such as 'Streetwear', 'Casual', 'Smart Casual', 'Minimalist', 'Formal', 'Athleisure'). "
            "Set `\"possible\": true` and ensure each outfit object has its own explicit `vibe` field."
        )

    user_prompt = (
        f"Closet Inventory JSON:\n{json.dumps(compact_inventory, indent=2)}\n"
        f"{vibe_instruction}\n"
        "Return ONLY the strict output JSON schema with `possible`, `message`, and `outfits`."
    )

    if api_key and GENAI_SDK:
        try:
            if GENAI_SDK == "google-genai":
                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=GEMINI_MODEL_NAME,
                    contents=user_prompt,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=STYLIST_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.65,
                    ),
                )
                parsed = extract_json_payload(response.text)
            else:
                legacy_genai.configure(api_key=api_key)
                model = legacy_genai.GenerativeModel(
                    model_name=GEMINI_MODEL_NAME,
                    system_instruction=STYLIST_SYSTEM_PROMPT,
                    generation_config={"response_mime_type": "application/json", "temperature": 0.65},
                )
                response = model.generate_content(user_prompt)
                parsed = extract_json_payload(response.text)

            if parsed.get("possible") is False:
                return {
                    "possible": False,
                    "message": str(
                        parsed.get("message")
                        or f"Insufficient wardrobe foundation: Unable to synthesize a {normalized_requested_vibe or 'cohesive'} outfit."
                    ).strip(),
                    "outfits": [],
                    "engine": f"gemini ({GEMINI_MODEL_NAME})",
                }

            outfits = validate_generated_outfits(
                parsed.get("outfits", []), inventory, default_vibe=normalized_requested_vibe
            )
            if outfits:
                return {
                    "possible": True,
                    "message": str(
                        parsed.get("message")
                        or f"Synthesized {len(outfits)} cohesive lookbook combinations."
                    ).strip(),
                    "outfits": outfits,
                    "engine": f"gemini ({GEMINI_MODEL_NAME})",
                }
        except Exception as exc:
            app.logger.warning(f"Gemini Outfit Stylist call failed, using algorithmic stylist fallback: {exc}")

    fallback_res = algorithmic_stylist_fallback(inventory, vibe)
    fallback_res["engine"] = "algorithmic-stylist"
    return fallback_res


def validate_generated_outfits(
    raw_outfits: List[Dict[str, Any]],
    inventory: List[Dict[str, Any]],
    default_vibe: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Filter out invalid item IDs and ensure each outfit has a valid foundational structure
    (at least 1 Top + 1 Bottom, or 1 One-Piece) and a standardized vibe.
    """
    items_by_id = {item["id"]: item for item in inventory}
    cleaned = []
    for idx, o in enumerate(raw_outfits):
        if not isinstance(o, dict):
            continue
        ids = []
        for raw_id in o.get("item_ids", []):
            try:
                if isinstance(raw_id, str) and raw_id.startswith("item_"):
                    raw_id = raw_id.replace("item_", "")
                num_id = int(raw_id)
                if num_id in items_by_id and num_id not in ids:
                    ids.append(num_id)
            except (ValueError, TypeError):
                continue

        if len(ids) < 1:
            continue

        categories_in_outfit = {
            database.normalize_category(items_by_id[i].get("category"))
            for i in ids
        }
        has_foundation = (
            ("Tops" in categories_in_outfit and "Bottoms" in categories_in_outfit)
            or ("One-Piece / Dresses" in categories_in_outfit and len(ids) >= 1)
        )
        if not has_foundation:
            continue

        outfit_name = str(o.get("outfit_name") or "Curated Archival Look").strip()
        styling_notes = str(
            o.get("styling_notes")
            or "Balanced silhouette proportions pairing complementary textures and grounded tonal hues."
        ).strip()
        raw_vibe = o.get("vibe")
        if raw_vibe and str(raw_vibe).strip().lower() not in ("all", "editorial mix"):
            vibe_tag = database.normalize_vibe(str(raw_vibe), default=default_vibe or "Casual")
        elif default_vibe:
            vibe_tag = default_vibe
        else:
            vibe_tag = database.infer_vibe_from_outfit_text(outfit_name, styling_notes, idx)

        cleaned.append({
            "outfit_name": outfit_name,
            "vibe": vibe_tag,
            "item_ids": ids,
            "styling_notes": styling_notes,
        })
    return cleaned


def algorithmic_stylist_fallback(
    inventory: List[Dict[str, Any]],
    vibe: str = "All"
) -> Dict[str, Any]:
    """
    Deterministic editorial stylist engine when Gemini API key is not configured.
    Verifies whether the requested vibe can be satisfied by the closet inventory.
    If not possible, returns `{"possible": False, "message": "...", "outfits": []}`.
    """
    possible, reason_msg, compat_tops, compat_bottoms, compat_one_pieces = evaluate_vibe_sufficiency(
        inventory, vibe=vibe
    )
    if not possible:
        return {
            "possible": False,
            "message": reason_msg,
            "outfits": [],
        }

    outerwear = [i for i in inventory if database.normalize_category(i.get("category")) == "Outerwear"]
    footwear = [i for i in inventory if database.normalize_category(i.get("category")) == "Footwear"]
    accessories = [i for i in inventory if database.normalize_category(i.get("category")) == "Accessories"]

    is_specific_vibe = bool(vibe and vibe.strip().lower() not in ("all", "editorial mix"))
    target_vibe = database.normalize_vibe(vibe, default="Casual") if is_specific_vibe else None

    vibe_profiles = [
        ("Streetwear", "Concrete Volume & Proportion"),
        ("Casual", "Coffee Run Relaxed Drape"),
        ("Smart Casual", "Gallery Opening Elevated Mix"),
        ("Minimalist", "Tonal Brutalist Uniform"),
        ("Formal", "Refined Sartorial Contrast"),
        ("Athleisure", "Off-Duty Lounge Rotation"),
        ("Workwear", "Heritage Workshop Utility"),
        ("Layered", "Transitional Weather Armor"),
    ]

    specific_titles = {
        "Streetwear": [
            "Concrete Volume & Proportion",
            "Boxy Archive Rotation",
            "Subway Utility Statement",
            "Heavyweight Street Uniform",
        ],
        "Casual": [
            "Coffee Run Relaxed Drape",
            "Weekend Flea-Market Uniform",
            "Effortless Matinee Layers",
            "Easygoing Texture Play",
        ],
        "Smart Casual": [
            "Gallery Opening Elevated Mix",
            "Creative Director Off-Duty",
            "Polished Weekend Tailoring",
            "Architectural Smart Layering",
        ],
        "Minimalist": [
            "Tonal Brutalist Uniform",
            "Nordic Gallery Proportions",
            "Clean-Line Monolith Study",
            "Quiet Luxury Silhouettes",
        ],
        "Formal": [
            "Sharp Sartorial Contrast",
            "Modernist Evening Tailoring",
            "Refined Creative Director",
            "Polished Tonal Drape",
        ],
        "Athleisure": [
            "Off-Duty Lounge Rotation",
            "Elevated Fleece & Sneaker Study",
            "Recovery Day Monochrome",
            "Sport-Utility Street Ease",
        ],
        "Workwear": [
            "Heritage Workshop Utility",
            "Canvas & Selvedge Study",
            "Chore-Coat Studio Rotation",
            "Industrial Earth-Tone Uniform",
        ],
        "Layered": [
            "Transitional Weather Armor",
            "Multi-Texture Studio Stack",
            "Autumnal Depth & Dimension",
            "Structured Outer-Shell Study",
        ],
    }

    base_combinations: List[List[Dict[str, Any]]] = []
    for t in compat_tops:
        for b in compat_bottoms:
            base_combinations.append([t, b])
    for op in compat_one_pieces:
        base_combinations.append([op])

    if not base_combinations:
        return {
            "possible": False,
            "message": (
                f"Insufficient wardrobe foundation: Unable to synthesize a {target_vibe or 'cohesive'} outfit "
                "with your current closet inventory."
            ),
            "outfits": [],
        }

    outfits = []
    max_outfits = min(len(base_combinations), 6)
    for idx in range(max_outfits):
        core_items = base_combinations[idx]
        chosen_ids = [item["id"] for item in core_items]
        layers_desc = []

        if target_vibe:
            outfit_vibe = target_vibe
            titles_list = specific_titles.get(outfit_vibe, specific_titles["Casual"])
            title = titles_list[idx % len(titles_list)]
        else:
            # Select only vibes that are actually feasible for this closet when cycling "All"
            candidate_vibe, candidate_title = vibe_profiles[idx % len(vibe_profiles)]
            v_ok, _, _, _, _ = evaluate_vibe_sufficiency(inventory, vibe=candidate_vibe)
            if v_ok:
                outfit_vibe, title = candidate_vibe, candidate_title
            else:
                outfit_vibe = "Casual"
                title = specific_titles["Casual"][idx % len(specific_titles["Casual"])]

        if outerwear and (idx % 2 == 0 or outfit_vibe in ("Layered", "Workwear", "Streetwear", "Smart Casual")):
            outer = outerwear[idx % len(outerwear)]
            if outer["id"] not in chosen_ids:
                chosen_ids.insert(0, outer["id"])
                layers_desc.append(f"anchored by the {outer['color']} {outer['type']}")
        elif outfit_vibe == "Layered" and len(compat_tops) >= 2:
            extra_top = compat_tops[(idx + 1) % len(compat_tops)]
            if extra_top["id"] not in chosen_ids:
                chosen_ids.insert(0, extra_top["id"])
                layers_desc.append(f"layered under the {extra_top['color']} {extra_top['type']}")

        if footwear:
            shoe = footwear[idx % len(footwear)]
            if shoe["id"] not in chosen_ids:
                chosen_ids.append(shoe["id"])
                layers_desc.append(f"grounded with {shoe['color']} {shoe['type']}")

        if accessories and idx % 2 == 1:
            acc = accessories[idx % len(accessories)]
            if acc["id"] not in chosen_ids:
                chosen_ids.append(acc["id"])
                layers_desc.append(f"finished with the {acc['type']}")

        layer_clause = (", " + " and ".join(layers_desc)) if layers_desc else ""
        if len(core_items) == 2:
            top, bottom = core_items[0], core_items[1]
            notes = (
                f"Balances the {top['fit']} silhouette of the {top['color']} {top['type']} "
                f"against the {bottom['fit']} cut of the {bottom['color']} {bottom['type']}{layer_clause}. "
                f"Curated for a cohesive {outfit_vibe.lower()} aesthetic."
            )
        else:
            op = core_items[0]
            notes = (
                f"Centers on the {op['fit']} silhouette of the {op['color']} {op['type']}{layer_clause} "
                f"for an effortless {outfit_vibe.lower()} statement."
            )

        outfits.append({
            "outfit_name": title,
            "vibe": outfit_vibe,
            "item_ids": chosen_ids,
            "styling_notes": notes,
        })

    success_msg = (
        f"Successfully curated {len(outfits)} outfit(s) for {target_vibe}."
        if target_vibe
        else f"Synthesized {len(outfits)} cohesive lookbook combinations."
    )
    return {
        "possible": True,
        "message": success_msg,
        "outfits": outfits,
    }


# ---------------------------------------------------------------------------
# Feature 1: "Buy Less" In-Store Purchase Evaluator
# ---------------------------------------------------------------------------

def extract_color_family(color_str: str) -> str:
    """Group nuanced color descriptions into core color families for redundancy comparison."""
    c = (color_str or "").lower()
    if any(w in c for w in ["black", "onyx", "espresso", "ebony", "jet"]):
        return "black"
    if any(w in c for w in ["charcoal", "graphite", "slate", "grey", "gray", "heather", "ash", "silver"]):
        return "grey/charcoal"
    if any(w in c for w in ["white", "ecru", "cream", "ivory", "bone", "chalk", "off-white"]):
        return "white/ecru"
    if any(w in c for w in ["indigo", "navy", "blue", "cobalt", "denim", "sky", "ice blue"]):
        return "blue/indigo"
    if any(w in c for w in ["olive", "green", "sage", "forest", "khaki", "moss", "drab"]):
        return "olive/green"
    if any(w in c for w in ["brown", "tobacco", "tan", "camel", "cognac", "taupe", "beige", "chocolate"]):
        return "brown/tan"
    if any(w in c for w in ["red", "maroon", "burgundy", "wine", "oxblood", "crimson", "pink"]):
        return "red/burgundy"
    return c.strip() or "neutral"


def detect_dominant_color_name(filepath: str) -> str:
    """Inspect image pixels via Pillow to estimate a human-readable color name when offline."""
    if not PIL_AVAILABLE or filepath.lower().endswith(".svg"):
        return "charcoal neutral"
    try:
        with Image.open(filepath) as img:
            img = img.convert("RGB").resize((48, 48))
            pixels = list(img.getdata())
            center_pixels = []
            for y in range(12, 36):
                for x in range(12, 36):
                    center_pixels.append(pixels[y * 48 + x])
            sample = center_pixels if center_pixels else pixels
            r_avg = sum(p[0] for p in sample) / len(sample)
            g_avg = sum(p[1] for p in sample) / len(sample)
            b_avg = sum(p[2] for p in sample) / len(sample)

            brightness = (r_avg + g_avg + b_avg) / 3.0
            if brightness < 45:
                return "solid black"
            if brightness > 220 and max(abs(r_avg - g_avg), abs(g_avg - b_avg)) < 18:
                return "crisp white"
            if brightness > 190 and r_avg >= g_avg >= b_avg:
                return "ecru cream"
            if max(abs(r_avg - g_avg), abs(g_avg - b_avg), abs(r_avg - b_avg)) < 18:
                return "charcoal grey" if brightness < 115 else "heather grey"
            if b_avg > r_avg + 20 and b_avg > g_avg + 10:
                return "deep navy blue" if brightness < 95 else "medium blue wash"
            if g_avg >= r_avg and g_avg > b_avg + 12:
                return "olive green"
            if r_avg > g_avg + 25 and r_avg > b_avg + 25:
                return "burgundy maroon" if brightness < 110 else "terracotta red"
            if r_avg > b_avg + 20 and g_avg > b_avg + 10:
                return "tobacco brown" if brightness < 130 else "warm taupe"
    except Exception:
        pass
    return "olive green"


def infer_candidate_from_hint(filepath: str, original_filename: str = "") -> Dict[str, str]:
    """Infer candidate garment metadata for offline evaluation fallback."""
    hint = (original_filename or os.path.basename(filepath)).lower()
    stem = os.path.splitext(hint)[0].replace("_", " ").replace("-", " ")

    detected_color = detect_dominant_color_name(filepath)
    for c_word in [
        "olive green", "olive", "black", "white", "ecru", "cream", "navy",
        "indigo", "blue", "green", "brown", "tobacco", "taupe", "tan",
        "khaki", "grey", "gray", "charcoal", "maroon", "burgundy",
    ]:
        if c_word in stem:
            detected_color = c_word
            break

    category = "Outerwear"
    fit = "relaxed"
    aesthetic = "contemporary street utility"

    if any(k in stem for k in ["dress", "jumpsuit", "romper", "gown", "one piece", "one-piece"]):
        category = "One-Piece / Dresses"
        fit = "tailored"
        aesthetic = "minimalist editorial"
    elif any(k in stem for k in ["jean", "denim", "pant", "trouser", "cargo", "chino", "short", "sweatpant", "skirt"]):
        category = "Bottoms"
        fit = "straight-leg" if "jean" in stem else "relaxed"
        aesthetic = "casual workwear" if "cargo" in stem else "modern casual"
    elif any(k in stem for k in ["shoe", "sneaker", "loafer", "boot", "derby", "mule", "runner", "heel"]):
        category = "Footwear"
        fit = "structured"
        aesthetic = "smart casual footwear"
    elif any(k in stem for k in ["belt", "bag", "tote", "cap", "hat", "beanie", "scarf", "watch"]):
        category = "Accessories"
        fit = "one-size"
        aesthetic = "minimalist accessory"
    elif any(k in stem for k in ["hoodie", "crewneck", "sweater", "shirt", "button", "polo", "tank", "tee", "t-shirt", "top", "knit"]):
        category = "Tops"
        fit = "oversized" if "hoodie" in stem else "relaxed"
        aesthetic = "streetwear essentials" if "hoodie" in stem else "clean everyday layering"
    elif any(k in stem for k in ["jacket", "coat", "trench", "bomber", "blazer", "parka", "cardigan", "overshirt"]):
        category = "Outerwear"
        fit = "boxy" if "work" in stem else "relaxed"
        aesthetic = "military utility" if "olive" in detected_color else "architectural outerwear"

    return {
        "category": category,
        "color": detected_color,
        "fit": fit,
        "aesthetic": aesthetic,
    }


def normalize_purchase_evaluation(
    raw_data: Dict[str, Any],
    inventory: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Validate and hydrate the purchase evaluation response so it strictly adheres
    to the required JSON format while also hydrating `existing_items` for UI cards.
    """
    clothes_by_id: Dict[int, Dict[str, Any]] = {}
    for item in inventory:
        try:
            clothes_by_id[int(item["id"])] = item
        except (KeyError, ValueError, TypeError):
            continue

    cand_raw = raw_data.get("candidate_item") or {}
    cand_category = database.normalize_category(cand_raw.get("category"), default="Outerwear")
    candidate_item = {
        "category": cand_category,
        "color": str(cand_raw.get("color") or "olive green").strip(),
        "fit": str(cand_raw.get("fit") or "relaxed").strip(),
        "aesthetic": str(cand_raw.get("aesthetic") or "contemporary utility").strip(),
    }

    raw_decision = str(raw_data.get("decision") or "BUY").strip().upper()
    if raw_decision in ("DON'T BUY", "DONT BUY", "NO", "SKIP", "PASS"):
        decision = "PASS"
    elif raw_decision in ("THINK TWICE", "MAYBE", "CONSIDER", "CAUTION"):
        decision = "THINK TWICE"
    else:
        decision = "BUY"

    is_redundant = bool(raw_data.get("is_redundant", False))
    redundancy_notes = raw_data.get("redundancy_notes")
    if redundancy_notes is not None:
        redundancy_notes = str(redundancy_notes).strip() or None

    decision_summary = str(
        raw_data.get("decision_summary")
        or "Evaluated against your current closet for silhouette versatility and color coordination."
    ).strip()

    try:
        total_outfit_count = max(0, int(raw_data.get("total_outfit_count", 0)))
    except (ValueError, TypeError):
        total_outfit_count = 0

    raw_top_outfits = raw_data.get("top_outfits") or []
    normalized_top_outfits = []

    for idx, o in enumerate(raw_top_outfits[:3]):
        if not isinstance(o, dict):
            continue
        raw_ids = o.get("existing_item_ids") or o.get("item_ids") or []
        formatted_str_ids: List[str] = []
        hydrated_items: List[Dict[str, Any]] = []

        for rid in raw_ids:
            num_id = None
            if isinstance(rid, int):
                num_id = rid
            elif isinstance(rid, str):
                cleaned_id = rid.strip().lower().replace("item_", "").replace("#", "")
                if cleaned_id.isdigit():
                    num_id = int(cleaned_id)
            if num_id is not None and num_id in clothes_by_id:
                str_id = f"item_{num_id}"
                if str_id not in formatted_str_ids:
                    formatted_str_ids.append(str_id)
                    hydrated_items.append(clothes_by_id[num_id])
            elif isinstance(rid, str) and rid.strip():
                if rid.strip() not in formatted_str_ids:
                    formatted_str_ids.append(rid.strip())

        outfit_vibe = database.normalize_vibe(o.get("vibe"), default="Casual")
        normalized_top_outfits.append({
            "title": str(o.get("title") or o.get("outfit_name") or f"Curated Look #{idx + 1}").strip(),
            "vibe": outfit_vibe,
            "existing_item_ids": formatted_str_ids,
            "existing_items": hydrated_items,
            "styling_tips": str(
                o.get("styling_tips")
                or o.get("styling_notes")
                or "Pair with complementary tonal pieces from your current rotation."
            ).strip(),
        })

    return {
        "candidate_item": candidate_item,
        "decision": decision,
        "decision_summary": decision_summary,
        "is_redundant": is_redundant,
        "redundancy_notes": redundancy_notes,
        "total_outfit_count": total_outfit_count,
        "top_outfits": normalized_top_outfits,
    }


def fallback_evaluate_purchase(
    filepath: str,
    original_filename: str,
    inventory: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Deterministic in-store Buy Less purchase evaluator when Gemini API key is not configured or fails.
    Compares the candidate item against the user's real closet inventory to detect duplicates,
    count viable outfit combinations, and build the top 3 visual outfits.
    """
    cand = infer_candidate_from_hint(filepath, original_filename)
    cand_cat = database.normalize_category(cand["category"], default="Outerwear")
    cand_color_fam = extract_color_family(cand["color"])

    # Partition existing closet
    same_cat_items = [
        i for i in inventory
        if database.normalize_category(i.get("category")) == cand_cat
    ]
    similar_items = [
        i for i in same_cat_items
        if extract_color_family(i.get("color", "")) == cand_color_fam
        and i.get("color", "").lower() != "unassigned"
    ]

    tops = [i for i in inventory if database.normalize_category(i.get("category")) == "Tops"]
    bottoms = [i for i in inventory if database.normalize_category(i.get("category")) == "Bottoms"]
    outerwear = [i for i in inventory if database.normalize_category(i.get("category")) == "Outerwear"]
    footwear = [i for i in inventory if database.normalize_category(i.get("category")) == "Footwear"]
    one_pieces = [i for i in inventory if database.normalize_category(i.get("category")) == "One-Piece / Dresses"]

    # Calculate outfit potential metric with existing closet pieces
    if cand_cat == "Tops":
        total_outfit_count = len(bottoms) * max(1, len(outerwear) + 1)
    elif cand_cat == "Bottoms":
        total_outfit_count = len(tops) * max(1, len(outerwear) + 1)
    elif cand_cat == "Outerwear":
        total_outfit_count = max(len(tops) * len(bottoms), (len(tops) * len(bottoms)) + len(one_pieces))
    elif cand_cat == "One-Piece / Dresses":
        total_outfit_count = max(1 if (outerwear or footwear) else 0, len(outerwear) + len(footwear))
    elif cand_cat == "Footwear":
        total_outfit_count = (len(tops) * len(bottoms)) + len(one_pieces)
    else:  # Accessories
        total_outfit_count = (len(tops) * len(bottoms)) + len(one_pieces)

    # Determine redundancy and BUY / THINK TWICE / PASS recommendation
    if len(similar_items) >= 2:
        is_redundant = True
        decision = "PASS"
        matched_names = ", ".join(f"#{i.get('id')} {i.get('color')} {i.get('type')}" for i in similar_items[:3])
        redundancy_notes = (
            f"You already own {len(similar_items)} {cand_color_fam} {cand_cat.lower()} pieces "
            f"({matched_names}). Adding another creates high wardrobe overlap."
        )
        decision_summary = (
            f"Smart textile preservation: your archive already contains {len(similar_items)} comparable "
            f"{cand['color']} {cand_cat.lower()} pieces. Skip this purchase and rotate your existing pieces."
        )
    elif len(similar_items) == 1:
        is_redundant = True
        existing_match = similar_items[0]
        decision = "THINK TWICE"
        redundancy_notes = (
            f"Closely overlaps with your existing #{existing_match.get('id')} {existing_match.get('color')} "
            f"{existing_match.get('type')} ({existing_match.get('fit')} fit)."
        )
        decision_summary = (
            f"While this {cand['color']} {cand_cat.lower()} piece unlocks {total_outfit_count} combinations, "
            f"you already own #{existing_match.get('id')} {existing_match.get('color')} {existing_match.get('type')}. "
            f"Only acquire if the {cand['fit']} cut serves a distinct styling role."
        )
    elif total_outfit_count == 0:
        is_redundant = False
        redundancy_notes = None
        decision = "THINK TWICE"
        decision_summary = (
            f"Zero overlap detected, but your archive currently lacks complementary foundational garments "
            f"to build complete looks around this {cand['color']} {cand_cat.lower()} piece."
        )
    else:
        is_redundant = False
        redundancy_notes = None
        decision = "BUY"
        gap_note = (
            f"fills a {cand_cat.lower()} category gap"
            if len(same_cat_items) <= 1
            else f"introduces a distinct {cand['color']} nuance to your {cand_cat.lower()} rotation"
        )
        decision_summary = (
            f"High-utility addition that {gap_note} and unlocks {total_outfit_count} cohesive outfit "
            f"pairings across your existing wardrobe archive."
        )

    # Build Top 3 Outfits combining the candidate item with existing closet pieces
    top_outfits = []
    vibe_sequence = ["Streetwear", "Smart Casual", "Minimalist"]
    title_sequence = [
        f"Urban {cand['color'].title()} Rotation",
        f"Elevated {cand['fit'].title()} Layering",
        f"Tonal Studio Archive Look",
    ]

    if total_outfit_count > 0:
        for idx in range(3):
            chosen: List[Dict[str, Any]] = []
            if cand_cat == "Tops":
                if bottoms:
                    chosen.append(bottoms[idx % len(bottoms)])
                if outerwear and idx < len(outerwear):
                    chosen.append(outerwear[idx % len(outerwear)])
                if footwear:
                    chosen.append(footwear[idx % len(footwear)])
            elif cand_cat == "Bottoms":
                if tops:
                    chosen.append(tops[idx % len(tops)])
                if outerwear and idx < len(outerwear):
                    chosen.append(outerwear[idx % len(outerwear)])
                if footwear:
                    chosen.append(footwear[idx % len(footwear)])
            elif cand_cat == "Outerwear":
                if tops and bottoms:
                    chosen.append(tops[idx % len(tops)])
                    chosen.append(bottoms[idx % len(bottoms)])
                elif one_pieces:
                    chosen.append(one_pieces[idx % len(one_pieces)])
                if footwear and idx % 2 == 0:
                    chosen.append(footwear[idx % len(footwear)])
            else:
                if tops and bottoms:
                    chosen.append(tops[idx % len(tops)])
                    chosen.append(bottoms[idx % len(bottoms)])
                elif one_pieces:
                    chosen.append(one_pieces[idx % len(one_pieces)])

            # Deduplicate chosen items
            unique_chosen = []
            seen_ids = set()
            for c_item in chosen:
                if c_item["id"] not in seen_ids:
                    seen_ids.add(c_item["id"])
                    unique_chosen.append(c_item)

            if unique_chosen:
                names_str = " and ".join(f"{it.get('color')} {it.get('type')}" for it in unique_chosen)
                top_outfits.append({
                    "title": title_sequence[idx % len(title_sequence)],
                    "vibe": vibe_sequence[idx % len(vibe_sequence)],
                    "existing_item_ids": [f"item_{it['id']}" for it in unique_chosen],
                    "existing_items": unique_chosen,
                    "styling_tips": (
                        f"Style this {cand['fit']} {cand['color']} candidate piece with your "
                        f"{names_str} for balanced archival proportions."
                    ),
                })
                if len(inventory) < 3 and idx >= 0:
                    break

    return {
        "candidate_item": {
            "category": cand_cat,
            "color": cand["color"],
            "fit": cand["fit"],
            "aesthetic": cand["aesthetic"],
        },
        "decision": decision,
        "decision_summary": decision_summary,
        "is_redundant": is_redundant,
        "redundancy_notes": redundancy_notes,
        "total_outfit_count": total_outfit_count,
        "top_outfits": top_outfits,
    }


def evaluate_purchase_with_gemini(
    filepath: str,
    original_filename: str,
    inventory: List[Dict[str, Any]],
    api_key: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Run Gemini Multimodal evaluation comparing the candidate item photo against the user's closet catalog.
    Falls back to `fallback_evaluate_purchase` if offline or no API key is configured.
    """
    mime_type, _ = mimetypes.guess_type(filepath)
    if not mime_type or mime_type == "image/svg+xml":
        mime_type = "image/jpeg"

    compact_catalog = [
        {
            "id": f"item_{item['id']}",
            "numeric_id": item["id"],
            "name": item.get("type", "Clothing Item"),
            "category": item.get("category", "Unassigned"),
            "color": item.get("color", "Unassigned"),
            "fit": item.get("fit", "Unassigned"),
            "description": item.get("description", ""),
        }
        for item in inventory
    ]

    prompt_text = (
        f"User's Current Closet Catalog ({len(compact_catalog)} items):\n"
        f"{json.dumps(compact_catalog, indent=2)}\n\n"
        "Analyze the attached candidate item image against this closet catalog and return the strict JSON evaluation."
    )

    if api_key and GENAI_SDK and not filepath.lower().endswith(".svg"):
        try:
            with open(filepath, "rb") as f:
                image_bytes = f.read()

            if GENAI_SDK == "google-genai":
                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=GEMINI_MODEL_NAME,
                    contents=[
                        genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        prompt_text,
                    ],
                    config=genai_types.GenerateContentConfig(
                        system_instruction=PURCHASE_EVALUATOR_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.3,
                    ),
                )
                parsed = extract_json_payload(response.text)
                return normalize_purchase_evaluation(parsed, inventory), f"gemini ({GEMINI_MODEL_NAME})"

            elif GENAI_SDK == "google-generativeai":
                legacy_genai.configure(api_key=api_key)
                model = legacy_genai.GenerativeModel(
                    model_name=GEMINI_MODEL_NAME,
                    system_instruction=PURCHASE_EVALUATOR_SYSTEM_PROMPT,
                    generation_config={"response_mime_type": "application/json", "temperature": 0.3},
                )
                response = model.generate_content(
                    [
                        {"mime_type": mime_type, "data": image_bytes},
                        prompt_text,
                    ]
                )
                parsed = extract_json_payload(response.text)
                return normalize_purchase_evaluation(parsed, inventory), f"gemini ({GEMINI_MODEL_NAME})"
        except Exception as exc:
            app.logger.warning(f"Gemini Buy Less Evaluator failed, using deterministic closet evaluator: {exc}")

    fallback_result = fallback_evaluate_purchase(filepath, original_filename, inventory)
    return normalize_purchase_evaluation(fallback_result, inventory), "closet-evaluator-fallback"


# ---------------------------------------------------------------------------
# Page & Static Asset Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Render Page 1: 'My Wardrobe' Digital Closet Storefront."""
    return render_template("index.html")


@app.route("/outfits")
def outfits_page():
    """Render Page 2: 'Outfit Lookbook' AI Recommendations."""
    return render_template("outfits.html")


@app.route("/static/placeholder-garment.svg")
def serve_placeholder_garment_svg():
    """Serve clean editorial SVG placeholder for missing or failed garment thumbnails."""
    return Response(PLACEHOLDER_GARMENT_SVG, mimetype="image/svg+xml")


@app.route("/static/css/<path:filename>")
def serve_css(filename: str):
    """Serve CSS from static/css/ or fallback to BASE_DIR."""
    static_css_dir = os.path.join(BASE_DIR, "static", "css")
    if os.path.exists(os.path.join(static_css_dir, filename)):
        return send_from_directory(static_css_dir, filename)
    return send_from_directory(BASE_DIR, filename)


@app.route("/static/js/<path:filename>")
def serve_js(filename: str):
    """Serve JS from static/js/ or fallback to BASE_DIR."""
    static_js_dir = os.path.join(BASE_DIR, "static", "js")
    if os.path.exists(os.path.join(static_js_dir, filename)):
        return send_from_directory(static_js_dir, filename)
    return send_from_directory(BASE_DIR, filename)


@app.route("/uploads/<path:filename>")
def uploaded_file(filename: str):
    """Serve uploaded garment images."""
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ---------------------------------------------------------------------------
# REST API Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/status", methods=["GET"])
def api_status():
    """Return engine configuration status and wardrobe summary."""
    api_key = get_active_api_key()
    all_items = database.get_all_clothes()
    return jsonify({
        "gemini_configured": bool(api_key),
        "sdk_installed": GENAI_SDK,
        "model": GEMINI_MODEL_NAME,
        "total_items": len(all_items),
        "canonical_categories": database.CANONICAL_CATEGORIES,
        "standard_vibes": database.STANDARD_VIBES,
    })


@app.route("/api/clothes", methods=["GET"])
def api_get_clothes():
    """
    GET /api/clothes
    Fetch all saved clothing items with optional category filter, search query, and sort order.
    """
    category = request.args.get("category", "all")
    search_query = request.args.get("q", "")
    sort_by = request.args.get("sort", "newest")

    items = database.get_all_clothes(category=category, search_query=search_query, sort_by=sort_by)
    all_unfiltered = database.get_all_clothes()

    # Initialize counts for both canonical names and lowercase/slug keys
    category_counts: Dict[str, int] = {
        "all": len(all_unfiltered),
        "All": len(all_unfiltered),
    }
    for canon in database.ALL_VALID_CATEGORIES:
        slug = re.sub(r"[^a-z0-9]+", "-", canon.lower()).strip("-")
        category_counts[canon] = 0
        category_counts[canon.lower()] = 0
        category_counts[slug] = 0

    never_worn_count = 0
    worn_today_count = 0

    for item in all_unfiltered:
        canon_cat = database.normalize_category(item.get("category"), default="Unassigned")
        slug_cat = re.sub(r"[^a-z0-9]+", "-", canon_cat.lower()).strip("-")
        category_counts[canon_cat] = category_counts.get(canon_cat, 0) + 1
        category_counts[canon_cat.lower()] = category_counts[canon_cat]
        category_counts[slug_cat] = category_counts[canon_cat]

        state = item.get("last_worn_status", {}).get("state")
        if state == "never":
            never_worn_count += 1
        elif state == "today":
            worn_today_count += 1

    return jsonify({
        "items": items,
        "stats": {
            "total": len(all_unfiltered),
            "filtered": len(items),
            "never_worn": never_worn_count,
            "worn_today": worn_today_count,
            "categories": category_counts,
        }
    })


@app.route("/api/upload", methods=["POST"])
def api_upload_cloth():
    """
    POST /api/upload
    Handle image upload (file or base64 camera capture), check against existing closet image files
    to reject exact duplicate uploads, call Gemini 2.5 Flash Vision API, save file to disk,
    write metadata to SQLite, and return the newly created item JSON.
    """
    api_key = get_active_api_key()
    saved_filename = None
    original_filename = ""
    image_bytes: Optional[bytes] = None
    ext = "jpg"

    # 1. Check standard multipart file upload
    if "image" in request.files and request.files["image"].filename:
        file = request.files["image"]
        original_filename = file.filename
        if not allowed_file(original_filename):
            return jsonify({"error": "Unsupported image file format. Use JPG, PNG, WEBP, GIF, or SVG."}), 400

        ext = original_filename.rsplit(".", 1)[1].lower()
        image_bytes = file.read()
        if not image_bytes:
            return jsonify({"error": "Uploaded image file is empty."}), 400
        unique_name = f"garment_{uuid.uuid4().hex[:12]}.{ext}"
        saved_filename = secure_filename(unique_name)

    # 2. Check base64 camera capture payload
    elif request.is_json and request.json.get("image_base64"):
        raw_b64 = request.json["image_base64"]
        original_filename = request.json.get("filename", "camera_capture.jpg")
        try:
            if "," in raw_b64:
                _, b64_data = raw_b64.split(",", 1)
            else:
                b64_data = raw_b64
            image_bytes = base64.b64decode(b64_data)
            if not image_bytes:
                return jsonify({"error": "Decoded camera image is empty."}), 400
            saved_filename = f"camera_{uuid.uuid4().hex[:12]}.jpg"
        except Exception as exc:
            return jsonify({"error": f"Failed to decode camera image: {exc}"}), 400
    else:
        return jsonify({"error": "No garment image provided in request."}), 400

    # 3. Issue 3 / Feat 3: Check existing closet image files and reject exact duplicates
    duplicate_match = find_duplicate_closet_image(image_bytes)
    if duplicate_match is not None:
        matched_item = duplicate_match.get("item")
        existing_desc = (
            f" (#{matched_item['id']} {matched_item.get('type', 'Clothing Item')})"
            if matched_item else ""
        )
        return jsonify({
            "error": f"Duplicate image rejected: this exact image file is already in your closet{existing_desc}.",
            "duplicate": True,
            "existing_item": matched_item,
        }), 409

    # 4. Save validated unique image to UPLOAD_FOLDER
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], saved_filename)
    with open(filepath, "wb") as f:
        f.write(image_bytes)

    # 5. Call Gemini Vision API (with Unassigned fallback when key is missing or call fails)
    metadata, engine_used, fallback_used = analyze_garment_with_gemini(
        filepath=filepath,
        original_filename=original_filename,
        api_key=api_key,
    )

    form_data = request.form if request.form else (request.json if request.is_json else {})
    if form_data.get("name") and not form_data.get("type"):
        metadata["type"] = form_data.get("name")
        metadata["name"] = form_data.get("name")
    if form_data.get("style_notes") and not form_data.get("description"):
        metadata["description"] = form_data.get("style_notes")
        metadata["style_notes"] = form_data.get("style_notes")
    for field in ("type", "category", "color", "fit", "size", "description"):
        if form_data.get(field):
            metadata[field] = form_data.get(field)

    image_url = f"/uploads/{saved_filename}"
    new_item = database.insert_cloth(
        image_url=image_url,
        garment_type=metadata.get("type", "Clothing Item"),
        category=metadata.get("category", "Unassigned"),
        color=metadata.get("color", "Unassigned"),
        fit=metadata.get("fit", "Unassigned"),
        size=metadata.get("size"),
        description=metadata.get("description", "Unassigned"),
    )

    fallback_notice = "AI tagging unavailable. Defaults set to Unassigned — please edit manually."
    return jsonify({
        "item": new_item,
        "engine": engine_used,
        "fallback_used": fallback_used,
        "notice": fallback_notice if fallback_used else None,
        "message": fallback_notice if fallback_used else "Garment cataloged and analyzed successfully."
    }), 201


@app.route("/api/evaluate-purchase", methods=["POST"])
def api_evaluate_purchase():
    """
    POST /api/evaluate-purchase
    Feature 1: "Buy Less" In-Store Evaluator.
    Accepts a candidate item image (multipart `image`/`file` or JSON `image_base64`)
    plus optional closet catalog JSON (defaults to current SQLite wardrobe).
    Returns structured evaluation JSON with decision, reasoning, redundancy alert,
    outfit potential metric, and top 3 outfits.
    """
    api_key = get_active_api_key()
    saved_filename = None
    original_filename = ""

    # 1. Extract candidate image from multipart upload or base64 JSON
    upload_file = None
    for file_key in ("image", "file", "candidate_image"):
        if file_key in request.files and request.files[file_key].filename:
            upload_file = request.files[file_key]
            break

    if upload_file is not None:
        original_filename = upload_file.filename
        if not allowed_file(original_filename):
            return jsonify({"error": "Unsupported image file format. Use JPG, PNG, WEBP, GIF, or SVG."}), 400
        ext = original_filename.rsplit(".", 1)[1].lower()
        unique_name = f"eval_{uuid.uuid4().hex[:12]}.{ext}"
        saved_filename = secure_filename(unique_name)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], saved_filename)
        upload_file.save(filepath)
    elif request.is_json and request.json.get("image_base64"):
        raw_b64 = request.json["image_base64"]
        original_filename = request.json.get("filename", "candidate_capture.jpg")
        try:
            if "," in raw_b64:
                _, b64_data = raw_b64.split(",", 1)
            else:
                b64_data = raw_b64
            image_bytes = base64.b64decode(b64_data)
            saved_filename = f"eval_{uuid.uuid4().hex[:12]}.jpg"
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], saved_filename)
            with open(filepath, "wb") as f:
                f.write(image_bytes)
        except Exception as exc:
            return jsonify({"error": f"Failed to decode candidate image: {exc}"}), 400
    else:
        return jsonify({"error": "No candidate garment image provided for evaluation."}), 400

    # 2. Load current closet catalog (from request payload if explicitly supplied, otherwise SQLite DB)
    inventory: Optional[List[Dict[str, Any]]] = None
    raw_catalog = None
    if request.form:
        raw_catalog = request.form.get("catalog") or request.form.get("closet")
    elif request.is_json and request.json:
        raw_catalog = request.json.get("catalog") or request.json.get("closet")

    if raw_catalog:
        try:
            parsed_cat = json.loads(raw_catalog) if isinstance(raw_catalog, str) else raw_catalog
            if isinstance(parsed_cat, list) and len(parsed_cat) > 0:
                inventory = parsed_cat
        except Exception:
            inventory = None

    if not inventory:
        inventory = database.get_all_clothes()

    # 3. Evaluate purchase via Gemini Multimodal (or deterministic closet fallback)
    evaluation, engine_used = evaluate_purchase_with_gemini(
        filepath=filepath,
        original_filename=original_filename,
        inventory=inventory,
        api_key=api_key,
    )

    evaluation["candidate_image_url"] = f"/uploads/{saved_filename}"
    evaluation["engine"] = engine_used
    return jsonify(evaluation), 200


@app.route("/api/clothes/<int:item_id>", methods=["PUT"])
def api_update_cloth(item_id: int):
    """
    PUT /api/clothes/<id>
    Allow manual user editing of type/name, category, color, fit, size, description/style_notes, or last_worn.
    Validates category against canonical categories.
    """
    existing = database.get_cloth_by_id(item_id)
    if not existing:
        return jsonify({"error": f"Clothing item #{item_id} not found."}), 404

    payload = request.get_json(silent=True) or {}
    if "category" in payload and payload["category"] is not None:
        raw_cat = str(payload["category"]).strip()
        norm_cat = database.normalize_category(raw_cat, default="__INVALID__")
        if norm_cat == "__INVALID__":
            return jsonify({
                "error": f"Invalid category '{raw_cat}'. Must be one of: {', '.join(database.CANONICAL_CATEGORIES)}"
            }), 400
        payload["category"] = norm_cat

    updated = database.update_cloth(item_id, payload)
    return jsonify({
        "item": updated,
        "message": "Garment metadata updated."
    })


@app.route("/api/clothes/<int:item_id>/wear", methods=["POST"])
def api_wear_cloth(item_id: int):
    """
    POST /api/clothes/<id>/wear
    Set last_worn timestamp to today's date (YYYY-MM-DD).
    """
    existing = database.get_cloth_by_id(item_id)
    if not existing:
        return jsonify({"error": f"Clothing item #{item_id} not found."}), 404

    updated = database.mark_cloth_worn_today(item_id)
    return jsonify({
        "item": updated,
        "message": f"Marked '{updated['type']}' as worn today."
    })


@app.route("/api/clothes/<int:item_id>", methods=["DELETE"])
def api_delete_cloth(item_id: int):
    """
    DELETE /api/clothes/<id>
    Remove a clothing item from the wardrobe database and clean up its image file on disk.
    """
    existing = database.get_cloth_by_id(item_id)
    if not existing:
        return jsonify({"error": f"Clothing item #{item_id} not found."}), 404

    image_url = existing.get("image_url") or ""
    database.delete_cloth(item_id)

    # Remove image file from UPLOAD_FOLDER if no other item references it
    fname = os.path.basename(image_url)
    if fname:
        still_referenced = any(
            os.path.basename(it.get("image_url") or "") == fname
            for it in database.get_all_clothes()
        )
        if not still_referenced:
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], fname)
            if os.path.isfile(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass

    return jsonify({"deleted": True, "id": item_id})


@app.route("/api/outfits", methods=["GET"])
def api_get_outfits():
    """
    GET /api/outfits
    Fetch saved AI outfit combinations hydrated with full garment records.
    Supports optional `?vibe=` filter query parameter.
    """
    vibe_filter = request.args.get("vibe", "").strip()
    outfits = database.get_hydrated_outfits()
    if vibe_filter and vibe_filter.lower() not in ("all", "editorial mix"):
        outfits = [
            o for o in outfits
            if (o.get("vibe") or "").strip().lower() == vibe_filter.lower()
        ]
    return jsonify({"outfits": outfits})


@app.route("/api/generate-outfits", methods=["POST"])
def api_generate_outfits():
    """
    POST /api/generate-outfits
    Query all closet items, verify whether the requested vibe is possible with the
    current inventory, and if feasible, persist newly generated outfits while preserving
    bookmarked (`is_saved = 1`) looks. If not possible, return `possible: False` with
    existing outfits untouched.
    """
    api_key = get_active_api_key()
    payload = request.get_json(silent=True) or {}
    vibe = payload.get("vibe", "All")

    inventory = database.get_all_clothes()
    result = generate_outfits_with_gemini(
        inventory=inventory,
        vibe=vibe,
        api_key=api_key,
    )

    possible = bool(result.get("possible", False))
    message = str(result.get("message") or "").strip()
    generated_list = result.get("outfits") or []
    engine_used = result.get("engine", "algorithmic-stylist")

    if not possible or not generated_list:
        return jsonify({
            "success": False,
            "possible": False,
            "message": message or f"Unable to synthesize outfits for {vibe}. Your closet lacks the necessary foundational pieces.",
            "outfits": database.get_hydrated_outfits(),
            "engine": engine_used,
            "vibe": vibe,
        }), 200

    hydrated = database.save_generated_outfits(generated_list, vibe=vibe, replace_existing=True)
    return jsonify({
        "success": True,
        "possible": True,
        "outfits": hydrated,
        "engine": engine_used,
        "vibe": vibe,
        "message": message or f"Synthesized {len(generated_list)} cohesive lookbook combinations."
    }), 200


@app.route("/api/outfits/<int:outfit_id>/toggle-save", methods=["POST"])
def api_toggle_save_outfit(outfit_id: int):
    """
    POST /api/outfits/<id>/toggle-save
    Toggle persistent bookmark status (`is_saved`) for an outfit in SQLite.
    """
    updated = database.toggle_save_outfit(outfit_id)
    if not updated:
        return jsonify({"error": "Outfit not found."}), 404

    is_saved = bool(updated.get("is_saved", False))
    message = "Look saved to archive." if is_saved else "Look removed from saved archive."
    return jsonify({
        "id": outfit_id,
        "is_saved": is_saved,
        "outfit": updated,
        "message": message,
    }), 200


@app.route("/api/outfits/<int:outfit_id>/wear", methods=["POST"])
def api_wear_outfit(outfit_id: int):
    """
    POST /api/outfits/<id>/wear
    Mark every clothing piece in the selected outfit as worn today.
    """
    outfits = database.get_hydrated_outfits()
    target = next((o for o in outfits if o["id"] == outfit_id), None)
    if not target:
        return jsonify({"error": "Outfit not found."}), 404

    for item_id in target["item_ids"]:
        database.mark_cloth_worn_today(item_id)

    updated_outfits = database.get_hydrated_outfits()
    updated_target = next((o for o in updated_outfits if o["id"] == outfit_id), None)
    return jsonify({
        "outfit": updated_target,
        "message": f"Marked all {len(target['item_ids'])} pieces in '{target['outfit_name']}' as worn today."
    })


@app.route("/api/outfits/<int:outfit_id>", methods=["DELETE"])
def api_delete_outfit(outfit_id: int):
    """DELETE /api/outfits/<id> - Remove a single outfit card from the lookbook."""
    deleted = database.delete_outfit(outfit_id)
    if not deleted:
        return jsonify({"error": "Outfit not found."}), 404
    return jsonify({"deleted": True, "id": outfit_id})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
