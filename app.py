import base64
import re
import urllib.parse
from datetime import datetime
from flask import Flask, render_template, request, Response, abort, url_for
import requests

app = Flask(__name__, static_folder="static")

# --------------------------
# Provider catalog (text cleaned)
# --------------------------
PROVIDERS = {
    "cogent": {
        "name": "Cogent (AS174)",
        "url": "https://www.cogentco.com/en/looking-glass",
    },
    "unitas": {
        "name": "Unitas Global (AS1828)",
        "url": "https://lg.unitasglobal.net",
    },
    "he": {
        "name": "Hurricane Electric (AS6939)",
        "url": "https://lg.he.net",
    },
    "hkix": {
        "name": "HKIX",
        "url": "https://www.hkix.net/hkix/hkixlg.htm",
    },
    "twelve99": {
        "name": "Arelion / Telia (AS1299)",
        "url": "https://lg.twelve99.net",
    },
    "singtel": {
        "name": "Singtel STIX",
        "url": "https://stixlg.singtel.com",
    },
    "lumen": {
        "name": "Lumen (Level3)",
        "url": "https://lookingglass.centurylink.com",
    },
    "ovh": {
        "name": "OVHcloud (AS16276)",
        "url": "https://lg.ovh.net",
    },
    "omantel": {
        "name": "Omantel / ZOI",
        "url": "https://lookingglass.omantel.om",
    },
    "nexlinx": {
        "name": "Nexlinx (PK)",
        "url": "http://lg.nexlinx.net.pk",
    },
}

# --------------------------
# Helpers
# --------------------------
def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")

def ub64(s: str) -> str:
    return base64.urlsafe_b64decode(s.encode("ascii")).decode("utf-8")

app.jinja_env.filters["b64"] = b64

ABS_ATTRS = ("src", "href", "action", "data-src", "data-href")

def absolutize(url: str, base: str) -> str:
    return urllib.parse.urljoin(base, url)

PROXY_PREFIX = "/embed/proxy?u="

def rewrite_html(html: str, base_url: str) -> str:
    """
    Rewrites common attributes to traverse our proxy so things load inside the frame.
    Kept deliberately simple & robust.
    """
    def repl(match):
        attr = match.group(1)
        quote = match.group(2)
        val = match.group(3)

        # Skip javascript:, mailto:, data:, tel:
        if val.startswith(("javascript:", "mailto:", "data:", "tel:")):
            return match.group(0)

        abs_url = absolutize(val, base_url)
        prox_url = f"{PROXY_PREFIX}{b64(abs_url)}"
        return f'{attr}={quote}{prox_url}{quote}'

    # Rewrite attributes
    pattern = r'(?i)\b(' + "|".join(ABS_ATTRS) + r')\s*=\s*([\'"])(.+?)\2'
    html = re.sub(pattern, repl, html)

    # Relax a few headers occasionally left inline (CSP/meta refresh)
    # Meta refresh:
    html = re.sub(
        r'(?i)<meta\s+http-equiv=["\']refresh["\']\s+content=["\']\s*\d+\s*;\s*url=([^"\']+)["\']\s*/?>',
        lambda m: f'<meta http-equiv="refresh" content="0; url={PROXY_PREFIX}{b64(absolutize(m.group(1), base_url))}">',
        html,
    )
    return html


# --------------------------
# Views
# --------------------------
@app.route("/")
def index():
    # Render sidebar + empty content; first provider will auto-load via JS.
    return render_template("index.html", providers=PROVIDERS, now=datetime.utcnow())

@app.route("/embed/frame/<pid>")
def embed_frame(pid):
    prov = PROVIDERS.get(pid)
    if not prov:
        abort(404)
    return render_template(
        "embed_frame.html",
        pid=pid,
        name=prov["name"],
        upstream_url=prov["url"],
        prox_url=f"{PROXY_PREFIX}{b64(prov['url'])}",
    )

@app.route("/embed/proxy", methods=["GET", "POST"])
def proxy():
    """
    Very lightweight proxy to allow embedding when X-Frame-Options/CORS block us.
    Only goal is 'viewing in a website', not bypassing provider auth/captcha.
    """
    u = request.args.get("u")
    if not u:
        abort(400)

    try:
        target = ub64(u)
    except Exception:
        abort(400)

    # Forward method, headers (lightly), and payload
    method = request.method.upper()
    headers = {
        "User-Agent": request.headers.get(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari",
        ),
        "Accept": request.headers.get("Accept", "*/*"),
        "Accept-Language": request.headers.get("Accept-Language", "en-US,en;q=0.9"),
        "Referer": target,  # keeps many sites happy
    }

    data = request.get_data() if method == "POST" else None

    try:
        upstream = requests.request(
            method,
            target,
            headers=headers,
            data=data,
            allow_redirects=True,
            timeout=20,
        )
    except requests.RequestException as e:
        return Response(f"Upstream error: {e}", status=502)

    content_type = upstream.headers.get("Content-Type", "")
    body = upstream.content

    # If HTML, rewrite links to come back through us
    if "text/html" in content_type.lower():
        html = upstream.text
        html = rewrite_html(html, target)
        return Response(html, status=upstream.status_code, headers={"Content-Type": content_type})

    # Non-HTML passthrough
    return Response(body, status=upstream.status_code, headers={"Content-Type": content_type})


if __name__ == "__main__":
    # Production tip: run with gunicorn or waitress in prod.
    app.run(host="0.0.0.0", port=5000, debug=False)
