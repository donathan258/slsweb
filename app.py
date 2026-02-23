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

def _get_font_widths(template_path: str, font_name: str):
    """Return (first_char, widths_list) from AcroForm /DR, or (None, None)."""
    try:
        reader = PdfReader(template_path)
        root   = reader.trailer["/Root"]
        acro   = root["/AcroForm"].get_object()
        dr     = acro.get("/DR", {})
        if hasattr(dr, "get_object"):    dr    = dr.get_object()
        fonts  = dr.get("/Font", {})
        if hasattr(fonts, "get_object"): fonts = fonts.get_object()
        fobj   = fonts.get(f"/{font_name}")
        if not fobj: return None, None
        fobj   = fobj.get_object() if hasattr(fobj, "get_object") else fobj
        fc     = int(fobj.get("/FirstChar", 0))
        wlist  = fobj.get("/Widths")
        if not wlist: return None, None
        return fc, [int(x) for x in wlist]
    except Exception:
        return None, None


def _fit_text(text: str, nominal_size: float,
              fc, widths, rect_width: float,
              min_size: float = 14.0):
    """Return (font_size, x_offset) that centres text inside rect_width.

    Reduces font size in 1-pt steps until text fits, down to min_size.
    Uses actual character-width tables when available for accuracy.
    """
    available = rect_width - 4.0

    def tw(s, sz):
        if widths is not None and fc is not None:
            return sum(widths[ord(c) - fc] for c in s
                       if 0 <= ord(c) - fc < len(widths)) * sz / 1000.0
        return len(s) * sz * 0.55

    size = nominal_size
    while size >= min_size:
        w = tw(text, size)
        if w <= available:
            x = max(2.0, (available - w) / 2.0 + 2.0)
            return size, x
        size -= 1.0
    return min_size, 2.0


def _patch_ap_stream(stream: bytes, new_text: str, rect_width: float,
                     font_size: float, fc=None, widths=None) -> bytes:
    """Replace text in an AP stream, scaling font to fit and re-centering.

    Preserves the original stream structure (clipping rect, y-offset) so
    the original /Q=1 (centred) layout is honoured.  Uses the actual
    MuseoSlab-700 width table when available for accurate measurement.
    """
    m = re.search(rb"([\d.]+) ([\d.]+) Td\n\(([^)]*)\) Tj", stream)
    if not m:
        return stream
    orig_y = m.group(2).decode()

    new_size, x_offset = _fit_text(new_text, font_size, fc, widths, rect_width)

    # Update Tf font size
    stream = re.sub(
        rb"(/[A-Za-z0-9_-]+) [\d.]+ Tf",
        lambda mo: mo.group(1) + f" {new_size:.1f} Tf".encode(),
        stream, count=1,
    )
    # Re-find Td after substitution (byte offsets shifted)
    m2 = re.search(rb"([\d.]+) ([\d.]+) Td\n\(([^)]*)\) Tj", stream)
    if not m2:
        return stream
    replacement = f"{x_offset:.3f} {m2.group(2).decode()} Td\n({new_text}) Tj".encode()
    return stream[: m2.start()] + replacement + stream[m2.end() :]



def _inject_helv_resource(pdf_path: str) -> None:
    """Add /Helv (Helvetica) to page /Resources /Font so strict viewers render it.

    fitz generates AP streams that reference /Helv but never registers the font
    in page resources.  PDF/A-compliant viewers (including macOS Preview) refuse
    to render text whose font isn't declared in page resources.
    """
    from pypdf.generic import NameObject, DictionaryObject

    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.append(reader)
    page = writer.pages[0]

    page_res = page.get("/Resources", DictionaryObject())
    if hasattr(page_res, "get_object"): page_res = page_res.get_object()
    page_fonts = page_res.get("/Font", DictionaryObject())
    if hasattr(page_fonts, "get_object"): page_fonts = page_fonts.get_object()

    if "/Helv" not in page_fonts:
        helv = DictionaryObject()
        helv[NameObject("/Type")]     = NameObject("/Font")
        helv[NameObject("/Subtype")]  = NameObject("/Type1")
        helv[NameObject("/BaseFont")] = NameObject("/Helvetica")
        page_fonts[NameObject("/Helv")] = writer._add_object(helv)
        page_res[NameObject("/Font")]   = page_fonts
        page[NameObject("/Resources")]  = page_res

    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    with open(tmp.name, "wb") as f:
        writer.write(f)
    os.replace(tmp.name, pdf_path)


