# 🎬 TeraStream — Secure TeraBox Video Streaming

A full-stack, secure video streaming platform for TeraBox links.
Built with **Flask** (backend) + **HTML/CSS/JS** (frontend) + **HLS.js** (player).

---

## 🗂️ Project Structure

```
terastream/
├── backend/
│   ├── app.py              # Flask application (all routes)
│   ├── requirements.txt    # Python dependencies
│   ├── .env.example        # Environment variable template
│   └── data/
│       ├── users.json      # User store (future auth)
│       └── usage.json      # Request logs
├── frontend/
│   ├── index.html          # Main HTML page
│   └── static/
│       ├── style.css       # Dark cinematic UI
│       └── app.js          # HLS player + API logic
├── Procfile                # Gunicorn start command
├── railway.json            # Railway deployment config
├── runtime.txt             # Python version
└── README.md
```

---

## 🔐 Security Features

| Feature | Implementation |
|---------|----------------|
| URL Validation | Only TeraBox domains allowed (server + client) |
| Real URL hiding | Stream URLs stored in server memory, never sent to browser |
| Token-based access | SHA-256 tokens expire in 5–10 minutes |
| Rate limiting | 20 requests/IP per 60 seconds (in-memory) |
| Anti-hotlink headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` |
| M3U8 rewriting | Segment URLs rewritten to go through proxy |
| No download URLs | `download_url` field discarded at API level |
| API caching | 5-minute cache prevents duplicate API calls |
| Token cleanup | Background thread removes expired tokens |

---

## 🎬 Streaming Features

- **HLS.js** player with adaptive bitrate
- Automatic fallback: HLS → native HLS (Safari) → direct MP4
- Keyboard shortcuts: `Space`/`K` = play/pause, `F` = fullscreen, `←`/`→` = ±10s, `M` = mute
- Picture-in-Picture support
- Token countdown bar with color shift (green → yellow → red)
- Auto-token expiry notification

---

## ⚙️ Backend Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Serve frontend |
| `/api/fetch` | POST | Validate URL → call API → return token + meta |
| `/stream/<token>` | GET | Proxy stream / rewrite M3U8 |
| `/api/token-info/<token>` | GET | Check token validity + TTL |
| `/api/stats` | GET | Last 100 usage log entries |

---

## 🚀 Local Development

### 1. Install dependencies

```bash
cd terastream
pip install -r backend/requirements.txt
```

### 2. Configure environment

```bash
cp backend/.env.example backend/.env
# Edit .env and set SECRET_KEY
```

### 3. Run Flask

```bash
cd backend
python app.py
```

Open: http://localhost:5000

---

## 🚂 Deploy to Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Set these environment variables in Railway dashboard:

```
SECRET_KEY=<generate a long random string>
TOKEN_TTL=600
RATE_LIMIT_MAX=20
PORT=5000
```

4. Railway will auto-detect `railway.json` and deploy

---

## 🌐 Deploy to Vercel (frontend) + Railway (backend)

For split deployment:
- Deploy Flask backend to Railway (as above)
- Set `ALLOWED_ORIGINS` to your Vercel frontend URL
- Update `app.js` fetch calls to use the Railway backend URL

---

## 📦 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | (required) | Used for token hashing |
| `TOKEN_TTL` | `600` | Token lifetime in seconds |
| `RATE_LIMIT_MAX` | `20` | Max requests per window per IP |
| `RATE_LIMIT_WINDOW` | `60` | Rate limit window in seconds |
| `CACHE_TTL` | `300` | API response cache duration |
| `PORT` | `5000` | Server port |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins |

---

## 📝 Notes

- **No database required** — uses JSON files for logging
- **Production**: replace in-memory stores with Redis
- **Scaling**: set `--workers` in Gunicorn to match CPU count × 2 + 1
- Only `stream_url` is ever used from the API — `download_url` is discarded
- Token store is in-memory; restarts clear all active tokens
