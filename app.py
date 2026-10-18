import os
import re
import json
import uuid
import base64
import mimetypes
from datetime import date, timedelta
from typing import Dict, Any, List, Optional, Tuple

from flask import (
    Flask,
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

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

# Support both standard `templates/` directory and flat single-folder layout
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(os.path.join(BASE_DIR, "templates")),
    FileSystemLoader(BASE_DIR),
])

# Ensure SQLite tables exist and legacy records are normalized at startup
database.init_db()

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
2. Ensure silhouette harmony (e.g., pairing slim tops with baggy pants, or balanced boxy proportions).
3. Ensure color coordination (monochromatic, complementary, earth tones, tonal matching).
4. Do not reuse the exact same combination of top + bottom across multiple outfits unless layered distinctly.
5. Provide a witty, creative title for each outfit.
6. Every outfit MUST include an explicit `vibe` tag matching one of the standardized styling options:
   "Streetwear", "Casual", "Smart Casual", "Minimalist", "Formal", "Athleisure", "Workwear", or "Layered".

Output JSON Format:
{
  "outfits": [
    {
      "outfit_name": "string (creative, memorable outfit name)",
      "vibe": "string (Streetwear | Casual | Smart Casual | Minimalist | Formal | Athleisure | Workwear | Layered)",
      "item_ids": [101, 104, 112],
      "styling_notes": "string (1-2 sentences on why this works and how to wear it)"
    }
  ]
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
# Utility & AI Helper Functions
# ---------------------------------------------------------------------------

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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
    Issue Fix 3: Graceful Offline & Missing API Key Fallback Defaults.
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
    returns the Issue Fix 3 Unassigned fallback defaults.
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
# Outfit Generation & Vibe Tagging (Issue Fix 1)
# ---------------------------------------------------------------------------

def generate_outfits_with_gemini(
    inventory: List[Dict[str, Any]],
    vibe: str = "All",
    api_key: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Send the user's wardrobe catalog JSON to Gemini 2.5 Flash to synthesize cohesive outfits.
    Every outfit includes an explicit standardized `vibe` tag.
    """
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

    is_specific_vibe = bool(vibe and vibe.strip().lower() not in ("all", "editorial mix"))
    normalized_requested_vibe = database.normalize_vibe(vibe, default="Casual") if is_specific_vibe else None

    if is_specific_vibe:
        vibe_instruction = (
            f"\nRequested Styling Direction / Vibe: {normalized_requested_vibe}. "
            f"Tailor the outfit combinations, layering choices, and titles toward '{normalized_requested_vibe}' "
            f"and set `vibe` to '{normalized_requested_vibe}' (or closely matching standardized vibes if generating variety)."
        )
    else:
        vibe_instruction = (
            "\nCreate a versatile range of 4 to 6 outfits across distinct standardized vibes "
            "(such as 'Streetwear', 'Casual', 'Smart Casual', 'Minimalist', 'Formal', 'Athleisure'). "
            "Ensure each outfit object has its own explicit `vibe` field set to one of those standardized options."
        )

    user_prompt = (
        f"Closet Inventory JSON:\n{json.dumps(compact_inventory, indent=2)}\n"
        f"{vibe_instruction}\n"
        "Generate between 4 and 6 distinct, cohesive outfits in the exact output JSON schema."
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
                outfits = validate_generated_outfits(
                    parsed.get("outfits", []), inventory, default_vibe=normalized_requested_vibe
                )
                if outfits:
                    return outfits, f"gemini ({GEMINI_MODEL_NAME})"

            elif GENAI_SDK == "google-generativeai":
                legacy_genai.configure(api_key=api_key)
                model = legacy_genai.GenerativeModel(
                    model_name=GEMINI_MODEL_NAME,
                    system_instruction=STYLIST_SYSTEM_PROMPT,
                    generation_config={"response_mime_type": "application/json", "temperature": 0.65},
                )
                response = model.generate_content(user_prompt)
                parsed = extract_json_payload(response.text)
                outfits = validate_generated_outfits(
                    parsed.get("outfits", []), inventory, default_vibe=normalized_requested_vibe
                )
                if outfits:
                    return outfits, f"gemini ({GEMINI_MODEL_NAME})"
        except Exception as exc:
            app.logger.warning(f"Gemini Outfit Stylist call failed, using algorithmic stylist fallback: {exc}")

    return algorithmic_stylist_fallback(inventory, vibe), "algorithmic-stylist"


def validate_generated_outfits(
    raw_outfits: List[Dict[str, Any]],
    inventory: List[Dict[str, Any]],
    default_vibe: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filter out invalid item IDs and ensure each outfit has at least 2 valid pieces and a standardized vibe."""
    valid_ids = {item["id"] for item in inventory}
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
                if num_id in valid_ids and num_id not in ids:
                    ids.append(num_id)
            except (ValueError, TypeError):
                continue
        if len(ids) >= 2:
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
) -> List[Dict[str, Any]]:
    """
    Deterministic editorial stylist engine when Gemini API key is not configured.
    Pairs tops + bottoms (or one-piece dresses) + optional outerwear, footwear, and accessories,
    tagging every outfit with an explicit standardized vibe.
    """
    tops = [i for i in inventory if database.normalize_category(i.get("category")) == "Tops"]
    bottoms = [i for i in inventory if database.normalize_category(i.get("category")) == "Bottoms"]
    one_pieces = [i for i in inventory if database.normalize_category(i.get("category")) == "One-Piece / Dresses"]
    outerwear = [i for i in inventory if database.normalize_category(i.get("category")) == "Outerwear"]
    footwear = [i for i in inventory if database.normalize_category(i.get("category")) == "Footwear"]
    accessories = [i for i in inventory if database.normalize_category(i.get("category")) == "Accessories"]

    is_specific_vibe = bool(vibe and vibe.strip().lower() not in ("all", "editorial mix"))
    target_vibe = database.normalize_vibe(vibe, default="Casual") if is_specific_vibe else None

    if (not tops or not bottoms) and not one_pieces:
        all_ids = [i["id"] for i in inventory]
        if len(all_ids) < 2:
            return []
        assigned_vibe = target_vibe or "Casual"
        return [{
            "outfit_name": f"{assigned_vibe} Capsule Pairing",
            "vibe": assigned_vibe,
            "item_ids": all_ids[:min(4, len(all_ids))],
            "styling_notes": "Cohesive studio combination balancing the textures and color tones currently cataloged in your wardrobe."
        }]

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
    for t in tops:
        for b in bottoms:
            base_combinations.append([t, b])
    for op in one_pieces:
        base_combinations.append([op])

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
            outfit_vibe, title = vibe_profiles[idx % len(vibe_profiles)]

        if outerwear and (idx % 2 == 0 or outfit_vibe in ("Layered", "Workwear", "Streetwear", "Smart Casual")):
            outer = outerwear[idx % len(outerwear)]
            if outer["id"] not in chosen_ids:
                chosen_ids.insert(0, outer["id"])
                layers_desc.append(f"anchored by the {outer['color']} {outer['type']}")

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

    return outfits


# ---------------------------------------------------------------------------
# Feature 1: "Should I Buy It?" In-Store Purchase Evaluator
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
    Deterministic in-store purchase evaluator when Gemini API key is not configured or fails.
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
        total_outfit_count = max(len(tops) * len(bottoms), len(tops) + len(bottoms) + len(one_pieces))
    elif cand_cat == "One-Piece / Dresses":
        total_outfit_count = max(1, len(outerwear) + len(footwear))
    elif cand_cat == "Footwear":
        total_outfit_count = max(len(tops) * len(bottoms), len(inventory) // 2)
    else:  # Accessories
        total_outfit_count = max(len(tops) * len(bottoms), len(inventory) // 2)

    # Determine redundancy and BUY / THINK TWICE / PASS recommendation
    if len(similar_items) >= 2:
        is_redundant = True
        decision = "PASS"
        matched_names = ", ".join(f"{i.get('color')} {i.get('type')}" for i in similar_items[:3])
        redundancy_notes = (
            f"You already own {len(similar_items)} {cand_color_fam} {cand_cat.lower()} pieces "
            f"({matched_names}). Adding another creates high wardrobe overlap."
        )
        decision_summary = (
            f"High redundancy alert: your closet already contains {len(similar_items)} very similar "
            f"{cand['color']} {cand_cat.lower()} pieces. Pass on this purchase and shop your existing archive instead."
        )
    elif len(similar_items) == 1:
        is_redundant = True
        existing_match = similar_items[0]
        decision = "THINK TWICE"
        redundancy_notes = (
            f"Closely overlaps with your existing {existing_match.get('color')} {existing_match.get('type')} "
            f"(#{existing_match.get('id')})."
        )
        decision_summary = (
            f"While this {cand['color']} {cand_cat.lower()} piece can form {total_outfit_count} outfits, "
            f"you already own a similar {existing_match.get('color')} {existing_match.get('type')}. "
            f"Only buy if the {cand['fit']} silhouette offers a distinct upgrade."
        )
    elif total_outfit_count == 0:
        is_redundant = False
        redundancy_notes = None
        decision = "THINK TWICE"
        decision_summary = (
            f"No duplicates found, but your closet currently lacks complementary foundational pieces "
            f"to build full outfits around this {cand['color']} {cand_cat.lower()} item."
        )
    else:
        is_redundant = False
        redundancy_notes = None
        decision = "BUY"
        gap_note = (
            f"fills a {cand_cat.lower()} gap"
            if len(same_cat_items) <= 1
            else f"introduces a fresh {cand['color']} tone to your {cand_cat.lower()} rotation"
        )
        decision_summary = (
            f"Strong wardrobe addition that {gap_note} and unlocks {total_outfit_count} cohesive outfit "
            f"combinations with your existing pieces."
        )

    # Build Top 3 Outfits combining the candidate item with existing closet pieces
    top_outfits = []
    vibe_sequence = ["Streetwear", "Smart Casual", "Minimalist"]
    title_sequence = [
        f"Urban {cand['color'].title()} Rotation",
        f"Elevated {cand['fit'].title()} Layering",
        f"Tonal Studio Archive Look",
    ]

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
            if tops:
                chosen.append(tops[idx % len(tops)])
            if bottoms:
                chosen.append(bottoms[idx % len(bottoms)])
            if footwear and idx % 2 == 0:
                chosen.append(footwear[idx % len(footwear)])
        else:
            if tops:
                chosen.append(tops[idx % len(tops)])
            if bottoms:
                chosen.append(bottoms[idx % len(bottoms)])

        # Deduplicate chosen items
        unique_chosen = []
        seen_ids = set()
        for c_item in chosen:
            if c_item["id"] not in seen_ids:
                seen_ids.add(c_item["id"])
                unique_chosen.append(c_item)

        if not unique_chosen and inventory:
            unique_chosen = inventory[idx:idx + 2] or inventory[:2]

        if unique_chosen:
            names_str = " and ".join(f"{it.get('color')} {it.get('type')}" for it in unique_chosen)
            top_outfits.append({
                "title": title_sequence[idx % len(title_sequence)],
                "vibe": vibe_sequence[idx % len(vibe_sequence)],
                "existing_item_ids": [f"item_{it['id']}" for it in unique_chosen],
                "existing_items": unique_chosen,
                "styling_tips": (
                    f"Pair this {cand['fit']} {cand['color']} candidate piece with your "
                    f"{names_str} for balanced proportions."
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
            app.logger.warning(f"Gemini Purchase Evaluator failed, using deterministic closet evaluator: {exc}")

    fallback_result = fallback_evaluate_purchase(filepath, original_filename, inventory)
    return normalize_purchase_evaluation(fallback_result, inventory), "closet-evaluator-fallback"


# ---------------------------------------------------------------------------
# Demo Wardrobe SVG Generator (1-Click Studio Capsule Seeder)
# ---------------------------------------------------------------------------

def generate_garment_svg(title: str, subtitle: str, bg_hex: str, accent_hex: str, silhouette: str) -> str:
    """Create a sleek editorial studio SVG illustration for demo garments."""
    paths = {
        "jacket": '<path d="M95 75 L145 55 L175 72 L205 55 L255 75 L295 195 L255 208 L235 125 L235 300 L115 300 L115 125 L95 208 L55 195 Z" fill="{fg}" stroke="#111" stroke-width="4" stroke-linejoin="round"/><line x1="175" y1="72" x2="175" y2="300" stroke="#111" stroke-width="4"/><rect x="130" y="195" width="32" height="38" fill="none" stroke="#111" stroke-width="3"/><rect x="188" y="195" width="32" height="38" fill="none" stroke="#111" stroke-width="3"/>',
        "tee": '<path d="M100 75 L145 58 Q175 78 205 58 L250 75 L285 150 L240 170 L225 125 L225 295 L125 295 L125 125 L110 170 L65 150 Z" fill="{fg}" stroke="#111" stroke-width="4" stroke-linejoin="round"/>',
        "shirt": '<path d="M100 72 L145 55 L175 75 L205 55 L250 72 L285 185 L245 195 L230 120 L230 298 L120 298 L120 120 L105 195 L65 185 Z" fill="{fg}" stroke="#111" stroke-width="4" stroke-linejoin="round"/><line x1="175" y1="75" x2="175" y2="298" stroke="#111" stroke-width="3" stroke-dasharray="6,6"/>',
        "sweater": '<path d="M102 75 L145 60 Q175 74 205 60 L248 75 L282 215 L245 222 L230 125 L230 290 L120 290 L120 125 L105 222 L68 215 Z" fill="{fg}" stroke="#111" stroke-width="4" stroke-linejoin="round"/>',
        "pants": '<path d="M120 55 L230 55 L248 315 L190 315 L175 145 L160 315 L102 315 Z" fill="{fg}" stroke="#111" stroke-width="4" stroke-linejoin="round"/><line x1="120" y1="78" x2="230" y2="78" stroke="#111" stroke-width="3"/>',
        "cargo": '<path d="M115 55 L235 55 L255 315 L192 315 L175 150 L158 315 L95 315 Z" fill="{fg}" stroke="#111" stroke-width="4" stroke-linejoin="round"/><rect x="102" y="165" width="26" height="45" fill="{fg}" stroke="#111" stroke-width="3"/><rect x="222" y="165" width="26" height="45" fill="{fg}" stroke="#111" stroke-width="3"/>',
        "shoes": '<path d="M75 215 Q95 165 145 175 L215 195 Q275 200 285 240 L285 265 L70 265 Z" fill="{fg}" stroke="#111" stroke-width="4" stroke-linejoin="round"/><rect x="68" y="262" width="220" height="18" rx="4" fill="#111"/>',
        "belt": '<circle cx="175" cy="185" r="78" fill="none" stroke="{fg}" stroke-width="26"/><circle cx="175" cy="185" r="78" fill="none" stroke="#111" stroke-width="4"/><rect x="155" y="92" width="40" height="28" rx="4" fill="none" stroke="#111" stroke-width="5"/>',
    }
    art = paths.get(silhouette, paths["tee"]).format(fg=accent_hex)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 350 420" width="100%" height="100%">
  <rect width="350" height="420" fill="{bg_hex}"/>
  <rect x="18" y="18" width="314" height="384" fill="none" stroke="#E2E4E8" stroke-width="1.5"/>
  <text x="34" y="44" font-family="monospace" font-size="10" fill="#71717A" letter-spacing="1.5">ARCHIVE STUDIO // LOT</text>
  {art}
  <text x="34" y="366" font-family="sans-serif" font-size="13" font-weight="600" fill="#111111" letter-spacing="0.5">{title.upper()}</text>
  <text x="34" y="384" font-family="monospace" font-size="10" fill="#6B7280">{subtitle}</text>
</svg>"""


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
    Handle image upload (file or base64 camera capture), call Gemini 2.5 Flash Vision API,
    save file to disk, write metadata to SQLite, and return the newly created item JSON.
    Implements Issue Fix 3: Graceful Offline & Missing API Key Fallback.
    """
    api_key = get_active_api_key()
    saved_filename = None
    original_filename = ""

    # 1. Check standard multipart file upload
    if "image" in request.files and request.files["image"].filename:
        file = request.files["image"]
        original_filename = file.filename
        if not allowed_file(original_filename):
            return jsonify({"error": "Unsupported image file format. Use JPG, PNG, WEBP, GIF, or SVG."}), 400

        ext = original_filename.rsplit(".", 1)[1].lower()
        unique_name = f"garment_{uuid.uuid4().hex[:12]}.{ext}"
        saved_filename = secure_filename(unique_name)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], saved_filename)
        file.save(filepath)

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
            saved_filename = f"camera_{uuid.uuid4().hex[:12]}.jpg"
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], saved_filename)
            with open(filepath, "wb") as f:
                f.write(image_bytes)
        except Exception as exc:
            return jsonify({"error": f"Failed to decode camera image: {exc}"}), 400
    else:
        return jsonify({"error": "No garment image provided in request."}), 400

    # 3. Call Gemini Vision API (with Issue Fix 3 Unassigned fallback when key is missing or call fails)
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
    Feature 1: "Should I Buy It?" In-Store Evaluator.
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
    Remove a clothing item from the wardrobe database.
    """
    existing = database.get_cloth_by_id(item_id)
    if not existing:
        return jsonify({"error": f"Clothing item #{item_id} not found."}), 404

    database.delete_cloth(item_id)
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
    Query all closet items, construct payload for Gemini 2.5 Flash Stylist,
    parse outfit IDs and explicit per-outfit vibe tags, persist to SQLite,
    and return hydrated outfit data with image paths.
    """
    api_key = get_active_api_key()
    payload = request.get_json(silent=True) or {}
    vibe = payload.get("vibe", "All")

    inventory = database.get_all_clothes()
    if len(inventory) < 2:
        return jsonify({
            "error": "Add at least 2 garments (ideally 1 Top and 1 Bottom) to your wardrobe before generating outfits."
        }), 400

    generated_list, engine_used = generate_outfits_with_gemini(
        inventory=inventory,
        vibe=vibe,
        api_key=api_key,
    )

    if not generated_list:
        return jsonify({
            "error": "Could not synthesize viable outfits from current inventory. Try adding at least 1 Top and 1 Bottom."
        }), 400

    hydrated = database.save_generated_outfits(generated_list, vibe=vibe, replace_existing=True)
    return jsonify({
        "outfits": hydrated,
        "engine": engine_used,
        "vibe": vibe,
        "message": f"Synthesized {len(hydrated)} cohesive lookbook combinations."
    })


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


@app.route("/api/seed-demo", methods=["POST"])
def api_seed_demo():
    """
    POST /api/seed-demo
    Populate an 8-piece designer capsule wardrobe with studio SVG garment illustrations
    using canonical categories so users can immediately test filtering, inline-editing,
    wearing, AI outfit styling, and the 'Should I Buy It?' In-Store Evaluator.
    """
    today = date.today()
    demo_pieces = [
        {
            "filename": "demo_work_jacket.svg",
            "title": "Tobacco Work Jacket",
            "subtitle": "BOXY // HEAVY DUCK CANVAS",
            "bg": "#F4F1EA",
            "fg": "#9A6B43",
            "silhouette": "jacket",
            "type": "vintage work jacket",
            "category": "Outerwear",
            "color": "faded tobacco brown",
            "fit": "boxy",
            "size": "L",
            "description": "Heavyweight 12oz duck canvas with contrast corduroy collar, patinated brass zipper, and dual patch pockets. Vintage workwear aesthetic.",
            "last_worn": (today - timedelta(days=5)).isoformat(),
        },
        {
            "filename": "demo_ecru_tee.svg",
            "title": "Heavyweight Boxy Tee",
            "subtitle": "BOXY // 300GSM ORGANIC JERSEY",
            "bg": "#F6F5F2",
            "fg": "#EAE6DF",
            "silhouette": "tee",
            "type": "heavyweight crewneck tee",
            "category": "Tops",
            "color": "ecru",
            "fit": "boxy",
            "size": "M",
            "description": "Dry-hand 300gsm combed cotton jersey with thick ribbed collar, dropped shoulders, and blind-stitched hems. Minimalist architectural base layer.",
            "last_worn": (today - timedelta(days=2)).isoformat(),
        },
        {
            "filename": "demo_poplin_shirt.svg",
            "title": "Crisp Poplin Button-Up",
            "subtitle": "OVERSIZED // ICE BLUE COTTON",
            "bg": "#F2F5F8",
            "fg": "#C5D8EB",
            "silhouette": "shirt",
            "type": "poplin button-up shirt",
            "category": "Tops",
            "color": "ice blue",
            "fit": "oversized",
            "size": None,
            "description": "Crisp Italian cotton poplin with mother-of-pearl buttons, elongated cuffs, and curved shirttail hem. Effortless gallery-ready tailoring.",
            "last_worn": None,
        },
        {
            "filename": "demo_charcoal_knit.svg",
            "title": "Charcoal Merino Crew",
            "subtitle": "RELAXED // FINE GAUGE RIB",
            "bg": "#F3F4F6",
            "fg": "#4B5563",
            "silhouette": "sweater",
            "type": "merino wool crewneck",
            "category": "Tops",
            "color": "deep charcoal",
            "fit": "relaxed",
            "size": "L",
            "description": "Fine-gauge extra-fine merino knit featuring saddle shoulders and articulated rib trims. Quiet luxury tonal layering piece.",
            "last_worn": (today - timedelta(days=11)).isoformat(),
        },
        {
            "filename": "demo_selvedge_jeans.svg",
            "title": "Raw Selvedge Denim",
            "subtitle": "STRAIGHT-LEG // 14OZ JAPANESE INDIGO",
            "bg": "#F1F3F6",
            "fg": "#2B3D5B",
            "silhouette": "pants",
            "type": "selvedge denim jeans",
            "category": "Bottoms",
            "color": "raw dark indigo",
            "fit": "straight-leg",
            "size": "32",
            "description": "Unwashed 14oz red-line Japanese selvedge denim with classic 5-pocket construction, copper rivets, and clean full-length stack.",
            "last_worn": (today - timedelta(days=1)).isoformat(),
        },
        {
            "filename": "demo_olive_cargos.svg",
            "title": "Ripstop Fatigue Pants",
            "subtitle": "WIDE-LEG // OLIVE DRAB",
            "bg": "#F2F4F0",
            "fg": "#5A6650",
            "silhouette": "cargo",
            "type": "utility cargo trousers",
            "category": "Bottoms",
            "color": "olive drab",
            "fit": "wide-leg",
            "size": None,
            "description": "Military-inspired cotton ripstop fatigue trousers with bellowed side pockets, knee pleats, and drawstring hem toggles.",
            "last_worn": None,
        },
        {
            "filename": "demo_penny_loafers.svg",
            "title": "Lug-Sole Penny Loafers",
            "subtitle": "STRUCTURED // ESPRESSO CALFSKIN",
            "bg": "#F5F4F2",
            "fg": "#272320",
            "silhouette": "shoes",
            "type": "leather penny loafers",
            "category": "Footwear",
            "color": "espresso black",
            "fit": "structured",
            "size": "42",
            "description": "Polished full-grain calfskin penny loafers set on a chunky Goodyear-welted commando lug sole. Bridges formal tailoring and streetwear.",
            "last_worn": (today - timedelta(days=4)).isoformat(),
        },
        {
            "filename": "demo_leather_belt.svg",
            "title": "Bridle Leather Belt",
            "subtitle": "ONE-SIZE // BRUSHED SILVER BUCKLE",
            "bg": "#F6F6F5",
            "fg": "#1F1F1F",
            "silhouette": "belt",
            "type": "minimalist leather belt",
            "category": "Accessories",
            "color": "matte black",
            "fit": "slim",
            "size": None,
            "description": "Vegetable-tanned bridle leather strap with beveled edges and architectural brushed nickel frame buckle.",
            "last_worn": None,
        },
    ]

    created_items = []
    for piece in demo_pieces:
        svg_markup = generate_garment_svg(
            piece["title"], piece["subtitle"], piece["bg"], piece["fg"], piece["silhouette"]
        )
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], piece["filename"])
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(svg_markup)

        item = database.insert_cloth(
            image_url=f"/uploads/{piece['filename']}",
            garment_type=piece["type"],
            category=piece["category"],
            color=piece["color"],
            fit=piece["fit"],
            size=piece["size"],
            description=piece["description"],
            last_worn=piece["last_worn"],
        )
        created_items.append(item)

    return jsonify({
        "items": created_items,
        "message": f"Loaded {len(created_items)} archival capsule pieces into your wardrobe."
    }), 201


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)