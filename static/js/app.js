/* ── TeraStream Frontend ──────────────────────────────────────────────────── */

let currentToken   = null;
let expireInterval = null;
let hlsInstance    = null;

const $ = id => document.getElementById(id);

// ── Helpers ───────────────────────────────────────────────────────────────────

function showError(msg) {
  $("errorBox").style.display = "flex";
  $("errorMsg").textContent   = msg;
}

function hideError() {
  $("errorBox").style.display = "none";
}

function setLoading(on, msg = "Fetching secure stream link…") {
  $("loadingSection").style.display = on ? "flex" : "none";
  $("loadingText").textContent       = msg;
  $("streamBtn").disabled            = on;
}

function setStatus(type, text) {
  const dot  = $("statusDot");
  const span = $("statusText");
  dot.className  = "status-dot " + type;
  span.textContent = text;
}

function formatBytes(bytes) {
  if (!bytes) return "";
  const b = parseInt(bytes, 10);
  if (isNaN(b)) return bytes;
  if (b < 1024)       return b + " B";
  if (b < 1048576)    return (b / 1024).toFixed(1) + " KB";
  if (b < 1073741824) return (b / 1048576).toFixed(1) + " MB";
  return (b / 1073741824).toFixed(2) + " GB";
}

function formatCountdown(seconds) {
  const m = String(Math.floor(seconds / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}

function startExpireTimer(seconds) {
  clearInterval(expireInterval);
  let remaining = seconds;
  $("expireTimer").textContent = formatCountdown(remaining);
  expireInterval = setInterval(() => {
    remaining--;
    if (remaining <= 0) {
      clearInterval(expireInterval);
      $("expireTimer").textContent = "00:00";
      setStatus("error", "Stream token expired. Please fetch a new link.");
      return;
    }
    $("expireTimer").textContent = formatCountdown(remaining);
  }, 1000);
}

// ── Main fetch handler ────────────────────────────────────────────────────────

async function handleFetch() {
  hideError();
  const url = $("urlInput").value.trim();

  if (!url) {
    showError("Please enter a TeraBox URL.");
    return;
  }

  // Basic client-side validation
  const allowed = [
    "terabox.com", "teraboxapp.com", "1024terabox.com",
    "terabox.fun", "teraboxlink.com", "nephobox.com",
    "4funbox.co", "mirrobox.com", "momerybox.com",
    "freeterabox.com"
  ];
  try {
    const parsed = new URL(url);
    const host   = parsed.hostname.replace(/^www\./, "");
    if (!allowed.includes(host)) {
      showError("URL must be from a supported TeraBox domain.");
      return;
    }
  } catch {
    showError("Please enter a valid URL.");
    return;
  }

  setLoading(true, "Calling secure API…");
  $("playerSection").style.display = "none";

  try {
    const resp = await fetch("/api/fetch", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ url }),
    });

    const data = await resp.json();

    if (!resp.ok) {
      throw new Error(data.error || `Server error ${resp.status}`);
    }

    setLoading(false);
    initPlayer(data);

  } catch (err) {
    setLoading(false);
    showError(err.message || "Network error. Please try again.");
  }
}

// ── Player initialisation ─────────────────────────────────────────────────────

function initPlayer(data) {
  const { token, expires_in, meta } = data;
  currentToken = token;

  // Populate meta
  $("metaTitle").textContent = meta.file_name || "Video";

  if (meta.size) {
    $("metaSize").textContent = formatBytes(meta.size);
    $("metaSize").style.display = "";
  } else {
    $("metaSize").style.display = "none";
  }

  if (meta.thumbnail) {
    const img = $("metaThumb");
    img.src = meta.thumbnail;
    img.style.display = "block";
    $("thumbPlaceholder").style.display = "none";
    img.onerror = () => {
      img.style.display = "none";
      $("thumbPlaceholder").style.display = "grid";
    };
  }

  startExpireTimer(expires_in);

  // Show player section
  $("playerSection").style.display = "block";
  $("playerSection").scrollIntoView({ behavior: "smooth", block: "nearest" });
  $("hero").style.marginBottom = "0";

  // Destroy old HLS
  if (hlsInstance) {
    hlsInstance.destroy();
    hlsInstance = null;
  }

  const video   = $("videoPlayer");
  const spinner = $("videoSpinner");
  const proxyUrl = `/stream/${token}`;

  setStatus("load", "Initialising HLS player…");

  if (Hls.isSupported()) {
    hlsInstance = new Hls({
      enableWorker:       true,
      lowLatencyMode:     false,
      maxBufferLength:    30,
      maxMaxBufferLength: 60,
      xhrSetup: (xhr) => {
        xhr.setRequestHeader("X-Requested-With", "TeraStream");
      },
    });

    hlsInstance.loadSource(proxyUrl);
    hlsInstance.attachMedia(video);

    hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => {
      spinner.classList.add("hidden");
      setStatus("live", "Streaming · HLS");
      video.play().catch(() => {});
    });

    hlsInstance.on(Hls.Events.ERROR, (_, errData) => {
      if (errData.fatal) {
        switch (errData.type) {
          case Hls.ErrorTypes.NETWORK_ERROR:
            setStatus("error", "Network error — retrying…");
            hlsInstance.startLoad();
            break;
          case Hls.ErrorTypes.MEDIA_ERROR:
            setStatus("error", "Media error — recovering…");
            hlsInstance.recoverMediaError();
            break;
          default:
            setStatus("error", "Playback error. Try fetching again.");
            spinner.classList.remove("hidden");
        }
      }
    });

    video.addEventListener("waiting",  () => { spinner.classList.remove("hidden"); setStatus("load", "Buffering…"); });
    video.addEventListener("playing",  () => { spinner.classList.add("hidden");    setStatus("live", "Streaming · HLS"); });
    video.addEventListener("stalled",  () => { setStatus("load", "Connection stalled…"); });
    video.addEventListener("ended",    () => { setStatus("", "Playback complete"); });

  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    // Safari native HLS
    video.src = proxyUrl;
    video.addEventListener("loadedmetadata", () => {
      spinner.classList.add("hidden");
      setStatus("live", "Streaming · Native HLS");
      video.play().catch(() => {});
    });
    video.addEventListener("error", () => {
      setStatus("error", "Playback failed on this browser.");
    });
  } else {
    // Fallback: treat as direct video
    video.src = proxyUrl;
    video.load();
    spinner.classList.add("hidden");
    setStatus("live", "Streaming · Direct");
    video.play().catch(() => {});
  }
}

// ── Reset ─────────────────────────────────────────────────────────────────────

function resetPlayer() {
  if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
  clearInterval(expireInterval);
  currentToken = null;

  const video = $("videoPlayer");
  video.pause();
  video.src = "";

  $("playerSection").style.display = "none";
  $("urlInput").value               = "";
  $("urlInput").focus();
  hideError();
}

// ── Enter key support ─────────────────────────────────────────────────────────
$("urlInput").addEventListener("keydown", e => {
  if (e.key === "Enter") handleFetch();
});

// ── Paste detection ───────────────────────────────────────────────────────────
$("urlInput").addEventListener("paste", () => {
  setTimeout(() => {
    const v = $("urlInput").value.trim();
    if (v.startsWith("http")) handleFetch();
  }, 50);
});
