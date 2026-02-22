"""
SLS Certificate & Name Tent Generator — Flask Web App
PDF generation uses PyMuPDF (fitz) to fill fields, render Museo fonts correctly,
and fully flatten the output so text cannot be edited afterwards.
"""

import csv
import io
import os
import tempfile
import zipfile

import fitz  # PyMuPDF
from flask import Flask, request, send_file, jsonify, Response

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB upload limit

TEMPLATES_DIR = os.path.dirname(os.path.abspath(__file__))

def _find_template(filename):
    """Search common locations for a template PDF relative to app.py."""
    candidates = [
        os.path.join(TEMPLATES_DIR, filename),
        os.path.join(TEMPLATES_DIR, "resources", filename),
        os.path.join(TEMPLATES_DIR, "templates", filename),
        os.path.join(TEMPLATES_DIR, "static", filename),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]

STAFF_PDF       = _find_template("Staff.pdf")
PARTICIPANT_PDF = _find_template("Participant.pdf")
TENT_PDF        = _find_template("SLS_Name_Tent.pdf")


# ── Startup check ─────────────────────────────────────────────────────────────

def _check_templates():
    all_ok = True
    for label, path in [("Staff.pdf", STAFF_PDF),
                        ("Participant.pdf", PARTICIPANT_PDF),
                        ("SLS_Name_Tent.pdf", TENT_PDF)]:
        if os.path.exists(path):
            print(f"[SLS] Template OK:      {label}  ({path})", flush=True)
        else:
            print(f"[SLS] Template MISSING: {label}  ({path})", flush=True)
            all_ok = False
    if all_ok:
        print("[SLS] All templates found. Ready.", flush=True)
    else:
        print("[SLS] WARNING: one or more templates are missing.", flush=True)
    return all_ok

try:
    _templates_ok = _check_templates()
except Exception as _e:
    import traceback
    print(f"[SLS] Startup check failed: {_e}", flush=True)
    print(traceback.format_exc(), flush=True)
    _templates_ok = False


# ── PDF helpers ───────────────────────────────────────────────────────────────

def fill_and_flatten(template_path, field_values, output_path):
    """Fill form fields using PyMuPDF, then call bake() to:
      - Render text using the fonts defined in each field's /DA string
        (MuseoSlab-700 for certs, MuseoSans-700/500Italic for tents)
      - Remove all interactive form annotations
      - Produce a static, uneditable PDF
    """
    doc = fitz.open(template_path)
    page = doc[0]
    for widget in page.widgets():
        if widget.field_name in field_values:
            widget.field_value = field_values[widget.field_name]
            widget.update()
    doc.bake()
    doc.save(output_path, garbage=4, deflate=True)
    doc.close()


def build_merged_pdf(page_paths, output_path):
    """Merge a list of single-page PDFs into one document using PyMuPDF."""
    merged = fitz.open()
    for path in page_paths:
        src = fitz.open(path)
        merged.insert_pdf(src)
        src.close()
    merged.save(output_path, garbage=4, deflate=True)
    merged.close()


def generate_certificates(entries, section, output_path):
    with tempfile.TemporaryDirectory() as tmpdir:
        pages = []
        for i, entry in enumerate(entries):
            template = STAFF_PDF if entry["role"].lower() == "staff" \
                       else PARTICIPANT_PDF
            page_path = os.path.join(tmpdir, f"cert_{i:04d}.pdf")
            fill_and_flatten(template,
                             {"Name": entry["name"], "Section": section},
                             page_path)
            pages.append(page_path)
        build_merged_pdf(pages, output_path)


