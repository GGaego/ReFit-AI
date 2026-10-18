/**
 * Smart Closet & Style Engine — Page 1: My Wardrobe (closet.js)
 * Strictly Vanilla ES6+ JavaScript
 */

const CANONICAL_CATEGORIES = [
  "Tops",
  "Bottoms",
  "Outerwear",
  "Footwear",
  "Accessories",
  "One-Piece / Dresses",
];

const state = {
  items: [],
  activeCategory: "all",
  searchQuery: "",
  sortBy: "newest",
  uploadMode: "file", // "file" | "camera"
  capturedBase64: null,
  cameraStream: null,
  evalMode: "file", // "file" | "camera"
  evalCapturedBase64: null,
  evalCameraStream: null,
  evalPreviewDataUrl: null,
};

function normalizeCategoryName(rawCategory = "") {
  const cleaned = String(rawCategory || "").trim();
  const lower = cleaned.toLowerCase();
  if (!cleaned || lower === "unassigned" || lower === "null" || lower === "none") {
    return "Unassigned";
  }
  for (const canon of CANONICAL_CATEGORIES) {
    if (lower === canon.toLowerCase()) return canon;
  }
  if (lower.includes("one-piece") || lower.includes("one piece") || lower.includes("dress") || lower.includes("jumpsuit")) {
    return "One-Piece / Dresses";
  }
  if (lower.includes("top") || lower.includes("shirt") || lower.includes("tee") || lower.includes("hoodie") || lower.includes("sweater")) {
    return "Tops";
  }
  if (lower.includes("bottom") || lower.includes("pant") || lower.includes("jean") || lower.includes("short") || lower.includes("trouser")) {
    return "Bottoms";
  }
  if (lower.includes("outer") || lower.includes("jacket") || lower.includes("coat") || lower.includes("bomber")) {
    return "Outerwear";
  }
  if (lower.includes("foot") || lower.includes("shoe") || lower.includes("sneaker") || lower.includes("loafer") || lower.includes("boot")) {
    return "Footwear";
  }
  if (lower.includes("access") || lower.includes("belt") || lower.includes("bag") || lower.includes("hat")) {
    return "Accessories";
  }
  return "Unassigned";
}

function resolveColorHex(colorName = "") {
  const c = String(colorName || "").toLowerCase();
  if (c.includes("unassigned")) return "#D4D4D8";
  if (c.includes("black") || c.includes("espresso") || c.includes("onyx")) return "#18181B";
  if (c.includes("ecru") || c.includes("cream") || c.includes("ivory") || c.includes("bone")) return "#EFECE6";
  if (c.includes("white") || c.includes("chalk")) return "#FFFFFF";
  if (c.includes("indigo") || c.includes("navy") || c.includes("midnight")) return "#1E293B";
  if (c.includes("ice blue") || c.includes("light blue") || c.includes("sky")) return "#BFDBFE";
  if (c.includes("blue") || c.includes("cobalt")) return "#2563EB";
  if (c.includes("tobacco") || c.includes("cognac") || c.includes("brown") || c.includes("camel") || c.includes("tan") || c.includes("taupe")) return "#8B5E34";
  if (c.includes("olive") || c.includes("sage") || c.includes("forest") || c.includes("khaki") || c.includes("green")) return "#556B2F";
  if (c.includes("charcoal") || c.includes("slate") || c.includes("graphite")) return "#3F3F46";
  if (c.includes("grey") || c.includes("gray") || c.includes("heather") || c.includes("silver")) return "#9CA3AF";
  if (c.includes("maroon") || c.includes("burgundy") || c.includes("wine") || c.includes("oxblood") || c.includes("red")) return "#7F1D1D";
  return "#71717A";
}

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
  const extraClass = type === true || type === "error" ? "error" : type === "warning" ? "warning" : "";
  toast.className = `toast ${extraClass}`.trim();
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 220);
  }, 4200);
}

async function checkEngineStatus() {
  try {
    const res = await fetch("/api/status", { headers: getApiHeaders(false) });
    const data = await res.json();
    const dot = document.getElementById("engineStatusDot");
    const text = document.getElementById("engineStatusText");
    const hasLocalKey = Boolean(localStorage.getItem("SMART_CLOSET_GEMINI_KEY"));
    const isLive = data.gemini_configured || hasLocalKey;

    if (dot && text) {
      dot.classList.toggle("live", isLive);
      text.textContent = isLive ? "Gemini 2.5 Flash: Live" : "Gemini 2.5: Set API Key";
    }
  } catch (err) {
    console.warn("Status check failed:", err);
  }
}

