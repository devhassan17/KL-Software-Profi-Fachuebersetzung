import os
import io
import uuid
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()  # Load .env into environment

import requests
from flask import (
    Flask, render_template, request, redirect,
    url_for, send_file, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from docx import Document
import pdfplumber

# -------------------------------------------------------------------
# Config / setup
# -------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"txt", "pdf", "docx"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


app = Flask(__name__)
app.config["SECRET_KEY"] = "change-me"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(
    INSTANCE_DIR, "app.sqlite"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Simple admin password (for demo only — replace with real auth later)
app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "admin123")

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

db = SQLAlchemy(app)


@app.context_processor
def inject_config():
    """Make config accessible as `config` inside templates."""
    return dict(config=app.config)


# -------------------------------------------------------------------
# Models
# -------------------------------------------------------------------
class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_uuid = db.Column(db.String(36), unique=True, index=True)
    client_email = db.Column(db.String(255))
    source_lang = db.Column(db.String(5))   # "DE" / "EN" / etc
    target_lang = db.Column(db.String(5))
    domain = db.Column(db.String(50))       # legal/medical/technical/other
    tone = db.Column(db.String(50))         # formal/friendly, etc
    deadline = db.Column(db.String(50))     # keep as string for simplicity
    word_count = db.Column(db.Integer)
    price_estimate = db.Column(db.Float)
    status = db.Column(db.String(50))       # Uploaded → Translating → Review → Done
    intent = db.Column(db.Text)             # prefilled from chatbot
    glossary_raw = db.Column(db.Text)       # raw glossary text from form
    original_filename = db.Column(db.String(255))
    original_text = db.Column(db.Text)
    translated_text = db.Column(db.Text)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"))
    event_type = db.Column(db.String(50))  # upload, translate, error, delete, etc
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    job = db.relationship("Job", backref=db.backref("audit_events", lazy=True))


# -------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------
def init_db():
    db.create_all()


def log_event(job, event_type, description):
    ev = AuditLog(job=job, event_type=event_type, description=description)
    db.session.add(ev)
    db.session.commit()


def count_words(text: str) -> int:
    return len((text or "").split())


def compute_price(words: int, domain: str) -> float:
    """Very simple pricing hook — tweak as needed."""
    base_rate = 0.05  # $0.05 per word
    multiplier = {
        "legal": 1.5,
        "medical": 1.6,
        "technical": 1.3,
    }.get(domain, 1.0)
    return round(words * base_rate * multiplier, 2)


def parse_glossary(glossary_raw: str):
    """
    Very simple glossary format:
    one pair per line: source_term => target_term
    e.g.:
    invoice => Rechnung
    contract => Vertrag
    """
    mapping = {}
    glossary_raw = glossary_raw or ""
    for line in glossary_raw.splitlines():
        if "=>" in line:
            src, tgt = line.split("=>", 1)
            mapping[src.strip()] = tgt.strip()
    return mapping


def apply_glossary(text: str, glossary_raw: str) -> str:
    mapping = parse_glossary(glossary_raw)
    for src, tgt in mapping.items():
        # naive replace; can be improved with regex word boundaries
        text = text.replace(src, tgt)
    return text


def deepl_translate(text: str, source_lang: str, target_lang: str) -> str:
    """
    Call DeepL API.
    Set DEEPL_API_KEY and DEEPL_API_PLAN=free|pro in your .env.
    """
    api_key = os.environ.get("DEEPL_API_KEY")
    plan = os.environ.get("DEEPL_API_PLAN", "free")

    if not api_key:
        # In development, just return original with note
        return f"[NO DEEPL_API_KEY SET]\n\nOriginal:\n{text}"

    if plan == "pro":
        base_url = "https://api.deepl.com/v2/translate"
    else:
        base_url = "https://api-free.deepl.com/v2/translate"

    data = {"text": text, "target_lang": target_lang}
    if source_lang:
        data["source_lang"] = source_lang

    response = requests.post(
        base_url,
        data=data,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    translations = payload.get("translations", [])
    if not translations:
        return ""
    return translations[0].get("text", "")


def read_file_content(file_storage):
    """Read TXT/DOCX/PDF into plain text."""
    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[-1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: .{ext}")

    if ext == "txt":
        return file_storage.read().decode("utf-8", errors="ignore")

    if ext == "docx":
        buf = io.BytesIO(file_storage.read())
        doc = Document(buf)
        return "\n".join(p.text for p in doc.paragraphs)

    if ext == "pdf":
        buf = io.BytesIO(file_storage.read())
        text_chunks = []
        with pdfplumber.open(buf) as pdf:
            for page in pdf.pages:
                text_chunks.append(page.extract_text() or "")
        return "\n".join(text_chunks)

    # Fallback — shouldn't be reached
    return file_storage.read().decode("utf-8", errors="ignore")


# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def upload_translate():
    """
    Main page:
      - upload single/multiple files
      - or paste text
      - set project settings
      - optional glossary
    """
    if request.method == "POST":
        client_email = request.form.get("client_email")
        source_lang = request.form.get("source_lang")
        target_lang = request.form.get("target_lang")
        domain = request.form.get("domain")
        tone = request.form.get("tone")
        deadline = request.form.get("deadline")
        intent = request.form.get("intent")
        glossary_raw = request.form.get("glossary")
        pasted_text = request.form.get("pasted_text") or ""

        uploaded_files = request.files.getlist("files")

        jobs = []

        # Case 1: text box mode
        if pasted_text.strip():
            job = create_and_run_job(
                client_email,
                source_lang,
                target_lang,
                domain,
                tone,
                deadline,
                intent,
                glossary_raw,
                original_text=pasted_text,
                original_filename=None,
            )
            jobs.append(job)

        # Case 2: files
        for file in uploaded_files:
            if not file or file.filename == "":
                continue
            if not allowed_file(file.filename):
                flash(f"Unsupported file type: {file.filename}", "error")
                continue

            job = create_and_run_job(
                client_email,
                source_lang,
                target_lang,
                domain,
                tone,
                deadline,
                intent,
                glossary_raw,
                original_text=None,
                original_filename=file.filename,
                file_storage=file,
            )
            jobs.append(job)

        if not jobs:
            flash("Please paste text or upload at least one file.", "error")
            return redirect(url_for("upload_translate"))

        if len(jobs) == 1:
            return redirect(url_for("job_detail", job_uuid=jobs[0].job_uuid))
        else:
            return render_template("job_list.html", jobs=jobs)

    # GET
    intent = request.args.get("intent", "")  # prefilled by chatbot
    return render_template("upload.html", intent=intent)


def create_and_run_job(
    client_email,
    source_lang,
    target_lang,
    domain,
    tone,
    deadline,
    intent,
    glossary_raw,
    original_text=None,
    original_filename=None,
    file_storage=None,
):
    job = Job(
        job_uuid=str(uuid.uuid4()),
        client_email=client_email,
        source_lang=source_lang,
        target_lang=target_lang,
        domain=domain,
        tone=tone,
        deadline=deadline,
        intent=intent,
        status="Uploaded",
        glossary_raw=glossary_raw,
        original_filename=original_filename,
    )
    db.session.add(job)
    db.session.commit()
    log_event(job, "upload", "Job created")

    if original_text is None and file_storage is not None:
        original_text = read_file_content(file_storage)

    job.original_text = original_text
    job.status = "Translating"
    db.session.commit()
    log_event(job, "status_change", "Status -> Translating")

    try:
        translated = deepl_translate(original_text, source_lang, target_lang)
        translated = apply_glossary(translated, glossary_raw)
        job.translated_text = translated

        words = count_words(original_text)
        job.word_count = words
        job.price_estimate = compute_price(words, domain)

        job.status = "Done"  # later you can add a "Review" step
        db.session.commit()
        log_event(job, "status_change", "Status -> Done")

    except Exception as e:
        job.status = "Error"
        job.error_message = str(e)
        db.session.commit()
        log_event(job, "error", str(e))

    return job


@app.route("/job/<job_uuid>")
def job_detail(job_uuid):
    job = Job.query.filter_by(job_uuid=job_uuid).first_or_404()
    return render_template("job_detail.html", job=job)


@app.route("/download/<job_uuid>")
def download_job(job_uuid):
    job = Job.query.filter_by(job_uuid=job_uuid).first_or_404()
    filename_root = job.original_filename or "translation"
    filename = f"{filename_root}.txt"
    buf = io.BytesIO()
    buf.write((job.translated_text or "").encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="text/plain; charset=utf-8",
    )


# -------------------------------------------------------------------
# Admin dashboard
# -------------------------------------------------------------------
def require_admin():
    token = request.args.get("password") or request.headers.get("X-Admin-Password")
    if token != app.config["ADMIN_PASSWORD"]:
        return False
    return True


@app.route("/admin")
def admin_dashboard():
    if not require_admin():
        return "Forbidden", 403
    jobs = Job.query.order_by(Job.created_at.desc()).all()
    return render_template("admin.html", jobs=jobs)


@app.route("/admin/delete/<job_uuid>", methods=["POST"])
def admin_delete(job_uuid):
    if not require_admin():
        return "Forbidden", 403
    job = Job.query.filter_by(job_uuid=job_uuid).first_or_404()
    log_event(job, "delete", "Job deleted by admin")
    db.session.delete(job)
    db.session.commit()
    return redirect(
        url_for("admin_dashboard") + f"?password={app.config['ADMIN_PASSWORD']}"
    )


# -------------------------------------------------------------------
# Simple API for future integrations
# -------------------------------------------------------------------
@app.route("/api/translate", methods=["POST"])
def api_translate():
    data = request.get_json() or {}
    text = data.get("text", "")
    source = data.get("source_lang") or ""
    target = data.get("target_lang") or "EN"
    translated = deepl_translate(text, source, target)
    return jsonify({"translated_text": translated})


@app.route("/api/status/<job_uuid>")
def api_status(job_uuid):
    job = Job.query.filter_by(job_uuid=job_uuid).first_or_404()
    return jsonify(
        {
            "job_uuid": job.job_uuid,
            "status": job.status,
            "word_count": job.word_count,
            "price_estimate": job.price_estimate,
        }
    )


@app.route("/api/download/<job_uuid>")
def api_download(job_uuid):
    job = Job.query.filter_by(job_uuid=job_uuid).first_or_404()
    return jsonify(
        {
            "job_uuid": job.job_uuid,
            "translated_text": job.translated_text,
        }
    )


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)
