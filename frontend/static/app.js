/* ═══════════════════════════════════════════════════
   TeraStream — Frontend Application Logic
   HLS.js player • Token management • Secure API calls
═══════════════════════════════════════════════════ */

'use strict';

// ── State ──────────────────────────────────────────
const State = {
  token: null,
  tokenExpiresAt: null,   // Unix ms
  tokenTtl: 600,          // total seconds
  hls: null,
  countdownInterval: null,
};

// ── DOM references ─────────────────────────────────
const $ = id => document.getElementById(id);

const DOM = {
  url:            () => $('teraboxUrl'),
  streamBtn:      () => $('streamBtn'),
  hero:           () => $('hero'),
  loadingSection: () => $('loadingSection'),
  playerSection:  () => $('playerSection'),
  loadingText:    () => $('loadingText'),
  errorBanner:    () => $('errorBanner'),
  errorMsg:       () => $('errorMsg'),
  // Meta
  metaThumbWrap:  () => $('metaThumbWrap'),
  metaThumb:      () => $('metaThumb'),
  metaTitle:      () => $('metaTitle'),
  metaSize:       () => $('metaSize'),
  metaType:       () => $('metaType'),
  metaTtl:        () => $('metaTtl'),
  // Player
  videoPlayer:    () => $('videoPlayer'),
  playerOverlay:  () => $('playerOverlay'),
  // Token bar
  tokenCountdown: () => $('tokenCountdown'),
  tokenFill:      () => $('tokenFill'),
};

// ── Helpers ────────────────────────────────────────
function show(el)  { el.style.display = ''; el.style.removeProperty('display'); el.removeAttribute('hidden'); }
function hide(el)  { el.style.display = 'none'; }
function setLoading(msg) {
  if (DOM.loadingText()) DOM.loadingText().textContent = msg || 'Fetching stream link…';
}

function showError(msg) {
  const banner = DOM.errorBanner();
  const msgEl  = DOM.errorMsg();
  if (!banner || !msgEl) return;
  msgEl.textContent = msg;
  show(banner);
  // auto-hide after 8s
  setTimeout(() => hide(banner), 8000);
}
function hideError() {
  if (DOM.errorBanner()) hide(DOM.errorBanner());
}

function formatBytes(bytes) {
  if (!bytes || isNaN(+bytes)) return 'Unknown size';
  const n = +bytes;
  if (n < 1024)         return `${n} B`;
  if (n < 1024 ** 2)    return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3)    return `${(n / 1024**2).toFixed(1)} MB`;
  return `${(n / 1024**3).toFixed(2)} GB`;
}

function formatCountdown(sec) {
  if (sec <= 0) return 'Expired';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `expires in ${m}m ${String(s).padStart(2, '0')}s`;
}

// ── Input validation (client-side, mirrors server) ─
const TERABOX_PATTERN = /^https?:\/\/(www\.)?(terabox\.com|1024terabox\.com|teraboxapp\.com|terabox\.fun|tb-video\.com|nephobox\.com|4funbox\.com|mirrobox\.com|momerybox\.com|tibibox\.com)\//i;

function isValidTeraboxUrl(url) {
  try {
    const u = new URL(url);
    if (!['http:', 'https:'].includes(u.protocol)) return false;
    return TERABOX_PATTERN.test(url);
  } catch {
    return false;
  }
}

// ── Token countdown ticker ─────────────────────────
function startCountdown() {
  clearInterval(State.countdownInterval);
  const fill = DOM.tokenFill();

  State.countdownInterval = setInterval(() => {
    const remaining = Math.max(0, Math.round((State.tokenExpiresAt - Date.now()) / 1000));
    const pct = (remaining / State.tokenTtl) * 100;

    if (DOM.tokenCountdown()) DOM.tokenCountdown().textContent = formatCountdown(remaining);
    if (fill) fill.style.width = `${pct}%`;

    // Color shift: green → yellow → red
    if (pct > 50)      fill && (fill.style.background = 'linear-gradient(90deg,#22c55e,#4ade80)');
    else if (pct > 20) fill && (fill.style.background = 'linear-gradient(90deg,#eab308,#facc15)');
    else               fill && (fill.style.background = 'linear-gradient(90deg,#e8441a,#ff6b35)');

    if (remaining === 0) {
      clearInterval(State.countdownInterval);
      handleTokenExpired();
    }
  }, 1000);
}

