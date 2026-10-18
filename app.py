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
from werkzeug.utils import secure_filename

import database

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

# Ensure SQLite tables exist at startup
database.init_db()

# ---------------------------------------------------------------------------
# Gemini System Prompts (Strict Schemas)
# ---------------------------------------------------------------------------

VISION_SYSTEM_PROMPT = """You are a precise fashion archivist and garment analyzer. 
Analyze the provided clothing image and extract its properties into a strict JSON object. 
Never hallucinate exact sizing (set size to null). Be specific with color nuances and silhouettes.

Output JSON Format:
{
  "type": "string (e.g., jeans, button-up, hoodie, crewneck, sweatpants, bomber jacket, trench coat, sneakers, loafers, etc.)",
  "category": "string (tops | bottoms | outerwear | footwear | accessories)",
  "color": "string (e.g., light blue wash, deep navy, maroon, olive drab, charcoal, ecru)",
  "fit": "string (e.g., oversized, boxy, relaxed, slim, skinny, straight-leg, cropped)",
  "size": null,
  "description": "string (Details on specific textures, graphics, branding, distressing, hardware, collars, stitching, or distinct aesthetic vibes like vintage workwear, streetwear, grunge, minimalist, preppy)"
}"""

STYLIST_SYSTEM_PROMPT = """You are an avant-garde personal wardrobe stylist and sustainability advocate. 
Given a user's closet inventory (list of garment objects with IDs), your job is to build as many practical, aesthetically cohesive outfits as possible.

Guidelines:
1. Every outfit MUST consist of at least:
   - 1 Top + 1 Bottom (or 1 full-body piece like a dress/jumpsuit)
   - Optional: Outerwear (jackets, coats), Footwear, and Accessories if available in the closet.
2. Ensure silhouette harmony (e.g., pairing slim tops with baggy pants, or balanced boxy proportions).
3. Ensure color coordination (monochromatic, complementary, earth tones, tonal matching).
4. Do not reuse the exact same combination of top + bottom across multiple outfits unless layered distinctly.
5. Provide a witty, creative title for each outfit.

Output JSON Format:
{
  "outfits": [
    {
      "outfit_name": "string (creative, memorable outfit name)",
      "item_ids": [101, 104, 112],
      "styling_notes": "string (1-2 sentences on why this works and how to wear it)"
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
    if header_key:
        return header_key
    env_key = (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )
    return env_key if env_key else None


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


def analyze_garment_with_gemini(
    filepath: str,
    original_filename: str = "",
    api_key: Optional[str] = None
) -> Tuple[Dict[str, Any], str]:
    """
    Analyze an uploaded garment photo using Gemini 2.5 Flash Vision.
    Returns (metadata_dict, source_engine_str).
    Falls back to intelligent visual/filename heuristic if no API key is configured.
    """
    mime_type, _ = mimetypes.guess_type(filepath)
    if not mime_type or mime_type == "image/svg+xml":
        mime_type = "image/jpeg"

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
                        "Analyze this garment image and return the strict JSON metadata object."
                    ],
                    config=genai_types.GenerateContentConfig(
                        system_instruction=VISION_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
                parsed = extract_json_payload(response.text)
                return normalize_garment_metadata(parsed), f"gemini ({GEMINI_MODEL_NAME})"

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
                return normalize_garment_metadata(parsed), f"gemini ({GEMINI_MODEL_NAME})"
        except Exception as exc:
            app.logger.warning(f"Gemini Vision API call failed, using fallback analyzer: {exc}")

    return fallback_garment_analyzer(filepath, original_filename), "heuristic-fallback"


def normalize_garment_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure all required keys exist and adhere to the schema."""
    category = str(data.get("category") or "tops").strip().lower()
    if category not in {"tops", "bottoms", "outerwear", "footwear", "accessories"}:
        if any(w in category for w in ["pant", "bottom", "jean", "trouser", "short", "skirt"]):
            category = "bottoms"
        elif any(w in category for w in ["outer", "jacket", "coat", "blazer"]):
            category = "outerwear"
        elif any(w in category for w in ["shoe", "foot", "boot", "sneaker", "loafer"]):
            category = "footwear"
        elif any(w in category for w in ["access", "bag", "belt", "hat", "cap", "scarf"]):
            category = "accessories"
        else:
            category = "tops"

    return {
        "type": str(data.get("type") or "archive garment").strip().lower(),
        "category": category,
        "color": str(data.get("color") or "charcoal neutral").strip().lower(),
        "fit": str(data.get("fit") or "relaxed").strip().lower(),
        "size": None,
        "description": str(
            data.get("description")
            or "Clean architectural construction with tonal stitching and versatile everyday drape."
        ).strip(),
    }


