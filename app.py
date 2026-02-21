"""
SLS Certificate & Name Tent Generator — Flask Web App
Reuses all PDF generation logic from the desktop app unchanged.
"""

import csv
import io
import os
import tempfile
import zipfile

from flask import (
    Flask, request, render_template,
    send_file, jsonify
)
from pypdf import PdfReader, PdfWriter, generic

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB upload limit

TEMPLATES_DIR = os.path.dirname(os.path.abspath(__file__))
STAFF_PDF      = os.path.join(TEMPLATES_DIR, "resources/Staff.pdf")
PARTICIPANT_PDF = os.path.join(TEMPLATES_DIR, "resources/Participant.pdf")
TENT_PDF       = os.path.join(TEMPLATES_DIR, "resources/SLS_Name_Tent.pdf")


# ── PDF Helpers (identical to desktop app) ───────────────────────────────────

def fill_cert_flatten(template_path, field_values, output_path):
    reader = PdfReader(template_path)
    writer = PdfWriter()
    writer.append(reader)
    writer.update_page_form_field_values(
        writer.pages[0], field_values, auto_regenerate=False)
    if "/AcroForm" in writer._root_object:
        del writer._root_object["/AcroForm"]
    with open(output_path, "wb") as f:
        writer.write(f)


def fill_tent_direct(template_path, field_values, output_path):
    reader = PdfReader(template_path)
    writer = PdfWriter()
    writer.append(reader)
    page = writer.pages[0]
    if "/Annots" in page:
        for annot_ref in page["/Annots"]:
            annot = annot_ref.get_object()
            field_name = annot.get("/T")
            if field_name is None:
                continue
            key = str(field_name)
            if key in field_values:
                annot.update({
                    generic.NameObject("/V"):
                        generic.create_string_object(field_values[key])
                })
                if "/AP" in annot:
                    del annot[generic.NameObject("/AP")]
    if "/AcroForm" in writer._root_object:
        del writer._root_object["/AcroForm"]
    with open(output_path, "wb") as f:
        writer.write(f)


def build_merged_pdf(page_paths, output_path):
    merged = PdfWriter()
    for p in page_paths:
        merged.append(PdfReader(p))
    with open(output_path, "wb") as f:
        merged.write(f)


def generate_certificates(entries, section, output_path):
    with tempfile.TemporaryDirectory() as tmpdir:
        pages = []
        for i, entry in enumerate(entries):
            template = STAFF_PDF if entry["role"].lower() == "staff" \
                       else PARTICIPANT_PDF
            page_path = os.path.join(tmpdir, f"cert_{i:04d}.pdf")
            fill_cert_flatten(template,
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
            fill_tent_direct(TENT_PDF,
                             {"Name": name_value, "Lodge": lodge_value},
                             page_path)
            pages.append(page_path)
        build_merged_pdf(pages, output_path)


# ── CSV / Text Parsing ───────────────────────────────────────────────────────

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


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    # ── Parse form data ──
    section     = request.form.get("section", "").strip()
    output_type = request.form.get("output_type", "both")   # certificates | tents | both
    input_mode  = request.form.get("input_mode", "csv")     # csv | plain

    raw_text = ""
    # File upload takes priority over pasted text
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

    # ── Generate PDFs into a temp directory ──
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "Certificates.pdf")
            tent_path = os.path.join(tmpdir, "Name_Tents.pdf")

            do_certs = output_type in ("certificates", "both")
            do_tents = output_type in ("tents", "both")

            if do_certs:
                generate_certificates(entries, section, cert_path)
            if do_tents:
                generate_name_tents(entries, tent_path)

            # ── Single PDF: return directly ──
            if output_type == "certificates":
                return send_file(
                    cert_path, as_attachment=True,
                    download_name="Certificates.pdf",
                    mimetype="application/pdf"
                )
            if output_type == "tents":
                return send_file(
                    tent_path, as_attachment=True,
                    download_name="Name_Tents.pdf",
                    mimetype="application/pdf"
                )

            # ── Both: zip them together ──
            zip_path = os.path.join(tmpdir, "SLS_Documents.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(cert_path, "Certificates.pdf")
                zf.write(tent_path, "Name_Tents.pdf")

            return send_file(
                zip_path, as_attachment=True,
                download_name="SLS_Documents.zip",
                mimetype="application/zip"
            )

    except Exception as e:
        return jsonify(error=f"PDF generation failed: {e}"), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
