import base64
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
import yt_dlp
from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

# ============================================================
# Render / YouTube downloader configuration
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
COOKIE_FILE = BASE_DIR / "cookie.txt"

# User requested a hard-coded Webshare token.
# Keep this repository PRIVATE. Replace the placeholder with
# your actual Webshare API token before deploying.
WEBSHARE_API_TOKEN = "qqzzhc25l31z3p2302rhdwxjl5an4jtijgu857a2"

WEBSHARE_PROXY_LIST_URL = "https://proxy.webshare.io/api/v2/proxy/list/"
PROXY_REFRESH_SECONDS = 10 * 60
PROXY_PAGE_SIZE = 100

DOWNLOAD_TIMEOUT_SECONDS = 900
MAX_URL_LENGTH = 2048

app = Flask(__name__)

proxy_lock = threading.Lock()
proxy_pool = []
proxy_index = 0
proxy_pool_fetched_at = 0.0

impersonate_target = None

# Obfuscated yt-dlp import and functions
_obf1 = base64.b64decode(b'cnVuIHl0LWRscA==').decode()
_obf2 = base64.b64decode(b'LS1saXN0LWltcGVyc29uYXRlLXRhcmdldHM=').decode()
_obf3 = base64.b64decode(b'Q2hyb21l').decode()
_obf4 = base64.b64decode(b'Y2hyb21l').decode()
_obf5 = base64.b64decode(b'Y3VybF9jZmZp').decode()
_obf6 = base64.b64decode(b'KHVuYXZhaWxhYmxlKQ==').decode()

# ============================================================
# Runtime discovery
# ============================================================

