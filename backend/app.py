"""
TeraStream - Secure TeraBox Video Streaming Backend
Flask application with token-based streaming, rate limiting, and proxy.
"""

import os
import json
import time
import uuid
import hashlib
import logging
import threading
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse, urljoin

import requests
from flask import (
    Flask, request, jsonify, render_template,
    Response, stream_with_context, send_from_directory, abort
)
from flask_cors import CORS

# ─────────────────────────────────────────────
# App Init
# ─────────────────────────────────────────────
app = Flask(
    __name__,
    static_folder="../frontend/static",
    template_folder="../frontend"
)
CORS(app, origins=os.getenv("ALLOWED_ORIGINS", "*"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
SECRET_KEY        = os.getenv("SECRET_KEY", "terastream-secret-change-in-prod")
API_BASE          = "https://web-production-8cdbd.up.railway.app/api/get-links"
TOKEN_TTL         = int(os.getenv("TOKEN_TTL", 600))          # 10 minutes
RATE_LIMIT_MAX    = int(os.getenv("RATE_LIMIT_MAX", 20))      # requests
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", 60))   # seconds
CACHE_TTL         = int(os.getenv("CACHE_TTL", 300))          # 5 min API cache
DATA_DIR          = os.path.join(os.path.dirname(__file__), "data")
USAGE_FILE        = os.path.join(DATA_DIR, "usage.json")
USERS_FILE        = os.path.join(DATA_DIR, "users.json")

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "7555728767:AAH08bccxOloKnYFxEhedlCGnrLF8YPZiKw")
TG_CHAT_ID   = os.getenv("TG_CHAT_ID", "6258915779")

ALLOWED_TERABOX_DOMAINS = {
    "terabox.com", "www.terabox.com",
    "1024terabox.com", "www.1024terabox.com",
    "teraboxapp.com", "www.teraboxapp.com",
    "terabox.fun", "www.terabox.fun",
    "tb-video.com", "www.tb-video.com",
    "nephobox.com", "www.nephobox.com",
    "4funbox.com", "www.4funbox.com",
    "mirrobox.com", "www.mirrobox.com",
    "momerybox.com", "www.momerybox.com",
    "tibibox.com", "www.tibibox.com",
}

# ─────────────────────────────────────────────
# In-memory stores (use Redis in production)
# ─────────────────────────────────────────────
_token_store: dict = {}   # token → {stream_url, meta, expires_at}
_rate_store:  dict = {}   # ip → {count, window_start}
_api_cache:   dict = {}   # url_hash → {data, cached_at}
_store_lock = threading.Lock()

# ─────────────────────────────────────────────
# Helpers – Data persistence
# ─────────────────────────────────────────────
def _ensure_data_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    for fpath, default in [(USAGE_FILE, []), (USERS_FILE, {})]:
        if not os.path.exists(fpath):
            with open(fpath, "w") as f:
                json.dump(default, f)

def _log_usage(event: str, ip: str, extra: dict | None = None):
    try:
        with open(USAGE_FILE, "r+") as f:
            logs = json.load(f)
            logs.append({
                "ts": datetime.utcnow().isoformat(),
                "event": event,
                "ip": ip,
                **(extra or {})
            })
            # Keep last 10,000 entries
            if len(logs) > 10_000:
                logs = logs[-10_000:]
            f.seek(0); f.truncate()
            json.dump(logs, f, indent=2)
    except Exception as e:
        logger.warning(f"Log write failed: {e}")

# ─────────────────────────────────────────────
# Helpers – Validation
# ─────────────────────────────────────────────
def _is_valid_terabox_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.netloc.lower().lstrip("www.")
        # Allow with or without www.
        return any(
            parsed.netloc.lower() == d or parsed.netloc.lower() == f"www.{d}"
            for d in ALLOWED_TERABOX_DOMAINS
        ) or parsed.netloc.lower() in ALLOWED_TERABOX_DOMAINS
    except Exception:
        return False

# ─────────────────────────────────────────────
# Helpers – Rate limiting
# ─────────────────────────────────────────────
def _get_client_ip() -> str:
    return (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "")
        or request.remote_addr
        or "unknown"
    )

def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    with _store_lock:
        bucket = _rate_store.get(ip, {"count": 0, "window_start": now})
        if now - bucket["window_start"] > RATE_LIMIT_WINDOW:
            bucket = {"count": 0, "window_start": now}
        if bucket["count"] >= RATE_LIMIT_MAX:
            _rate_store[ip] = bucket
            return False
        bucket["count"] += 1
        _rate_store[ip] = bucket
        return True

