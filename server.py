from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import anthropic
import requests
import base64
import xmlrpc.client
import ssl
import re
import json
import os
import io
from PIL import Image

app = Flask(__name__, static_folder="static")
CORS(app)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

MAX_IMAGE_PX = 1800  # Claude many-image limit is 2000px — we use 1800 to be safe


def resize_image_for_claude(b64_data, mime_type):
    """
    Resize image so neither dimension exceeds MAX_IMAGE_PX.
    Returns (new_b64, new_mime). If already small enough, returns original.
    """
    try:
        img_bytes = base64.b64decode(b64_data)
        img = Image.open(io.BytesIO(img_bytes))

        # Convert RGBA/P to RGB for JPEG compatibility
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            mime_type = "image/jpeg"

        w, h = img.size
        if w <= MAX_IMAGE_PX and h <= MAX_IMAGE_PX:
            return b64_data, mime_type  # already fine

        # Scale down proportionally
        ratio = min(MAX_IMAGE_PX / w, MAX_IMAGE_PX / h)
        new_w, new_h = int(w * ratio), int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Save to buffer
        fmt = "JPEG" if "jpeg" in mime_type or "jpg" in mime_type else "PNG"
        buf = io.BytesIO()
        img.save(buf, format=fmt, quality=88)
        new_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return new_b64, mime_type
    except Exception:
        return b64_data, mime_type  # if resize fails, return original


FIXED_PROMPT = """You are a professional home decor content writer. Write a warm, sophisticated blog article for interior design enthusiasts based on the uploaded images.

Analyse each image carefully — describe its color palette, textures, materials, mood, and styling details.

Structure the article EXACTLY in this order — follow this structure precisely:

1. # Article Title (H1)

2. Introduction paragraph (2-3 sentences, warm and inviting)

3. ## [Main Keyword] (H2) — derive a natural main keyword or theme from the images e.g. "Elegant Living Room Decor Ideas" or "Cosy Bedroom Styling Tips"

4. For EACH image, one section:
   ### [Descriptive Idea Name] (H3)
   [IMAGE_PLACEHOLDER]
   2-3 sentence description of that image — colors, textures, mood, styling tips

5. ## Final Words (H2)

6. Conclusion paragraph (2-3 sentences with a call-to-action)

IMPORTANT RULES:
- Write exactly as many ### sections as there are images
- Each ### section corresponds to one image in order
- Write [IMAGE_PLACEHOLDER] on its own line where the image should go — the system will replace it with the actual image
- Do not add any extra commentary or preamble
- Output clean Markdown only

Tone: elegant, approachable, and inspiring."""


# ── Config helpers ────────────────────────────────────────────────────────────
# Credentials are loaded from:
# 1. Environment variables (for cloud hosting — Railway, Render, etc.)
# 2. config.json file (for local use)
# Values set via the Settings UI are saved to config.json locally
# or can be set as env vars on the cloud server.

def load_config():
    # Start with env vars (cloud priority)
    cfg = {
        "claude_api_key": os.environ.get("CLAUDE_API_KEY", ""),
        "wp_url":         os.environ.get("WP_URL", ""),
        "wp_user":        os.environ.get("WP_USER", ""),
        "wp_pass":        os.environ.get("WP_PASS", ""),
    }
    # Fill in any missing values from config.json (local fallback)
    try:
        with open(CONFIG_FILE, "r") as f:
            file_cfg = json.load(f)
            for key in cfg:
                if not cfg[key]:
                    cfg[key] = file_cfg.get(key, "")
    except Exception:
        pass
    return cfg


def save_config(data):
    """Save to config.json for local use."""
    existing = {}
    try:
        with open(CONFIG_FILE, "r") as f:
            existing = json.load(f)
    except Exception:
        pass
    existing.update({
        "claude_api_key": data.get("claude_api_key", ""),
        "wp_url":         data.get("wp_url", ""),
        "wp_user":        data.get("wp_user", ""),
        "wp_pass":        data.get("wp_pass", ""),
    })
    with open(CONFIG_FILE, "w") as f:
        json.dump(existing, f, indent=2)


# ── XML-RPC helpers ───────────────────────────────────────────────────────────

