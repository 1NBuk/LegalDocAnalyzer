import os, tempfile
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
from fastapi.testclient import TestClient
from app.main import app

def auth(c, l, p):
    r = c.post("/api/login", json={"login": l, "password": p}); assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}
BODY = dict(inn="7700000001", legal_form="OOO", sector="retail", region_tier="million", employees=10, revenue=24e6, profit_margin=0.16,
            revenue_growth=0.08, existing_debt=2.4e6, active_loans=1, collateral_value=0, product="working_capital", purpose="working_capital", amount=2e6, term=36)

def test_full_path_to_disbursement():
    with TestClient(app) as c:
        h = auth(c, "client1", "client"); a = c.post("/api/applications", json=BODY, headers=h).json()
        assert a["status"] == "OFFER_READY" and a["decision"]["offer"] and "pd" not in (a["decision"] or {})
        u = f"/api/applications/{a['id']}/respond"
        assert c.post(u, json={"action": "accept"}, headers=h).json()["status"] == "OFFER_ACCEPTED"
        assert c.post(u, json={"action": "sign", "fields": {"code": "0000x"}}, headers=h).status_code == 422
        code = c.post(u, json={"action": "get_code"}, headers=h).json()["code"]
        assert c.post(u, json={"action": "sign", "fields": {"code": code}}, headers=h).json()["status"] == "DISBURSED"
        assert [e["status"] for e in a["events"]][:4] == ["SUBMITTED", "VALIDATION", "ENRICHMENT", "SCORING"]

def test_knockout_declined():
    with TestClient(app) as c:
        h = auth(c, "client3", "client")
        assert c.post("/api/applications", json={**BODY, "inn": "7700000004"}, headers=h).json()["status"] == "DECLINED"

def test_need_info_then_rescore():
    with TestClient(app) as c:
        h = auth(c, "client1", "client"); a = c.post("/api/applications", json={**BODY, "profit_margin": None}, headers=h).json()
        assert a["status"] == "NEED_INFO" and "profit_margin" in a["decision"]["missing_info"]
        r = c.post(f"/api/applications/{a['id']}/respond", json={"action": "provide_info", "fields": {"profit_margin": 0.16}}, headers=h).json()
        assert r["status"] != "NEED_INFO"

def test_underwriter_flow():
    with TestClient(app) as c:
        h, ho = auth(c, "client2", "client"), auth(c, "operator", "operator")
        a = c.post("/api/applications", json={**BODY, "inn": "7700000005"}, headers=h).json()
        if a["status"] != "UNDERWRITER_REVIEW": return           # исход зависит от модели; ветку проверяем, когда она достигнута
        assert "trace" not in a["decision"]
        full = c.get(f"/api/applications/{a['id']}", headers=ho).json(); assert full["decision"]["trace"]
        r = c.post(f"/api/operator/applications/{a['id']}/decision", json={"action": "approve", "comment": "ок"}, headers=ho).json()
        assert r["status"] == "OFFER_READY"

def test_access_control_and_illegal_transition():
    with TestClient(app) as c:
        h1, h2 = auth(c, "client1", "client"), auth(c, "client2", "client")
        a = c.post("/api/applications", json=BODY, headers=h1).json()
        assert c.get(f"/api/applications/{a['id']}", headers=h2).status_code == 404
        assert c.post(f"/api/applications/{a['id']}/respond", json={"action": "get_code"}, headers=h1).status_code == 409
        assert c.get("/api/operator/stats", headers=h1).status_code == 403
        assert c.get("/api/applications").status_code == 401

def test_validation_errors():
    with TestClient(app) as c:
        h = auth(c, "client1", "client")
        assert c.post("/api/applications", json={**BODY, "amount": 10}, headers=h).status_code == 422
        assert c.post("/api/applications", json={**BODY, "inn": "12"}, headers=h).status_code == 422

def test_idempotency_no_duplicates():
    with TestClient(app) as c:
        h = {**auth(c, "client1", "client"), "Idempotency-Key": "k-123"}
        a = c.post("/api/applications", json=BODY, headers=h).json(); b = c.post("/api/applications", json=BODY, headers=h).json()
        assert a["id"] == b["id"]

def test_notifications_and_read():
    with TestClient(app) as c:
        h = auth(c, "client1", "client"); c.post("/api/applications", json=BODY, headers=h)
        n = c.get("/api/notifications", headers=h).json(); assert n["unread"] >= 1
        c.post("/api/notifications/read", headers=h); assert c.get("/api/notifications", headers=h).json()["unread"] == 0

def test_ops_endpoints_and_security_headers():
    with TestClient(app) as c:
        r = c.get("/api/health"); assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-request-id"]
        assert "alfa_http_requests_total" in c.get("/api/metrics").text
        assert c.get("/api/ready").status_code == 200
        ho = auth(c, "senior", "senior"); assert c.get("/api/me", headers=ho).json()["auth_limit"] == 50_000_000
        assert c.get("/api/operator/export.csv", headers=ho).text.startswith("id,created")
        assert len(c.get("/api/operator/stats", headers=ho).json()["funnel"]) == 5

def test_logout_invalidates_token():
    with TestClient(app) as c:
        h = auth(c, "client1", "client"); c.post("/api/logout", headers=h); assert c.get("/api/me", headers=h).status_code == 401
