import os
import json
import time
import uuid
import hashlib
import logging
import requests
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Flask, request, jsonify, render_template,
    Response, stream_with_context, abort, send_from_directory
)

# ── Config ────────────────────────────────────────────────────────────────────
SECRET_KEY      = os.environ.get("SECRET_KEY", "change-me-in-prod-" + str(uuid.uuid4()))
TOKEN_TTL       = int(os.environ.get("TOKEN_TTL", 1800))   # 30 min default
RATE_LIMIT      = int(os.environ.get("RATE_LIMIT", 10))    # requests / minute
API_BASE        = "https://web-production-8cdbd.up.railway.app/api/get-links"
USAGE_LOG       = os.path.join(os.path.dirname(__file__), "data", "usage.json")
USERS_FILE      = os.path.join(os.path.dirname(__file__), "data", "users.json")

ALLOWED_DOMAINS = {
    "terabox.com", "www.terabox.com",
    "teraboxapp.com", "www.teraboxapp.com",
    "1024terabox.com", "www.1024terabox.com",
    "terabox.fun", "teraboxlink.com",
    "nephobox.com", "4funbox.co",
    "mirrobox.com", "momerybox.com",
    "freeterabox.com", "www.freeterabox.com",
}

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.makedirs(os.path.join(os.path.dirname(__file__), "data"), exist_ok=True)

# ── In-memory stores ──────────────────────────────────────────────────────────
token_store: dict  = {}   # token → {stream_url, expires, ip, meta}
rate_store:  dict  = {}   # ip    → [timestamps]
api_cache:   dict  = {}   # url   → {data, ts}

CACHE_TTL = 300  # 5 min API cache


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def _log_usage(event: str, ip: str, extra: dict | None = None):
    logs = _load_json(USAGE_LOG, [])
    entry = {"ts": datetime.utcnow().isoformat(), "event": event, "ip": ip}
    if extra:
        entry.update(extra)
    logs.append(entry)
    logs = logs[-5000:]          # keep last 5 000 entries
    _save_json(USAGE_LOG, logs)

def _validate_terabox_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and parsed.hostname in ALLOWED_DOMAINS
    except Exception:
        return False

def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    window = now - 60
    hits = rate_store.get(ip, [])
    hits = [t for t in hits if t > window]
    rate_store[ip] = hits
    if len(hits) >= RATE_LIMIT:
        return False
    rate_store[ip].append(now)
    return True

def _make_token(stream_url: str, ip: str) -> str:
    raw   = f"{stream_url}{ip}{time.time()}{SECRET_KEY}"
    token = hashlib.sha256(raw.encode()).hexdigest()[:40]
    return token

def _purge_expired():
    now = time.time()
    expired = [k for k, v in token_store.items() if v["expires"] < now]
    for k in expired:
        del token_store[k]

def _proxy_headers():
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()

    # Rate limit
    if not _check_rate_limit(ip):
        _log_usage("rate_limited", ip)
        return jsonify({"error": "Too many requests. Please wait a minute."}), 429

    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided."}), 400

    if not _validate_terabox_url(url):
        _log_usage("invalid_url", ip, {"url": url})
        return jsonify({"error": "Invalid or unsupported URL. Only TeraBox links are allowed."}), 400

    # Cache check
    now = time.time()
    if url in api_cache and now - api_cache[url]["ts"] < CACHE_TTL:
        result = api_cache[url]["data"]
        logger.info("Cache hit for %s", url)
    else:
        try:
            resp = requests.get(API_BASE, params={"url": url}, timeout=15)
            resp.raise_for_status()
            result = resp.json()
            api_cache[url] = {"data": result, "ts": now}
        except requests.Timeout:
            return jsonify({"error": "API timed out. Please try again."}), 504
        except requests.RequestException as e:
            logger.error("API error: %s", e)
            return jsonify({"error": "Failed to fetch link from API."}), 502
        except ValueError:
            return jsonify({"error": "Invalid API response."}), 502

    # Extract stream_url ONLY
    stream_url = None
    if isinstance(result, list) and len(result) > 0:
        stream_url = result[0].get("stream_url") or result[0].get("streamUrl")
    elif isinstance(result, dict):
        stream_url = result.get("stream_url") or result.get("streamUrl")

    if not stream_url:
        return jsonify({"error": "No stream URL found in API response."}), 404

    # Build safe token
    _purge_expired()
    token = _make_token(stream_url, ip)
    token_store[token] = {
        "stream_url": stream_url,
        "expires":    now + TOKEN_TTL,
        "ip":         ip,
        "meta": {
            "file_name": _extract_meta(result, "file_name") or "Video",
            "size":      _extract_meta(result, "size") or "",
            "thumbnail": _extract_meta(result, "thumbnail") or "",
        }
    }

    _log_usage("fetch", ip, {"url": url, "token": token})
    return jsonify({
        "token":     token,
        "expires_in": TOKEN_TTL,
        "meta":      token_store[token]["meta"],
    })


def _extract_meta(result, key):
    if isinstance(result, list) and result:
        return result[0].get(key)
    if isinstance(result, dict):
        return result.get(key)
    return None


@app.route("/stream/<token>")
def stream_proxy(token):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()
    _purge_expired()

    entry = token_store.get(token)
    if not entry:
        abort(404)

    if time.time() > entry["expires"]:
        del token_store[token]
        abort(410)   # Gone

    # Optional: lock token to originating IP
    # if entry["ip"] != ip:
    #     abort(403)

    stream_url = entry["stream_url"]
    _log_usage("stream", ip, {"token": token})

    # Detect m3u8 vs direct video
    is_m3u8 = ".m3u8" in stream_url.lower() or "m3u8" in stream_url.lower()

    if is_m3u8:
        return _proxy_m3u8(stream_url)
    else:
        return _proxy_video(stream_url)


def _proxy_m3u8(stream_url: str):
    """Fetch and return HLS manifest, rewriting segment URLs through our proxy."""
    try:
        r = requests.get(stream_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except Exception as e:
        logger.error("m3u8 fetch error: %s", e)
        abort(502)

    content_type = r.headers.get("Content-Type", "application/vnd.apple.mpegurl")
    headers = {
        **_proxy_headers(),
        "Content-Type": content_type,
        "Access-Control-Allow-Origin": "*",
    }
    return Response(r.content, status=200, headers=headers)


def _proxy_video(stream_url: str):
    """Stream a direct video file through Flask with range support."""
    range_header = request.headers.get("Range")
    req_headers  = {"User-Agent": "Mozilla/5.0"}
    if range_header:
        req_headers["Range"] = range_header

    try:
        upstream = requests.get(stream_url, headers=req_headers, stream=True, timeout=20)
    except Exception as e:
        logger.error("Video proxy error: %s", e)
        abort(502)

    status = upstream.status_code
    resp_headers = {
        **_proxy_headers(),
        "Content-Type":  upstream.headers.get("Content-Type", "video/mp4"),
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
    }
    for h in ("Content-Length", "Content-Range"):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]

    def generate():
        for chunk in upstream.iter_content(chunk_size=1024 * 64):
            yield chunk

    return Response(stream_with_context(generate()), status=status, headers=resp_headers)


@app.route("/api/check/<token>")
def check_token(token):
    _purge_expired()
    entry = token_store.get(token)
    if not entry or time.time() > entry["expires"]:
        return jsonify({"valid": False}), 404
    remaining = int(entry["expires"] - time.time())
    return jsonify({"valid": True, "expires_in": remaining, "meta": entry["meta"]})


@app.after_request
def add_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"]         = "SAMEORIGIN"
    resp.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