async function fetchWardrobe() {
  try {
    const params = new URLSearchParams({
      category: state.activeCategory,
      q: state.searchQuery,
      sort: state.sortBy,
    });
    const res = await fetch(`/api/clothes?${params.toString()}`, {
      headers: getApiHeaders(false),
    });
    const data = await res.json();
    state.items = data.items || [];
    updateTelemetry(data.stats || {});
    renderProductGrid();
  } catch (err) {
    showToast("Failed to load wardrobe catalog.", "error");
  }
}

function updateTelemetry(stats) {
  const totalEl = document.getElementById("statTotal");
  const neverEl = document.getElementById("statNeverWorn");
  const todayEl = document.getElementById("statWornToday");

  if (totalEl) totalEl.textContent = stats.total ?? 0;
  if (neverEl) neverEl.textContent = stats.never_worn ?? 0;
  if (todayEl) todayEl.textContent = stats.worn_today ?? 0;

  const counts = stats.categories || {};
  const badgeMap = {
    "count-all": counts["all"] ?? counts["All"] ?? 0,
    "count-tops": counts["Tops"] ?? counts["tops"] ?? 0,
    "count-bottoms": counts["Bottoms"] ?? counts["bottoms"] ?? 0,
    "count-outerwear": counts["Outerwear"] ?? counts["outerwear"] ?? 0,
    "count-footwear": counts["Footwear"] ?? counts["footwear"] ?? 0,
    "count-accessories": counts["Accessories"] ?? counts["accessories"] ?? 0,
    "count-one-piece-dresses":
      counts["One-Piece / Dresses"] ??
      counts["one-piece-dresses"] ??
      counts["one-piece / dresses"] ??
      0,
    "count-unassigned": counts["Unassigned"] ?? counts["unassigned"] ?? 0,
  };

  Object.entries(badgeMap).forEach(([badgeId, val]) => {
    const el = document.getElementById(badgeId);
    if (el) el.textContent = val;
  });

  const tabUnassigned = document.getElementById("tabUnassigned");
  if (tabUnassigned) {
    const unassignedCount = badgeMap["count-unassigned"];
    tabUnassigned.style.display =
      unassignedCount > 0 || state.activeCategory.toLowerCase() === "unassigned"
        ? "inline-flex"
        : "none";
  }
}

