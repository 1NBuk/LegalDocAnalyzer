import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURE = Path(__file__).parent / "fixtures" / "contract_sample.pdf"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import importlib
    import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), tmp_path


def test_health(client):
    c, _ = client
    assert c.get("/health").json() == {"status": "ok"}


def test_analyze_pdf_returns_result_and_metrics_and_cleans_upload(client):
    c, tmp = client
    r = c.post("/analyze", files={"file": ("test.pdf", FIXTURE.read_bytes(), "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["doc_type"] == "договор"
    assert body["result"]["parties"] == ['ООО "Альфа"', "ИП Иванов И.И."]
    assert body["metrics"]["total_s"] >= 0 and body["metrics"]["pages"] == 1
    assert list((tmp / "data").iterdir()) == []  # загруженный документ удалён
    saved = c.get(f"/results/{body['id']}").json()
    assert saved["result"] == body["result"]


def test_path_traversal_filename_is_neutralized(client):
    c, tmp = client
    r = c.post("/analyze", files={"file": ("../../evil.pdf", FIXTURE.read_bytes(), "application/pdf")})
    assert r.status_code == 200 and r.json()["filename"] == "evil.pdf"
    assert not (tmp.parent / "evil.pdf").exists()


def test_rejects_unsupported_extension(client):
    c, _ = client
    assert c.post("/analyze", files={"file": ("a.exe", b"MZ", "application/octet-stream")}).status_code == 415


def test_rejects_too_large_file(client):
    c, tmp = client
    big = io.BytesIO(b"0" * (2 * 1024 * 1024))
    assert c.post("/analyze", files={"file": ("big.pdf", big, "application/pdf")}).status_code == 413
    assert list((tmp / "data").iterdir()) == []


def test_results_endpoint_validates_id(client):
    c, _ = client
    assert c.get("/results/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)
    assert c.get("/results/00000000-0000-0000-0000-000000000000").status_code == 404