def fallback_garment_analyzer(filepath: str, original_filename: str = "") -> Dict[str, Any]:
    """Smart filename & dominant-color heuristic fallback when Gemini API key is absent."""
    hint = (original_filename or os.path.basename(filepath)).lower()
    name_stem = os.path.splitext(hint)[0].replace("_", " ").replace("-", " ")

    category = "tops"
    g_type = "heavyweight tee"
    fit = "relaxed"
    color = "washed charcoal"
    desc = "Minimalist studio garment with clean tonal seaming, structured drape, and contemporary proportions."

    if any(k in name_stem for k in ["jean", "denim", "pant", "trouser", "cargo", "chino", "short", "sweatpant", "skirt"]):
        category = "bottoms"
        g_type = next((k for k in ["jeans", "cargo pants", "trousers", "chinos", "shorts", "sweatpants", "skirt"] if k[:4] in name_stem), "wide-leg trousers")
        fit = "straight-leg"
        color = "raw indigo" if "jean" in name_stem or "denim" in name_stem else "deep charcoal"
        desc = "Structured lower-body silhouette featuring reinforced pocket detailing and balanced full-length drape."
    elif any(k in name_stem for k in ["jacket", "coat", "trench", "bomber", "blazer", "parka", "cardigan", "overshirt"]):
        category = "outerwear"
        g_type = next((k for k in ["bomber jacket", "trench coat", "work jacket", "blazer", "cardigan", "overshirt"] if k.split()[0] in name_stem), "boxy work jacket")
        fit = "boxy"
        color = "faded tobacco brown"
        desc = "Architectural outer layer with tactile twill weave, structured collar, and utility hardware."
    elif any(k in name_stem for k in ["shoe", "sneaker", "loafer", "boot", "derby", "mule", "runner"]):
        category = "footwear"
        g_type = next((k for k in ["loafers", "sneakers", "leather boots", "derbies"] if k[:4] in name_stem), "leather loafers")
        fit = "structured"
        color = "espresso black"
        desc = "Grounded footwear silhouette with welted sole construction and subtle matte finish."
    elif any(k in name_stem for k in ["belt", "bag", "tote", "cap", "hat", "beanie", "scarf", "glasses"]):
        category = "accessories"
        g_type = next((k for k in ["leather belt", "canvas tote", "wool beanie", "baseball cap", "scarf"] if k.split()[-1] in name_stem), "minimalist accessory")
        fit = "one-size"
        color = "matte black"
        desc = "Refined finishing accent crafted to anchor monochromatic and layered ensembles."
    elif any(k in name_stem for k in ["hoodie", "crewneck", "sweater", "shirt", "button", "polo", "tank", "tee"]):
        category = "tops"
        if "hoodie" in name_stem:
            g_type, fit = "heavyweight hoodie", "oversized"
        elif "crew" in name_stem or "sweater" in name_stem:
            g_type, fit = "merino crewneck sweater", "relaxed"
        elif "shirt" in name_stem or "button" in name_stem:
            g_type, fit = "poplin button-up shirt", "oversized"
        else:
            g_type, fit = "boxy heavyweight tee", "boxy"
        color = "ecru"

    for c_word in ["black", "white", "ecru", "cream", "navy", "indigo", "blue", "olive", "green", "brown", "tan", "khaki", "grey", "gray", "charcoal", "maroon", "burgundy"]:
        if c_word in name_stem:
            color = c_word
            break

    return {
        "type": g_type,
        "category": category,
        "color": color,
        "fit": fit,
        "size": None,
        "description": desc,
    }