def get_xmlrpc_client(wp_url):
    xmlrpc_url = wp_url.rstrip("/") + "/xmlrpc.php"
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    transport = xmlrpc.client.SafeTransport(context=context)
    return xmlrpc.client.ServerProxy(xmlrpc_url, transport=transport, allow_none=True)


def build_html_with_images(article_md, image_urls):
    lines     = article_md.split("\n")
    result    = []
    img_index = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        if re.match(r'^# (?!#)', line):
            result.append(f"<h1>{line[2:].strip()}</h1>")
            i += 1
        elif re.match(r'^## (?!#)', line):
            result.append(f"<h2>{line[3:].strip()}</h2>")
            i += 1
        elif re.match(r'^### ', line):
            result.append(f"<h3>{line[4:].strip()}</h3>")
            i += 1
        elif line.strip() == "[IMAGE_PLACEHOLDER]":
            if img_index < len(image_urls):
                prev_h3 = next(
                    (r[4:-5] for r in reversed(result) if r.startswith("<h3>")),
                    f"Home decor image {img_index + 1}"
                )
                result.append(
                    f'<figure class="wp-block-image size-large">'
                    f'<img src="{image_urls[img_index]}" alt="{prev_h3}" />'
                    f'</figure>'
                )
                img_index += 1
            i += 1
        elif line.strip() == "":
            i += 1
        else:
            para_lines = []
            while i < len(lines) and lines[i].strip() != "" and not re.match(r'^#{1,3} ', lines[i]) and lines[i].strip() != "[IMAGE_PLACEHOLDER]":
                para_lines.append(lines[i].strip())
                i += 1
            if para_lines:
                para_text = " ".join(para_lines)
                para_text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', para_text)
                para_text = re.sub(r'\*(.+?)\*',     r'<em>\1</em>',         para_text)
                result.append(f"<p>{para_text}</p>")

    return "\n".join(result)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/config", methods=["GET"])
def get_config():
    cfg = load_config()
    # Mask passwords before sending to browser
    return jsonify({
        "claude_api_key": cfg["claude_api_key"],
        "wp_url":         cfg["wp_url"],
        "wp_user":        cfg["wp_user"],
        "wp_pass":        cfg["wp_pass"],
        "from_env": {
            "claude_api_key": bool(os.environ.get("CLAUDE_API_KEY")),
            "wp_url":         bool(os.environ.get("WP_URL")),
            "wp_user":        bool(os.environ.get("WP_USER")),
            "wp_pass":        bool(os.environ.get("WP_PASS")),
        }
    })


@app.route("/api/config", methods=["POST"])
def set_config():
    try:
        save_config(request.json)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/test-wp", methods=["POST"])
def test_wp():
    data    = request.json
    wp_url  = data.get("wpUrl", "").strip().rstrip("/")
    wp_user = data.get("wpUser", "").strip()
    wp_pass = data.get("wpPass", "").strip()
    results = {}

    try:
        r = requests.get(f"{wp_url}/wp-json/", timeout=10)
        results["rest_api"] = f"✅ REST API reachable (HTTP {r.status_code})"
    except Exception as e:
        results["rest_api"] = f"❌ Cannot reach REST API: {e}"

    try:
        r = requests.post(
            f"{wp_url}/xmlrpc.php", timeout=10,
            data='<?xml version="1.0"?><methodCall><methodName>demo.sayHello</methodName></methodCall>',
            headers={"Content-Type": "text/xml"},
        )
        results["xmlrpc"] = f"✅ XML-RPC is enabled (HTTP {r.status_code})"
    except Exception as e:
        results["xmlrpc"] = f"❌ XML-RPC not reachable: {e}"

    try:
        client = get_xmlrpc_client(wp_url)
        blogs  = client.wp.getUsersBlogs(wp_user, wp_pass)
        results["auth"] = f"✅ Login successful! Blog: '{blogs[0].get('blogName', '?')}'" if blogs else "⚠️ Login ok but no blogs"
    except xmlrpc.client.Fault as e:
        results["auth"] = f"❌ Login failed: {e.faultString}"
    except Exception as e:
        results["auth"] = f"❌ XML-RPC error: {e}"

    return jsonify(results)