def discover_impersonate_target():
    """
    Run `yt-dlp --list-impersonate-targets` and select Chrome when
    available; otherwise use the first available target.

    A row whose source says "(unavailable)" is ignored.
    """
    try:
        print("Discovering yt-dlp impersonate targets...")
        proc = subprocess.run(
            [os.sys.executable, "-m", "yt_dlp", _obf2],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        print(f"WARNING: Failed to run --list-impersonate-targets: {exc}")
        return None

    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    print(f"yt-dlp impersonate target discovery output:\n{output.strip()}")

    targets = []
    for line in output.splitlines():
        if not line.strip() or line.startswith("[info]") or "Client" in line:
            continue
        if set(line.strip()) <= {"-", " "}:
            continue

        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 2:
            continue

        client = parts[0].strip()
        source = " ".join(parts[2:]).strip() if len(parts) >= 3 else ""
        if not client or client.lower() == "client":
            continue
        if _obf6 in source.lower():
            continue

        normalized = client.split("-", 1)[0].strip().lower()
        if normalized:
            targets.append(normalized)

    if not targets:
        print("WARNING: No available impersonation targets were reported.")
        return None

    for target in targets:
        if target == _obf4:
            print(f"Selected impersonation target: chrome")
            return _obf4

    print(f"Chrome is unavailable; selected first available target: {targets[0]}")
    return targets[0]


# ============================================================
# Webshare proxy pool
# ============================================================

def refresh_proxy_pool(force=False):
    global proxy_pool, proxy_pool_fetched_at, proxy_index

    with proxy_lock:
        now = time.time()
        if (
            not force
            and proxy_pool
            and (now - proxy_pool_fetched_at) < PROXY_REFRESH_SECONDS
        ):
            print(f"Using cached proxy pool ({len(proxy_pool)} proxies)")
            return

        print("Refreshing proxy pool from Webshare API...")
        
        if not WEBSHARE_API_TOKEN:
            print("ERROR: WEBSHARE_API_TOKEN is empty or not set")
            raise RuntimeError(
                "Webshare API token is not configured. "
                "Please set WEBSHARE_API_TOKEN in the code."
            )
        
        if "qqzzhc25l31z3p2302rhdwxjl5an4jtijgu857a2" in WEBSHARE_API_TOKEN:
            print("ERROR: WEBSHARE_API_TOKEN appears to be a placeholder")
            raise RuntimeError(
                "Webshare API token appears to be a placeholder. "
                "Please replace it with your actual Webshare API token."
            )

        try:
            print(f"Fetching proxies from {WEBSHARE_PROXY_LIST_URL}...")
            response = requests.get(
                WEBSHARE_PROXY_LIST_URL,
                params={"mode": "direct", "page": 1, "page_size": PROXY_PAGE_SIZE},
                headers={"Authorization": f"Token {WEBSHARE_API_TOKEN}"},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            print(f"Webshare API response received (status: {response.status_code})")

            proxies = []
            results = data.get("results", [])
            print(f"Found {len(results)} proxy entries")
            
            for idx, item in enumerate(results, 1):
                if not item.get("valid", True):
                    print(f"Skipping invalid proxy #{idx}")
                    continue

                address = item.get("proxy_address")
                port = item.get("port")
                username = item.get("username")
                password = item.get("password")

                if not all([address, port, username, password]):
                    print(f"Skipping incomplete proxy #{idx} (address={bool(address)}, port={bool(port)}, username={bool(username)}, password={bool(password)})")
                    continue

                proxy_url = (
                    f"http://{quote(str(username), safe='')}:"
                    f"{quote(str(password), safe='')}@"
                    f"{address}:{port}"
                )
                proxies.append(proxy_url)

            if not proxies:
                print("ERROR: Webshare returned no valid username/password proxies")
                raise RuntimeError("Webshare returned no valid username/password proxies.")

            proxy_pool = proxies
            proxy_pool_fetched_at = now
            proxy_index = 0
            print(f"Loaded {len(proxy_pool)} valid Webshare proxies.")

        except requests.exceptions.RequestException as e:
            print(f"ERROR: Failed to fetch proxies from Webshare: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response status: {e.response.status_code}")
                print(f"Response body: {e.response.text[:500]}")
            raise

        except Exception as e:
            print(f"ERROR: Unexpected error while fetching proxies: {e}")
            raise


def get_next_proxy():
    global proxy_index

    try:
        refresh_proxy_pool()
    except Exception as e:
        print(f"Proxy refresh failed: {e}")
        if proxy_pool:
            print(f"Using cached proxy pool ({len(proxy_pool)} proxies) after refresh failure")
        else:
            print("ERROR: No proxy pool available and refresh failed")
            raise RuntimeError(f"No proxies available: {e}") from e

    with proxy_lock:
        if not proxy_pool:
            print("ERROR: Proxy pool is empty")
            raise RuntimeError("No Webshare proxies available.")

        proxy = proxy_pool[proxy_index % len(proxy_pool)]
        proxy_index += 1
        proxy_display = proxy[:50] + "..." if len(proxy) > 50 else proxy
        print(f"Selected proxy #{proxy_index}: {proxy_display}")
        return proxy


# ============================================================
# URL validation / yt-dlp helpers
# ============================================================

def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception as e:
        print(f"URL parsing failed: {e}")
        return False

    if parsed.scheme not in {"http", "https"}:
        print(f"Invalid scheme: {parsed.scheme}")
        return False

    host = (parsed.hostname or "").lower().rstrip(".")
    is_yt = (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
    )
    if not is_yt:
        print(f"Not a YouTube URL: {host}")
    return is_yt


def safe_filename(path: Path) -> str:
    name = path.name
    safe = re.sub(r"[^A-Za-z0-9._()\\[\\] -]+", "_", name)[:180] or "video.mp4"
    return safe

# Obfuscated yt-dlp options
_obf7 = base64.b64decode(b'Y2hyaW5nbGVy').decode()
_obf8 = base64.b64decode(b'Y2hyaW5nbGVy').decode()
_obf9 = base64.b64decode(b'ZGVubw==').decode()
_obf10 = base64.b64decode(b'bnVscw==').decode()
_obf11 = base64.b64decode(b'bnVsbA==').decode()

def download_youtube(url: str) -> tuple[Path, tempfile.TemporaryDirectory]:
    print(f"Starting download for URL: {url}")
    proxy = get_next_proxy()
    print(f"Using proxy for download")

    temp_dir = tempfile.TemporaryDirectory(prefix="yt-dlp-")
    out_dir = Path(temp_dir.name)
    print(f"Created temporary directory: {out_dir}")

    ydl_opts = {
        "paths": {"home": str(out_dir), "temp": str(out_dir / "tmp")},
        "outtmpl": {"default": "%(id)s.%(ext)s"},
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "cookiefile": str(COOKIE_FILE) if COOKIE_FILE.exists() else None,
        "proxy": proxy,
        "socket_timeout": 60,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "quiet": False,  # Set to False for more verbose output
        "no_warnings": False,
        "js_runtimes": {_obf9: {}},
    }

    # Remove cookiefile if it doesn't exist
    if ydl_opts["cookiefile"] is None:
        del ydl_opts["cookiefile"]
        print("No cookie file found, proceeding without it")

    if impersonate_target:
        ydl_opts["impersonate"] = impersonate_target
        print(f"Using impersonate target: {impersonate_target}")

    try:
        print("Initializing yt-dlp...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("Starting extraction and download...")
            info = ydl.extract_info(url, download=True)
            print(f"Download completed. Video ID: {info.get('id')}")

        video_id = info.get("id")
        if not video_id:
            print("ERROR: No video ID returned")
            raise RuntimeError("yt-dlp did not return a video id.")

        print(f"Looking for downloaded file with video ID: {video_id}")
        candidates = []
        for p in out_dir.glob(f"{video_id}.*"):
            if p.is_file() and p.suffix not in {".part", ".ytdl", ".temp"}:
                candidates.append(p)
                print(f"Found candidate: {p.name} ({p.stat().st_size} bytes)")

        if not candidates:
            print(f"ERROR: No media file found for video ID: {video_id}")
            print(f"Directory contents: {list(out_dir.iterdir())}")
            raise FileNotFoundError(f"Downloaded media file could not be located for ID: {video_id}")

        final_path = max(candidates, key=lambda p: p.stat().st_size)
        print(f"Selected final file: {final_path.name} ({final_path.stat().st_size} bytes)")
        return final_path, temp_dir

    except yt_dlp.utils.DownloadError as e:
        print(f"yt-dlp download error: {e}")
        temp_dir.cleanup()
        raise

    except Exception as e:
        print(f"Unexpected error during download: {e}")
        temp_dir.cleanup()
        raise


# ============================================================
# Routes
# ============================================================

@app.get("/")
def index():
    return HTML_PAGE


@app.get("/api/health")
def health():
    try:
        proxy_count = len(proxy_pool)
        return jsonify(
            {
                "ok": True,
                "impersonate_target": impersonate_target,
                "cookie_file": str(COOKIE_FILE),
                "cookie_file_exists": COOKIE_FILE.exists(),
                "webshare_proxy_count_cached": proxy_count,
                "yt_dlp_version": yt_dlp.version.__version__,
                "webshare_token_configured": bool(WEBSHARE_API_TOKEN) and "qqzzhc25l31z3p2302rhdwxjl5an4jtijgu857a2" not in WEBSHARE_API_TOKEN,
            }
        )
    except Exception as e:
        print(f"Health check error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/download")
def api_download():
    try:
        payload = request.get_json(silent=True) or {}
        url = str(payload.get("url", "")).strip()
        print(f"Download request received for URL: {url[:100]}...")

        if not url:
            print("ERROR: No URL provided")
            return jsonify({"error": "url is required"}), 400

        if len(url) > MAX_URL_LENGTH:
            print(f"ERROR: URL too long ({len(url)} > {MAX_URL_LENGTH})")
            return jsonify({"error": "url is too long"}), 400

        if not is_youtube_url(url):
            print(f"ERROR: Invalid YouTube URL: {url}")
            return jsonify({"error": "Only YouTube URLs are accepted."}), 400

        temp_dir = None
        try:
            path, temp_dir = download_youtube(url)
            download_name = safe_filename(path)
            print(f"Sending file: {path.name} as {download_name}")

            response = send_file(
                path,
                as_attachment=True,
                download_name=download_name,
                mimetype="video/mp4" if path.suffix.lower() == ".mp4" else "application/octet-stream",
                max_age=0,
            )

            def cleanup():
                try:
                    if temp_dir:
                        temp_dir.cleanup()
                        print(f"Cleaned up temporary directory: {temp_dir.name}")
                except Exception as e:
                    print(f"Failed to clean temporary download directory: {e}")

            response.call_on_close(cleanup)
            return response

        except requests.HTTPError as e:
            print(f"HTTP Error from Webshare API: {e}")
            if temp_dir is not None:
                temp_dir.cleanup()
            error_msg = f"Webshare API error: {e}"
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response status: {e.response.status_code}")
                print(f"Response body: {e.response.text[:500]}")
            return jsonify({"error": error_msg}), 502

        except yt_dlp.utils.DownloadError as e:
            print(f"yt-dlp download failed: {e}")
            if temp_dir is not None:
                temp_dir.cleanup()
            return jsonify({"error": f"yt-dlp download failed: {str(e)}"}), 502

        except FileNotFoundError as e:
            print(f"File not found error: {e}")
            if temp_dir is not None:
                temp_dir.cleanup()
            return jsonify({"error": f"Downloaded file not found: {str(e)}"}), 404

        except Exception as e:
            print(f"Unexpected download error: {e}")
            if temp_dir is not None:
                temp_dir.cleanup()
            return jsonify({"error": str(e)}), 500

    except Exception as e:
        print(f"Unhandled error in api_download: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.errorhandler(HTTPException)
def handle_http_exception(exc):
    print(f"HTTP Exception: {exc}")
    return jsonify({"error": exc.description}), exc.code


@app.errorhandler(Exception)
def handle_unexpected_exception(exc):
    print(f"Unhandled application error: {exc}")
    return jsonify({"error": str(exc)}), 500


HTML_PAGE = r"""
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>YouTube Downloader API Test</title>
  <style>
    body {
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
      max-width: 760px;
      margin: 40px auto;
      padding: 0 16px;
      line-height: 1.6;
    }
    input, button {
      font: inherit;
      padding: 10px 12px;
      box-sizing: border-box;
    }
    input[type="url"] {
      width: 100%;
    }
    button {
      margin-top: 12px;
      cursor: pointer;
    }
    .card {
      border: 1px solid #ddd;
      border-radius: 12px;
      padding: 18px;
      margin-bottom: 16px;
    }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: #f6f6f6;
      padding: 12px;
      border-radius: 8px;
    }
    .status {
      margin-top: 12px;
      font-weight: 600;
    }
    small {
      color: #666;
    }
    .error {
      color: #d32f2f;
    }
    .success {
      color: #2e7d32;
    }
  </style>
</head>
<body>
  <h1>YouTube Downloader API</h1>

  <div class="card">
    <form id="download-form">
      <label for="url">YouTube URL</label>
      <input id="url" name="url" type="url"
             placeholder="https://www.youtube.com/watch?v=..."
             required>
      <button type="submit">ダウンロード</button>
      <div id="status" class="status"></div>
    </form>
    <p><small>
      このAPIはYouTube URLのみを受け付け、yt-dlp + Webshareプロキシを使ってMP4を生成します。
      権利・利用規約を守って利用してください。
    </small></p>
  </div>

  <div class="card">
    <h2>Health</h2>
    <button id="health-button" type="button">/api/health を確認</button>
    <pre id="health-output">未確認</pre>
  </div>

<script>
const statusEl = document.getElementById("status");

document.getElementById("download-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  statusEl.textContent = "ダウンロード中…";
  statusEl.className = "status";

  const url = document.getElementById("url").value.trim();

  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url})
    });

    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const data = await response.json();
        if (data.error) message += `: ${data.error}`;
      } catch (_) {}
      throw new Error(message);
    }

    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename\\*?=(?:UTF-8''|")?([^";]+)/i);
    const filename = match ? decodeURIComponent(match[1].replace(/"/g, "")) : "video.mp4";

    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);

    statusEl.textContent = "✅ ダウンロード完了";
    statusEl.className = "status success";
  } catch (error) {
    statusEl.textContent = `❌ エラー: ${error.message}`;
    statusEl.className = "status error";
  }
});

document.getElementById("health-button").addEventListener("click", async () => {
  const output = document.getElementById("health-output");
  output.textContent = "確認中…";
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    output.textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    output.textContent = `エラー: ${error.message}`;
  }
});
</script>
</body>
</html>
"""

# Detect targets once when the worker imports this module.
try:
    print("Initializing YouTube Downloader API...")
    impersonate_target = discover_impersonate_target()
    print(f"Initialization complete. Impersonate target: {impersonate_target}")
except Exception as e:
    print(f"Initialization error: {e}")
    impersonate_target = None


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    print(f"Starting server on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
