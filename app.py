"""SOPHIA Input Generator - Flask web app.

Flow:  PDF upload -> case list (Claude, report_cases)
       -> case selection -> geometry spec (Claude, build_spec, editable)
       -> deterministic particle generation (particle_gen) -> input.txt

Session state lives in the in-process dict ``SESSIONS``. Therefore the app
must run with a SINGLE worker process (gunicorn --workers 1 --threads 8);
multiple workers would each hold their own SESSIONS and return
"session not found".
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid

from flask import Flask, abort, jsonify, render_template, request, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path: str) -> None:
    """Minimal .env loader (KEY=VALUE per line) - avoids an extra dependency."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(BASE_DIR, ".env"))

DEMO = os.environ.get("SOPHIA_DEMO", "0").strip() == "1"  # strip: cmd "set X=1 && ..." keeps a space
if DEMO:  # no API key needed: returns bundled example cases
    import demo as llm  # noqa: E402
else:
    import llm  # noqa: E402  (needs env loaded first)
import particle_gen  # noqa: E402
from rag import load_manuals  # noqa: E402

OUTPUT_DIR = os.environ.get("SOPHIA_OUTPUT_DIR", os.path.join(BASE_DIR, "outputs"))
MANUAL_DIR = os.environ.get("SOPHIA_MANUAL_DIR", os.path.join(BASE_DIR, "manuals"))
SESSION_TTL = int(os.environ.get("SESSION_TTL_SEC", str(6 * 3600)))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024  # 40 MB PDF limit
app.json.sort_keys = False  # keep spec keys in the model's / file's order

SESSIONS: dict = {}
_LOCK = threading.Lock()
MANUALS = load_manuals(MANUAL_DIR)
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _gc_sessions() -> None:
    now = time.time()
    with _LOCK:
        for sid in [s for s, v in SESSIONS.items() if now - v["created"] > SESSION_TTL]:
            SESSIONS.pop(sid, None)


def _get_session(sid: str) -> dict:
    with _LOCK:
        sess = SESSIONS.get(sid)
    if sess is None:
        abort(404, description="session not found (expired, or server runs with >1 worker)")
    return sess


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(500)
def _json_error(err):
    code = getattr(err, "code", 500)
    return jsonify(error=getattr(err, "description", str(err))), code


@app.get("/")
def index():
    return render_template("index.html", manual_chunks=len(MANUALS.docs), demo=DEMO)


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, sessions=len(SESSIONS), manual_chunks=len(MANUALS.docs))


@app.post("/api/upload")
def upload():
    """Upload a paper PDF and get the list of reproducible cases."""
    _gc_sessions()
    f = request.files.get("pdf")
    if f is None or not f.filename.lower().endswith(".pdf"):
        abort(400, description="PDF 파일을 업로드하십시오 (field name: pdf)")
    pdf = f.read()
    ctx = MANUALS.context("input.txt particle type boundary fluid DEM open boundary", k=4)
    try:
        report = llm.analyze_paper(pdf, ctx)
    except Exception as e:  # surface API errors to the UI
        abort(500, description=f"논문 분석 실패: {e}")
    sid = uuid.uuid4().hex
    with _LOCK:
        SESSIONS[sid] = {
            "created": time.time(),
            "filename": f.filename,
            "pdf": pdf,
            "report": report,
            "specs": {},
            "outputs": {},
        }
    return jsonify(session_id=sid, **report)


@app.post("/api/spec")
def spec():
    """Ask Claude for the geometry spec of the selected case."""
    body = request.get_json(force=True)
    sess = _get_session(body.get("session_id", ""))
    case_id = body.get("case_id")
    case = next((c for c in sess["report"].get("cases", []) if c.get("id") == case_id), None)
    if case is None:
        abort(400, description=f"unknown case_id '{case_id}'")
    query = " ".join(str(case.get(k, "")) for k in ("name", "geometry", "parameters", "physics"))
    try:
        result = llm.build_spec(sess["pdf"], case, MANUALS.context(query, k=4), body.get("notes", ""))
    except Exception as e:
        abort(500, description=f"spec 생성 실패: {e}")
    sess["specs"][case_id] = result
    return jsonify(spec=result)


@app.post("/api/generate")
def generate():
    """Deterministically generate input.txt from a (possibly user-edited) spec."""
    body = request.get_json(force=True)
    spec_in = body.get("spec")
    if not isinstance(spec_in, dict):
        abort(400, description="'spec' (object) is required")
    sid = body.get("session_id") or "direct"
    if sid != "direct":
        _get_session(sid)
    name = _SAFE.sub("_", str(spec_in.get("name") or body.get("case_id") or "case"))[:80]
    out_dir = os.path.join(OUTPUT_DIR, sid, name)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "input.txt")
    try:
        fields = particle_gen.generate(spec_in)
        from sophia_format import select_columns, write_input_txt

        cols = select_columns(fields, spec_in.get("extra_columns"))
        write_input_txt(out_path, fields, cols)
        summary = particle_gen.summarize(fields, spec_in)
    except particle_gen.SpecError as e:
        abort(400, description=f"spec 오류: {e}")
    with open(out_path, encoding="utf-8") as fh:
        head = [next(fh, "") for _ in range(6)]
    return jsonify(
        summary=summary,
        head="".join(head),
        preview=particle_gen.preview_points(fields, int(spec_in.get("dim", 2))),
        dim=int(spec_in.get("dim", 2)),
        download=f"/api/download/{sid}/{name}",
    )


@app.get("/api/download/<sid>/<name>")
def download(sid: str, name: str):
    path = os.path.join(OUTPUT_DIR, _SAFE.sub("_", sid), _SAFE.sub("_", name), "input.txt")
    if not os.path.isfile(path):
        abort(404, description="file not found")
    return send_file(path, as_attachment=True, download_name="input.txt", mimetype="text/plain")


if __name__ == "__main__":
    # Development server (Windows local). For deployment use start_server.sh.
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
