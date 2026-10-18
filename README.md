# Smart Closet & Style Engine (Gemini 2.5 Flash)

## Project Structure

```text
smart_closet/
├── app.py
├── database.py
├── requirements.txt
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
   cd smart_closet
   pip install -r requirements.txt
   ```

2. **Configure Your Gemini API Key**:
   Set the `GEMINI_API_KEY` environment variable (or click the **Gemini 2.5 Flash** status pill in the top-right navigation bar of the web app to enter your key directly in the browser):

   ```bash
   export GEMINI_API_KEY="your-google-gemini-api-key"
   ```

3. **Run the Flask Server**:

   ```bash
   python app.py
   ```

4. **Explore Your Digital Closet**:
   - Open `http://localhost:5000` in your browser.
   - Click **"+ Load Studio Capsule"** to immediately populate an 8-piece designer capsule wardrobe with SVG studio illustrations, or click **"↑ Upload Piece"** to drag-and-drop a photo / capture a frame from your webcam for live Gemini 2.5 Flash Vision analysis.
   - Click any badge on a product card (`type`, `#color`, `fit`, `category`, `Size`) to edit it in-place, or click **"Generate Outfits →"** (`http://localhost:5000/outfits`) to synthesize AI-styled lookbook combinations.