function renderProductGrid() {
  const grid = document.getElementById("productGrid");
  const emptyState = document.getElementById("emptyState");
  if (!grid || !emptyState) return;

  grid.innerHTML = "";

  if (state.items.length === 0) {
    grid.style.display = "none";
    emptyState.style.display = "block";
    return;
  }

  grid.style.display = "grid";
  emptyState.style.display = "none";

  state.items.forEach((item) => {
    const card = document.createElement("article");
    const canonicalCat = normalizeCategoryName(item.category);
    const isUnassigned =
      canonicalCat === "Unassigned" ||
      String(item.color || "").toLowerCase() === "unassigned" ||
      String(item.fit || "").toLowerCase() === "unassigned";

    card.className = `product-card ${isUnassigned ? "is-unassigned" : ""}`.trim();
    card.dataset.id = item.id;
    card.dataset.category = canonicalCat;

    const wornInfo = item.last_worn_status || { label: "Never worn", state: "never" };
    const sizeLabel =
      item.size && String(item.size).toLowerCase() !== "unassigned"
        ? `Size: ${item.size}`
        : "Size: Unassigned";
    const sizeClass =
      item.size && String(item.size).toLowerCase() !== "unassigned"
        ? ""
        : "size-unassigned";
    const swatchHex = resolveColorHex(item.color);

    const colorUnassignedClass =
      String(item.color || "").toLowerCase() === "unassigned" ? "pill-unassigned" : "";
    const fitUnassignedClass =
      String(item.fit || "").toLowerCase() === "unassigned" ? "pill-unassigned" : "";
    const catUnassignedClass = canonicalCat === "Unassigned" ? "pill-unassigned" : "";
    const badgeUnassignedClass = canonicalCat === "Unassigned" ? "badge-unassigned" : "";

    const displayTitle = item.type || item.name || "Clothing Item";
    const displayDesc =
      item.description && item.description !== "Unassigned"
        ? item.description
        : item.description === "Unassigned"
        ? "Unassigned — click to add style notes and categorize this garment."
        : "No description provided.";

    card.innerHTML = `
      <div class="card-media">
        <img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(displayTitle)}" loading="lazy" />
        <div class="card-top-badges">
          <span class="sku-badge ${badgeUnassignedClass}">#${item.id} // ${escapeHtml(canonicalCat)}</span>
          <div class="worn-tracker-group">
            <span class="worn-badge state-${wornInfo.state}" title="Last worn date: ${item.last_worn || 'Never'}">
              ${escapeHtml(wornInfo.label)}
            </span>
            <button
              type="button"
              class="btn-wear-quick"
              data-action="wear"
              data-id="${item.id}"
              title="Mark as worn today"
              aria-label="Mark as worn today"
            >✓</button>
          </div>
        </div>
      </div>

      <div class="card-body">
        <div class="card-title-row">
          <h2
            class="garment-title"
            data-inline-field="type"
            data-id="${item.id}"
            title="Click to edit garment name/type inline"
          >${escapeHtml(displayTitle)}</h2>
        </div>

        <div class="metadata-pills">
          <span
            class="meta-pill ${colorUnassignedClass}"
            data-inline-field="color"
            data-id="${item.id}"
            title="Click to edit color nuance"
          >
            <span class="color-dot" style="background:${swatchHex};"></span>
            #${escapeHtml(item.color || "Unassigned")}
          </span>

          <span
            class="meta-pill ${fitUnassignedClass}"
            data-inline-field="fit"
            data-id="${item.id}"
            title="Click to edit fit / silhouette"
          >fit: ${escapeHtml(item.fit || "Unassigned")}</span>

          <span
            class="meta-pill ${catUnassignedClass}"
            data-inline-field="category"
            data-id="${item.id}"
            title="Click to select canonical category"
          >${escapeHtml(canonicalCat)}</span>

          <span
            class="meta-pill ${sizeClass}"
            data-inline-field="size"
            data-id="${item.id}"
            title="Click to set or edit garment size"
          >${escapeHtml(sizeLabel)}</span>
        </div>

        <p
          class="garment-description"
          data-action="open-edit"
          data-id="${item.id}"
          title="Click to edit full description and attributes"
        >${escapeHtml(displayDesc)}</p>
      </div>

      <div class="card-footer">
        <button
          type="button"
          class="btn btn-ghost btn-sm"
          data-action="open-edit"
          data-id="${item.id}"
        >✎ Quick Edit</button>

        <button
          type="button"
          class="btn btn-outline btn-sm"
          data-action="wear"
          data-id="${item.id}"
        >✓ Worn Today</button>
      </div>
    `;

    grid.appendChild(card);
  });
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Issue Fix 2: Standardized Category Dropdown in Edit Mode (Inline & Modal).
 * When editing `category` inline, renders a controlled <select> restricted to
 * the 6 canonical system categories instead of a freeform text input.
 */
function startInlineEdit(targetEl, itemId, field) {
  if (targetEl.querySelector("input, select")) return;
  const item = state.items.find((i) => i.id === Number(itemId));
  if (!item) return;

  const originalHtml = targetEl.innerHTML;

  if (field === "category") {
    const currentCat = normalizeCategoryName(item.category);
    const select = document.createElement("select");
    select.className = "inline-edit-input";

    if (currentCat === "Unassigned") {
      const placeholderOpt = document.createElement("option");
      placeholderOpt.value = "Unassigned";
      placeholderOpt.textContent = "Unassigned";
      placeholderOpt.selected = true;
      select.appendChild(placeholderOpt);
    }

    CANONICAL_CATEGORIES.forEach((cat) => {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat;
      if (cat === currentCat) opt.selected = true;
      select.appendChild(opt);
    });

    targetEl.innerHTML = "";
    targetEl.appendChild(select);
    select.focus();

    let committed = false;
    const commitSelect = async () => {
      if (committed) return;
      committed = true;
      const newCat = select.value;
      if (!newCat || newCat === currentCat) {
        targetEl.innerHTML = originalHtml;
        return;
      }
      try {
        const res = await fetch(`/api/clothes/${itemId}`, {
          method: "PUT",
          headers: getApiHeaders(true),
          body: JSON.stringify({ category: newCat }),
        });
        if (!res.ok) throw new Error("Category update failed");
        showToast(`Updated category to "${newCat}" for #${itemId}`);
        await fetchWardrobe();
      } catch (err) {
        targetEl.innerHTML = originalHtml;
        showToast("Could not update category.", "error");
      }
    };

    select.addEventListener("change", commitSelect);
    select.addEventListener("blur", commitSelect);
    select.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        committed = true;
        targetEl.innerHTML = originalHtml;
      }
    });
    return;
  }

  const currentVal = item[field] || "";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "inline-edit-input";
  input.value = currentVal === "Unassigned" ? "" : currentVal;
  input.placeholder = field === "size" ? "e.g. M, L, 32" : field;

  targetEl.innerHTML = "";
  targetEl.appendChild(input);
  input.focus();
  input.select();

  const commitChange = async () => {
    const newVal = input.value.trim();
    if (newVal === currentVal || (!newVal && currentVal === "Unassigned")) {
      targetEl.innerHTML = originalHtml;
      return;
    }
    try {
      const res = await fetch(`/api/clothes/${itemId}`, {
        method: "PUT",
        headers: getApiHeaders(true),
        body: JSON.stringify({ [field]: newVal || (field === "size" ? null : "Unassigned") }),
      });
      if (!res.ok) throw new Error("Update failed");
      showToast(`Updated ${field} for #${itemId}`);
      await fetchWardrobe();
    } catch (err) {
      targetEl.innerHTML = originalHtml;
      showToast(`Could not update ${field}.`, "error");
    }
  };

  input.addEventListener("blur", commitChange, { once: true });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      input.blur();
    } else if (e.key === "Escape") {
      input.removeEventListener("blur", commitChange);
      targetEl.innerHTML = originalHtml;
    }
  });
}

