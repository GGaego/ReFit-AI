# Smart Closet & Style Engine (Gemini 2.5 Flash) — Argon 4 Release

## Overview & Key Features

1. **"Should I Buy It?" In-Store Evaluator (`/api/evaluate-purchase`):**
   - Capture or upload a photo of a clothing item while shopping (`<input type="file" accept="image/*" capture="environment">` or live webcam stream).
   - Evaluates the candidate item against your existing closet catalog using Gemini Multimodal Vision (with an automatic deterministic closet-comparison fallback when offline).
   - Displays a direct **Recommendation Badge** (`BUY`, `PASS`, or `THINK TWICE`), **Reasoning Summary**, **Similarity / Redundancy Alert**, **Outfit Potential Metric** (`total_outfit_count`), and visual cards for the **Top 3 Outfits** combining the candidate piece with owned items.
2. **Synchronized Style / Vibe Filtering (`/outfits` & `/api/generate-outfits`):**
   - Every generated outfit includes an explicit standardized `vibe` tag (`Streetwear`, `Casual`, `Smart Casual`, `Minimalist`, `Formal`, `Athleisure`, `Workwear`, `Layered`).
   - Clicking vibe buttons toggles the active state and filters `.lookbook-card[data-vibe]` elements in real time, reverting to all outfits when deselected or when `All` is chosen, and displaying a clean empty state (*"No outfits generated under this aesthetic"*) when no outfits match.
3. **Standardized Category Dropdown in Edit Mode (`PUT /api/clothes/<id>`):**
   - Both the Quick-Edit Modal and inline category badge editor use a controlled `<select>` dropdown restricted to canonical categories: `Tops`, `Bottoms`, `Outerwear`, `Footwear`, `Accessories`, and `One-Piece / Dresses`.
   - Updates persist to SQLite and immediately synchronize with the category filter tabs on Page 1.
4. **Graceful Offline & Missing API Key Fallback (`POST /api/upload`):**
   - Uploading a garment without an active Gemini API key or during network failures safely saves the item with fallback defaults (`name`/`type`: `"Clothing Item"`, `category`: `"Unassigned"`, `color`: `"Unassigned"`, `fit`: `"Unassigned"`, `size`: `null`, `style_notes`/`description`: `"Unassigned"`).
   - Displays a non-blocking toast notice (*"AI tagging unavailable. Defaults set to Unassigned — please edit manually."*) and prompts the user in the Edit Modal to categorize the piece.

## Project Structure 

```text
smart_closet/
├── app.py
├── database.py
├── closet.db
├── README.md
├── uploads/
├── templates/
│   ├── index.html
│   └── outfits.html
└── static/
    ├── css/
    │   └── styles.css
    └── js/
        ├── closet.js
        └── outfits.js
```

## Quickstart & Running the Application

1. **Install Dependencies**:

   ```bash
   pip install flask pillow google-genai
   ```

2. **Configure Your Gemini API Key (Optional — Offline Fallback Supported)**:

   ```bash
   export GEMINI_API_KEY="your-google-gemini-api-key"
   ```

3. **Run the Flask Server**:

   ```bash
   python app.py
   ```
