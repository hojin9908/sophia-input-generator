import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as webapp  # noqa: E402


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUTPUT_DIR", str(tmp_path))
    return webapp.app.test_client()


def test_full_flow_with_mocked_llm(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    with open(os.path.join(ROOT, "examples", "dam_break_2d.json"), encoding="utf-8") as f:
        spec = json.load(f)
    monkeypatch.setattr(webapp.llm, "analyze_paper", lambda pdf, ctx: {
        "paper_title": "T", "cases": [{"id": "c1", "name": "dam", "dim": 2, "geometry": "g", "parameters": "p"}]})
    monkeypatch.setattr(webapp.llm, "build_spec", lambda pdf, case, ctx, notes: spec)

    r = c.post("/api/upload", data={"pdf": (io.BytesIO(b"%PDF-1.4"), "paper.pdf")},
               content_type="multipart/form-data")
    assert r.status_code == 200, r.json
    sid = r.json["session_id"]
    r = c.post("/api/spec", json={"session_id": sid, "case_id": "c1"})
    assert r.status_code == 200 and r.json["spec"]["dp"] == 0.01
    r = c.post("/api/generate", json={"session_id": sid, "case_id": "c1", "spec": spec})
    assert r.status_code == 200, r.json
    assert r.json["summary"]["fluid"] == 3600
    d = c.get(r.json["download"])
    assert d.status_code == 200
    assert d.data.decode().splitlines()[0] == "\t".join(str(i) for i in range(1, 14))


def test_unknown_session_and_bad_spec(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/spec", json={"session_id": "nope", "case_id": "x"})
    assert r.status_code == 404 and "session" in r.json["error"]
    r = c.post("/api/generate", json={"spec": {"dim": 2, "dp": -1}})
    assert r.status_code == 400
    assert c.get("/").status_code == 200