def _merge_ap_into_page(filled_path: str, output_path: str, *,
                        fonts: dict, template_path: str | None) -> None:
    """Merge annotation AP streams into page content, embed font data, flatten."""
    from pypdf.generic import (
        ArrayObject, NameObject, DecodedStreamObject, DictionaryObject, NumberObject
    )

    reader2 = PdfReader(filled_path)
    writer2 = PdfWriter()
    writer2.append(reader2)
    page = writer2.pages[0]

    # Copy AcroForm /DR font entries into page /Resources /Font so that the
    # merged AP content (which uses e.g. /MuseoSlab-700) can resolve the name.
    # When font file bytes are available in `fonts`, embed them so the PDF is
    # self-contained (required for Preview / offline viewing).
    if template_path:
        orig = PdfReader(template_path)
        root = orig.trailer["/Root"]
        if "/AcroForm" in root:
            acro     = root["/AcroForm"].get_object()
            dr       = acro.get("/DR", {})
            if hasattr(dr, "get_object"):      dr       = dr.get_object()
            dr_fonts = dr.get("/Font", {})
            if hasattr(dr_fonts, "get_object"): dr_fonts = dr_fonts.get_object()

            page_res   = page.get("/Resources", DictionaryObject())
            if hasattr(page_res, "get_object"):   page_res   = page_res.get_object()
            page_fonts = page_res.get("/Font", DictionaryObject())
            if hasattr(page_fonts, "get_object"): page_fonts = page_fonts.get_object()

            for fname, fref in (dr_fonts.items() if hasattr(dr_fonts, "items") else []):
                pname = fname.lstrip("/")
                if pname in fonts:
                    # Build a font dict that references an embedded font stream.
                    f_obj  = fref.get_object() if hasattr(fref, "get_object") else fref
                    ff     = DecodedStreamObject()
                    ff.set_data(fonts[pname])
                    ff[NameObject("/Subtype")] = NameObject("/OpenType")
                    ff_ref = writer2._add_object(ff)
                    new_fd = DictionaryObject()
                    orig_fd = f_obj.get("/FontDescriptor")
                    if orig_fd:
                        for k, v in (orig_fd.get_object() if hasattr(orig_fd, "get_object") else orig_fd).items():
                            new_fd[NameObject(k)] = v
                    new_fd[NameObject("/FontFile3")] = ff_ref
                    new_font = DictionaryObject()
                    for k, v in f_obj.items():
                        if k != "/FontDescriptor":
                            new_font[NameObject(k)] = v
                    new_font[NameObject("/FontDescriptor")] = writer2._add_object(new_fd)
                    page_fonts[NameObject(fname)] = writer2._add_object(new_font)
                elif fname not in page_fonts:
                    page_fonts[NameObject(fname)] = fref

            page_res[NameObject("/Font")]    = page_fonts
            page[NameObject("/Resources")] = page_res

    # Also register any fonts present in FONTS but missing from DR
    # (e.g. MuseoSans-700 is used in DA but absent from the template's /DR)
    for font_key, font_bytes in fonts.items():
        slash_key = f"/{font_key}"
        if slash_key not in page_fonts:
            ff = DecodedStreamObject()
            ff.set_data(font_bytes)
            ff[NameObject("/Subtype")] = NameObject("/OpenType")
            ff_ref = writer2._add_object(ff)
            fd = DictionaryObject()
            fd[NameObject("/FontName")] = NameObject(f"/{font_key}")
            fd[NameObject("/Flags")] = NumberObject(32)
            fd[NameObject("/FontFile3")] = ff_ref
            new_font = DictionaryObject()
            new_font[NameObject("/Type")] = NameObject("/Font")
            new_font[NameObject("/Subtype")] = NameObject("/Type1")
            new_font[NameObject("/BaseFont")] = NameObject(f"/{font_key}")
            new_font[NameObject("/FontDescriptor")] = writer2._add_object(fd)
            page_fonts[NameObject(slash_key)] = writer2._add_object(new_font)

    # Stamp each annotation's /AP /N stream into the page content stream.
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
            r = [float(x) for x in annot.get("/Rect")]
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
        ns = DecodedStreamObject()
        ns.set_data(b"\n".join(existing + ap_parts))
        page[NameObject("/Contents")] = writer2._add_object(ns)

    page[NameObject("/Annots")] = ArrayObject()
    if "/AcroForm" in writer2._root_object:
        del writer2._root_object["/AcroForm"]

    with open(output_path, "wb") as f:
        writer2.write(f)
    os.unlink(filled_path)


