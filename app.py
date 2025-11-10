# app.py
import os, re, uuid, datetime
from flask import Flask, render_template, request, redirect, url_for, send_file, flash
from werkzeug.utils import secure_filename

try:
    import argostranslate.package as argos_pkg
    import argostranslate.translate as argos_trans
    ARGOS_OK = True
except Exception:
    ARGOS_OK = False

from langdetect import detect
from docx import Document as DocxDocument
from PyPDF2 import PdfReader

ALLOWED_EXT = {"txt", "pdf", "docx"}
UPLOAD_DIR = "uploads"
OUTPUT_DIR = "outputs"

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    def load_argos_models():
        if not ARGOS_OK:
            return None
        try:
            installed = argos_trans.get_installed_languages()
            de = next((l for l in installed if l.code == "de"), None)
            en = next((l for l in installed if l.code == "en"), None)
            if de and en:
                return {"de_en": de.get_translation(en), "en_de": en.get_translation(de)}
        except Exception:
            return None
        return None

    ARGOS_MODELS = load_argos_models()

    def simple_rule_translate(text, direction="de_en"):
        pairs_de_en = {"hallo":"hello","angebot":"quote","beglaubigt":"certified","medizin":"medical","übersetzung":"translation","uebersetzung":"translation","dokument":"document","kontakt":"contact"}
        pairs_en_de = {v:k for k,v in pairs_de_en.items()}
        out = []
        for token in re.split(r"(\W+)", text):
            t = token.lower()
            if direction == "de_en":
                out.append(pairs_de_en.get(t, token))
            else:
                out.append(pairs_en_de.get(t, token))
        return "".join(out)

    def translate_text(text, direction):
        if ARGOS_MODELS:
            try:
                if direction == "de_en":
                    return ARGOS_MODELS["de_en"].translate(text)
                else:
                    return ARGOS_MODELS["en_de"].translate(text)
            except Exception:
                pass
        return simple_rule_translate(text, direction=direction)

    def extract_text_from_upload(path):
        ext = path.rsplit(".", 1)[-1].lower()
        if ext == "txt":
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        if ext == "docx":
            doc = DocxDocument(path)
            return "\n".join([p.text for p in doc.paragraphs])
        if ext == "pdf":
            reader = PdfReader(path)
            texts = []
            for page in reader.pages:
                try:
                    texts.append(page.extract_text() or "")
                except Exception:
                    pass
            return "\n".join(texts)
        return ""

    @app.route("/", methods=["GET"])
    def index():
        return render_template("index.html", build_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

    @app.route("/translate", methods=["POST"])
    def translate_route():
        direction = request.form.get("direction", "de_en")
        input_text = request.form.get("input_text", "").strip()
        upfile = request.files.get("file")

        raw_text = ""
        uploaded_filename = None

        if upfile and upfile.filename:
            fname = secure_filename(upfile.filename)
            ext = fname.rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED_EXT:
                flash("Unsupported file type. Use TXT, DOCX, or PDF.")
                return redirect(url_for("index"))
            fpath = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}_{fname}")
            upfile.save(fpath)
            raw_text = extract_text_from_upload(fpath) or ""
            uploaded_filename = os.path.basename(fpath)

        if not raw_text and input_text:
            raw_text = input_text

        if not raw_text:
            flash("Please upload a file or paste some text.")
            return redirect(url_for("index"))

        try:
            lang = detect(raw_text[:5000])
        except Exception:
            lang = None

        if lang == "en" and direction == "de_en":
            suggest = "en_de"
        elif lang == "de" and direction == "en_de":
            suggest = "de_en"
        else:
            suggest = direction

        translated = translate_text(raw_text, suggest)

        out_id = uuid.uuid4().hex
        out_name = f"translation_{out_id}.txt"
        out_path = os.path.join(OUTPUT_DIR, out_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(translated)

        return render_template("result.html", original_preview=raw_text[:2000], translated_preview=translated[:2000], download_name=out_name, direction=suggest, uploaded=uploaded_filename)

    @app.route("/download/<name>", methods=["GET"])
    def download(name):
        path = os.path.join(OUTPUT_DIR, secure_filename(name))
        if not os.path.exists(path):
            flash("File not found.")
            return redirect(url_for("index"))
        return send_file(path, as_attachment=True, download_name=name, mimetype="text/plain")

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
