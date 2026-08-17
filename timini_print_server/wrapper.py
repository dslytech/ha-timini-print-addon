"""Tiny, dependency-free HTTP wrapper around TiMini-Print's own
command-line client (timiniprint_command_line.py), so it can be
triggered over the network from Home Assistant (or a plain browser,
for manual testing) instead of only interactively from a shell.

This does not modify TiMini-Print's own code at all - it only shells
out to its documented CLI flags (--scan, --text, --bluetooth), exactly
as you would by hand over SSH.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLI = ["python3", "timiniprint_command_line.py"]
CWD = "/opt/timini-print"

PRINTER_NAME = os.environ.get("TIMINI_PRINTER_NAME") or None

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".pdf"}

# Only one Bluetooth operation (scan or print) can sanely run at a time
# against a single physical adapter - this lock serializes actual CLI
# calls while still letting the HTTP layer itself accept and hold
# multiple connections concurrently (see ThreadingHTTPServer below).
# Without this, a burst of requests (e.g. impatiently re-clicking Scan,
# or the Lovelace card and a manual test overlapping) would previously
# queue up behind Python's single-threaded HTTPServer and often time
# out client-side before ever being served, surfacing as
# `BrokenPipeError` in the log and "Could not reach the add-on" in
# Home Assistant even though the add-on was actually fine, just busy.
_CLI_LOCK = threading.Lock()

# NOTE: earlier versions of this add-on rendered text into an image
# ourselves (via Pillow) to control its printed size, working around
# what looked like TiMini-Print's image auto-crop discarding any size
# we chose. Digging into TiMini-Print's actual source (shared directly
# by the person setting this add-on up - see git history / README for
# the story) turned up the REAL, native, intended way to control text
# size: `timiniprint_command_line.py --text-columns N` (fewer columns
# = bigger letters), exactly matching the "Characters per line"
# setting in the project's own Android app. That's what's used below -
# no Pillow rendering, no cropping workarounds, no guessing.

INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>TiMini Print</title>
<style>
  :root[data-theme="dark"] {
    --bg: #10131a; --panel: #1a1f2b; --accent: #ff9f4a; --accent2: #4ac3ff;
    --text: #e8ecf5; --muted: #8b93a7; --border: #2a3140; --pre-bg: #0d1017;
    --pre-text: #b9d9c4;
  }
  :root[data-theme="light"] {
    --bg: #f4f5f8; --panel: #ffffff; --accent: #d9700c; --accent2: #0d7cb5;
    --text: #1b1f27; --muted: #666f80; --border: #d8dce3; --pre-bg: #f0f2f5;
    --pre-text: #1f4d31;
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text);
    max-width: 40rem; margin: 0 auto; padding: 2rem 1.25rem 4rem;
    font-size: var(--fs, 16px);
    transition: background .15s ease, color .15s ease;
  }
  .toolbar {
    display: flex; justify-content: flex-end; gap: .4rem; margin-bottom: 1rem;
  }
  .toolbar button {
    background: var(--panel); border: 1px solid var(--border); color: var(--text);
    border-radius: 8px; padding: .35rem .6rem; font-size: .8rem; cursor: pointer;
  }
  .toolbar button.active { border-color: var(--accent2); color: var(--accent2); }
  h1 { display: flex; align-items: center; gap: .6rem; font-size: 1.6em; margin-bottom: .2rem; }
  h1::before { content: "🐱🖨️"; font-size: .9em; }
  .subtitle { color: var(--muted); font-size: .92em; margin-bottom: 2rem; line-height: 1.5; }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
    padding: 1.25rem 1.4rem; margin-bottom: 1.5rem;
  }
  .card h2 {
    font-size: 1.05em; margin: 0 0 .9rem; display: flex; align-items: center; gap: .5rem;
  }
  .card h2 .dot { width: .55em; height: .55em; border-radius: 50%; background: var(--accent2); }
  .card.print h2 .dot { background: var(--accent); }
  button {
    background: linear-gradient(180deg, color-mix(in srgb, var(--panel) 70%, var(--text) 8%), var(--panel));
    color: var(--text); border: 1px solid var(--border); border-radius: 9px;
    padding: .6rem 1.1rem; font-size: .92em; cursor: pointer; margin-right: .5rem;
    transition: transform .05s ease, border-color .15s ease;
  }
  button:hover { border-color: var(--accent2); }
  button:active { transform: scale(.97); }
  button.primary {
    background: linear-gradient(180deg, var(--accent), color-mix(in srgb, var(--accent) 70%, black));
    color: #1a0e00; border: none; font-weight: 600;
  }
  button.primary:hover { filter: brightness(1.08); }
  select, textarea, input[type="number"] {
    width: 100%; background: var(--pre-bg); color: var(--text); border: 1px solid var(--border);
    border-radius: 9px; padding: .6rem .8rem; font-size: .95em; resize: vertical;
    font-family: inherit; margin-bottom: .8rem;
  }
  textarea:focus, select:focus, input:focus, button:focus { outline: 2px solid var(--accent2); outline-offset: 1px; }
  pre {
    background: var(--pre-bg); border: 1px solid var(--border); border-radius: 9px;
    padding: .8rem .9rem; font-size: .82em; overflow-x: auto; white-space: pre-wrap;
    word-break: break-word; color: var(--pre-text); margin-top: .9rem; min-height: 1.2em;
  }
  label { display: block; font-size: .85em; color: var(--muted); margin-bottom: .3rem; }
  .hint { font-size: .8em; color: var(--muted); margin-top: .6rem; line-height: 1.5; }
</style>
</head>
<body>
<div class="toolbar">
  <button id="theme-dark" onclick="setTheme('dark')">Dark</button>
  <button id="theme-light" onclick="setTheme('light')">Light</button>
</div>

<h1>TiMini Print</h1>
<p class="subtitle">Minimal test UI for the TiMini Print Server add-on. For
real automations, call this add-on's HTTP API directly (see the add-on's
README) - this page just lets you try a scan/print from a browser
without SSH.</p>

<div class="card">
  <h2><span class="dot"></span>Scan</h2>
  <button onclick="doScan()">Scan for printers</button>
  <button onclick="doHelp()">Show CLI options</button>
  <pre id="scan-result"></pre>
  <label for="printer-select">Printer</label>
  <select id="printer-select">
    <option value="">Auto (first supported printer found - scan to pick a specific one)</option>
  </select>
  <label style="display:flex; align-items:center; gap:.4rem; font-size:.85em; color:var(--muted); margin-bottom:.6rem; cursor:pointer;">
    <input type="checkbox" id="unsupported-checkbox" onchange="onUnsupportedToggle()">
    Unsupported / unrecognized device (let me pick its model manually)
  </label>
  <div id="model-row" style="display:none; margin-bottom:.8rem;">
    <label for="printer-model-input">Model</label>
    <input type="text" id="printer-model-input" list="model-list" placeholder="loading models...">
    <datalist id="model-list"></datalist>
  </div>
  <p class="hint">This printer/model selection is shared by both cards
  below. Scan always shows every discovered Bluetooth device - the
  checkbox above doesn't change what gets found, it just reveals the
  model field for devices TiMini-Print didn't auto-recognize.</p>
</div>

<div class="card print">
  <h2><span class="dot"></span>Print text</h2>
  <label for="text-columns-input">Characters per line (fewer = bigger text, leave blank for automatic)</label>
  <input type="number" id="text-columns-input" min="1" max="200" step="1" placeholder="auto"
    style="width:100%; margin-bottom:.8rem;">
  <label for="darkness-text-input">Print darkness (1-5)</label>
  <input type="number" id="darkness-text-input" min="1" max="5" step="1" value="3"
    style="width:100%; margin-bottom:.8rem;">
  <textarea id="text" rows="4" placeholder="Type something to print..."></textarea>
  <button class="primary" onclick="doPrint()">Print</button>
  <pre id="print-result"></pre>
  <p class="hint">Uses TiMini-Print's own native text rendering
  (<code>--text-columns</code>, matching the "Characters per line"
  setting in the project's own Android app - not an image we render
  ourselves). Each print connects, sends the job, then disconnects
  (fire and forget, no persistent background connection) - so the
  printer's LED will blink again shortly after a successful print.
  That's expected, not an error.</p>
</div>

<div class="card print">
  <h2><span class="dot"></span>Print image or PDF</h2>
  <label for="darkness-image-input">Print darkness (1-5)</label>
  <input type="number" id="darkness-image-input" min="1" max="5" step="1" value="3"
    style="width:100%; margin-bottom:.8rem;">
  <label for="image-file">Image (.png .jpg .jpeg .gif .bmp) or PDF</label>
  <input type="file" id="image-file" accept=".png,.jpg,.jpeg,.gif,.bmp,.pdf" onchange="previewImage()">
  <canvas id="image-preview-canvas" style="max-width:100%; border-radius:9px; border:1px solid var(--border); margin:.6rem 0; display:none;"></canvas>
  <div id="pdf-preview-label" class="hint" style="display:none;">PDF selected - all pages will be printed. No inline preview.</div>
  <div id="darkness-row" style="display:none; margin-bottom:.8rem;">
    <label for="darkness-slider">Brightness adjustment: <span id="darkness-value">0</span></label>
    <input type="range" id="darkness-slider" min="-120" max="120" step="5" value="0" style="width:100%;" oninput="onDarknessChange()">
    <p class="hint" style="margin-top:.3rem;">The preview is dithered (not a hard black/white cutoff), so detail and gradients are preserved - this slider shifts the whole image lighter or darker before dithering, rather than losing detail at the extremes. This is exactly what gets sent to the printer.</p>
  </div>
  <button class="primary" onclick="doPrintImage()">Print image/PDF</button>
  <pre id="print-image-result"></pre>
</div>

<script>
window.timiniImageState = { img: null, isPdf: false, fileName: '' };
function ditherImageData(imgData, brightnessOffset) {
    const w = imgData.width, h = imgData.height;
    const d = imgData.data;
    const gray = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) {
        const r = d[i * 4], g = d[i * 4 + 1], b = d[i * 4 + 2];
        let v = 0.299 * r + 0.587 * g + 0.114 * b + brightnessOffset;
        gray[i] = Math.max(0, Math.min(255, v));
    }
    for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
            const idx = y * w + x;
            const old = gray[idx];
            const newVal = old < 128 ? 0 : 255;
            const err = old - newVal;
            gray[idx] = newVal;
            if (x + 1 < w) gray[idx + 1] += err * 7 / 16;
            if (y + 1 < h) {
                if (x > 0) gray[idx + w - 1] += err * 3 / 16;
                gray[idx + w] += err * 5 / 16;
                if (x + 1 < w) gray[idx + w + 1] += err * 1 / 16;
            }
        }
    }
    for (let i = 0; i < w * h; i++) {
        d[i * 4] = d[i * 4 + 1] = d[i * 4 + 2] = gray[i];
    }
}
function applyThreshold() {
    const state = window.timiniImageState;
    if (!state.img) return;
    const canvas = document.getElementById('image-preview-canvas');
    const brightnessOffset = parseInt(document.getElementById('darkness-slider').value, 10);
    const maxWidth = 600;
    const scale = Math.min(1, maxWidth / state.img.naturalWidth);
    const w = Math.round(state.img.naturalWidth * scale);
    const h = Math.round(state.img.naturalHeight * scale);
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(state.img, 0, 0, w, h);
    const imgData = ctx.getImageData(0, 0, w, h);
    ditherImageData(imgData, brightnessOffset);
    ctx.putImageData(imgData, 0, 0);
    canvas.style.display = 'block';
}
function onDarknessChange() {
    document.getElementById('darkness-value').innerText = document.getElementById('darkness-slider').value;
    applyThreshold();
}
</script>
<script>
function setTheme(mode) {
    document.documentElement.setAttribute('data-theme', mode);
    localStorage.setItem('timini-theme', mode);
    document.getElementById('theme-dark').classList.toggle('active', mode === 'dark');
    document.getElementById('theme-light').classList.toggle('active', mode === 'light');
}
setTheme(localStorage.getItem('timini-theme') || 'dark');

function populatePrinterSelect(scanStdout) {
    const select = document.getElementById('printer-select');
    while (select.options.length > 1) select.remove(1);
    (scanStdout || '').split('\\n').map(l => l.trim()).filter(Boolean).forEach(line => {
        const name = line.split(/\\s/)[0];
        if (!name) return;
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = line;
        select.appendChild(opt);
    });
}
async function doScan() {
    document.getElementById('scan-result').innerText = 'Scanning...';
    const r = await fetch('/scan');
    const j = await r.json();
    document.getElementById('scan-result').innerText = JSON.stringify(j, null, 2);
    populatePrinterSelect(j.stdout);
}
async function doHelp() {
    document.getElementById('scan-result').innerText = 'Loading...';
    const r = await fetch('/help');
    const j = await r.json();
    document.getElementById('scan-result').innerText = j.stdout || j.stderr;
}

let modelListCache = null;
async function ensureModelListLoaded() {
    if (modelListCache) return modelListCache;
    const r = await fetch('/list-models');
    const j = await r.json();
    modelListCache = j.models || [];
    const datalist = document.getElementById('model-list');
    datalist.innerHTML = '';
    for (const m of modelListCache) {
        const opt = document.createElement('option');
        opt.value = m.key;
        opt.label = m.label;
        datalist.appendChild(opt);
    }
    return modelListCache;
}
async function onUnsupportedToggle() {
    const checked = document.getElementById('unsupported-checkbox').checked;
    const row = document.getElementById('model-row');
    row.style.display = checked ? 'block' : 'none';
    if (checked) {
        const input = document.getElementById('printer-model-input');
        input.placeholder = 'loading models...';
        const models = await ensureModelListLoaded();
        input.placeholder = models.length
            ? 'type to search ' + models.length + ' models...'
            : 'no models found';
    }
}

async function doPrint() {
    document.getElementById('print-result').innerText = 'Printing...';
    const text = document.getElementById('text').value;
    const printer = document.getElementById('printer-select').value;
    const columnsRaw = document.getElementById('text-columns-input').value;
    const darkness = document.getElementById('darkness-text-input').value;
    const body = {text};
    if (columnsRaw) body.text_columns = parseInt(columnsRaw, 10);
    if (darkness) body.darkness = parseInt(darkness, 10);
    if (printer) body.printer = printer;
    const printerModel = document.getElementById('printer-model-input').value;
    if (printerModel) body.printer_model = printerModel;
    const r = await fetch('/print', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
    });
    const j = await r.json();
    document.getElementById('print-result').innerText = JSON.stringify(j, null, 2);
}

let selectedFile = null;

function previewImage() {
    const input = document.getElementById('image-file');
    const canvas = document.getElementById('image-preview-canvas');
    const pdfLabel = document.getElementById('pdf-preview-label');
    const darknessRow = document.getElementById('darkness-row');
    selectedFile = input.files[0] || null;
    canvas.style.display = 'none';
    pdfLabel.style.display = 'none';
    darknessRow.style.display = 'none';
    window.timiniImageState.img = null;
    window.timiniImageState.isPdf = false;
    if (!selectedFile) return;
    window.timiniImageState.fileName = selectedFile.name;
    if (selectedFile.type === 'application/pdf' || selectedFile.name.toLowerCase().endsWith('.pdf')) {
        window.timiniImageState.isPdf = true;
        pdfLabel.style.display = 'block';
        return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
        const img = new Image();
        img.onload = () => {
            window.timiniImageState.img = img;
            darknessRow.style.display = 'block';
            applyThreshold();
        };
        img.src = e.target.result;
    };
    reader.readAsDataURL(selectedFile);
}

async function doPrintImage() {
    const resultEl = document.getElementById('print-image-result');
    if (!selectedFile) {
        resultEl.innerText = 'Pick a file first.';
        return;
    }
    resultEl.innerText = 'Uploading and printing...';
    const printer = document.getElementById('printer-select').value;
    const state = window.timiniImageState;

    const send = (base64, filename) => {
        const body = {image_b64: base64, filename};
        if (printer) body.printer = printer;
        const darkness = document.getElementById('darkness-image-input').value;
        if (darkness) body.darkness = parseInt(darkness, 10);
        const printerModel = document.getElementById('printer-model-input').value;
        if (printerModel) body.printer_model = printerModel;
        fetch('/print_image', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        }).then(r => r.json()).then(j => {
            resultEl.innerText = JSON.stringify(j, null, 2);
        });
    };

    if (state.isPdf) {
        // PDFs can't be canvas-thresholded client-side - send the
        // original file unchanged, same as before.
        const reader = new FileReader();
        reader.onload = (e) => {
            const base64 = e.target.result.split(',', 2)[1];
            send(base64, selectedFile.name);
        };
        reader.readAsDataURL(selectedFile);
    } else {
        // Send exactly what the black/white preview shows (already
        // thresholded at the chosen darkness level), as a PNG.
        const canvas = document.getElementById('image-preview-canvas');
        const dataUrl = canvas.toDataURL('image/png');
        const base64 = dataUrl.split(',', 2)[1];
        send(base64, state.fileName.replace(/\\.[^.]+$/, '') + '.png');
    }
}
</script>
</body></html>
"""