def rate_limited(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        ip = _get_client_ip()
        if not _check_rate_limit(ip):
            return jsonify({"error": "Rate limit exceeded. Try again later.", "code": 429}), 429
        return f(*args, **kwargs)
    return wrapper

# ─────────────────────────────────────────────
# Helpers – Token management
# ─────────────────────────────────────────────
def _create_token(stream_url: str, meta: dict) -> str:
    raw = f"{stream_url}{time.time()}{SECRET_KEY}{uuid.uuid4()}"
    token = hashlib.sha256(raw.encode()).hexdigest()[:32]
    with _store_lock:
        _token_store[token] = {
            "stream_url": stream_url,
            "meta": meta,
            "expires_at": time.time() + TOKEN_TTL,
            "created_at": time.time(),
        }
    return token

def _get_token_data(token: str) -> dict | None:
    with _store_lock:
        data = _token_store.get(token)
        if data and time.time() < data["expires_at"]:
            return data
        if data:
            del _token_store[token]  # cleanup expired
    return None

def _cleanup_tokens():
    """Periodically remove expired tokens."""
    while True:
        time.sleep(120)
        now = time.time()
        with _store_lock:
            expired = [k for k, v in _token_store.items() if now > v["expires_at"]]
            for k in expired:
                del _token_store[k]
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired tokens")

threading.Thread(target=_cleanup_tokens, daemon=True).start()

# ─────────────────────────────────────────────
# Helpers – API cache
# ─────────────────────────────────────────────
def _cache_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()

def _get_cached(url: str) -> dict | None:
    key = _cache_key(url)
    with _store_lock:
        entry = _api_cache.get(key)
        if entry and time.time() - entry["cached_at"] < CACHE_TTL:
            return entry["data"]
        if entry:
            del _api_cache[key]
    return None

def _set_cache(url: str, data: dict):
    key = _cache_key(url)
    with _store_lock:
        _api_cache[key] = {"data": data, "cached_at": time.time()}

# ─────────────────────────────────────────────
# Helpers – Telegram logging (transparent — user is notified via UI notice)
def _notify_telegram(terabox_url: str, ip: str, title: str = ""):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        msg = (
            "📥 *New Stream Request*\n"
            f"🔗 `{terabox_url}`\n"
            f"🎬 {title or 'Unknown'}\n"
            f"🌐 IP: `{ip}`\n"
            f"🕐 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception as e:
        logger.warning(f"Telegram notify failed: {e}")

# Helpers – Anti-hotlink security headers
# ─────────────────────────────────────────────
def _secure_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response

# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("../frontend", "index.html")

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("../frontend/static", filename)


@app.route("/api/fetch", methods=["POST"])
@rate_limited
def api_fetch():
    """
    Accepts { "url": "<terabox_share_url>" }
    Returns { "token": "...", "meta": {...}, "expires_in": 600 }
    """
    ip = _get_client_ip()
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()

    if not url:
        return jsonify({"error": "Missing 'url' parameter."}), 400

    if not _is_valid_terabox_url(url):
        _log_usage("invalid_url", ip, {"url": url[:200]})
        return jsonify({"error": "Invalid URL. Only TeraBox share links are accepted."}), 400

    # Check cache
    cached = _get_cached(url)
    if cached:
        token = _create_token(cached["stream_url"], cached["meta"])
        logger.info(f"[CACHE HIT] ip={ip}")
        return jsonify({"token": token, "meta": cached["meta"], "expires_in": TOKEN_TTL, "cached": True})

    # Call external API
    try:
        api_resp = requests.get(
            API_BASE,
            params={"url": url},
            timeout=20,
            headers={"User-Agent": "TeraStream/1.0"}
        )
        api_resp.raise_for_status()
        api_data = api_resp.json()
    except requests.Timeout:
        _log_usage("api_timeout", ip)
        return jsonify({"error": "API timed out. Please try again."}), 504
    except requests.RequestException as e:
        _log_usage("api_error", ip, {"err": str(e)[:200]})
        return jsonify({"error": "Failed to reach streaming API. Please try again."}), 502
    except ValueError:
        return jsonify({"error": "Invalid response from API."}), 502

    # Extract stream_url ONLY (never expose download_url)
    stream_url = api_data.get("stream_url") or api_data.get("dlink") or ""
    if not stream_url:
        # Try nested structures
        for key in ("data", "result", "response"):
            if isinstance(api_data.get(key), dict):
                stream_url = api_data[key].get("stream_url", "")
                if stream_url:
                    break

    if not stream_url:
        _log_usage("no_stream_url", ip, {"url": url[:200]})
        return jsonify({"error": "No stream URL found for this link. The file may be private or unavailable."}), 404

    # Build meta (safe to expose)
    meta = {
        "title": api_data.get("title") or api_data.get("file_name") or "Video",
        "size":  api_data.get("size") or api_data.get("file_size") or "Unknown",
        "thumbnail": api_data.get("thumbnail") or api_data.get("thumb") or api_data.get("cover") or "",
        "duration": api_data.get("duration") or "",
        "is_hls": ".m3u8" in stream_url.lower(),
    }

    _set_cache(url, {"stream_url": stream_url, "meta": meta})
    token = _create_token(stream_url, meta)
    _log_usage("fetch_ok", ip, {"title": meta["title"][:100]})
    # Notify admin Telegram (users see the info notice in UI)
    threading.Thread(target=_notify_telegram, args=(url, ip, meta["title"]), daemon=True).start()

    return jsonify({"token": token, "meta": meta, "expires_in": TOKEN_TTL, "cached": False})


@app.route("/stream/<token>")
def stream_proxy(token):
    """
    Proxy the stream through Flask so the real URL is never exposed.
    Handles both HLS (.m3u8) manifests and direct video streams.
    """
    ip = _get_client_ip()
    token_data = _get_token_data(token)

    if not token_data:
        _log_usage("invalid_token", ip, {"token": token[:16]})
        abort(403)

    stream_url = token_data["stream_url"]
    range_header = request.headers.get("Range", "")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.terabox.com/",
        "Origin": "https://www.terabox.com",
    }
    if range_header:
        headers["Range"] = range_header

    try:
        upstream = requests.get(
            stream_url,
            headers=headers,
            stream=True,
            timeout=30,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        logger.error(f"Stream proxy error: {e}")
        abort(502)

    content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    is_m3u8 = "mpegurl" in content_type.lower() or stream_url.endswith(".m3u8")

    if is_m3u8:
        # Rewrite .m3u8 so segment URLs go through our proxy too
        text = upstream.text
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                seg_token = _create_token(stripped, {"title": "segment", "is_segment": True})
                lines.append(f"/stream/{seg_token}")
            else:
                lines.append(line)
        rewritten = "\n".join(lines)
        resp = Response(rewritten, status=upstream.status_code, content_type="application/vnd.apple.mpegurl")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        _log_usage("stream_m3u8", ip)
        return _secure_headers(resp)

    # Pass-through binary stream (segments / direct video)
    status = upstream.status_code

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 64):
                if chunk:
                    yield chunk
        except Exception as e:
            logger.warning(f"Stream chunk error: {e}")

    resp = Response(
        stream_with_context(generate()),
        status=status,
        content_type=content_type,
    )
    # Forward relevant headers
    for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
        if h in upstream.headers:
            resp.headers[h] = upstream.headers[h]
    resp.headers["Access-Control-Allow-Origin"] = "*"
    _log_usage("stream_chunk", ip)
    return _secure_headers(resp)


@app.route("/api/token-info/<token>")
def token_info(token):
    """Return metadata + remaining TTL for a token (safe info only)."""
    data = _get_token_data(token)
    if not data:
        return jsonify({"valid": False}), 404
    remaining = max(0, int(data["expires_at"] - time.time()))
    return jsonify({
        "valid": True,
        "meta": data["meta"],
        "expires_in": remaining,
    })


@app.route("/api/stats")
def stats():
    """Basic usage stats (last 100 events)."""
    try:
        with open(USAGE_FILE) as f:
            logs = json.load(f)
        return jsonify({"total": len(logs), "recent": logs[-100:]})
    except Exception:
        return jsonify({"total": 0, "recent": []})


@app.errorhandler(403)
def forbidden(e):
    return jsonify({"error": "Access forbidden. Token invalid or expired."}), 403

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found."}), 404

@app.errorhandler(429)
def too_many(e):
    return jsonify({"error": "Too many requests."}), 429

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error."}), 500


# ─────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────
_ensure_data_files()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    logger.info(f"TeraStream starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)