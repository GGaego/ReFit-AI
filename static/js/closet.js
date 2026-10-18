/**
 * Smart Closet & Style Engine — Page 1: My Wardrobe (closet.js)
 * Strictly Vanilla ES6+ JavaScript
 */

const state = {
  items: [],
  activeCategory: "all",
  searchQuery: "",
  sortBy: "newest",
  uploadMode: "file", // "file" | "camera"
  capturedBase64: null,
  cameraStream: null,
};

function resolveColorHex(colorName = "") {
  const c = colorName.toLowerCase();
  if (c.includes("black") || c.includes("espresso") || c.includes("onyx")) return "#18181B";
  if (c.includes("ecru") || c.includes("cream") || c.includes("ivory") || c.includes("bone")) return "#EFECE6";
  if (c.includes("white") || c.includes("chalk")) return "#FFFFFF";
  if (c.includes("indigo") || c.includes("navy") || c.includes("midnight")) return "#1E293B";
  if (c.includes("ice blue") || c.includes("light blue") || c.includes("sky")) return "#BFDBFE";
  if (c.includes("blue") || c.includes("cobalt")) return "#2563EB";
  if (c.includes("tobacco") || c.includes("cognac") || c.includes("brown") || c.includes("camel") || c.includes("tan")) return "#8B5E34";
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
    showToast("Failed to load wardrobe catalog.", true);
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
  ["all", "tops", "outerwear", "bottoms", "footwear", "accessories"].forEach((cat) => {
    const badge = document.getElementById(`count-${cat}`);
    if (badge) badge.textContent = counts[cat] ?? 0;
  });
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
    card.className = "product-card";
    card.dataset.id = item.id;

    const wornInfo = item.last_worn_status || { label: "Never worn", state: "never" };
    const sizeLabel = item.size ? `Size: ${item.size}` : "Size: Unassigned";
    const sizeClass = item.size ? "" : "size-unassigned";
    const swatchHex = resolveColorHex(item.color);

    card.innerHTML = `
      <div class="card-media">
        <img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.type)}" loading="lazy" />
        <div class="card-top-badges">
          <span class="sku-badge">#${item.id} // ${escapeHtml(item.category)}</span>
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
            title="Click to edit garment type inline"
          >${escapeHtml(item.type)}</h2>
        </div>

        <div class="metadata-pills">
          <span
            class="meta-pill"
            data-inline-field="color"
            data-id="${item.id}"
            title="Click to edit color nuance"
          >
            <span class="color-dot" style="background:${swatchHex};"></span>
            #${escapeHtml(item.color)}
          </span>

          <span
            class="meta-pill"
            data-inline-field="fit"
            data-id="${item.id}"
            title="Click to edit fit / silhouette"
          >fit: ${escapeHtml(item.fit)}</span>

          <span
            class="meta-pill"
            data-inline-field="category"
            data-id="${item.id}"
            title="Click to edit category"
          >${escapeHtml(item.category)}</span>

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
        >${escapeHtml(item.description || "No description provided.")}</p>
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

function startInlineEdit(targetEl, itemId, field) {
  if (targetEl.querySelector("input, select")) return;
  const item = state.items.find((i) => i.id === Number(itemId));
  if (!item) return;

  const currentVal = item[field] || "";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "inline-edit-input";
  input.value = currentVal;
  input.placeholder = field === "size" ? "e.g. M, L, 32" : field;

  const originalHtml = targetEl.innerHTML;
  targetEl.innerHTML = "";
  targetEl.appendChild(input);
  input.focus();
  input.select();

  const commitChange = async () => {
    const newVal = input.value.trim();
    if (newVal === currentVal) {
      targetEl.innerHTML = originalHtml;
      return;
    }
    try {
      const res = await fetch(`/api/clothes/${itemId}`, {
        method: "PUT",
        headers: getApiHeaders(true),
        body: JSON.stringify({ [field]: newVal || null }),
      });
      if (!res.ok) throw new Error("Update failed");
      showToast(`Updated ${field} for #${itemId}`);
      await fetchWardrobe();
    } catch (err) {
      targetEl.innerHTML = originalHtml;
      showToast(`Could not update ${field}.`, true);
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
    showToast(err.message, true);
  }
}

function openEditModal(itemId) {
  const item = state.items.find((i) => i.id === Number(itemId));
  if (!item) return;

  document.getElementById("editItemId").value = item.id;
  document.getElementById("editType").value = item.type || "";
  document.getElementById("editCategory").value = item.category || "tops";
  document.getElementById("editColor").value = item.color || "";
  document.getElementById("editFit").value = item.fit || "";
  document.getElementById("editSize").value = item.size || "";
  document.getElementById("editLastWorn").value = item.last_worn ? item.last_worn.slice(0, 10) : "";
  document.getElementById("editDescription").value = item.description || "";

  document.getElementById("editModal").classList.add("open");
}

async function handleEditSubmit(e) {
  e.preventDefault();
  const itemId = document.getElementById("editItemId").value;
  const payload = {
    type: document.getElementById("editType").value.trim(),
    category: document.getElementById("editCategory").value,
    color: document.getElementById("editColor").value.trim(),
    fit: document.getElementById("editFit").value.trim(),
    size: document.getElementById("editSize").value.trim() || null,
    last_worn: document.getElementById("editLastWorn").value || null,
    description: document.getElementById("editDescription").value.trim(),
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
    showToast("Garment metadata saved.");
    await fetchWardrobe();
  } catch (err) {
    showToast(err.message, true);
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
    showToast(err.message, true);
  }
}

function stopCameraStream() {
  if (state.cameraStream) {
    state.cameraStream.getTracks().forEach((track) => track.stop());
    state.cameraStream = null;
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
      showToast("Camera access unavailable. Using file upload instead.", true);
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
    showToast("Please select or capture a garment photo first.", true);
    return;
  }

  const originalBtnHtml = submitBtn.innerHTML;
  submitBtn.disabled = true;
  submitBtn.innerHTML = `<span class="spinner"></span> Analyzing with Gemini...`;

  try {
    const res = await fetch("/api/upload", fetchOptions);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload failed");

    showToast(`Added "${data.item.type}" (${data.engine})`);
    closeModal("uploadModal");
    resetUploadForm();
    await fetchWardrobe();
  } catch (err) {
    showToast(err.message, true);
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
    showToast(err.message, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function closeModal(modalId) {
  if (modalId === "uploadModal") resetUploadForm();
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

  const openUpload = () => {
    resetUploadForm();
    switchUploadMode("file");
    document.getElementById("uploadModal")?.classList.add("open");
  };
  document.getElementById("btnOpenUpload")?.addEventListener("click", openUpload);
  document.getElementById("btnEmptyUpload")?.addEventListener("click", openUpload);

  document.getElementById("btnSeedDemo")?.addEventListener("click", seedDemoCapsule);
  document.getElementById("btnEmptySeed")?.addEventListener("click", seedDemoCapsule);

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

  document.getElementById("uploadForm")?.addEventListener("submit", handleUploadSubmit);
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