function handleTokenExpired() {
  showError('Your streaming token has expired. Please load the video again.');
  destroyHls();
  if (DOM.metaTtl()) {
    DOM.metaTtl().textContent = '⏱ Expired';
    DOM.metaTtl().style.color = '#e8441a';
  }
}

// ── HLS / Video setup ──────────────────────────────
function destroyHls() {
  if (State.hls) {
    State.hls.destroy();
    State.hls = null;
  }
}

function setupPlayer(streamSrc, isHls) {
  const video = DOM.videoPlayer();
  if (!video) return;

  destroyHls();

  // Click overlay to hide it
  const overlay = DOM.playerOverlay();
  if (overlay) {
    overlay.addEventListener('click', () => {
      video.paused ? video.play() : video.pause();
    });
    video.addEventListener('play',  () => overlay.classList.add('hidden'));
    video.addEventListener('pause', () => overlay.classList.remove('hidden'));
  }

  if (isHls && Hls.isSupported()) {
    const hls = new Hls({
      enableWorker: true,
      lowLatencyMode: false,
      backBufferLength: 90,
      maxMaxBufferLength: 600,
      startLevel: -1, // auto
    });
    State.hls = hls;

    hls.loadSource(streamSrc);
    hls.attachMedia(video);

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      console.log('[HLS] Manifest parsed, playing');
      video.play().catch(e => console.warn('[Autoplay]', e));
    });

    hls.on(Hls.Events.ERROR, (event, data) => {
      if (data.fatal) {
        switch (data.type) {
          case Hls.ErrorTypes.NETWORK_ERROR:
            console.warn('[HLS] Network error, trying to recover');
            hls.startLoad();
            break;
          case Hls.ErrorTypes.MEDIA_ERROR:
            console.warn('[HLS] Media error, recovering');
            hls.recoverMediaError();
            break;
          default:
            console.error('[HLS] Fatal error', data);
            showError('Streaming error occurred. The link may have expired.');
            destroyHls();
        }
      }
    });

  } else if (video.canPlayType('application/vnd.apple.mpegurl') && isHls) {
    // Native HLS (Safari)
    video.src = streamSrc;
    video.play().catch(e => console.warn('[Native HLS autoplay]', e));

  } else {
    // Direct video (mp4 etc)
    video.src = streamSrc;
    video.play().catch(e => console.warn('[Direct autoplay]', e));
  }
}