def generate_name_tents(entries, output_path):
    with tempfile.TemporaryDirectory() as tmpdir:
        pages = []
        for i, entry in enumerate(entries):
            name_value  = "\n" + entry["name"]
            lodge_value = entry["lodge"]
            if entry["role"].lower() == "staff":
                lodge_value = "STAFF - " + lodge_value
            page_path = os.path.join(tmpdir, f"tent_{i:04d}.pdf")
            fill_and_flatten(TENT_PDF,
                             {"Name": name_value, "Lodge": lodge_value},
                             page_path)
            pages.append(page_path)
        build_merged_pdf(pages, output_path)


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_csv(text):
    entries = []
    reader = csv.DictReader(io.StringIO(text.strip()))
    for row in reader:
        r = {k.strip().lower(): v.strip() for k, v in row.items()}
        name  = r.get("name", "").strip()
        lodge = r.get("lodge", "").strip()
        role  = r.get("role", "Participant").strip()
        if name:
            entries.append({"name": name, "lodge": lodge, "role": role})
    return entries


def parse_plain(text):
    entries = []
    for line in text.strip().splitlines():
        name = line.strip()
        if name:
            entries.append({"name": name, "lodge": "", "role": "Participant"})
    return entries


# ── Embedded HTML ─────────────────────────────────────────────────────────────

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SLS Generator — Order of the Arrow</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700&family=Crimson+Pro:ital,wght@0,300;0,400;0,600;1,300;1,400&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --cream:    #F7F3EC;
      --parchment:#EDE5D4;
      --gold:     #8B6914;
      --gold-lt:  #C49A2A;
      --bark:     #2C1F0E;
      --moss:     #2D4A2D;
      --red:      #8B1A1A;
      --border:   #C4A96B;
      --shadow:   rgba(44,31,14,0.18);
    }

    html { font-size: 16px; scroll-behavior: smooth; }

    body {
      font-family: 'Crimson Pro', Georgia, serif;
      background: var(--cream);
      color: var(--bark);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    /* ── Texture overlay ── */
    body::before {
      content: '';
      position: fixed; inset: 0;
      background-image:
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='300' height='300' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");
      pointer-events: none;
      z-index: 0;
    }

    /* ── Header ── */
    header {
      position: relative; z-index: 1;
      background: var(--bark);
      border-bottom: 3px solid var(--gold);
      padding: 2rem 2rem 1.6rem;
      text-align: center;
    }

    header::after {
      content: '';
      display: block;
      height: 1px;
      background: var(--gold-lt);
      opacity: 0.4;
      margin-top: 1.4rem;
    }

    .header-eyebrow {
      font-family: 'Cinzel', serif;
      font-size: 0.7rem;
      letter-spacing: 0.25em;
      color: var(--gold-lt);
      text-transform: uppercase;
      margin-bottom: 0.5rem;
    }

    .header-title {
      font-family: 'Cinzel', serif;
      font-size: clamp(1.4rem, 3.5vw, 2.2rem);
      font-weight: 700;
      color: var(--cream);
      line-height: 1.2;
      letter-spacing: 0.04em;
    }

    .header-title span {
      color: var(--gold-lt);
    }

    .header-sub {
      font-family: 'Crimson Pro', serif;
      font-style: italic;
      font-size: 1.05rem;
      color: #A89070;
      margin-top: 0.35rem;
    }

    /* ── Main layout ── */
    main {
      position: relative; z-index: 1;
      flex: 1;
      max-width: 780px;
      width: 100%;
      margin: 0 auto;
      padding: 2.5rem 1.5rem 4rem;
    }

    /* ── Card ── */
    .card {
      background: #FFFDF8;
      border: 1px solid var(--border);
      border-radius: 2px;
      box-shadow: 0 4px 24px var(--shadow), 0 1px 0 var(--gold) inset;
      padding: 2.2rem 2.4rem;
      margin-bottom: 1.5rem;
      position: relative;
    }

    .card::before {
      content: '';
      position: absolute;
      top: 6px; left: 6px; right: 6px; bottom: 6px;
      border: 1px solid rgba(196,169,107,0.2);
      border-radius: 1px;
      pointer-events: none;
    }

    /* ── Section heading ── */
    .section-label {
      font-family: 'Cinzel', serif;
      font-size: 0.65rem;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: var(--gold);
      margin-bottom: 1.2rem;
      display: flex;
      align-items: center;
      gap: 0.8rem;
    }

    .section-label::after {
      content: '';
      flex: 1;
      height: 1px;
      background: linear-gradient(to right, var(--border), transparent);
    }

    /* ── Form elements ── */
    .field { margin-bottom: 1.4rem; }

    label {
      display: block;
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--bark);
      margin-bottom: 0.4rem;
      letter-spacing: 0.01em;
    }

    label .hint {
      font-weight: 300;
      font-style: italic;
      color: #8A7355;
      font-size: 0.82rem;
    }

    input[type="text"],
    textarea,
    select {
      width: 100%;
      font-family: 'Crimson Pro', serif;
      font-size: 1rem;
      color: var(--bark);
      background: var(--cream);
      border: 1px solid var(--border);
      border-radius: 2px;
      padding: 0.55rem 0.75rem;
      transition: border-color 0.2s, box-shadow 0.2s;
      outline: none;
    }

    input[type="text"]:focus,
    textarea:focus {
      border-color: var(--gold);
      box-shadow: 0 0 0 3px rgba(139,105,20,0.1);
    }

    textarea {
      resize: vertical;
      min-height: 160px;
      font-size: 0.92rem;
      font-family: 'Courier New', monospace;
      line-height: 1.5;
    }

    /* ── Radio / toggle groups ── */
    .toggle-group {
      display: flex;
      gap: 0;
      border: 1px solid var(--border);
      border-radius: 2px;
      overflow: hidden;
    }

    .toggle-group input[type="radio"] { display: none; }

    .toggle-group label {
      flex: 1;
      margin: 0;
      padding: 0.55rem 0.5rem;
      text-align: center;
      font-size: 0.88rem;
      font-weight: 400;
      cursor: pointer;
      border-right: 1px solid var(--border);
      background: var(--cream);
      color: #7A6040;
      transition: background 0.15s, color 0.15s;
    }

    .toggle-group label:last-child { border-right: none; }

    .toggle-group input[type="radio"]:checked + label {
      background: var(--bark);
      color: var(--cream);
      font-weight: 600;
    }

    /* ── File drop zone ── */
    .drop-zone {
      border: 2px dashed var(--border);
      border-radius: 2px;
      padding: 1.4rem 1rem;
      text-align: center;
      cursor: pointer;
      transition: border-color 0.2s, background 0.2s;
      background: var(--cream);
      position: relative;
    }

    .drop-zone:hover,
    .drop-zone.dragover { border-color: var(--gold); background: #F5EDD8; }

    .drop-zone input[type="file"] {
      position: absolute; inset: 0;
      opacity: 0; cursor: pointer; width: 100%; height: 100%;
    }

    .drop-icon { font-size: 1.6rem; margin-bottom: 0.3rem; color: var(--gold); }

    .drop-label {
      font-size: 0.9rem;
      color: #8A7355;
    }

    .drop-label strong { color: var(--bark); }

    .file-chosen {
      margin-top: 0.5rem;
      font-size: 0.82rem;
      color: var(--moss);
      font-style: italic;
    }

    /* ── Divider ── */
    .or-divider {
      display: flex;
      align-items: center;
      gap: 0.8rem;
      margin: 1.2rem 0;
      color: #A89070;
      font-style: italic;
      font-size: 0.9rem;
    }

    .or-divider::before,
    .or-divider::after {
      content: '';
      flex: 1;
      height: 1px;
      background: var(--border);
      opacity: 0.6;
    }

    /* ── Submit button ── */
    .btn-generate {
      width: 100%;
      padding: 1rem;
      font-family: 'Cinzel', serif;
      font-size: 0.95rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--cream);
      background: var(--bark);
      border: none;
      border-radius: 2px;
      cursor: pointer;
      position: relative;
      overflow: hidden;
      transition: background 0.2s;
    }

    .btn-generate::before {
      content: '';
      position: absolute;
      inset: 0;
      background: linear-gradient(to bottom, rgba(255,255,255,0.06), transparent);
    }

    .btn-generate:hover { background: #3D2A12; }
    .btn-generate:active { background: #1A0F04; }

    .btn-generate:disabled {
      background: #9A8870;
      cursor: not-allowed;
    }

    /* ── Status / error ── */
    .status-bar {
      margin-top: 1.2rem;
      padding: 0.9rem 1.1rem;
      border-radius: 2px;
      font-size: 0.92rem;
      display: none;
    }

    .status-bar.loading {
      display: flex;
      align-items: center;
      gap: 0.8rem;
      background: var(--parchment);
      border: 1px solid var(--border);
      color: var(--bark);
    }

    .status-bar.error {
      display: block;
      background: #FDF0F0;
      border: 1px solid #D4A0A0;
      color: var(--red);
    }

    .status-bar.success {
      display: block;
      background: #F0F5F0;
      border: 1px solid #90B890;
      color: var(--moss);
    }

    /* ── Spinner ── */
    .spinner {
      width: 18px; height: 18px;
      border: 2px solid var(--border);
      border-top-color: var(--gold);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      flex-shrink: 0;
    }

    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── Format hint table ── */
    .format-hint {
      margin-top: 0.7rem;
      font-size: 0.82rem;
      font-family: 'Courier New', monospace;
      background: var(--parchment);
      border: 1px solid var(--border);
      border-radius: 2px;
      padding: 0.7rem 0.9rem;
      color: #5A4020;
      line-height: 1.6;
    }

    /* ── Footer ── */
    footer {
      position: relative; z-index: 1;
      text-align: center;
      padding: 1.2rem;
      font-size: 0.78rem;
      color: #A89070;
      border-top: 1px solid var(--border);
      font-style: italic;
    }

    /* ── Responsive ── */
    @media (max-width: 520px) {
      .card { padding: 1.4rem 1.2rem; }
      .toggle-group label { font-size: 0.78rem; padding: 0.5rem 0.3rem; }
    }
  </style>
</head>
<body>

<header>
  <p class="header-eyebrow">Order of the Arrow</p>
  <h1 class="header-title">Section Leadership <span>Seminar</span></h1>
  <p class="header-sub">Certificate &amp; Name Tent Generator</p>
</header>

<main>
  <form id="genForm" enctype="multipart/form-data">

    <!-- ── Settings ── -->
    <div class="card">
      <p class="section-label">Settings</p>

      <div class="field">
        <label for="section">Section <span class="hint">— applied to all certificates</span></label>
        <input type="text" id="section" name="section" placeholder="e.g. SE-5" autocomplete="off">
      </div>

      <div class="field">
        <label>Generate</label>
        <div class="toggle-group">
          <input type="radio" name="output_type" id="opt-both" value="both" checked>
          <label for="opt-both">Certificates &amp; Name Tents</label>
          <input type="radio" name="output_type" id="opt-certs" value="certificates">
          <label for="opt-certs">Certificates Only</label>
          <input type="radio" name="output_type" id="opt-tents" value="tents">
          <label for="opt-tents">Name Tents Only</label>
        </div>
      </div>

      <div class="field">
        <label>Input Format</label>
        <div class="toggle-group">
          <input type="radio" name="input_mode" id="mode-csv" value="csv" checked>
          <label for="mode-csv">CSV &nbsp;(Name, Lodge, Role)</label>
          <input type="radio" name="input_mode" id="mode-plain" value="plain">
          <label for="mode-plain">Plain Text &nbsp;(one name per line)</label>
        </div>
      </div>
    </div>

    <!-- ── Input ── -->
    <div class="card">
      <p class="section-label">Names</p>

      <div class="field">
        <label>Upload a CSV file</label>
        <div class="drop-zone" id="dropZone">
          <input type="file" name="csv_file" id="fileInput" accept=".csv,.txt">
          <div class="drop-icon">&#8659;</div>
          <div class="drop-label"><strong>Choose a file</strong> or drag and drop here</div>
          <div class="file-chosen" id="fileChosen"></div>
        </div>
      </div>

      <div class="or-divider">or paste directly</div>

      <div class="field">
        <label for="input_text">Input Data</label>
        <textarea id="input_text" name="input_text" spellcheck="false"
          placeholder="Name,Lodge,Role&#10;Donathan Linebrink,Shenandoah,Staff&#10;David Gosik,Blue Heron,Participant"></textarea>
        <div class="format-hint" id="formatHint">
          CSV: &nbsp;Name,Lodge,Role<br>
          &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Donathan Linebrink,Shenandoah,Staff<br>
          &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;David Gosik,Blue Heron,Participant
        </div>
      </div>

      <button type="submit" class="btn-generate" id="submitBtn">Generate PDF</button>

      <div class="status-bar loading" id="statusLoading">
        <div class="spinner"></div>
        <span>Generating your documents&hellip;</span>
      </div>
      <div class="status-bar error" id="statusError"></div>
      <div class="status-bar success" id="statusSuccess"></div>
    </div>

  </form>
</main>

<footer>
  Section Leadership Seminar &mdash; Order of the Arrow, Boy Scouts of America
</footer>

<script>
  const form        = document.getElementById('genForm');
  const submitBtn   = document.getElementById('submitBtn');
  const fileInput   = document.getElementById('fileInput');
  const fileChosen  = document.getElementById('fileChosen');
  const dropZone    = document.getElementById('dropZone');
  const formatHint  = document.getElementById('formatHint');
  const statusLoad  = document.getElementById('statusLoading');
  const statusErr   = document.getElementById('statusError');
  const statusOk    = document.getElementById('statusSuccess');
  const modeRadios  = document.querySelectorAll('input[name="input_mode"]');
  const outputRadios = document.querySelectorAll('input[name="output_type"]');

  // ── Update format hint when mode changes ──
  const hints = {
    csv:   'CSV:&nbsp;&nbsp;&nbsp;Name,Lodge,Role<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Donathan Linebrink,Shenandoah,Staff<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;David Gosik,Blue Heron,Participant',
    plain: 'Plain text: one name per line.<br>Role defaults to Participant; lodge will be blank.'
  };
  modeRadios.forEach(r => r.addEventListener('change', () => {
    formatHint.innerHTML = hints[r.value];
  }));

  // ── Update button label ──
  function updateButtonLabel() {
    const type = document.querySelector('input[name="output_type"]:checked').value;
    const labels = {
      both: 'Generate Certificates & Name Tents',
      certificates: 'Generate Certificates',
      tents: 'Generate Name Tents'
    };
    submitBtn.textContent = labels[type];
  }
  outputRadios.forEach(r => r.addEventListener('change', updateButtonLabel));
  updateButtonLabel();

  // ── File input display ──
  fileInput.addEventListener('change', () => {
    fileChosen.textContent = fileInput.files[0]?.name ?? '';
  });

  // ── Drag and drop ──
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    const f = e.dataTransfer.files[0];
    if (f) {
      const dt = new DataTransfer();
      dt.items.add(f);
      fileInput.files = dt.files;
      fileChosen.textContent = f.name;
    }
  });

  // ── Status helpers ──
  function showLoading()  { statusLoad.style.display = 'flex'; statusErr.style.display = 'none'; statusOk.style.display = 'none'; }
  function showError(msg) { statusLoad.style.display = 'none'; statusErr.style.display = 'block'; statusErr.textContent = msg; }
  function showSuccess(msg){ statusLoad.style.display = 'none'; statusOk.style.display = 'block'; statusOk.textContent = msg; }
  function clearStatus()  { [statusLoad, statusErr, statusOk].forEach(el => el.style.display = 'none'); }

  // ── Form submit ──
  form.addEventListener('submit', async e => {
    e.preventDefault();
    clearStatus();
    submitBtn.disabled = true;
    showLoading();

    const data = new FormData(form);

    try {
      const resp = await fetch('/generate', { method: 'POST', body: data });

      if (!resp.ok) {
        const body = await resp.json().catch(() => ({ error: 'Unknown server error.' }));
        showError(body.error || `Server error ${resp.status}`);
        return;
      }

      // Trigger browser download
      const blob = await resp.blob();
      const cd   = resp.headers.get('Content-Disposition') || '';
      const match = cd.match(/filename="?([^";\\n]+)"?/);
      const filename = match ? match[1] : 'SLS_Documents.zip';

      const url = URL.createObjectURL(blob);
      const a   = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click();
      a.remove(); URL.revokeObjectURL(url);

      const type = document.querySelector('input[name="output_type"]:checked').value;
      const msg  = type === 'both'
        ? 'Certificates.pdf and Name_Tents.pdf downloaded as SLS_Documents.zip'
        : filename + ' downloaded successfully.';
      showSuccess(msg);

    } catch (err) {
      showError('Request failed. Check your connection and try again.');
    } finally {
      submitBtn.disabled = false;
    }
  });
