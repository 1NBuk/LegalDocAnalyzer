import json, secrets, time, threading, hashlib, os, re, csv, io, logging, sqlite3
try:
    from psycopg.errors import UniqueViolation
except ImportError:
    UniqueViolation = sqlite3.IntegrityError
from datetime import datetime, timedelta
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from . import db, engine
from .engine import PRODUCTS, PURPOSE_LABELS

logging.basicConfig(level=logging.INFO, format="%(message)s"); log = logging.getLogger("alfa")
PROD = os.getenv("APP_ENV") == "production"
app = FastAPI(title="Альфа-Бизнес · Умный кредитный кабинет", version="4.0.0", docs_url=None if PROD else "/docs", redoc_url=None if PROD else "/redoc",
              description="Клиентский кабинет + внутренняя обработка заявки. DS-модуль: /ds/*. Метрики: /api/metrics")
@app.on_event("startup")
def _start():
    if PROD and (os.getenv("SIGN_SALT", "demo-salt") == "demo-salt" or os.getenv("SEED_DEMO", "1") == "1"):
        raise RuntimeError("APP_ENV=production: задайте SIGN_SALT и SEED_DEMO=0 (демо-пользователи запрещены)")
    db.init(); threading.Thread(target=engine.get_scorer, daemon=True).start()   # прогрев модели

