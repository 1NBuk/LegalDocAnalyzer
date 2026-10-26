"""CLI администратора: python -m app.manage create-user --login ivan --role operator --name "Иван" --limit 3000000 [--inn 7700000001]
Пароль берётся из переменной USER_PASSWORD или запрашивается интерактивно (в аргументах командной строки не передаётся)."""
import argparse, getpass, json, os
from . import db
p = argparse.ArgumentParser(); s = p.add_subparsers(dest="cmd", required=True); c = s.add_parser("create-user")
c.add_argument("--login", required=True); c.add_argument("--name", required=True); c.add_argument("--role", choices=["client", "operator"], required=True)
c.add_argument("--limit", type=float); c.add_argument("--inn")
a = p.parse_args(); db.init()
pw = os.getenv("USER_PASSWORD") or getpass.getpass("Пароль: ")
if len(pw) < 12: raise SystemExit("Пароль слишком короткий (минимум 12 символов)")
comp = json.dumps({"inn": a.inn, "legal_form": "OOO", "sector": "retail", "region_tier": "million", "employees": 1, "revenue": None, "profit_margin": None,
                   "revenue_growth": 0, "existing_debt": 0, "active_loans": 0, "collateral_value": 0}) if a.inn and a.role == "client" else None
db.create_user(a.login, pw, a.role, a.name, comp, a.limit); print("Пользователь создан:", a.login)