def run_cli(args, timeout=60):
    with _CLI_LOCK:
        try:
            result = subprocess.run(
                CLI + args,
                cwd=CWD,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired as err:
            def _decode(x):
                if x is None:
                    return ""
                return x.decode("utf-8", "replace") if isinstance(x, bytes) else x
            return {
                "returncode": None,
                "stdout": _decode(err.stdout),
                "stderr": _decode(err.stderr) + "\n[wrapper] Timed out.",
            }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json;charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The client (browser) gave up waiting and closed the
            # connection before we could respond - harmless and
            # common (e.g. a page refresh while a slower request like
            # /list-models was still in flight). Nothing to recover;
            # just don't let it print a scary traceback to the log.
            pass

    def do_GET(self):    # pylint: disable=invalid-name
        if self.path == "/" or self.path == "/index.html":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html;charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/scan":
            result = run_cli(["--scan"], timeout=30)
            self._send_json(200, result)
            return
        if self.path == "/help":
            result = run_cli(["--help"], timeout=15)
            self._send_json(200, result)
            return
        if self.path == "/list-models":
            result = run_cli(["--list-models"], timeout=15)
            models = []
            for line in (result.get("stdout") or "").splitlines():
                line = line.strip()
                if not line or ":" not in line:
                    continue
                key = line.split(":", 1)[0].strip()
                if key:
                    models.append({"key": key, "label": line})
            self._send_json(200, {"models": models, "raw": result})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):    # pylint: disable=invalid-name
        if self.path == "/print":
            self._handle_print_text()
            return
        if self.path == "/print_image":
            self._handle_print_image()
            return
        self._send_json(404, {"error": "not found"})

    def _handle_print_text(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON body"})
            return
        text = data.get("text")
        if not text:
            self._send_json(400, {"error": "missing 'text' field"})
            return

        args = ["--text", text]

        columns = data.get("text_columns")
        if columns not in (None, "", 0):
            try:
                columns = int(columns)
            except (TypeError, ValueError):
                self._send_json(400, {"error": "'text_columns' must be a number"})
                return
            if columns < 1:
                self._send_json(400, {"error": "'text_columns' must be at least 1"})
                return
            args = ["--text-columns", str(columns)] + args

        darkness = data.get("darkness")
        if darkness not in (None, ""):
            try:
                darkness = int(darkness)
            except (TypeError, ValueError):
                self._send_json(400, {"error": "'darkness' must be a number"})
                return
            if not 1 <= darkness <= 5:
                self._send_json(400, {"error": "'darkness' must be between 1 and 5"})
                return
            args = ["--darkness", str(darkness)] + args

        printer_model = data.get("printer_model")
        if printer_model:
            args = ["--printer-model", str(printer_model)] + args

        printer = data.get("printer") or PRINTER_NAME
        if printer:
            args = ["--bluetooth", printer] + args

        result = run_cli(args, timeout=180)
        status = 200 if result.get("returncode") == 0 else 500
        self._send_json(status, result)

    @staticmethod
    def _print_local_file(tmp_path: str, printer: str | None, timeout: int, darkness=None, printer_model=None):
        args = []
        if printer_model:
            args = ["--printer-model", str(printer_model)] + args
        if darkness not in (None, ""):
            args = ["--darkness", str(darkness)] + args
        if printer:
            args = ["--bluetooth", printer] + args
        args = args + [tmp_path]
        return run_cli(args, timeout=timeout)

    def _handle_print_image(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON body"})
            return

        image_b64 = data.get("image_b64")
        filename = data.get("filename") or ""
        if not image_b64:
            self._send_json(400, {"error": "missing 'image_b64' field"})
            return

        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            self._send_json(
                400,
                {
                    "error": (
                        f"unsupported or missing file extension "
                        f"'{ext}' - expected one of "
                        f"{sorted(ALLOWED_IMAGE_EXTENSIONS)}"
                    )
                },
            )
            return

        try:
            if "," in image_b64 and image_b64.strip().startswith("data:"):
                image_b64 = image_b64.split(",", 1)[1]
            image_bytes = base64.b64decode(image_b64, validate=True)
        except (ValueError, base64.binascii.Error):    # type: ignore[attr-defined]
            self._send_json(400, {"error": "invalid base64 in 'image_b64'"})
            return

        darkness = data.get("darkness")
        if darkness not in (None, ""):
            try:
                darkness = int(darkness)
            except (TypeError, ValueError):
                self._send_json(400, {"error": "'darkness' must be a number"})
                return
            if not 1 <= darkness <= 5:
                self._send_json(400, {"error": "'darkness' must be between 1 and 5"})
                return
        else:
            darkness = None

        tmp_path = os.path.join(
            tempfile.gettempdir(), f"timini-upload-{uuid.uuid4().hex}{ext}"
        )
        try:
            with open(tmp_path, "wb") as f:
                f.write(image_bytes)
            printer = data.get("printer") or PRINTER_NAME
            printer_model = data.get("printer_model")
            result = self._print_local_file(tmp_path, printer, timeout=240, darkness=darkness, printer_model=printer_model)
            status = 200 if result.get("returncode") == 0 else 500
            self._send_json(status, result)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def main():
    port = 8096
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    print(f"TiMini Print wrapper listening on 0.0.0.0:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
