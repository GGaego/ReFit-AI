/**
 * Smart Closet & Style Engine — Page 2: Outfit Lookbook (outfits.js)
 * Strictly Vanilla ES6+ JavaScript
 */

const lookbookState = {
  outfits: [],
  selectedVibe: "All",
  totalClothes: 0,
};

function getApiHeaders(isJson = true) {
  const headers = {};
  if (isJson) headers["Content-Type"] = "application/json";
  const savedKey = localStorage.getItem("SMART_CLOSET_GEMINI_KEY") || "";
  if (savedKey) headers["X-Gemini-Api-Key"] = savedKey;
  return headers;
}

function showToast(message, isError = false) {
  const container = document.getElementById("toastContainer");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `toast ${isError ? "error" : ""}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 220);
  }, 3400);
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function initLookbookPage() {
  try {
    const [statusRes, outfitsRes] = await Promise.all([
      fetch("/api/status", { headers: getApiHeaders(false) }),
      fetch("/api/outfits", { headers: getApiHeaders(false) }),
    ]);
    const statusData = await statusRes.json();
    const outfitsData = await outfitsRes.json();

    lookbookState.totalClothes = statusData.total_items || 0;
    lookbookState.outfits = outfitsData.outfits || [];

    const hasLocalKey = Boolean(localStorage.getItem("SMART_CLOSET_GEMINI_KEY"));
    const isLive = statusData.gemini_configured || hasLocalKey;
    const dot = document.getElementById("engineStatusDot");
    const text = document.getElementById("engineStatusText");
    if (dot && text) {
      dot.classList.toggle("live", isLive);
      text.textContent = isLive ? "Gemini 2.5 Stylist: Live" : "Gemini 2.5: Set API Key";
    }

    updateLookbookStats();
    renderLookbookGrid();
  } catch (err) {
    showToast("Failed to load lookbook data.", true);
  }
}

function getFilteredOutfits() {
  const activeVibe = (lookbookState.selectedVibe || "All").trim().toLowerCase();
  if (!activeVibe || activeVibe === "all" || activeVibe === "editorial mix") {
    return lookbookState.outfits;
  }
  return lookbookState.outfits.filter(
    (o) => String(o.vibe || "").trim().toLowerCase() === activeVibe
  );
}

function updateLookbookStats() {
  const catEl = document.getElementById("statCatalogCount");
  const lookEl = document.getElementById("statLookCount");
  if (catEl) catEl.textContent = lookbookState.totalClothes;
  if (lookEl) {
    const filtered = getFilteredOutfits();
    lookEl.textContent = filtered.length;
  }
}

/**
 * Issue Fix 1: Render outfit cards with explicit `data-vibe` attribute
 * and synchronize DOM filtering + graceful empty state when no outfits match a selected vibe.
 */
function renderLookbookGrid() {
  const grid = document.getElementById("lookbookGrid");
  const emptyTotal = document.getElementById("emptyLookbook");
  const emptyFilter = document.getElementById("emptyVibeFilter");
  if (!grid || !emptyTotal) return;

  grid.innerHTML = "";

  // Case 1: No outfits generated at all
  if (lookbookState.outfits.length === 0) {
    grid.style.display = "none";
    if (emptyFilter) emptyFilter.style.display = "none";
    emptyTotal.style.display = "block";
    return;
  }

  emptyTotal.style.display = "none";

  // Render all outfit cards into the DOM with `data-vibe` attribute, then apply visibility filter
  const activeVibeLower = (lookbookState.selectedVibe || "All").trim().toLowerCase();
  const isAllFilter = !activeVibeLower || activeVibeLower === "all" || activeVibeLower === "editorial mix";
  let visibleCount = 0;

  lookbookState.outfits.forEach((outfit, idx) => {
    const lookNum = String(idx + 1).padStart(2, "0");
    const outfitVibe = String(outfit.vibe || "Casual").trim();
    const outfitVibeLower = outfitVibe.toLowerCase();
    const matchesFilter = isAllFilter || outfitVibeLower === activeVibeLower;

    if (matchesFilter) visibleCount++;

    const card = document.createElement("article");
    card.className = "lookbook-card";
    card.dataset.id = outfit.id;
    card.dataset.vibe = outfitVibeLower;
    if (!matchesFilter) {
      card.style.display = "none";
    }

    const collageHtml = (outfit.items || [])
      .map(
        (item) => `
        <div class="collage-tile" title="${escapeHtml(item.type)} (${escapeHtml(item.color)})">
          <img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.type)}" loading="lazy" />
          <div class="collage-tile-caption">
            #${item.id} ${escapeHtml(item.type)}
          </div>
        </div>
      `
      )
      .join("");

    const manifestHtml = (outfit.items || [])
      .map((item) => {
        const wornLabel = item.last_worn_status ? item.last_worn_status.label : "Never worn";
        const sizeBadge = item.size ? `Size ${escapeHtml(item.size)}` : "Unassigned";
        return `
          <li class="manifest-item">
            <div class="manifest-item-left">
              <span class="manifest-dot"></span>
              <span class="manifest-item-name">${escapeHtml(item.type)}</span>
            </div>
            <span class="manifest-item-meta">
              #${escapeHtml(item.color)} · ${escapeHtml(item.fit)} · ${sizeBadge} · ${escapeHtml(wornLabel)}
            </span>
          </li>
        `;
      })
      .join("");

    card.innerHTML = `
      <div class="lookbook-card-header">
        <div>
          <div class="look-kicker">LOOK ${lookNum} // ${escapeHtml(outfitVibe.toUpperCase())}</div>
          <h2 class="look-title">"${escapeHtml(outfit.outfit_name)}"</h2>
        </div>
        <div style="display: flex; align-items: center; gap: 6px;">
          <span class="sku-badge" data-vibe-badge="${escapeHtml(outfitVibeLower)}">${escapeHtml(outfitVibe)}</span>
          <span class="sku-badge">${(outfit.items || []).length} Pieces</span>
        </div>
      </div>

      <div class="look-collage">
        ${collageHtml}
      </div>

      <div class="lookbook-card-body">
        <div class="styling-rationale-box">
          <div class="rationale-label">Stylist Rationale // Silhouette &amp; Palette</div>
          <p class="rationale-text">${escapeHtml(outfit.styling_notes)}</p>
        </div>

        <div>
          <div class="manifest-heading">Included Wardrobe Pieces</div>
          <ul class="manifest-list">
            ${manifestHtml}
          </ul>
        </div>
      </div>

      <div class="lookbook-card-footer">
        <button
          type="button"
          class="btn btn-ghost btn-sm"
          data-action="delete-outfit"
          data-id="${outfit.id}"
          style="color: var(--danger-text);"
        >Remove Look</button>

        <button
          type="button"
          class="btn btn-primary btn-sm"
          data-action="wear-outfit"
          data-id="${outfit.id}"
        >✓ Wear Entire Look Today</button>
      </div>
    `;

    grid.appendChild(card);
  });

  // Case 2: Outfits exist, but 0 match the selected aesthetic filter
  if (visibleCount === 0) {
    grid.style.display = "none";
    if (emptyFilter) {
      const titleEl = document.getElementById("emptyVibeTitle");
      if (titleEl) {
        titleEl.textContent = "No outfits generated under this aesthetic";
      }
      const copyEl = document.getElementById("emptyVibeCopy");
      if (copyEl) {
        copyEl.textContent = `No outfits currently match the "${lookbookState.selectedVibe}" vibe. Click "Show All Outfits" or generate new outfits for "${lookbookState.selectedVibe}".`;
      }
      emptyFilter.style.display = "block";
    }
  } else {
    grid.style.display = "grid";
    if (emptyFilter) emptyFilter.style.display = "none";
  }

  updateLookbookStats();
}

function setVibeFilter(vibeName) {
  lookbookState.selectedVibe = vibeName || "All";
  const targetLower = lookbookState.selectedVibe.toLowerCase();

  document.querySelectorAll(".vibe-pill").forEach((pill) => {
    const pillVibe = (pill.dataset.vibe || "").toLowerCase();
    pill.classList.toggle("active", pillVibe === targetLower);
  });

  renderLookbookGrid();
}

async function generateOutfits() {
  const btns = [
    document.getElementById("btnGenerateMain"),
    document.getElementById("btnGenerateHeader"),
    document.getElementById("btnEmptyGenerate"),
    document.getElementById("btnGenerateForVibe"),
  ].filter(Boolean);

  const vibeLabel =
    lookbookState.selectedVibe && lookbookState.selectedVibe.toLowerCase() !== "all"
      ? lookbookState.selectedVibe
      : "All Vibes";

  btns.forEach((b) => {
    b.disabled = true;
    b.dataset.orig = b.innerHTML;
    b.innerHTML = `<span class="spinner"></span> Styling (${vibeLabel})...`;
  });

  try {
    const res = await fetch("/api/generate-outfits", {
      method: "POST",
      headers: getApiHeaders(true),
      body: JSON.stringify({ vibe: lookbookState.selectedVibe }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not generate outfits.");

    lookbookState.outfits = data.outfits || [];
    renderLookbookGrid();
    showToast(`${data.message} (${data.engine})`);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btns.forEach((b) => {
      b.disabled = false;
      if (b.dataset.orig) b.innerHTML = b.dataset.orig;
    });
  }
}

async function wearEntireOutfit(outfitId) {
  try {
    const res = await fetch(`/api/outfits/${outfitId}/wear`, {
      method: "POST",
      headers: getApiHeaders(true),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to mark outfit worn");
    showToast(data.message);
    await initLookbookPage();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function deleteOutfitCard(outfitId) {
  try {
    const res = await fetch(`/api/outfits/${outfitId}`, {
      method: "DELETE",
      headers: getApiHeaders(false),
    });
    if (!res.ok) throw new Error("Failed to remove outfit");
    lookbookState.outfits = lookbookState.outfits.filter((o) => o.id !== Number(outfitId));
    renderLookbookGrid();
    showToast("Removed look from lookbook.");
  } catch (err) {
    showToast(err.message, true);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initLookbookPage();

  // Issue Fix 1: Vibe selector buttons toggle active state and filter outfits immediately
  document.getElementById("vibePills")?.addEventListener("click", (e) => {
    const pill = e.target.closest(".vibe-pill");
    if (!pill) return;

    const clickedVibe = pill.dataset.vibe || "All";
    const isAlreadyActive = pill.classList.contains("active");

    // Clicking an active non-"All" filter deselects it and shows all outfits
    if (isAlreadyActive && clickedVibe.toLowerCase() !== "all") {
      setVibeFilter("All");
    } else {
      setVibeFilter(clickedVibe);
    }
  });

  document.getElementById("btnResetVibeFilter")?.addEventListener("click", () => {
    setVibeFilter("All");
  });

  document.getElementById("btnGenerateMain")?.addEventListener("click", generateOutfits);
  document.getElementById("btnGenerateHeader")?.addEventListener("click", generateOutfits);
  document.getElementById("btnEmptyGenerate")?.addEventListener("click", generateOutfits);
  document.getElementById("btnGenerateForVibe")?.addEventListener("click", generateOutfits);

  document.getElementById("lookbookGrid")?.addEventListener("click", (e) => {
    const wearBtn = e.target.closest("[data-action='wear-outfit']");
    if (wearBtn) {
      wearEntireOutfit(wearBtn.dataset.id);
      return;
    }

    const delBtn = e.target.closest("[data-action='delete-outfit']");
    if (delBtn) {
      deleteOutfitCard(delBtn.dataset.id);
    }
  });

  document.getElementById("btnApiKeyModal")?.addEventListener("click", () => {
    document.getElementById("inputGeminiKey").value =
      localStorage.getItem("SMART_CLOSET_GEMINI_KEY") || "";
    document.getElementById("apiKeyModal")?.classList.add("open");
  });

  document.getElementById("btnSaveApiKey")?.addEventListener("click", () => {
    const val = document.getElementById("inputGeminiKey").value.trim();
    if (val) {
      localStorage.setItem("SMART_CLOSET_GEMINI_KEY", val);
      showToast("Gemini API key saved.");
    }
    document.getElementById("apiKeyModal")?.classList.remove("open");
    initLookbookPage();
  });

  document.getElementById("btnClearApiKey")?.addEventListener("click", () => {
    localStorage.removeItem("SMART_CLOSET_GEMINI_KEY");
    document.getElementById("inputGeminiKey").value = "";
    showToast("Cleared saved API key.");
    document.getElementById("apiKeyModal")?.classList.remove("open");
    initLookbookPage();
  });

  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(btn.dataset.close)?.classList.remove("open");
    });
  });
});