// ── UI transitions ─────────────────────────────────
function showPhase(phase) {
  // phase: 'hero' | 'loading' | 'player'
  hide(DOM.hero());
  hide(DOM.loadingSection());
  hide(DOM.playerSection());

  if (phase === 'hero') {
    show(DOM.hero());
  } else if (phase === 'loading') {
    show(DOM.loadingSection());
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } else if (phase === 'player') {
    show(DOM.playerSection());
    setTimeout(() => {
      DOM.playerSection().scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
  }
}

// ── Main fetch + stream handler ────────────────────
async function handleStream() {
  hideError();
  const urlInput = DOM.url();
  const btn = DOM.streamBtn();
  if (!urlInput || !btn) return;

  const url = urlInput.value.trim();

  // Client-side validation
  if (!url) {
    showError('Please enter a TeraBox share link.');
    urlInput.focus();
    return;
  }
  if (!isValidTeraboxUrl(url)) {
    showError('Invalid URL. Please enter a valid TeraBox share link (e.g. https://terabox.com/s/...)');
    urlInput.focus();
    return;
  }

  // Start loading
  btn.disabled = true;
  showPhase('loading');
  setLoading('Verifying link…');

  // Animate loading messages
  const msgs = [
    'Verifying link…',
    'Calling stream API…',
    'Generating secure token…',
    'Almost ready…',
  ];
  let mi = 0;
  const msgInterval = setInterval(() => {
    mi = (mi + 1) % msgs.length;
    setLoading(msgs[mi]);
  }, 1800);

  try {
    const resp = await fetch('/api/fetch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    clearInterval(msgInterval);

    if (resp.status === 429) {
      showPhase('hero');
      showError('Rate limit exceeded. Please wait a minute and try again.');
      btn.disabled = false;
      return;
    }

    const data = await resp.json();

    if (!resp.ok) {
      showPhase('hero');
      showError(data.error || 'Failed to fetch stream. Please try again.');
      btn.disabled = false;
      return;
    }

    // Store token state
    State.token       = data.token;
    State.tokenTtl    = data.expires_in || 600;
    State.tokenExpiresAt = Date.now() + State.tokenTtl * 1000;

    // Populate meta UI
    const meta = data.meta || {};
    if (DOM.metaTitle()) DOM.metaTitle().textContent = meta.title || 'Unknown';
    if (DOM.metaSize())  DOM.metaSize().textContent  = typeof meta.size === 'number' ? formatBytes(meta.size) : (meta.size || '—');
    if (DOM.metaType())  DOM.metaType().textContent  = meta.is_hls ? '🎬 HLS Stream' : '📹 Direct';
    if (DOM.metaTtl())   DOM.metaTtl().textContent   = `⏱ ${State.tokenTtl / 60}min token`;

    if (meta.thumbnail && DOM.metaThumb()) {
      DOM.metaThumb().src = meta.thumbnail;
      show(DOM.metaThumbWrap());
    }

    // Build proxy stream URL
    const streamSrc = `/stream/${State.token}`;
    setupPlayer(streamSrc, meta.is_hls !== false);
    startCountdown();
    showPhase('player');

  } catch (err) {
    clearInterval(msgInterval);
    console.error('[Fetch error]', err);
    showPhase('hero');
    showError('Network error. Please check your connection and try again.');
  } finally {
    btn.disabled = false;
  }
}

// ── Reset / New Video ──────────────────────────────
function resetPlayer() {
  destroyHls();
  clearInterval(State.countdownInterval);
  State.token = null;
  State.tokenExpiresAt = null;

  const video = DOM.videoPlayer();
  if (video) { video.pause(); video.src = ''; video.load(); }

  const overlay = DOM.playerOverlay();
  if (overlay) overlay.classList.remove('hidden');

  if (DOM.url()) DOM.url().value = '';
  if (DOM.metaThumbWrap()) hide(DOM.metaThumbWrap());

  showPhase('hero');
  hideError();
  setTimeout(() => DOM.url() && DOM.url().focus(), 400);
}

// ── PiP & Fullscreen ───────────────────────────────
async function togglePiP() {
  const video = DOM.videoPlayer();
  if (!video) return;
  try {
    if (document.pictureInPictureElement) {
      await document.exitPictureInPicture();
    } else if (document.pictureInPictureEnabled) {
      await video.requestPictureInPicture();
    }
  } catch (e) {
    console.warn('[PiP]', e);
  }
}

function toggleFullscreen() {
  const container = document.querySelector('.player-container');
  if (!container) return;
  if (!document.fullscreenElement) {
    container.requestFullscreen().catch(e => console.warn('[Fullscreen]', e));
  } else {
    document.exitFullscreen();
  }
}

// ── Keyboard shortcut ──────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  const video = DOM.videoPlayer();
  if (!video) return;

  switch (e.key) {
    case ' ':
    case 'k':
      e.preventDefault();
      video.paused ? video.play() : video.pause();
      break;
    case 'f':
      toggleFullscreen();
      break;
    case 'ArrowRight':
      video.currentTime = Math.min(video.duration || 0, video.currentTime + 10);
      break;
    case 'ArrowLeft':
      video.currentTime = Math.max(0, video.currentTime - 10);
      break;
    case 'm':
      video.muted = !video.muted;
      break;
  }
});

// ── Enter key on input ─────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const urlInput = DOM.url();
  if (urlInput) {
    urlInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') handleStream();
    });

    // Paste auto-clean
    urlInput.addEventListener('paste', e => {
      setTimeout(() => {
        urlInput.value = urlInput.value.trim();
      }, 0);
    });
  }

  // Initial phase
  showPhase('hero');
});
