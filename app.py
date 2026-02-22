"""
SLS Certificate & Name Tent Generator — Flask Web App

Font rendering
--------------
The PDF templates reference MuseoSlab-700, MuseoSlab-500Italic,
MuseoSans-700, and MuseoSans-500Italic in their form fields. PyMuPDF
can only use these on systems that have them installed. Render's Linux
servers have no system fonts, so without the files present the output
falls back to Helvetica.

To get correct Museo rendering, add the font files to a fonts/ subfolder
in this project and commit them to your repository:

    fonts/MuseoSlab-700.otf        (or .ttf)
    fonts/MuseoSlab-500Italic.otf
    fonts/MuseoSans-700.otf
    fonts/MuseoSans-500Italic.otf

Find the files on your Mac with:  fc-list | grep -i museo
"""

import csv
import io
import os
import re
import tempfile
import zipfile

import fitz
from flask import Flask, request, send_file, jsonify, Response
from pypdf import PdfReader, PdfWriter

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

TEMPLATES_DIR = os.path.dirname(os.path.abspath(__file__))

def _find_template(filename):
    for candidate in [
        os.path.join(TEMPLATES_DIR, filename),
        os.path.join(TEMPLATES_DIR, "resources", filename),
    ]:
        if os.path.exists(candidate):
            return candidate
    return os.path.join(TEMPLATES_DIR, filename)

STAFF_PDF       = _find_template("Staff.pdf")
PARTICIPANT_PDF = _find_template("Participant.pdf")
TENT_PDF        = _find_template("SLS_Name_Tent.pdf")


# ── Font loading ──────────────────────────────────────────────────────────────

def _load_fonts():
    fonts_dir = os.path.join(TEMPLATES_DIR, "fonts")
    # Each PDF font name maps to a list of candidate filenames to try
    mapping = {
        "MuseoSlab-700":       ["MuseoSlab-700.otf",       "MuseoSlab-700.ttf",
                                 "Museo_Slab_700.otf",      "Museo_Slab_700.ttf",
                                 "MuseoSlab700.otf",        "MuseoSlab700.ttf"],
        "MuseoSlab-500Italic": ["MuseoSlab-500Italic.otf", "MuseoSlab-500Italic.ttf",
                                 "Museo_Slab_500Italic.otf","Museo_Slab_500Italic.ttf"],
        "MuseoSans-700":       ["MuseoSans-700.otf",       "MuseoSans-700.ttf",
                                 "Museo_Sans_700.otf",      "Museo_Sans_700.ttf",
                                 "MuseoSans700.otf",        "MuseoSans700.ttf"],
        "MuseoSans-500Italic": ["MuseoSans-500Italic.otf", "MuseoSans-500Italic.ttf",
                                 "Museo_Sans_500Italic.otf","Museo_Sans_500Italic.ttf"],
    }
    loaded = {}
    for font_name, candidates in mapping.items():
        for filename in candidates:
            path = os.path.join(fonts_dir, filename)
            if os.path.exists(path):
                loaded[font_name] = open(path, "rb").read()
                print(f"[SLS] Font loaded:   {font_name}  ({path})", flush=True)
                break
        else:
            print(f"[SLS] Font MISSING:  {font_name} — add to fonts/ folder", flush=True)
    return loaded

FONTS = _load_fonts()

if len(FONTS) == 4:
    print("[SLS] All Museo fonts loaded. Rendering will be correct.", flush=True)
else:
    missing = 4 - len(FONTS)
    print(f"[SLS] WARNING: {missing} Museo font(s) missing. "
          "Output will use Helvetica. See module docstring.", flush=True)


# ── Startup check ─────────────────────────────────────────────────────────────

def _check_templates():
    all_ok = True
    for label, path in [("Staff.pdf", STAFF_PDF),
                        ("Participant.pdf", PARTICIPANT_PDF),
                        ("SLS_Name_Tent.pdf", TENT_PDF)]:
        if os.path.exists(path):
            print(f"[SLS] Template OK:   {label}", flush=True)
        else:
            print(f"[SLS] Template MISS: {label}  ({path})", flush=True)
            all_ok = False
    return all_ok

try:
    _templates_ok = _check_templates()
except Exception as _e:
    import traceback
    print(f"[SLS] Startup error: {_e}\n{traceback.format_exc()}", flush=True)
    _templates_ok = False