# ---------- middleware: request-id, структурные логи, метрики, заголовки безопасности ----------
MET = {"req": {}, "lat": 0.0, "n": 0}
@app.middleware("http")
async def mw(request: Request, call_next):
    rid = request.headers.get("x-request-id") or secrets.token_hex(6); t = time.time()
    resp = await call_next(request); ms = (time.time() - t) * 1000; p = request.url.path
    resp.headers.update({"X-Request-Id": rid, "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})
    if not p.startswith(("/docs", "/redoc", "/openapi")):
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"
    if p.startswith(("/api", "/ds")):
        k = (request.method, re.sub(r"/\d+", "/{id}", p), resp.status_code); MET["req"][k] = MET["req"].get(k, 0) + 1; MET["lat"] += ms; MET["n"] += 1
        log.info(json.dumps({"rid": rid, "m": request.method, "p": p, "s": resp.status_code, "ms": round(ms, 1)}))
    return resp

# Конечный автомат заявки (диаграмма: docs/states.md)
T = {"SUBMITTED": ["VALIDATION"], "VALIDATION": ["ENRICHMENT"], "ENRICHMENT": ["SCORING"],
     "SCORING": ["OFFER_READY", "NEED_INFO", "UNDERWRITER_REVIEW", "DECLINED"],
     "NEED_INFO": ["SCORING", "CANCELLED"], "UNDERWRITER_REVIEW": ["OFFER_READY", "NEED_INFO", "DECLINED"],
     "OFFER_READY": ["OFFER_ACCEPTED", "CANCELLED", "EXPIRED"], "OFFER_ACCEPTED": ["SIGNED", "CANCELLED"], "SIGNED": ["DISBURSED"]}
STAGE = {"SUBMITTED": "SUBMITTED", "VALIDATION": "SUBMITTED", "ENRICHMENT": "SUBMITTED", "SCORING": "SUBMITTED", "UNDERWRITER_REVIEW": "SUBMITTED",
         "NEED_INFO": "NEED_INFO", "OFFER_READY": "OFFER_READY", "DECLINED": "DECLINED", "OFFER_ACCEPTED": "OFFER_ACCEPTED", "SIGNED": "SIGNING", "DISBURSED": "SIGNING"}
SLA_HOURS = int(os.getenv("SLA_HOURS", 4)); SIGN_SALT = os.getenv("SIGN_SALT", "demo-salt")
now = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
tk = lambda t: hashlib.sha256(t.encode()).hexdigest()

# ---------- auth ----------
def user(authorization: str = Header(None)):
    r = db.q("select u.*, s.created sc from sessions s join users u on u.id=s.user_id where s.token=?", (tk((authorization or "").removeprefix("Bearer ")),), one=True)
    if not r or time.time() - r["sc"] > 12 * 3600: raise HTTPException(401, "Сессия истекла. Войдите заново")
    return dict(r)
def client(u=Depends(user)):
    if u["role"] != "client": raise HTTPException(403, "Доступно только клиенту")
    return u
def operator(u=Depends(user)):
    if u["role"] != "operator": raise HTTPException(403, "Доступно только сотруднику банка")
    return u
def company_of(u): return json.loads(u["company"]) if u.get("company") else None
pub = lambda r: {"id": r["id"], "login": r["login"], "role": r["role"], "name": r["name"], "auth_limit": r["auth_limit"]}

class Login(BaseModel): login: str; password: str
@app.post("/api/login", tags=["auth"])
def login(b: Login):
    if db.q("select count(*) n from login_attempts where login=? and ts>?", (b.login, time.time() - 60), one=True)["n"] >= 5: raise HTTPException(429, "Слишком много попыток. Подождите минуту")
    r = db.q("select * from users where login=?", (b.login,), one=True)
    if not r or not db.check_pw(b.password, r["pw"]):
        db.q("insert into login_attempts values(?,?)", (b.login, time.time()), commit=True); log.warning(json.dumps({"event": "login_failed", "login": b.login[:40]}))
        raise HTTPException(401, "Неверный логин или пароль")
    t = secrets.token_hex(24); db.q("insert into sessions values(?,?,?)", (tk(t), r["id"], time.time()), commit=True)   # в БД — только хеш токена
    return {"token": t, "user": pub(r)}
@app.get("/api/me", tags=["auth"])
def me(u=Depends(user)): return pub(u)
@app.post("/api/logout", tags=["auth"])
def logout(u=Depends(user), authorization: str = Header(None)):
    db.q("delete from sessions where token=?", (tk(authorization.removeprefix("Bearer ")),), commit=True); return {"ok": True}

# ---------- справочники и профиль ----------
Sector = Literal["retail", "food", "services", "manufacturing", "construction", "it", "transport", "agro"]
DEMO_INNS = {"7700000001": "Надёжный клиент", "7700000002": "Тонкая кредитная история", "7700000003": "Перегруженный долгами",
             "7700000004": "Компания в ликвидации", "7700000005": "Серая зона", "7700000006": "Налоговая задолженность"}
@app.get("/api/catalog", tags=["catalog"])
def catalog(u=Depends(user)):
    return {"products": {k: {kk: v[kk] for kk in ("name", "pitch", "min_amount", "max_amount", "min_term", "max_term", "purposes")} for k, v in PRODUCTS.items()},
            "purposes": PURPOSE_LABELS, "demo_inns": DEMO_INNS}

class Profile(BaseModel):
    inn: str = Field(pattern=r"^\d{10}(\d{2})?$"); legal_form: Literal["OOO", "IP"] = "OOO"; sector: Sector = "retail"
    region_tier: Literal["msk_spb", "million", "other"] = "million"; employees: int = Field(1, ge=0, le=100000)
    revenue: float | None = Field(None, gt=0, le=1e12); profit_margin: float | None = Field(None, ge=-1, le=1)
    revenue_growth: float = Field(0, ge=-1, le=5); existing_debt: float = Field(0, ge=0); active_loans: int = Field(0, ge=0, le=100)
    collateral_value: float = Field(0, ge=0)
class Form(Profile):
    product: str; purpose: str; amount: float = Field(gt=0, le=1e9); term: int = Field(ge=1, le=240)
    @field_validator("product")
    def _p(cls, v):
        if v not in PRODUCTS: raise ValueError("неизвестный продукт")
        return v
    @field_validator("purpose")
    def _u(cls, v):
        if v not in PURPOSE_LABELS: raise ValueError("неизвестная цель")
        return v

@app.get("/api/company", tags=["company"])
def get_company(u=Depends(client)): return company_of(u)
@app.put("/api/company", tags=["company"])
def put_company(p: Profile, u=Depends(client)):
    db.q("update users set company=? where id=?", (p.model_dump_json(), u["id"]), commit=True); return p
@app.get("/api/company/lookup/{inn}", tags=["company"])
def lookup(inn: str, u=Depends(user)):
    from credit_ds.mock_integrations import enrich
    if not (inn.isdigit() and len(inn) in (10, 12)): raise HTTPException(422, "ИНН: 10 или 12 цифр")
    return {"egrul": enrich(inn)["egrul"], "demo": DEMO_INNS.get(inn)}

class Ask(BaseModel): text: str = Field(min_length=3, max_length=1000)
@app.post("/api/assistant", tags=["ai"])
def assistant(b: Ask, u=Depends(client)):
    c = company_of(u)
    if not c or not c.get("revenue") or c.get("profit_margin") is None: raise HTTPException(422, "Заполните данные компании (выручка и маржа), чтобы рассчитать лимиты")
    return engine.assistant(c, b.text)

# ---------- уведомления ----------
NOTE = {"UNDERWRITER_REVIEW": ("Заявка на рассмотрении", "Специалист банка проверяет заявку №{id}"), "OFFER_READY": ("Предложение готово", "Условия по заявке №{id} ждут вашего решения"),
        "NEED_INFO": ("Нужны данные", "По заявке №{id} требуется уточнение"), "DECLINED": ("Решение по заявке", "По заявке №{id} принят отказ"),
        "DISBURSED": ("Деньги выданы", "Средства по заявке №{id} зачислены на счёт"), "EXPIRED": ("Предложение истекло", "Срок предложения по заявке №{id} закончился")}
def note(uid, title, body, aid):
    db.q("insert into notifications(user_id,ts,title,body,app_id) values(?,?,?,?,?)", (uid, now(), title, body, aid), commit=True)
def notify(aid, new):
    a = db.q("select user_id from applications where id=?", (aid,), one=True)
    if new in NOTE: note(a["user_id"], NOTE[new][0], NOTE[new][1].format(id=aid), aid)
    if new == "UNDERWRITER_REVIEW":
        for r in db.q("select id from users where role='operator'"): note(r["id"], "Новая заявка на разбор", f"Заявка №{aid} ждёт андеррайтера (SLA {SLA_HOURS} ч)", aid)
@app.get("/api/notifications", tags=["notifications"])
def notifications(u=Depends(user)):
    items = [dict(r) for r in db.q("select id,ts,title,body,app_id,read from notifications where user_id=? order by id desc limit 30", (u["id"],))]
    return {"items": items, "unread": sum(1 for i in items if not i["read"])}
@app.post("/api/notifications/read", tags=["notifications"])
def notes_read(u=Depends(user)):
    db.q("update notifications set read=1 where user_id=?", (u["id"],), commit=True); return {"ok": True}

# ---------- заявки ----------
def move(aid, new, note_, actor):
    """Атомарный переход: проверка графа + оптимистичная блокировка (update ... where status=<прежний>) + событие в одной транзакции."""
    c = db.conn()
    try:
        cur = c.execute("select status from applications where id=?", (aid,)).fetchone()["status"]
        if new not in T.get(cur, []): raise HTTPException(409, f"Переход {cur} → {new} запрещён")
        sla = (datetime.now() + timedelta(hours=SLA_HOURS)).strftime("%Y-%m-%d %H:%M:%S") if new == "UNDERWRITER_REVIEW" else None
        n = c.execute("update applications set status=?, updated=?, version=version+1, sla_due=coalesce(?, sla_due) where id=? and status=?", (new, now(), sla, aid, cur)).rowcount
        if n != 1: raise HTTPException(409, "Заявка уже изменена другим пользователем. Обновите страницу")
        c.execute("insert into events(app_id,ts,status,note,actor) values(?,?,?,?,?)", (aid, now(), new, note_, actor)); c.commit()
    finally: c.close()
    notify(aid, new)
def ev(aid, status, note_, actor): db.q("insert into events(app_id,ts,status,note,actor) values(?,?,?,?,?)", (aid, now(), status, note_, actor), commit=True)
def stamp_offer(aid):
    d = json.loads(db.q("select decision from applications where id=?", (aid,), one=True)["decision"])
    if d.get("offer"):
        d["offer"]["valid_until"] = (datetime.now() + timedelta(days=d["offer"].get("valid_days", 14))).strftime("%Y-%m-%d")
        db.q("update applications set decision=? where id=?", (engine.dumps(d), aid), commit=True)
def expire(aid):
    r = db.q("select status, decision from applications where id=?", (aid,), one=True)
    if r and r["status"] == "OFFER_READY" and r["decision"]:
        vu = (json.loads(r["decision"]).get("offer") or {}).get("valid_until")
        if vu and datetime.now().strftime("%Y-%m-%d") > vu: move(aid, "EXPIRED", "Срок действия предложения истёк", "система")

def view(aid, u):
    expire(aid)
    r = db.q("select a.*, u.name client from applications a join users u on u.id=a.user_id where a.id=?", (aid,), one=True)
    if not r or (u["role"] == "client" and r["user_id"] != u["id"]): raise HTTPException(404, "Заявка не найдена")
    r = dict(r); r["form"] = json.loads(r["form"]); d = json.loads(r["decision"]) if r["decision"] else None; r.pop("idem_key", None)
    r["overdue"] = bool(r["status"] == "UNDERWRITER_REVIEW" and r["sla_due"] and now() > r["sla_due"])
    if d and u["role"] == "client": d = {k: d.get(k) for k in ("outcome", "offer", "reasons", "missing_info", "drivers", "explanation", "underwriter")}
    if u["role"] == "client": r.pop("assigned_to", None); r.pop("sla_due", None)
    r["decision"] = d
    r["events"] = [dict(e) for e in db.q("select ts,status,note,actor from events where app_id=? order by id", (aid,))]
    return r

def pipeline(aid, actor):
    a = db.q("select * from applications where id=?", (aid,), one=True); form = json.loads(a["form"])
    try: res = engine.run(form)
    except Exception as e:
        log.error(json.dumps({"event": "scoring_failed", "app": aid, "err": type(e).__name__})); ev(aid, a["status"], f"Сбой сервиса оценки: {type(e).__name__}", "система")
        raise HTTPException(503, "Сервис оценки временно недоступен. Повторите через минуту")
    en = res["enrichment"]; b = en["bki"]["bki_score"]
    if a["status"] == "SUBMITTED":
        move(aid, "VALIDATION", "Проверка формата и полноты данных", "система")
        move(aid, "ENRICHMENT", f"ЕГРЮЛ: {'действует' if en['egrul']['status'] == 'active' else 'не действует'}; ФНС: {'есть задолженность' if en['fns']['tax_arrears'] else 'без долгов'}; БКИ: {'скор ' + str(b) if b else 'нет истории'} (симуляция)", "интеграции")
    move(aid, "SCORING", "Оценка риска и проверка условий продукта", "модель")
    db.q("update applications set decision=? where id=?", (engine.dumps(res), aid), commit=True)
    move(aid, res["next_status"], res["explanation"], "решение")
    if res["next_status"] == "OFFER_READY": stamp_offer(aid)

@app.post("/api/applications", status_code=201, tags=["applications"])
def create(f: Form, u=Depends(client), idempotency_key: str | None = Header(None)):
    key = idempotency_key[:64] if idempotency_key else None
    if key:
        ex = db.q("select id from applications where user_id=? and idem_key=?", (u["id"], key), one=True)
        if ex: return view(ex["id"], u)      # повторный запрос с тем же ключом не создаёт дубль
    if f.purpose not in PRODUCTS[f.product]["purposes"]: raise HTTPException(422, "Продукт не подходит для выбранной цели")
    p = PRODUCTS[f.product]
    if not p["min_amount"] <= f.amount <= p["max_amount"]: raise HTTPException(422, f"Сумма для продукта: {p['min_amount']:,}–{p['max_amount']:,} ₽".replace(",", " "))
    if not p["min_term"] <= f.term <= p["max_term"]: raise HTTPException(422, f"Срок для продукта: {p['min_term']}–{p['max_term']} мес.")
    try: aid = db.q("insert into applications(user_id,status,form,created,updated,idem_key) values(?,?,?,?,?,?)", (u["id"], "SUBMITTED", f.model_dump_json(), now(), now(), key), commit=True)
    except (sqlite3.IntegrityError, UniqueViolation): return view(db.q("select id from applications where user_id=? and idem_key=?", (u["id"], key), one=True)["id"], u)
    ev(aid, "SUBMITTED", "Заявка отправлена", u["name"])
    db.q("update users set company=? where id=?", (Profile(**f.model_dump()).model_dump_json(), u["id"]), commit=True)   # профиль запоминается
    pipeline(aid, u["name"]); return view(aid, u)

@app.get("/api/applications", tags=["applications"])
def apps(u=Depends(user)):
    op = u["role"] == "operator"
    rows = db.q("select id from applications" + ("" if op else " where user_id=?") + " order by id desc", () if op else (u["id"],))
    return [view(r["id"], u) for r in rows]
@app.get("/api/applications/{aid}", tags=["applications"])
def get(aid: int, u=Depends(user)): return view(aid, u)

def sign_code(aid): return f"{int(hashlib.sha256(f'{aid}:{SIGN_SALT}'.encode()).hexdigest(), 16) % 10000:04d}"   # симуляция ЭП/кода подтверждения
class Respond(BaseModel): action: Literal["provide_info", "accept", "decline", "get_code", "sign"]; fields: dict = {}; message: str = Field("", max_length=500)
@app.post("/api/applications/{aid}/respond", tags=["applications"])
def respond(aid: int, b: Respond, u=Depends(client)):
    a = view(aid, u); s = a["status"]
    if b.action == "provide_info":
        if s != "NEED_INFO": raise HTTPException(409, "Заявка не ждёт данных")
        allowed = set(Profile.model_fields) - {"inn"}
        form = Form(**{**a["form"], **{k: v for k, v in b.fields.items() if k in allowed and v not in ("", None)}})
        db.q("update applications set form=? where id=?", (form.model_dump_json(), aid), commit=True)
        ev(aid, s, "Клиент дополнил данные" + (f": {b.message}" if b.message else ""), u["name"]); pipeline(aid, u["name"])
    elif b.action == "accept": move(aid, "OFFER_ACCEPTED", "Клиент принял условия", u["name"])
    elif b.action == "decline": move(aid, "CANCELLED", "Клиент отказался от заявки", u["name"])
    elif b.action == "get_code":
        if s != "OFFER_ACCEPTED": raise HTTPException(409, "Подписание недоступно на этом шаге")
        return {"code": sign_code(aid)}
    else:
        if str(b.fields.get("code", "")) != sign_code(aid): raise HTTPException(422, "Неверный код подтверждения")
        move(aid, "SIGNED", "Договор подписан (симуляция электронной подписи)", u["name"]); move(aid, "DISBURSED", "Средства зачислены на счёт (симуляция)", "банк")
    return view(aid, u)

# ---------- кабинет банка ----------
@app.post("/api/operator/applications/{aid}/assign", tags=["operator"])
def assign(aid: int, u=Depends(operator)):
    a = view(aid, u)
    if a["status"] != "UNDERWRITER_REVIEW": raise HTTPException(409, "Заявка не на рассмотрении андеррайтера")
    c = db.conn()
    try:
        n = c.execute("update applications set assigned_to=? where id=? and (assigned_to is null or assigned_to=?)", (u["name"], aid, u["name"])).rowcount; c.commit()
    finally: c.close()
    if n != 1: raise HTTPException(409, f"Заявка уже в работе у {a['assigned_to']}")
    ev(aid, "UNDERWRITER_REVIEW", "Взята в работу", u["name"]); return view(aid, u)

class OpDecision(BaseModel):
    action: Literal["approve", "decline", "request_info", "escalate"]; comment: str = Field("", max_length=500)
    amount: float | None = Field(None, gt=0); term: int | None = Field(None, ge=1, le=240); rate: float | None = Field(None, ge=0.01, le=0.6)
@app.post("/api/operator/applications/{aid}/decision", tags=["operator"])
def op_decision(aid: int, b: OpDecision, u=Depends(operator)):
    a = view(aid, u)
    if a["status"] != "UNDERWRITER_REVIEW": raise HTTPException(409, "Заявка не на рассмотрении андеррайтера")
    if a["assigned_to"] and a["assigned_to"] != u["name"]: raise HTTPException(409, f"Заявка в работе у {a['assigned_to']}")
    d = a["decision"]; pid = a["form"]["product"]; p = PRODUCTS[pid]
    if b.action == "escalate":
        d["escalated"] = True; db.q("update applications set assigned_to=null, decision=? where id=?", (engine.dumps(d), aid), commit=True)
        ev(aid, "UNDERWRITER_REVIEW", f"Эскалация старшему андеррайтеру. {b.comment}".strip(), u["name"]); return view(aid, u)
    if b.action == "approve":
        s = d.get("suggested_offer") or d.get("offer")
        if not s and not (b.amount and b.term and b.rate): raise HTTPException(422, "Укажите сумму, срок и ставку")
        amt, term, rate = b.amount or s["amount"], b.term or s["term_months"], b.rate or s["rate"]
        if amt > (u["auth_limit"] or 0): raise HTTPException(403, f"Сумма превышает ваш лимит полномочий ({int(u['auth_limit'] or 0):,} ₽). Эскалируйте заявку".replace(",", " "))
        if amt > a["form"]["amount"] or not p["min_amount"] <= amt <= p["max_amount"]: raise HTTPException(422, "Сумма вне лимитов продукта или выше запрошенной")
        if not p["min_term"] <= term <= p["max_term"]: raise HTTPException(422, "Срок вне диапазона продукта")
        d["offer"] = engine.make_offer(pid, amt, term, rate); new, txt = "OFFER_READY", "Одобрено андеррайтером"
    else: new, txt = ("DECLINED", "Отказ андеррайтера") if b.action == "decline" else ("NEED_INFO", "Андеррайтер запросил данные")
    d["underwriter"] = {"by": u["name"], "action": b.action, "comment": b.comment, "at": now()}
    if new == "NEED_INFO": d["missing_info"] = []
    db.q("update applications set decision=? where id=?", (engine.dumps(d), aid), commit=True)
    move(aid, new, f"{txt}. {b.comment}".strip(), u["name"])
    if new == "OFFER_READY": stamp_offer(aid)
    return view(aid, u)

@app.get("/api/operator/stats", tags=["operator"])
def stats(u=Depends(operator)):
    rows = [view(r["id"], u) for r in db.q("select id from applications")]
    by: dict = {}
    for r in rows: by[r["status"]] = by.get(r["status"], 0) + 1
    decided = [r for r in rows if r["decision"] and r["decision"].get("outcome") in ("APPROVED", "COUNTER_OFFER", "DECLINED")]
    pds = [r["decision"]["pd"] for r in rows if r["decision"] and r["decision"].get("pd") is not None]
    ok = sum(1 for r in decided if r["decision"]["outcome"] != "DECLINED")
    fr = {r["status"]: r["n"] for r in db.q("select status, count(distinct app_id) n from events group by status")}
    funnel = [{"label": l, "n": fr.get(s, 0)} for l, s in [("Подано", "SUBMITTED"), ("Прошли оценку", "SCORING"), ("Получили предложение", "OFFER_READY"), ("Приняли условия", "OFFER_ACCEPTED"), ("Выдано", "DISBURSED")]]
    return {"total": len(rows), "by_status": by, "approval_rate": ok / len(decided) if decided else None, "avg_pd": sum(pds) / len(pds) if pds else None,
            "funnel": funnel, "overdue": sum(1 for r in rows if r["overdue"])}
@app.get("/api/operator/export.csv", tags=["operator"])
def export(u=Depends(operator)):
    out = io.StringIO(); w = csv.writer(out); w.writerow(["id", "created", "client", "inn", "product", "amount", "term", "status", "outcome", "grade", "pd", "assigned_to"])
    for r in db.q("select id from applications order by id"):
        a = view(r["id"], u); d = a["decision"] or {}
        w.writerow([a["id"], a["created"], a["client"], a["form"]["inn"], a["form"]["product"], a["form"]["amount"], a["form"]["term"], a["status"], d.get("outcome"), d.get("grade"), d.get("pd"), a["assigned_to"]])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=applications.csv"})
@app.get("/api/operator/model-card", tags=["operator"])
def model_card(u=Depends(operator)): return engine.get_scorer().card

@app.get("/api/news", tags=["ai"])
def news(u=Depends(client)):
    c = company_of(u) or {}; mine = apps(u); last = mine[0] if mine else None
    stage = STAGE.get(last["status"], "DRAFT") if last else "DRAFT"
    return {"stage": stage, "items": engine.news(c, last, stage)}

# ---------- эксплуатация ----------
@app.get("/api/health", tags=["ops"])
def health(): return {"ok": True}
@app.get("/api/ready", tags=["ops"])
def ready():
    try: db.q("select 1"); engine.get_scorer()
    except Exception: raise HTTPException(503, "not ready")
    return {"ready": True}
@app.get("/api/metrics", tags=["ops"])
def metrics():
    L = ["# TYPE alfa_http_requests_total counter"] + [f'alfa_http_requests_total{{method="{m}",path="{p}",status="{s}"}} {n}' for (m, p, s), n in sorted(MET["req"].items())]
    L += ["# TYPE alfa_http_latency_ms_sum counter", f"alfa_http_latency_ms_sum {MET['lat']:.1f}", f"alfa_http_latency_ms_count {MET['n']}", "# TYPE alfa_applications gauge"]
    L += [f'alfa_applications{{status="{r["status"]}"}} {r["n"]}' for r in db.q("select status, count(*) n from applications group by status")]
    return Response("\n".join(L) + "\n", media_type="text/plain; version=0.0.4")

from credit_ds.api import router as ds_router        # /ds/* — DS-сервис в составе бэкенда (только для авторизованных)
app.include_router(ds_router, dependencies=[Depends(user)])
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), html=True), name="static")
