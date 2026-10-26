"""Слой БД: PostgreSQL (DATABASE_URL, пул соединений) в продакшене, SQLite — для локального запуска и тестов.
Один и тот же SQL; различия диалектов скрыты в обёртке (placeholders, returning id, DDL)."""
import os, re, sqlite3, hashlib, hmac, secrets, json, threading
URL = os.getenv("DATABASE_URL", "")
PG = URL.startswith("postgres")
DB = os.getenv("DB_PATH", "data/app.db")
ID_TABLES = {"applications", "events", "notifications", "users"}
_pool = None; _lock = threading.Lock()

MIGRATIONS = [
"""create table if not exists users(id integer primary key, login text unique, pw text, role text, name text, company text);
create table if not exists sessions(token text primary key, user_id integer, created real);
create table if not exists applications(id integer primary key, user_id integer, status text, form text, decision text, created text, updated text);
create table if not exists events(id integer primary key, app_id integer, ts text, status text, note text, actor text);
create index if not exists ix_app_user on applications(user_id);
create index if not exists ix_ev_app on events(app_id);""",
"""alter table applications add column version integer default 0;
alter table applications add column idem_key text;
alter table applications add column assigned_to text;
alter table applications add column sla_due text;
create unique index if not exists ux_idem on applications(user_id, idem_key);
create table if not exists notifications(id integer primary key, user_id integer, ts text, title text, body text, app_id integer, read integer default 0);
create index if not exists ix_note_user on notifications(user_id, read);""",
"alter table users add column auth_limit real;",
"""create table if not exists login_attempts(login text, ts real);
create index if not exists ix_login_attempts on login_attempts(login, ts);
create index if not exists ix_app_status on applications(status);"""]

def ddl(sql):
    return re.sub(r"\breal\b", "double precision", sql.replace("id integer primary key", "id bigserial primary key")) if PG else sql
def tr(sql):
    """SQLite-диалект -> PostgreSQL. Возвращает (sql, нужно_ли_читать_returning_id)."""
    sql = sql.replace("?", "%s"); ret = False
    m = re.match(r"\s*insert (or ignore )?into (\w+)", sql, re.I)
    if m:
        if m.group(1): sql = re.sub(r"insert or ignore into", "insert into", sql, flags=re.I) + " on conflict do nothing"
        if m.group(2) in ID_TABLES: sql += " returning id"; ret = True
    return sql, ret

class Conn:
    def __init__(self, raw): self.raw = raw; self.lastrowid = None
    def execute(self, sql, args=()):
        if not PG:
            cur = self.raw.execute(sql, args); self.lastrowid = cur.lastrowid; return cur
        sql, ret = tr(sql); cur = self.raw.execute(sql, args)
        if ret: row = cur.fetchone(); self.lastrowid = row["id"] if row else None
        return cur
    def script(self, sql):
        if not PG: self.raw.executescript(sql); return
        for st in ddl(sql).split(";"):
            if st.strip(): self.raw.execute(st)
    def commit(self): self.raw.commit()
    def close(self):
        if PG: self.raw.rollback(); _pool.putconn(self.raw)
        else: self.raw.close()

def conn():
    global _pool
    if PG:
        with _lock:
            if _pool is None:
                from psycopg.rows import dict_row; from psycopg_pool import ConnectionPool
                _pool = ConnectionPool(URL, min_size=1, max_size=int(os.getenv("DB_POOL", 10)), kwargs={"row_factory": dict_row}, open=False)
                _pool.open(wait=True, timeout=30)
        return Conn(_pool.getconn())
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    c = sqlite3.connect(DB, timeout=10); c.row_factory = sqlite3.Row
    c.execute("pragma journal_mode=wal"); c.execute("pragma foreign_keys=on"); return Conn(c)
def q(sql, args=(), one=False, commit=False):
    c = conn()
    try:
        cur = c.execute(sql, args)
        if commit: c.commit(); return c.lastrowid
        r = cur.fetchall(); return (r[0] if r else None) if one else r
    finally: c.close()

def hash_pw(p, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 120_000).hex()
def check_pw(p, stored):
    if "$" not in stored:   # устаревший формат из ранних демо-баз
        return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", p.encode(), b"alfa-demo-salt", 50000).hex(), stored)
    return hmac.compare_digest(hash_pw(p, stored.split("$")[0]), stored)
def co(inn, form, sector, rev, margin, growth, debt, emp, region="million", loans=1):
    return json.dumps({"inn": inn, "legal_form": form, "sector": sector, "region_tier": region, "employees": emp, "revenue": rev,
                       "profit_margin": margin, "revenue_growth": growth, "existing_debt": debt, "active_loans": loans, "collateral_value": 0})
DEMO = [("client1", "client", "client", "ООО «Ромашка»", co("7700000001", "OOO", "retail", 48e6, .18, .09, 3.8e6, 24), None),
        ("client2", "client", "client", "ИП Кузнецов А. В.", co("7700000005", "IP", "construction", 18e6, .12, .02, 4.5e6, 3, "other", 2), None),
        ("client3", "client", "client", "ООО «Вектор»", co("7700000003", "OOO", "food", 30e6, .09, -.05, 18e6, 12, "million", 4), None),
        ("operator", "operator", "operator", "Андеррайтер Иванова", None, 3_000_000),
        ("senior", "senior", "operator", "Старший андеррайтер Петров", None, 50_000_000)]
def create_user(login, password, role, name, company=None, limit=None):
    q("insert or ignore into users(login,pw,role,name,company,auth_limit) values(?,?,?,?,?,?)", (login, hash_pw(password), role, name, company, limit), commit=True)
def init():
    c = conn()
    try:
        if PG: c.execute("select pg_advisory_lock(727274)")        # несколько реплик стартуют одновременно
        c.execute("create table if not exists schema_migrations(v integer primary key)"); c.commit()
        done = {r["v"] for r in c.execute("select v from schema_migrations").fetchall()}
        for i, sql in enumerate(MIGRATIONS, 1):
            if i not in done: c.script(sql); c.execute("insert into schema_migrations values(?)", (i,)); c.commit()
        if os.getenv("SEED_DEMO", "1") == "1":
            for l, p, r, n, comp, lim in DEMO:
                c.execute("insert or ignore into users(login,pw,role,name,company,auth_limit) values(?,?,?,?,?,?)", (l, hash_pw(p), r, n, comp, lim))
                if lim: c.execute("update users set auth_limit=? where login=?", (lim, l))
        c.execute("delete from sessions where created<?", (__import__("time").time() - 86400,))
        c.execute("delete from login_attempts where ts<?", (__import__("time").time() - 86400,)); c.commit()
        if PG: c.execute("select pg_advisory_unlock(727274)")
    finally: c.close()
