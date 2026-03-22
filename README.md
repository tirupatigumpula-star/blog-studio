# 🪴 Home Decor Blog Studio

AI-powered tool that analyses your images and publishes a home decor blog article directly to WordPress.

---

## Quick Start (3 steps)

### Step 1 — Install Python dependencies

Make sure Python 3.8+ is installed, then run:

```bash
pip install -r requirements.txt
```

### Step 2 — Start the server

```bash
python server.py
```

You should see:
```
✅  Home Decor Blog Studio is running!
   Open http://localhost:5000 in your browser
```

### Step 3 — Open in your browser

Go to: **http://localhost:5000**

---

## How to use

1. **Claude API Key** — Get from console.anthropic.com → API Keys → Create Key
2. **WordPress Connection** — Enter your site URL, username, and an Application Password
   - Generate one at: WP Admin → Users → Your Profile → Application Passwords
3. **Article Style** — Prompt is fixed for home decor. Optionally enter a post title.
4. **Upload Images** — Drag & drop or browse (up to 10 images, JPEG/PNG/WebP)
5. Click **Analyse & Publish to WordPress**

The tool will:
- Send your images to Claude AI for analysis
- Generate a full blog article (intro + per-image sections + conclusion)
- Upload your images to your WordPress Media Library
- Save the article as a **Draft** post in WordPress
- Give you a direct link to edit the post

---

## Notes

- Posts are saved as **drafts** — you review and publish manually
- The first uploaded image becomes the featured image
- Requires Python 3.8 or higher
- API usage is billed to your Anthropic account
