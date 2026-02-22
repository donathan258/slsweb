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
        os.path.join(TEMPLATES_DIR, "templates", filename),
        os.path.join(TEMPLATES_DIR, "static", filename),
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
    """Fill PDF form fields and flatten by merging AP streams into page content.

    Uses pypdf to fill fields, which preserves the original Museo font AP streams
    created by the PDF authoring tool. Copies the AcroForm DR fonts into page
    resources, then stamps each annotation's appearance stream directly into the
    page content. Result: fully static, uneditable PDF with proper Unicode text
    (no CID encoding) that renders correctly in Preview, Adobe Reader, and Chrome.

    No fitz re-rendering is used, so the font rendering is 100% determined by
    what was in the original AP stream — Museo if the font files are present,
    with correct text regardless.
    """
    from pypdf.generic import (
        ArrayObject, NameObject, DecodedStreamObject, DictionaryObject
    )

    # ── Step 1: fill fields ───────────────────────────────────────────────────
    reader = PdfReader(template_path)
    writer = PdfWriter()
    writer.append(reader)

    # update_page_form_field_values fails on the tent template (no /DR).
    # In that case, use fitz to fill the widgets (it tolerates missing /DR),
    # which generates valid AP streams we can then merge below.
    filled_via_fitz = False
    try:
        writer.update_page_form_field_values(
            writer.pages[0], field_values, auto_regenerate=False
        )
    except Exception:
        filled_via_fitz = True

    tmp1 = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp1.close()

    if filled_via_fitz:
        # Write the unfilled pypdf copy then re-open with fitz to fill widgets
        with open(tmp1.name, "wb") as f:
            writer.write(f)
        doc_fitz = fitz.open(tmp1.name)
        page_fitz = doc_fitz[0]
        for w in page_fitz.widgets():
            if w.field_name in field_values:
                w.field_value = field_values[w.field_name]
                w.text_font = "Helv"
                w.update()
        tmp1b = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp1b.close()
        doc_fitz.save(tmp1b.name)
        doc_fitz.close()
        os.unlink(tmp1.name)
        tmp1.name = tmp1b.name
    else:
        with open(tmp1.name, "wb") as f:
            writer.write(f)

    # ── Step 2: copy AcroForm DR fonts into page resources ────────────────────
    # The AP streams reference fonts by name (e.g. /MuseoSlab-700). Those names
    # must exist in the page /Resources /Font dict after we remove AcroForm,
    # otherwise PDF viewers (especially Preview) can't resolve them and show blank.
    reader2   = PdfReader(tmp1.name)
    writer2   = PdfWriter()
    writer2.append(reader2)
    page      = writer2.pages[0]

    orig_reader = PdfReader(template_path)
    orig_root   = orig_reader.trailer["/Root"]
    if "/AcroForm" in orig_root:
        acroform  = orig_root["/AcroForm"].get_object()
        dr        = acroform.get("/DR", {})
        if hasattr(dr, "get_object"):
            dr = dr.get_object()
        dr_fonts  = dr.get("/Font", {})
        if hasattr(dr_fonts, "get_object"):
            dr_fonts = dr_fonts.get_object()

        page_res  = page.get("/Resources", DictionaryObject())
        if hasattr(page_res, "get_object"):
            page_res = page_res.get_object()
        page_fonts = page_res.get("/Font", DictionaryObject())
        if hasattr(page_fonts, "get_object"):
            page_fonts = page_fonts.get_object()

        for fname, fref in (dr_fonts.items() if hasattr(dr_fonts, "items") else []):
            if fname not in page_fonts:
                page_fonts[NameObject(fname)] = fref

        page_res[NameObject("/Font")]   = page_fonts
        page[NameObject("/Resources")] = page_res

    # ── Step 3: stamp AP streams into page content ────────────────────────────
    ap_parts   = []
    annots_raw = page.get("/Annots")
    annots     = annots_raw.get_object() if hasattr(annots_raw, "get_object") else (annots_raw or [])
    for ref in annots:
        annot = ref.get_object() if hasattr(ref, "get_object") else ref
        ap    = annot.get("/AP")
        if not ap:
            continue
        n = (ap.get_object() if hasattr(ap, "get_object") else ap).get("/N")
        if not n:
            continue
        n_obj = n.get_object() if hasattr(n, "get_object") else n
        try:
            rect  = annot.get("/Rect")
            r     = [float(x) for x in rect]
            # Translate AP coordinate system (origin = annotation lower-left)
            # to page coordinate system
            ap_parts.append(
                f"q 1 0 0 1 {r[0]} {r[1]} cm\n".encode() +
                n_obj.get_data() + b"\nQ\n"
            )
        except Exception:
            pass

    if ap_parts:
        contents = page.get("/Contents")
        existing = []
        if contents is not None:
            co = contents.get_object() if hasattr(contents, "get_object") else contents
            if isinstance(co, ArrayObject):
                for s in co:
                    so = s.get_object() if hasattr(s, "get_object") else s
                    existing.append(so.get_data())
            elif hasattr(co, "get_data"):
                existing.append(co.get_data())

        merged = b"\n".join(existing + ap_parts)
        ns     = DecodedStreamObject()
        ns.set_data(merged)
        page[NameObject("/Contents")] = writer2._add_object(ns)

    # Remove annotations (now stamped into content) and AcroForm
    page[NameObject("/Annots")] = ArrayObject()
    if "/AcroForm" in writer2._root_object:
        del writer2._root_object["/AcroForm"]

    with open(output_path, "wb") as f:
        writer2.write(f)
    os.unlink(tmp1.name)


def build_merged_pdf(page_paths, output_path):
    merged = PdfWriter()
    for path in page_paths:
        merged.append(PdfReader(path))
    with open(output_path, "wb") as f:
        merged.write(f)


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

# Load the HTML page from index.html at startup.
# Edit index.html directly — no need to touch app.py for UI changes.
_html_path = os.path.join(TEMPLATES_DIR, "index.html")
INDEX_HTML  = open(_html_path, encoding="utf-8").read()


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