# ── PDF helpers ───────────────────────────────────────────────────────────────

def fill_and_flatten(template_path, field_values, output_path):
    """Fill PDF form fields with correct Museo font rendering and flatten.

    Flow:
    1. Open template with fitz; register Museo fonts into page resources
       (only if font files are present in FONTS)
    2. Set widget values; fitz writes /Helv fallback into AP streams
    3. Save to temp file; reopen with pypdf and patch AP streams to
       replace /Helv with the correct Museo font name — fitz now has
       that font registered in page resources so it resolves correctly
    4. Reopen patched file with fitz and call bake() to stamp AP content
       into the page as static, uneditable graphics
    """
    # ── Step 1 & 2: fill widgets ──────────────────────────────────────────
    doc = fitz.open(template_path)
    page = doc[0]

    # Map each widget to its intended font (from the /DA string)
    widget_fonts = {}
    for w in page.widgets():
        widget_fonts[w.field_name] = (w.text_font, w.text_fontsize)

    # Register Museo fonts into page resources so bake() can find them
    for font_name, font_bytes in FONTS.items():
        page.insert_font(fontname=font_name, fontbuffer=font_bytes)

    # Fill values (fitz will write /Helv into AP streams — we patch below)
    for w in page.widgets():
        if w.field_name in field_values:
            w.field_value = field_values[w.field_name]
            w.update()

    tmp1 = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp1.close()
    doc.save(tmp1.name)
    doc.close()

    # ── Step 3: patch AP streams if we have the fonts ─────────────────────
    if FONTS:
        reader = PdfReader(tmp1.name)
        writer = PdfWriter()
        writer.append(reader)
        page_w = writer.pages[0]

        for annot_ref in page_w.get("/Annots", []):
            annot = annot_ref.get_object()
            field_name = str(annot.get("/T", ""))
            if field_name not in widget_fonts:
                continue
            intended_font, font_size = widget_fonts[field_name]
            if intended_font not in FONTS:
                continue  # font not available, leave Helv

            ap = annot.get("/AP")
            if not ap:
                continue
            ap_obj = ap.get_object()
            n = ap_obj.get("/N")
            if not n:
                continue
            n_obj = n.get_object()
            try:
                stream = n_obj.get_data()
                # Replace whatever fallback font fitz used with the correct one
                fixed = re.sub(
                    rb"/[A-Za-z0-9_-]+\s+[\d.]+\s+Tf",
                    f"/{intended_font} {font_size} Tf".encode(),
                    stream,
                    count=1,
                )
                if fixed != stream:
                    n_obj._data   = fixed
                    n_obj._stream = fixed
            except Exception:
                pass  # leave stream unpatched on error

        tmp2 = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp2.close()
        with open(tmp2.name, "wb") as f:
            writer.write(f)
        os.unlink(tmp1.name)
        bake_src = tmp2.name
    else:
        bake_src = tmp1.name

    # ── Step 4: bake to static content ───────────────────────────────────
    doc3 = fitz.open(bake_src)
    doc3.bake()
    doc3.save(output_path, garbage=4, deflate=True)
    doc3.close()
    os.unlink(bake_src)


def build_merged_pdf(page_paths, output_path):
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
        status[label] = "OK" if found else f"MISSING — {path}"
        if not found:
            all_ok = False
    status["fonts"] = {
        name: "loaded" if name in FONTS else "MISSING — add to fonts/ folder"
        for name in ["MuseoSlab-700", "MuseoSlab-500Italic",
                     "MuseoSans-700", "MuseoSans-500Italic"]
    }
    status["ready"] = all_ok
    return jsonify(status), 200 if all_ok else 500


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/generate", methods=["POST"])
def generate():
    missing_templates = [
        name for name, path in [("Staff.pdf", STAFF_PDF),
                                  ("Participant.pdf", PARTICIPANT_PDF),
                                  ("SLS_Name_Tent.pdf", TENT_PDF)]
        if not os.path.exists(path)
    ]
    if missing_templates:
        return jsonify(error=f"Template files missing: {', '.join(missing_templates)}"), 500

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
        print(f"[SLS] Generation error:\n{tb}", flush=True)
        return jsonify(error=f"PDF generation failed: {e}", traceback=tb), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