</script>
</body>
</html>
"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/healthcheck")
def healthcheck():
    status = {}
    all_ok = True
    for label, path in [("Staff.pdf", STAFF_PDF),
                        ("Participant.pdf", PARTICIPANT_PDF),
                        ("SLS_Name_Tent.pdf", TENT_PDF)]:
        found = os.path.exists(path)
        status[label] = f"OK — {path}" if found else f"MISSING — looked at {path}"
        if not found:
            all_ok = False
    status["templates_dir"] = TEMPLATES_DIR
    status["ready"] = all_ok
    return jsonify(status), 200 if all_ok else 500


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/generate", methods=["POST"])
def generate():
    missing = [name for name, path in [("Staff.pdf", STAFF_PDF),
                                        ("Participant.pdf", PARTICIPANT_PDF),
                                        ("SLS_Name_Tent.pdf", TENT_PDF)]
               if not os.path.exists(path)]
    if missing:
        return jsonify(error=(
            f"Server configuration error: template files missing: "
            f"{', '.join(missing)}. Ensure they are committed to the repository."
        )), 500

    section     = request.form.get("section", "").strip()
    output_type = request.form.get("output_type", "both")
    input_mode  = request.form.get("input_mode", "csv")

    raw_text = ""
    uploaded = request.files.get("csv_file")
    if uploaded and uploaded.filename:
        raw_text = uploaded.read().decode("utf-8-sig")
    else:
        raw_text = request.form.get("input_text", "")

    if not raw_text.strip():
        return jsonify(error="No input data provided."), 400

    try:
        entries = parse_csv(raw_text) if input_mode == "csv" else parse_plain(raw_text)
    except Exception as e:
        return jsonify(error=f"Could not parse input: {e}"), 400

    if not entries:
        return jsonify(error="No valid names found in the input."), 400

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "Certificates.pdf")
            tent_path = os.path.join(tmpdir, "Name_Tents.pdf")

            if output_type in ("certificates", "both"):
                generate_certificates(entries, section, cert_path)
            if output_type in ("tents", "both"):
                generate_name_tents(entries, tent_path)

            if output_type == "certificates":
                return send_file(cert_path, as_attachment=True,
                                 download_name="Certificates.pdf",
                                 mimetype="application/pdf")
            if output_type == "tents":
                return send_file(tent_path, as_attachment=True,
                                 download_name="Name_Tents.pdf",
                                 mimetype="application/pdf")

            zip_path = os.path.join(tmpdir, "SLS_Documents.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(cert_path, "Certificates.pdf")
                zf.write(tent_path, "Name_Tents.pdf")

            return send_file(zip_path, as_attachment=True,
                             download_name="SLS_Documents.zip",
                             mimetype="application/zip")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[SLS] PDF generation error:\n{tb}", flush=True)
        return jsonify(error=f"PDF generation failed: {e}", traceback=tb), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