async function markWornToday(itemId) {
  try {
    const res = await fetch(`/api/clothes/${itemId}/wear`, {
      method: "POST",
      headers: getApiHeaders(true),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to update worn status");
    showToast(data.message || "Marked as worn today!");
    await fetchWardrobe();
  } catch (err) {
    showToast(err.message, "error");
  }
}

/**
 * Issue Fix 2 & 3: Open Edit Modal with controlled canonical category <select>
 * and clean handling of "Unassigned" fallback values.
 */
function openEditModal(itemId) {
  const item = state.items.find((i) => i.id === Number(itemId));
  if (!item) return;

  const canonicalCat = normalizeCategoryName(item.category);
  const hasUnassigned =
    canonicalCat === "Unassigned" ||
    String(item.color || "").toLowerCase() === "unassigned" ||
    String(item.fit || "").toLowerCase() === "unassigned" ||
    String(item.description || "").toLowerCase() === "unassigned";

  const noticeEl = document.getElementById("editUnassignedNotice");
  if (noticeEl) {
    noticeEl.style.display = hasUnassigned ? "block" : "none";
  }

  const optUnassigned = document.getElementById("optUnassignedCategory");
  if (optUnassigned) {
    optUnassigned.style.display = canonicalCat === "Unassigned" ? "block" : "none";
  }

  document.getElementById("editItemId").value = item.id;
  document.getElementById("editType").value = item.type || item.name || "Clothing Item";
  document.getElementById("editCategory").value = canonicalCat;
  document.getElementById("editColor").value = item.color || "Unassigned";
  document.getElementById("editFit").value = item.fit || "Unassigned";
  document.getElementById("editSize").value = item.size || "";
  document.getElementById("editLastWorn").value = item.last_worn ? item.last_worn.slice(0, 10) : "";
  document.getElementById("editDescription").value = item.description || "Unassigned";

  document.getElementById("editModal").classList.add("open");
}

async function handleEditSubmit(e) {
  e.preventDefault();
  const itemId = document.getElementById("editItemId").value;
  const selectedCategory = document.getElementById("editCategory").value;

  const payload = {
    type: document.getElementById("editType").value.trim() || "Clothing Item",
    name: document.getElementById("editType").value.trim() || "Clothing Item",
    category: selectedCategory,
    color: document.getElementById("editColor").value.trim() || "Unassigned",
    fit: document.getElementById("editFit").value.trim() || "Unassigned",
    size: document.getElementById("editSize").value.trim() || null,
    last_worn: document.getElementById("editLastWorn").value || null,
    description: document.getElementById("editDescription").value.trim() || "Unassigned",
    style_notes: document.getElementById("editDescription").value.trim() || "Unassigned",
  };

  try {
    const res = await fetch(`/api/clothes/${itemId}`, {
      method: "PUT",
      headers: getApiHeaders(true),
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to update item");
    document.getElementById("editModal").classList.remove("open");
    showToast(`Saved changes (${payload.category}).`);
    await fetchWardrobe();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function handleDeleteItem() {
  const itemId = document.getElementById("editItemId").value;
  if (!itemId) return;
  try {
    const res = await fetch(`/api/clothes/${itemId}`, {
      method: "DELETE",
      headers: getApiHeaders(false),
    });
    if (!res.ok) throw new Error("Failed to delete piece");
    document.getElementById("editModal").classList.remove("open");
    showToast(`Removed piece #${itemId} from wardrobe.`);
    await fetchWardrobe();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function stopCameraStream() {
  if (state.cameraStream) {
    state.cameraStream.getTracks().forEach((track) => track.stop());
    state.cameraStream = null;
  }
}

function stopEvalCameraStream() {
  if (state.evalCameraStream) {
    state.evalCameraStream.getTracks().forEach((track) => track.stop());
    state.evalCameraStream = null;
  }
}

async function switchUploadMode(mode) {
  state.uploadMode = mode;
  const tabFile = document.getElementById("tabFileMode");
  const tabCam = document.getElementById("tabCameraMode");
  const dropzone = document.getElementById("dropzoneArea");
  const cameraArea = document.getElementById("cameraArea");

  if (mode === "camera") {
    tabCam.className = "btn btn-sm btn-primary";
    tabFile.className = "btn btn-sm btn-outline";
    dropzone.style.display = "none";
    cameraArea.style.display = "flex";
    try {
      state.cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
        audio: false,
      });
      const video = document.getElementById("cameraStream");
      video.srcObject = state.cameraStream;
    } catch (err) {
      showToast("Camera access unavailable. Using file upload instead.", "warning");
      switchUploadMode("file");
    }
  } else {
    stopCameraStream();
    tabFile.className = "btn btn-sm btn-primary";
    tabCam.className = "btn btn-sm btn-outline";
    dropzone.style.display = "block";
    cameraArea.style.display = "none";
  }
}

function captureCameraFrame() {
  const video = document.getElementById("cameraStream");
  if (!video || !video.videoWidth) return;
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0);
  state.capturedBase64 = canvas.toDataURL("image/jpeg", 0.9);

  const previewContainer = document.getElementById("previewContainer");
  const previewImg = document.getElementById("imagePreview");
  previewImg.src = state.capturedBase64;
  previewContainer.style.display = "block";
  stopCameraStream();
  document.getElementById("cameraArea").style.display = "none";
}

/**
 * Issue Fix 3: Upload handler with non-blocking fallback notice when AI tagging is unavailable.
 */
async function handleUploadSubmit(e) {
  e.preventDefault();
  const submitBtn = document.getElementById("btnSubmitUpload");
  const fileInput = document.getElementById("fileInput");

  let fetchOptions;
  if (state.capturedBase64) {
    fetchOptions = {
      method: "POST",
      headers: getApiHeaders(true),
      body: JSON.stringify({
        image_base64: state.capturedBase64,
        filename: "camera_garment.jpg",
      }),
    };
  } else if (fileInput.files && fileInput.files[0]) {
    const formData = new FormData();
    formData.append("image", fileInput.files[0]);
    fetchOptions = {
      method: "POST",
      headers: getApiHeaders(false),
      body: formData,
    };
  } else {
    showToast("Please select or capture a garment photo first.", "error");
    return;
  }

  const originalBtnHtml = submitBtn.innerHTML;
  submitBtn.disabled = true;
  submitBtn.innerHTML = `<span class="spinner"></span> Analyzing with Gemini...`;

  try {
    const res = await fetch("/api/upload", fetchOptions);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload failed");

    if (data.fallback_used || data.engine === "fallback-unassigned") {
      showToast(
        data.notice || "AI tagging unavailable. Defaults set to Unassigned — please edit manually.",
        "warning"
      );
    } else {
      showToast(`Added "${data.item.type}" (${data.engine})`);
    }

    closeModal("uploadModal");
    resetUploadForm();
    await fetchWardrobe();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = originalBtnHtml;
  }
}

function resetUploadForm() {
  stopCameraStream();
  state.capturedBase64 = null;
  const fileInput = document.getElementById("fileInput");
  if (fileInput) fileInput.value = "";
  const previewContainer = document.getElementById("previewContainer");
  if (previewContainer) previewContainer.style.display = "none";
}

// =========================================================================
// Feature 1: "Should I Buy It?" In-Store Evaluator Logic
// =========================================================================

async function switchEvalMode(mode) {
  state.evalMode = mode;
  const tabFile = document.getElementById("evalTabFileMode");
  const tabCam = document.getElementById("evalTabCameraMode");
  const dropzone = document.getElementById("evalDropzoneArea");
  const cameraArea = document.getElementById("evalCameraArea");

  if (mode === "camera") {
    tabCam.className = "btn btn-sm btn-primary";
    tabFile.className = "btn btn-sm btn-outline";
    dropzone.style.display = "none";
    cameraArea.style.display = "flex";
    try {
      state.evalCameraStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
        audio: false,
      });
      const video = document.getElementById("evalCameraStream");
      video.srcObject = state.evalCameraStream;
    } catch (err) {
      showToast("Webcam stream unavailable. Use Photo Upload / Mobile Camera instead.", "warning");
      switchEvalMode("file");
    }
  } else {
    stopEvalCameraStream();
    tabFile.className = "btn btn-sm btn-primary";
    tabCam.className = "btn btn-sm btn-outline";
    dropzone.style.display = "block";
    cameraArea.style.display = "none";
  }
}

function captureEvalCameraFrame() {
  const video = document.getElementById("evalCameraStream");
  if (!video || !video.videoWidth) return;
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0);
  state.evalCapturedBase64 = canvas.toDataURL("image/jpeg", 0.9);
  state.evalPreviewDataUrl = state.evalCapturedBase64;

  const previewContainer = document.getElementById("evalPreviewContainer");
  const previewImg = document.getElementById("evalImagePreview");
  previewImg.src = state.evalCapturedBase64;
  previewContainer.style.display = "block";
  stopEvalCameraStream();
  document.getElementById("evalCameraArea").style.display = "none";
}

function resetEvaluatorForm() {
  stopEvalCameraStream();
  state.evalCapturedBase64 = null;
  state.evalPreviewDataUrl = null;
  const evalFileInput = document.getElementById("evalFileInput");
  if (evalFileInput) evalFileInput.value = "";
  const previewContainer = document.getElementById("evalPreviewContainer");
  if (previewContainer) previewContainer.style.display = "none";
  const resultsSec = document.getElementById("evalResultsSection");
  if (resultsSec) resultsSec.style.display = "none";
}

async function handleEvaluatorSubmit(e) {
  e.preventDefault();
  const submitBtn = document.getElementById("btnSubmitEvaluation");
  const evalFileInput = document.getElementById("evalFileInput");

  let fetchOptions;
  if (state.evalCapturedBase64) {
    fetchOptions = {
      method: "POST",
      headers: getApiHeaders(true),
      body: JSON.stringify({
        image_base64: state.evalCapturedBase64,
        filename: "in_store_candidate.jpg",
        catalog: state.items,
      }),
    };
  } else if (evalFileInput.files && evalFileInput.files[0]) {
    const formData = new FormData();
    formData.append("image", evalFileInput.files[0]);
    formData.append("catalog", JSON.stringify(state.items));
    fetchOptions = {
      method: "POST",
      headers: getApiHeaders(false),
      body: formData,
    };
  } else {
    showToast("Please upload or capture a photo of the candidate item first.", "error");
    return;
  }

  const originalBtnHtml = submitBtn.innerHTML;
  submitBtn.disabled = true;
  submitBtn.innerHTML = `<span class="spinner"></span> Evaluating Against Closet...`;

  try {
    const res = await fetch("/api/evaluate-purchase", fetchOptions);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Purchase evaluation failed");

    renderEvaluationResults(data);
    showToast(`Verdict: ${data.decision} — ${data.total_outfit_count} outfit combinations found.`);
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = originalBtnHtml;
  }
}

function resolveClosetItemById(rawId) {
  const numId = Number(String(rawId).replace(/^item_/i, "").replace(/^#/, ""));
  return state.items.find((it) => it.id === numId) || null;
}

function renderEvaluationResults(data) {
  const resultsSec = document.getElementById("evalResultsSection");
  if (!resultsSec) return;

  // 1. Recommendation Badge
  const badgeEl = document.getElementById("evalDecisionBadge");
  const rawDecision = String(data.decision || "BUY").toUpperCase();
  badgeEl.textContent = rawDecision;
  badgeEl.className = "eval-badge";
  if (rawDecision === "BUY") {
    badgeEl.classList.add("decision-buy");
  } else if (rawDecision === "PASS" || rawDecision === "DON'T BUY") {
    badgeEl.classList.add("decision-pass");
  } else {
    badgeEl.classList.add("decision-think");
  }

  // 2. Candidate Metadata Pills
  const cand = data.candidate_item || {};
  const metaContainer = document.getElementById("evalCandidateMeta");
  const swatchHex = resolveColorHex(cand.color || "");
  metaContainer.innerHTML = `
    <span class="meta-pill">${escapeHtml(cand.category || "Outerwear")}</span>
    <span class="meta-pill">
      <span class="color-dot" style="background:${swatchHex};"></span>
      #${escapeHtml(cand.color || "neutral")}
    </span>
    <span class="meta-pill">fit: ${escapeHtml(cand.fit || "relaxed")}</span>
    <span class="meta-pill">vibe: ${escapeHtml(cand.aesthetic || "contemporary")}</span>
  `;

  // 3. Reasoning Summary
  const summaryEl = document.getElementById("evalDecisionSummary");
  summaryEl.textContent = data.decision_summary || "";

  // 4. Outfit Potential Metric
  const countEl = document.getElementById("evalOutfitCount");
  countEl.textContent = data.total_outfit_count ?? 0;

  // 5. Similarity / Redundancy Alert
  const redAlert = document.getElementById("evalRedundancyAlert");
  const redIcon = document.getElementById("evalRedundancyIcon");
  const redText = document.getElementById("evalRedundancyText");
  if (data.is_redundant || (data.redundancy_notes && String(data.redundancy_notes).trim())) {
    redAlert.className = "eval-redundancy-alert is-redundant";
    redIcon.textContent = "⚠️";
    redText.textContent =
      data.redundancy_notes || "Similar pieces already exist in your wardrobe catalog.";
  } else {
    redAlert.className = "eval-redundancy-alert not-redundant";
    redIcon.textContent = "✓";
    redText.textContent = "No redundant overlap — this piece adds fresh versatility to your closet.";
  }

  // 6. Top 3 Outfits Visual Cards
  const outfitsGrid = document.getElementById("evalTopOutfitsGrid");
  outfitsGrid.innerHTML = "";
  const candidateThumbSrc =
    data.candidate_image_url ||
    state.evalPreviewDataUrl ||
    document.getElementById("evalImagePreview")?.src ||
    "";

  const topOutfits = Array.isArray(data.top_outfits) ? data.top_outfits.slice(0, 3) : [];
  if (topOutfits.length === 0) {
    outfitsGrid.innerHTML = `
      <div style="grid-column: 1 / -1; padding: 16px; background: var(--bg-canvas); border-radius: var(--radius-sm); font-size: 0.82rem; color: var(--text-secondary);">
        Add more garments to your closet catalog to preview complete outfit pairings with this candidate piece.
      </div>
    `;
  } else {
    topOutfits.forEach((outfit, idx) => {
      // Resolve existing closet items from either hydrated `existing_items` or `existing_item_ids`
      let matchedItems = Array.isArray(outfit.existing_items) && outfit.existing_items.length
        ? outfit.existing_items
        : (outfit.existing_item_ids || []).map(resolveClosetItemById).filter(Boolean);

      const candidateTileHtml = candidateThumbSrc
        ? `
          <div class="eval-thumb-tile" title="Candidate: ${escapeHtml(cand.color || '')} ${escapeHtml(cand.category || '')}">
            <img src="${escapeHtml(candidateThumbSrc)}" alt="Candidate Item" />
            <div class="eval-thumb-caption">★ NEW (${escapeHtml(cand.category || "Piece")})</div>
          </div>
        `
        : "";

      const existingTilesHtml = matchedItems
        .map(
          (item) => `
          <div class="eval-thumb-tile" title="#${item.id} ${escapeHtml(item.type)} (${escapeHtml(item.color)})">
            <img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.type)}" loading="lazy" />
            <div class="eval-thumb-caption">#${item.id} ${escapeHtml(item.type)}</div>
          </div>
        `
        )
        .join("");

      const card = document.createElement("div");
      card.className = "eval-outfit-card";
      card.innerHTML = `
        <div class="eval-outfit-header">
          <div class="eval-outfit-vibe">TOP LOOK 0${idx + 1} // ${escapeHtml(outfit.vibe || "Casual")}</div>
          <div class="eval-outfit-title">"${escapeHtml(outfit.title || `Styled Combo #${idx + 1}`)}"</div>
        </div>
        <div class="eval-outfit-thumbs">
          ${candidateTileHtml}
          ${existingTilesHtml}
        </div>
        <div class="eval-outfit-tips">
          ${escapeHtml(outfit.styling_tips || "")}
        </div>
      `;
      outfitsGrid.appendChild(card);
    });
  }

  resultsSec.style.display = "flex";
}

async function seedDemoCapsule() {
  const btn = document.getElementById("btnSeedDemo");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch("/api/seed-demo", {
      method: "POST",
      headers: getApiHeaders(true),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to seed studio capsule");
    showToast(data.message);
    await fetchWardrobe();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function closeModal(modalId) {
  if (modalId === "uploadModal") resetUploadForm();
  if (modalId === "evaluatorModal") stopEvalCameraStream();
  const el = document.getElementById(modalId);
  if (el) el.classList.remove("open");
}

document.addEventListener("DOMContentLoaded", () => {
  checkEngineStatus();
  fetchWardrobe();

  document.getElementById("categoryTabs")?.addEventListener("click", (e) => {
    const tab = e.target.closest(".cat-tab");
    if (!tab) return;
    document.querySelectorAll(".cat-tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    state.activeCategory = tab.dataset.category || "all";
    fetchWardrobe();
  });

  let searchTimer = null;
  document.getElementById("searchInput")?.addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.searchQuery = e.target.value;
      fetchWardrobe();
    }, 200);
  });

  document.getElementById("sortSelect")?.addEventListener("change", (e) => {
    state.sortBy = e.target.value;
    fetchWardrobe();
  });

  document.getElementById("productGrid")?.addEventListener("click", (e) => {
    const wearBtn = e.target.closest("[data-action='wear']");
    if (wearBtn) {
      markWornToday(wearBtn.dataset.id);
      return;
    }

    const editBtn = e.target.closest("[data-action='open-edit']");
    if (editBtn) {
      openEditModal(editBtn.dataset.id);
      return;
    }

    const inlineEl = e.target.closest("[data-inline-field]");
    if (inlineEl) {
      startInlineEdit(inlineEl, inlineEl.dataset.id, inlineEl.dataset.inlineField);
    }
  });

  // Upload Modal Triggers
  const openUpload = () => {
    resetUploadForm();
    switchUploadMode("file");
    document.getElementById("uploadModal")?.classList.add("open");
  };
  document.getElementById("btnOpenUpload")?.addEventListener("click", openUpload);
  document.getElementById("btnEmptyUpload")?.addEventListener("click", openUpload);

  // In-Store Purchase Evaluator Modal Triggers (Feature 1)
  const openEvaluator = () => {
    resetEvaluatorForm();
    switchEvalMode("file");
    document.getElementById("evaluatorModal")?.classList.add("open");
  };
  document.getElementById("btnOpenEvaluator")?.addEventListener("click", openEvaluator);
  document.getElementById("btnBannerEvaluator")?.addEventListener("click", openEvaluator);

  document.getElementById("btnSeedDemo")?.addEventListener("click", seedDemoCapsule);
  document.getElementById("btnEmptySeed")?.addEventListener("click", seedDemoCapsule);

  // Upload Mode & Dropzone Listeners
  document.getElementById("tabFileMode")?.addEventListener("click", () => switchUploadMode("file"));
  document.getElementById("tabCameraMode")?.addEventListener("click", () => switchUploadMode("camera"));
  document.getElementById("btnCapturePhoto")?.addEventListener("click", captureCameraFrame);

  const dropzone = document.getElementById("dropzoneArea");
  const fileInput = document.getElementById("fileInput");

  dropzone?.addEventListener("click", () => fileInput?.click());
  dropzone?.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone?.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone?.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      fileInput.files = e.dataTransfer.files;
      previewSelectedFile(e.dataTransfer.files[0]);
    }
  });

  fileInput?.addEventListener("change", () => {
    if (fileInput.files && fileInput.files[0]) {
      previewSelectedFile(fileInput.files[0]);
    }
  });

  function previewSelectedFile(file) {
    state.capturedBase64 = null;
    const reader = new FileReader();
    reader.onload = (ev) => {
      document.getElementById("imagePreview").src = ev.target.result;
      document.getElementById("previewContainer").style.display = "block";
    };
    reader.readAsDataURL(file);
  }

  // Evaluator Mode & Dropzone Listeners (Feature 1)
  document.getElementById("evalTabFileMode")?.addEventListener("click", () => switchEvalMode("file"));
  document.getElementById("evalTabCameraMode")?.addEventListener("click", () => switchEvalMode("camera"));
  document.getElementById("btnEvalCapturePhoto")?.addEventListener("click", captureEvalCameraFrame);

  const evalDropzone = document.getElementById("evalDropzoneArea");
  const evalFileInput = document.getElementById("evalFileInput");

  evalDropzone?.addEventListener("click", () => evalFileInput?.click());
  evalDropzone?.addEventListener("dragover", (e) => {
    e.preventDefault();
    evalDropzone.classList.add("dragover");
  });
  evalDropzone?.addEventListener("dragleave", () => evalDropzone.classList.remove("dragover"));
  evalDropzone?.addEventListener("drop", (e) => {
    e.preventDefault();
    evalDropzone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      evalFileInput.files = e.dataTransfer.files;
      previewEvalSelectedFile(e.dataTransfer.files[0]);
    }
  });

  evalFileInput?.addEventListener("change", () => {
    if (evalFileInput.files && evalFileInput.files[0]) {
      previewEvalSelectedFile(evalFileInput.files[0]);
    }
  });

  function previewEvalSelectedFile(file) {
    state.evalCapturedBase64 = null;
    const reader = new FileReader();
    reader.onload = (ev) => {
      state.evalPreviewDataUrl = ev.target.result;
      document.getElementById("evalImagePreview").src = ev.target.result;
      document.getElementById("evalPreviewContainer").style.display = "block";
    };
    reader.readAsDataURL(file);
  }

  document.getElementById("uploadForm")?.addEventListener("submit", handleUploadSubmit);
  document.getElementById("evaluatorForm")?.addEventListener("submit", handleEvaluatorSubmit);
  document.getElementById("editForm")?.addEventListener("submit", handleEditSubmit);
  document.getElementById("btnDeleteItem")?.addEventListener("click", handleDeleteItem);

  document.getElementById("btnApiKeyModal")?.addEventListener("click", () => {
    document.getElementById("inputGeminiKey").value =
      localStorage.getItem("SMART_CLOSET_GEMINI_KEY") || "";
    document.getElementById("apiKeyModal")?.classList.add("open");
  });

  document.getElementById("btnSaveApiKey")?.addEventListener("click", () => {
    const val = document.getElementById("inputGeminiKey").value.trim();
    if (val) {
      localStorage.setItem("SMART_CLOSET_GEMINI_KEY", val);
      showToast("Gemini API key saved in browser session.");
    }
    closeModal("apiKeyModal");
    checkEngineStatus();
  });

  document.getElementById("btnClearApiKey")?.addEventListener("click", () => {
    localStorage.removeItem("SMART_CLOSET_GEMINI_KEY");
    document.getElementById("inputGeminiKey").value = "";
    showToast("Cleared browser-stored Gemini API key.");
    closeModal("apiKeyModal");
    checkEngineStatus();
  });

  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => closeModal(btn.dataset.close));
  });
});