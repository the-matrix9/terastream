# TeraStream 🎬 — Secure TeraBox Video Proxy

A full-stack Flask application that lets you stream TeraBox videos securely through a backend proxy, hiding real stream URLs from clients.

## Features

- 🔐 **Backend proxy** — real `stream_url` never exposed to browser
- ⏱️ **Token expiry** — session tokens expire in 30 min (configurable)
- 🚦 **Rate limiting** — per-IP request throttling (10 req/min default)
- ✅ **URL validation** — only TeraBox domains accepted
- 🚀 **HLS.js player** — adaptive bitrate streaming
- ⚡ **API caching** — 5-minute response cache
- 📊 **Usage logs** — stored in `data/usage.json`
- 🎨 **Dark Netflix UI** — glassmorphism + smooth animations

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and edit environment variables
cp .env.example .env
# Edit .env and change SECRET_KEY

# 3. Run the server
python app.py
# Open http://localhost:5000
```

## Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Serve frontend |
| `/api/fetch` | POST | Validate URL → call API → return token |
| `/stream/<token>` | GET | Proxy stream (m3u8 or direct video) |
| `/api/check/<token>` | GET | Check token validity + remaining time |

## API Flow

```
Browser → POST /api/fetch { url: "terabox.com/s/xxx" }
         → Flask validates URL
         → Calls https://web-production-8cdbd.up.railway.app/api/get-links?url=...
         → Extracts stream_url ONLY (never download_url)
         → Creates secure token → returns { token, expires_in, meta }

Browser → GET /stream/<token>
         → Flask verifies token not expired
         → Fetches stream from real URL server-side
         → Streams chunks to browser (real URL hidden)
```

## Security Notes

- `SECRET_KEY` must be changed in production
- Token-to-IP binding can be enabled (commented out in `stream_proxy`)
- All streams have `no-store` cache headers to prevent caching
- Hotlinking blocked via `Referrer-Policy: no-referrer`

## File Structure

```
terastream/
├── app.py              # Flask backend
├── requirements.txt
├── .env.example
├── data/
│   └── usage.json      # Auto-created usage logs
├── templates/
│   └── index.html      # Frontend HTML
└── static/
    ├── css/style.css   # Dark UI styles
    └── js/app.js       # HLS player + fetch logic
```

## Deployment

For production, use Gunicorn:
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```