@app.route("/api/generate", methods=["POST"])
def generate():
    data       = request.json
    api_key    = data.get("apiKey", "").strip()
    post_title = data.get("postTitle", "").strip()
    images     = data.get("images", [])
    wp_url     = data.get("wpUrl", "").strip().rstrip("/")
    wp_user    = data.get("wpUser", "").strip()
    wp_pass    = data.get("wpPass", "").strip()

    if not api_key: return jsonify({"error": "Claude API key is required"}), 400
    if not images:  return jsonify({"error": "At least one image is required"}), 400
    if not wp_url or not wp_user or not wp_pass:
        return jsonify({"error": "WordPress credentials are required"}), 400

    # ── Connect via XML-RPC ───────────────────────────────────────────────────
    try:
        client = get_xmlrpc_client(wp_url)
        client.wp.getUsersBlogs(wp_user, wp_pass)
    except xmlrpc.client.Fault as e:
        return jsonify({"error": f"WordPress login failed: {e.faultString}"}), 401
    except Exception as e:
        return jsonify({"error": f"Cannot connect to WordPress: {str(e)}"}), 500

    # ── Generate article with Claude ──────────────────────────────────────────
    try:
        claude_client = anthropic.Anthropic(api_key=api_key)
        content = []
        for img in images:
            resized_b64, resized_mime = resize_image_for_claude(img["base64"], img["mime"])
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": resized_mime, "data": resized_b64},
            })
        prompt = FIXED_PROMPT + (f'\n\nPreferred title: "{post_title}"' if post_title else "")
        content.append({"type": "text", "text": prompt})

        message = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=6000,
            messages=[{"role": "user", "content": content}],
        )
        article = "".join(b.text for b in message.content if hasattr(b, "text"))
        if not article:
            raise ValueError("Claude returned empty content")
    except Exception as e:
        return jsonify({"error": f"Claude API error: {str(e)}"}), 500

    # ── Upload images via XML-RPC ─────────────────────────────────────────────
    media_ids    = []
    media_urls   = []
    media_errors = []

    for i, img in enumerate(images):
        try:
            img_bytes = base64.b64decode(img["base64"])
            filename  = img.get("name") or f"image-{i+1}.{img['mime'].split('/')[-1]}"
            result    = client.wp.uploadFile(1, wp_user, wp_pass, {
                "name":      filename,
                "type":      img["mime"],
                "bits":      xmlrpc.client.Binary(img_bytes),
                "overwrite": False,
            })
            att_id  = result.get("attachment_id") or result.get("id") or result.get("ID") or ""
            att_url = result.get("url") or result.get("link") or ""
            if att_id:
                media_ids.append(int(att_id))
                media_urls.append(att_url)
            else:
                media_errors.append(f"Image {i+1}: uploaded but no ID returned")
        except xmlrpc.client.Fault as e:
            media_errors.append(f"Image {i+1} skipped: {e.faultString}")
        except Exception as e:
            media_errors.append(f"Image {i+1} skipped: {str(e)}")

    # ── Build HTML and publish ────────────────────────────────────────────────
    raw_title    = next((l.strip() for l in article.splitlines() if l.strip()), "Home Decor Inspiration")
    final_title  = post_title or raw_title.lstrip("#").strip()
    html_content = build_html_with_images(article, media_urls)

    post_data = {
        "post_title":   final_title,
        "post_content": html_content,
        "post_status":  "draft",
        "post_type":    "post",
    }
    if media_ids:
        post_data["post_thumbnail"] = str(media_ids[0])

    try:
        post_id = int(client.wp.newPost(1, wp_user, wp_pass, post_data))
    except Exception as e:
        return jsonify({"error": f"Failed to create WordPress post: {str(e)}"}), 500

    if media_ids:
        try:
            client.wp.editPost(1, wp_user, wp_pass, post_id, {"post_thumbnail": str(media_ids[0])})
        except Exception:
            pass

    return jsonify({
        "ok":          True,
        "postId":      post_id,
        "title":       final_title,
        "editUrl":     f"{wp_url}/wp-admin/post.php?post={post_id}&action=edit",
        "article":     article,
        "mediaIds":    media_ids,
        "mediaUrls":   media_urls,
        "mediaErrors": media_errors,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n✅  Home Decor Blog Studio is running!")
    print(f"   Open http://localhost:{port} in your browser\n")
    app.run(debug=False, host="0.0.0.0", port=port)