def fill_and_flatten(template_path, field_values, output_path):
    """Fill PDF form fields and flatten to static, uneditable content.

    Strategy
    --------
    Cert templates (Staff.pdf, Participant.pdf) have an AcroForm with /DR and
    pre-existing centred AP streams.  We patch those AP streams in-place —
    replacing only the text string and recalculating the centred x-offset —
    then embed the Museo font bytes (if available) and stamp the AP content
    into the page stream.

    The tent template has no AcroForm /DR, so we fall back to fitz with Helv
    to generate AP streams, then do the same stamp-and-flatten step.

    Result: fully static PDF, Preview-compatible, no CID-encoding issues.
    """
    from pypdf.generic import (
        ArrayObject, NameObject, DecodedStreamObject,
        DictionaryObject, create_string_object,
    )

    reader = PdfReader(template_path)
    writer = PdfWriter()
    writer.append(reader)
    page   = writer.pages[0]

    # Detect whether this template has a proper AcroForm /DR (cert) or not (tent).
    root = reader.trailer["/Root"]
    has_dr = False
    print(f"[SLS] fill_and_flatten: template={template_path}, has_dr={has_dr}", flush=True)
    if "/AcroForm" in root and "/DR" in root["/AcroForm"].get_object():
        annots_raw = page.get("/Annots")
        annots = annots_raw.get_object() if hasattr(annots_raw, "get_object") else (annots_raw or [])
        has_dr = any(
            (ref.get_object() if hasattr(ref, "get_object") else ref).get("/AP") is not None
            for ref in annots
        )

    if not has_dr:
        # ── Tent path: fitz fills with Helv (tolerates missing /DR) ──────────
        # Build AP streams directly with pypdf — no fitz involvement.
        # fitz generates AP with font declared AFTER Td moves, which confuses
        # strict viewers (macOS Preview).  We write a clean stream with the font
        # declared first, text properly centred, and /Helv registered in page
        # resources so the font reference resolves without system font lookup.
        annots_raw = page.get("/Annots")
        for ref in annots_raw.get_object() if hasattr(annots_raw, "get_object") else (annots_raw or []):
            annot      = ref.get_object() if hasattr(ref, "get_object") else ref
            field_name = str(annot.get("/T", ""))
            if field_name not in field_values:
                continue
            rect      = [float(x) for x in annot.get("/Rect")]
            rect_w    = rect[2] - rect[0]
            rect_h    = rect[3] - rect[1]
            da        = str(annot.get("/DA", ""))
            fm        = re.search(r"/([A-Za-z0-9_-]+)\s+([\d.]+)\s+Tf", da)
            i_font    = fm.group(1) if fm else "Helv"
            font_size = float(fm.group(2)) if fm else 12.0
            # Use Museo font if available (loaded on Render), else Helv
            font_name = i_font if i_font in FONTS else "Helv"
            print(f"[SLS] tent field '{field_name}': i_font={i_font}, in FONTS={i_font in FONTS}, font_name={font_name}, FONTS keys={list(FONTS.keys())}", flush=True)

            text      = field_values[field_name]
            em        = 0.52 if "Museo" in font_name else 0.55
            text_w    = len(text) * font_size * em
            avail     = rect_w - 4.0
            x         = 2.0
            y         = max(2.0, (rect_h - font_size) / 2.0)

            ap_stream = (
                f"q\n1 1 {rect_w-2:.3f} {rect_h-2:.3f} re W n\n"
                f"BT\n/{font_name} {font_size:.1f} Tf\n0 g\n"
                f"{x:.3f} {y:.3f} Td\n({text}) Tj\nET\nQ\n"
            ).encode()

            n_obj = DecodedStreamObject()
            n_obj.set_data(ap_stream)
            ap_d = DictionaryObject()
            ap_d[NameObject("/N")] = writer._add_object(n_obj)
            annot[NameObject("/AP")] = writer._add_object(ap_d)
            annot[NameObject("/V")] = create_string_object(text)

        # Register /Helv (or the Museo font) in page resources so it resolves
        page_res   = page.get("/Resources", DictionaryObject())
        if hasattr(page_res, "get_object"): page_res = page_res.get_object()
        page_fonts = page_res.get("/Font", DictionaryObject())
        if hasattr(page_fonts, "get_object"): page_fonts = page_fonts.get_object()
        if "/Helv" not in page_fonts:
            helv = DictionaryObject()
            helv[NameObject("/Type")]     = NameObject("/Font")
            helv[NameObject("/Subtype")]  = NameObject("/Type1")
            helv[NameObject("/BaseFont")] = NameObject("/Helvetica")
            page_fonts[NameObject("/Helv")] = writer._add_object(helv)
            page_res[NameObject("/Font")]   = page_fonts
            page[NameObject("/Resources")]  = page_res

        tmp1 = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp1.close()
        with open(tmp1.name, "wb") as f:
            writer.write(f)
        _merge_ap_into_page(tmp1.name, output_path, fonts=FONTS, template_path=TENT_PDF)
        return

    # ── Cert path: patch existing AP streams directly ─────────────────────────
    # pypdf's update_page_form_field_values always regenerates AP left-aligned
    # (ignoring /Q=1).  Instead we patch the original AP in-place: replace the
    # text string and recalculate the centred x-offset, preserving everything else.
    annots_raw = page.get("/Annots")
    annots     = annots_raw.get_object() if hasattr(annots_raw, "get_object") else (annots_raw or [])
    for ref in annots:
        annot      = ref.get_object() if hasattr(ref, "get_object") else ref
        field_name = str(annot.get("/T", ""))
        if field_name not in field_values:
            continue
        ap = annot.get("/AP")
        if not ap:
            continue
        n = (ap.get_object() if hasattr(ap, "get_object") else ap).get("/N")
        if not n:
            continue
        n_obj = n.get_object() if hasattr(n, "get_object") else n
        rect       = [float(x) for x in annot.get("/Rect")]
        rect_width = rect[2] - rect[0]
        da         = str(annot.get("/DA", ""))
        fm         = re.search(r"/([A-Za-z0-9_-]+)\s+([\d.]+)\s+Tf", da)
        font_name  = fm.group(1) if fm else "Helv"
        font_size  = float(fm.group(2)) if fm else 12.0
        fc, widths = _get_font_widths(template_path, font_name)
        try:
            new_stream = _patch_ap_stream(
                n_obj.get_data(), field_values[field_name], rect_width, font_size,
                fc=fc, widths=widths,
            )
            n_obj.set_data(new_stream)
        except Exception as exc:
            print(f"[SLS] AP patch error '{field_name}': {exc}", flush=True)
        annot[NameObject("/V")] = create_string_object(field_values[field_name])

    tmp1 = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp1.close()
    with open(tmp1.name, "wb") as f:
        writer.write(f)
    _merge_ap_into_page(tmp1.name, output_path, fonts=FONTS, template_path=template_path)


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
            name_value  = entry["name"]
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

@app.route("/sample.csv")
def sample_csv():
    csv_content = "Name,Lodge,Role\nCortland Bolles,Wewikit Lodge,Staff\nChristopher Grove,Tipisa Lodge,Participant\nBrea Baygents,Wewikit Lodge,Participant\nDonathan Linebrink,Shenandoah Lodge,Staff\n"
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=SLS_Sample.csv"}
    )


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
