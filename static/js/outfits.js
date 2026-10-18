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

function showToast(message, type = "default") {
  const container = document.getElementById("toastContainer");
  if (!container) return;
  const toast = document.createElement("div");
  const extraClass =
    type === true || type === "error"
      ? "error"
      : type === "warning"
      ? "warning"
      : "";
  toast.className = `toast ${extraClass}`.trim();
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 220);
  }, 4400);
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function showInsufficiencyBanner(message) {
  const banner = document.getElementById("vibeInsufficiencyBanner");
  const msgEl = document.getElementById("vibeInsufficiencyMessage");
  if (!banner || !msgEl) return;

  const formatted = String(message || "").startsWith("⚠️")
    ? message
    : `⚠️ Not Possible: ${message}`;
  msgEl.textContent = formatted;
  banner.style.display = "flex";
  banner.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function hideInsufficiencyBanner() {
  const banner = document.getElementById("vibeInsufficiencyBanner");
  if (banner) {
    banner.style.display = "none";
  }
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
    showToast("Failed to load lookbook data.", "error");
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
  const savedEl = document.getElementById("statSavedCount");

  if (catEl) catEl.textContent = lookbookState.totalClothes;
  if (lookEl) {
    const filtered = getFilteredOutfits();
    lookEl.textContent = filtered.length;
  }
  if (savedEl) {
    const savedCount = lookbookState.outfits.filter((o) => Boolean(o.is_saved)).length;
    savedEl.textContent = savedCount;
  }
}

/**
 * Render outfit cards with explicit `data-vibe` attribute, persistent Bookmark toggle button
 * (`★ Saved` vs `☆ Save Look`), and standardized image fallback handling.
 */
function renderLookbookGrid(options = {}) {
  const { preserveCurrentCardsOnZeroMatch = false } = options;
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
    updateLookbookStats();
    return;
  }

  emptyTotal.style.display = "none";

  const activeVibeLower = (lookbookState.selectedVibe || "All").trim().toLowerCase();
  const isAllFilter =
    !activeVibeLower || activeVibeLower === "all" || activeVibeLower === "editorial mix";
  let visibleCount = 0;

  lookbookState.outfits.forEach((outfit, idx) => {
    const lookNum = String(idx + 1).padStart(2, "0");
    const outfitVibe = String(outfit.vibe || "Casual").trim();
    const outfitVibeLower = outfitVibe.toLowerCase();
    const isSaved = Boolean(outfit.is_saved);
    const matchesFilter = isAllFilter || outfitVibeLower === activeVibeLower;

    if (matchesFilter) visibleCount++;

    const card = document.createElement("article");
    card.className = `lookbook-card ${isSaved ? "is-saved" : ""}`.trim();
    card.dataset.id = outfit.id;
    card.dataset.vibe = outfitVibeLower;
    if (!matchesFilter && !preserveCurrentCardsOnZeroMatch) {
      card.style.display = "none";
    }

    const collageHtml = (outfit.items || [])
      .map(
        (item) => `
        <div class="collage-tile" title="${escapeHtml(item.type)} (${escapeHtml(item.color)})">
          <img
            src="${escapeHtml(item.image_url)}"
            alt="${escapeHtml(item.type)}"
            loading="lazy"
            onerror="this.onerror=null; this.src='/static/placeholder-garment.svg';"
          />
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

    const saveBtnClass = isSaved ? "btn btn-sm btn-bookmark btn-saved" : "btn btn-sm btn-outline btn-bookmark";
    const saveBtnLabel = isSaved ? "★ Saved" : "☆ Save Look";

    card.innerHTML = `
      <div class="lookbook-card-header">
        <div>
          <div class="look-kicker">LOOK ${lookNum} // ${escapeHtml(outfitVibe.toUpperCase())}</div>
          <h2 class="look-title">"${escapeHtml(outfit.outfit_name)}"</h2>
        </div>
        <div class="lookbook-header-badges">
          <span class="sku-badge" data-vibe-badge="${escapeHtml(outfitVibeLower)}">${escapeHtml(outfitVibe)}</span>
          <span class="sku-badge">${(outfit.items || []).length} Pieces</span>
          <button
            type="button"
            class="${saveBtnClass}"
            data-action="toggle-save"
            data-id="${outfit.id}"
            aria-pressed="${isSaved ? "true" : "false"}"
            title="${isSaved ? "Saved in archive (click to unsave)" : "Bookmark look to keep across generations"}"
          >${saveBtnLabel}</button>
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

        <div class="lookbook-footer-actions">
          <button
            type="button"
            class="btn btn-primary btn-sm"
            data-action="wear-outfit"
            data-id="${outfit.id}"
          >✓ Wear Entire Look Today</button>
        </div>
      </div>
    `;

    grid.appendChild(card);
  });

  // If preserveCurrentCardsOnZeroMatch is true and 0 cards matched the requested vibe,
  // unhide all existing cards so currently rendered outfits stay on screen.
  if (visibleCount === 0 && preserveCurrentCardsOnZeroMatch && lookbookState.outfits.length > 0) {
    grid.querySelectorAll(".lookbook-card").forEach((c) => {
      c.style.display = "";
    });
    visibleCount = lookbookState.outfits.length;
  }

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

  hideInsufficiencyBanner();
  renderLookbookGrid();
}

async function generateOutfits(requestedVibe) {
  const targetVibe =
    typeof requestedVibe === "string" && requestedVibe.trim()
      ? requestedVibe.trim()
      : lookbookState.selectedVibe || "All";

  const btns = [
    document.getElementById("btnGenerateMain"),
    document.getElementById("btnGenerateHeader"),
    document.getElementById("btnEmptyGenerate"),
    document.getElementById("btnGenerateForVibe"),
  ].filter(Boolean);

  const vibeLabel =
    targetVibe && targetVibe.toLowerCase() !== "all" ? targetVibe : "All Vibes";

  btns.forEach((b) => {
    b.disabled = true;
    b.dataset.orig = b.innerHTML;
    b.innerHTML = `<span class="spinner"></span> Styling (${vibeLabel})...`;
  });

  try {
    const res = await fetch("/api/generate-outfits", {
      method: "POST",
      headers: getApiHeaders(true),
      body: JSON.stringify({ vibe: targetVibe }),
    });
    const data = await res.json();

    if (data.possible === false) {
      const warnMsg =
        data.message ||
        `Cannot synthesize a ${targetVibe} look with your current closet inventory.`;
      showInsufficiencyBanner(warnMsg);
      showToast(`⚠️ Not Possible: ${warnMsg}`, "warning");

      if (Array.isArray(data.outfits) && data.outfits.length > 0) {
        lookbookState.outfits = data.outfits;
      }
      // Keep all currently rendered outfits visible on screen
      renderLookbookGrid({ preserveCurrentCardsOnZeroMatch: true });
      return;
    }

    if (!res.ok) {
      throw new Error(data.error || data.message || "Could not generate outfits.");
    }

    hideInsufficiencyBanner();
    lookbookState.outfits = data.outfits || [];
    renderLookbookGrid();
    showToast(`${data.message} (${data.engine})`);
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btns.forEach((b) => {
      b.disabled = false;
      if (b.dataset.orig) b.innerHTML = b.dataset.orig;
    });
  }
}

async function toggleSaveOutfit(outfitId, btnEl) {
  const numericId = Number(outfitId);
  if (!numericId) return;

  if (btnEl) btnEl.disabled = true;
  try {
    const res = await fetch(`/api/outfits/${numericId}/toggle-save`, {
      method: "POST",
      headers: getApiHeaders(true),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not update bookmark state");

    const isSaved = Boolean(data.is_saved);
    const target = lookbookState.outfits.find((o) => o.id === numericId);
    if (target) {
      target.is_saved = isSaved;
    }

    // Update the button and card DOM in place without purging unsaved outfits
    const cardEl =
      btnEl?.closest(".lookbook-card") ||
      document.querySelector(`.lookbook-card[data-id="${numericId}"]`);
    if (cardEl) {
      cardEl.classList.toggle("is-saved", isSaved);
      const buttons = cardEl.querySelectorAll("[data-action='toggle-save']");
      buttons.forEach((b) => {
        b.classList.toggle("btn-saved", isSaved);
        b.classList.toggle("btn-outline", !isSaved);
        b.setAttribute("aria-pressed", isSaved ? "true" : "false");
        b.textContent = isSaved ? "★ Saved" : "☆ Save Look";
        b.title = isSaved
          ? "Saved in archive (click to unsave)"
          : "Bookmark look to keep across generations";
      });
    }

    updateLookbookStats();
    showToast(data.message || (isSaved ? "Look saved to archive." : "Look removed from saved archive."));
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    if (btnEl) btnEl.disabled = false;
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
    showToast(err.message, "error");
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
    showToast(err.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initLookbookPage();

  // Vibe selector buttons toggle active state and filter outfits immediately
  document.getElementById("vibePills")?.addEventListener("click", (e) => {
    const pill = e.target.closest(".vibe-pill");
    if (!pill) return;

    const clickedVibe = pill.dataset.vibe || "All";
    const isAlreadyActive = pill.classList.contains("active");

    if (isAlreadyActive && clickedVibe.toLowerCase() !== "all") {
      setVibeFilter("All");
    } else {
      setVibeFilter(clickedVibe);
    }
  });

  document.getElementById("btnResetVibeFilter")?.addEventListener("click", () => {
    setVibeFilter("All");
  });

  document.getElementById("btnDismissInsufficiency")?.addEventListener("click", () => {
    hideInsufficiencyBanner();
  });

  document.getElementById("btnGenerateMain")?.addEventListener("click", () => {
    generateOutfits(lookbookState.selectedVibe);
  });
  document.getElementById("btnGenerateHeader")?.addEventListener("click", () => {
    generateOutfits(lookbookState.selectedVibe);
  });
  document.getElementById("btnEmptyGenerate")?.addEventListener("click", () => {
    generateOutfits(lookbookState.selectedVibe);
  });
  document.getElementById("btnGenerateForVibe")?.addEventListener("click", () => {
    generateOutfits(lookbookState.selectedVibe);
  });

  document.getElementById("lookbookGrid")?.addEventListener("click", (e) => {
    const saveBtn = e.target.closest("[data-action='toggle-save']");
    if (saveBtn) {
      toggleSaveOutfit(saveBtn.dataset.id, saveBtn);
      return;
    }

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