def generate_outfits_with_gemini(
    inventory: List[Dict[str, Any]],
    vibe: str = "Editorial Mix",
    api_key: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Send the user's wardrobe catalog JSON to Gemini 2.5 Flash to synthesize cohesive outfits.
    Returns (list_of_outfit_dicts, engine_used).
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

    vibe_instruction = (
        f"\nRequested Styling Direction / Vibe: {vibe}. "
        "Tailor the outfit combinations, layering choices, and titles toward this aesthetic while staying faithful to the items available."
        if vibe and vibe.lower() not in ("all", "editorial mix")
        else "\nCreate a versatile range of high-taste editorial outfits across different moods."
    )

    user_prompt = (
        f"Closet Inventory JSON:\n{json.dumps(compact_inventory, indent=2)}\n"
        f"{vibe_instruction}\n"
        "Generate between 3 and 6 distinct, cohesive outfits in the exact output JSON schema."
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
                outfits = validate_generated_outfits(parsed.get("outfits", []), inventory)
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
                outfits = validate_generated_outfits(parsed.get("outfits", []), inventory)
                if outfits:
                    return outfits, f"gemini ({GEMINI_MODEL_NAME})"
        except Exception as exc:
            app.logger.warning(f"Gemini Outfit Stylist call failed, using algorithmic stylist fallback: {exc}")

    return algorithmic_stylist_fallback(inventory, vibe), "algorithmic-stylist"


def validate_generated_outfits(
    raw_outfits: List[Dict[str, Any]],
    inventory: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Filter out invalid item IDs and ensure each outfit has at least 2 valid pieces."""
    valid_ids = {item["id"] for item in inventory}
    cleaned = []
    for o in raw_outfits:
        if not isinstance(o, dict):
            continue
        ids = []
        for i in o.get("item_ids", []):
            try:
                num_id = int(i)
                if num_id in valid_ids and num_id not in ids:
                    ids.append(num_id)
            except (ValueError, TypeError):
                continue
        if len(ids) >= 2:
            cleaned.append({
                "outfit_name": str(o.get("outfit_name") or "Curated Archival Look").strip(),
                "item_ids": ids,
                "styling_notes": str(
                    o.get("styling_notes")
                    or "Balanced silhouette proportions pairing complementary textures and grounded tonal hues."
                ).strip(),
            })
    return cleaned


def algorithmic_stylist_fallback(
    inventory: List[Dict[str, Any]],
    vibe: str = "Editorial Mix"
) -> List[Dict[str, Any]]:
    """
    Deterministic editorial stylist engine when Gemini API key is not configured.
    Pairs tops + bottoms + optional outerwear, footwear, and accessories with rich styling notes.
    """
    tops = [i for i in inventory if i["category"] == "tops"]
    bottoms = [i for i in inventory if i["category"] == "bottoms"]
    outerwear = [i for i in inventory if i["category"] == "outerwear"]
    footwear = [i for i in inventory if i["category"] == "footwear"]
    accessories = [i for i in inventory if i["category"] == "accessories"]

    if not tops or not bottoms:
        all_ids = [i["id"] for i in inventory]
        if len(all_ids) < 2:
            return []
        return [{
            "outfit_name": f"{vibe} Capsule Pairing",
            "item_ids": all_ids[:min(4, len(all_ids))],
            "styling_notes": "Cohesive studio combination balancing the textures and color tones currently cataloged in your wardrobe."
        }]

    creative_titles = {
        "Editorial Mix": [
            "Downtown Monochrome Slouch",
            "Sunday Art-Walk Layering",
            "Gallery Opening Utilitarian",
            "Off-Duty Architectural Ease",
        ],
        "Minimalist": [
            "Tonal Brutalist Uniform",
            "Nordic Gallery Proportions",
            "Clean-Line Monolith Study",
            "Quiet Luxury Silhouettes",
        ],
        "Casual": [
            "Coffee Run Relaxed Drape",
            "Weekend Flea-Market Uniform",
            "Effortless Matinee Layers",
            "Easygoing Texture Play",
        ],
        "Layered": [
            "Transitional Weather Armor",
            "Multi-Texture Studio Stack",
            "Autumnal Depth & Dimension",
            "Structured Outer-Shell Study",
        ],
        "Formal": [
            "Sharp Sartorial Contrast",
            "Modernist Evening Tailoring",
            "Refined Creative Director",
            "Polished Tonal Drape",
        ],
        "Streetwear": [
            "Concrete Volume & Proportion",
            "Boxy Archive Rotation",
            "Subway Utility Statement",
            "Heavyweight Street Uniform",
        ],
        "Workwear": [
            "Heritage Workshop Utility",
            "Canvas & Selvedge Study",
            "Chore-Coat Studio Rotation",
            "Industrial Earth-Tone Uniform",
        ],
    }

    titles = creative_titles.get(vibe, creative_titles["Editorial Mix"])
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for t in tops:
        for b in bottoms:
            pairs.append((t, b))

    outfits = []
    max_outfits = min(len(pairs), 4)
    for idx in range(max_outfits):
        top, bottom = pairs[idx]
        chosen_ids = [top["id"], bottom["id"]]
        layers_desc = []

        if outerwear and (idx % 2 == 0 or vibe in ("Layered", "Workwear", "Editorial Mix")):
            outer = outerwear[idx % len(outerwear)]
            chosen_ids.insert(0, outer["id"])
            layers_desc.append(f"anchored by the {outer['color']} {outer['type']}")

        if footwear:
            shoe = footwear[idx % len(footwear)]
            chosen_ids.append(shoe["id"])
            layers_desc.append(f"grounded with {shoe['color']} {shoe['type']}")

        if accessories and idx % 2 == 1:
            acc = accessories[idx % len(accessories)]
            chosen_ids.append(acc["id"])
            layers_desc.append(f"finished with the {acc['type']}")

        layer_clause = (", " + " and ".join(layers_desc)) if layers_desc else ""
        notes = (
            f"Balances the {top['fit']} silhouette of the {top['color']} {top['type']} "
            f"against the {bottom['fit']} cut of the {bottom['color']} {bottom['type']}{layer_clause}. "
            f"The palette bridges tonal neutrals with rich tactile contrast."
        )

        outfits.append({
            "outfit_name": titles[idx % len(titles)],
            "item_ids": chosen_ids,
            "styling_notes": notes,
        })

    return outfits


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
# Page Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Render Page 1: 'My Wardrobe' Digital Closet Storefront."""
    return render_template("index.html")


@app.route("/outfits")
def outfits_page():
    """Render Page 2: 'Outfit Lookbook' AI Recommendations."""
    return render_template("outfits.html")


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

    category_counts = {"all": len(all_unfiltered)}
    never_worn_count = 0
    worn_today_count = 0

    for item in all_unfiltered:
        cat = item.get("category", "tops").lower()
        category_counts[cat] = category_counts.get(cat, 0) + 1
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
                header, b64_data = raw_b64.split(",", 1)
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

    # 3. Call Gemini Vision API (with automatic fallback)
    metadata, engine_used = analyze_garment_with_gemini(
        filepath=filepath,
        original_filename=original_filename,
        api_key=api_key,
    )

    form_data = request.form if request.form else (request.json if request.is_json else {})
    for field in ("type", "category", "color", "fit", "size", "description"):
        if form_data.get(field):
            metadata[field] = form_data.get(field)

    image_url = f"/uploads/{saved_filename}"
    new_item = database.insert_cloth(
        image_url=image_url,
        garment_type=metadata.get("type", "archive garment"),
        category=metadata.get("category", "tops"),
        color=metadata.get("color", "neutral"),
        fit=metadata.get("fit", "relaxed"),
        size=metadata.get("size"),
        description=metadata.get("description", ""),
    )

    return jsonify({
        "item": new_item,
        "engine": engine_used,
        "message": "Garment cataloged and analyzed successfully."
    }), 201


@app.route("/api/clothes/<int:item_id>", methods=["PUT"])
def api_update_cloth(item_id: int):
    """
    PUT /api/clothes/<id>
    Allow manual user editing of type, category, color, fit, size, description, or last_worn.
    """
    existing = database.get_cloth_by_id(item_id)
    if not existing:
        return jsonify({"error": f"Clothing item #{item_id} not found."}), 404

    payload = request.get_json(silent=True) or {}
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
    """
    outfits = database.get_hydrated_outfits()
    return jsonify({"outfits": outfits})


@app.route("/api/generate-outfits", methods=["POST"])
def api_generate_outfits():
    """
    POST /api/generate-outfits
    Query all closet items, construct payload for Gemini 2.5 Flash Stylist,
    parse outfit IDs, persist to SQLite, and return hydrated outfit data with image paths.
    """
    api_key = get_active_api_key()
    payload = request.get_json(silent=True) or {}
    vibe = payload.get("vibe", "Editorial Mix")

    inventory = database.get_all_clothes()
    if len(inventory) < 2:
        return jsonify({
            "error": "Add at least 2 garments (ideally 1 top and 1 bottom) to your wardrobe before generating outfits."
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
    so users can immediately test filtering, inline-editing, wearing, and AI outfit styling.
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
            "category": "outerwear",
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
            "category": "tops",
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
            "category": "tops",
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
            "category": "tops",
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
            "category": "bottoms",
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
            "category": "bottoms",
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
            "category": "footwear",
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
            "category": "accessories",
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
