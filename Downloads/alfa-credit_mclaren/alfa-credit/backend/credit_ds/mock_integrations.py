"""Моки внешних источников: ЕГРЮЛ/ЕГРИП, ФНС «Прозрачный бизнес», БКИ.
Детерминированы по ИНН (хеш) -> результат воспроизводим. Для демо есть фикстуры.
Все данные синтетические."""
import hashlib
from datetime import date

DEMO_FIXTURES = {   # ИНН -> переопределения (сценарии для end-to-end демонстрации)
    "7700000001": {"label": "Надёжный клиент"},
    "7700000002": {"label": "Тонкая кредитная история", "bki_score": None, "age_months": 8},
    "7700000003": {"label": "Перегруженный долгами", "bki_score": 540, "overdue_cnt_12m": 3, "max_overdue_days_24m": 75},
    "7700000004": {"label": "Компания в стадии ликвидации", "egrul_status": "liquidating"},
    "7700000005": {"label": "Пограничный случай (серая зона)", "bki_score": 585, "overdue_cnt_12m": 2, "max_overdue_days_24m": 45},
    "7700000006": {"label": "Налоговая задолженность", "tax_arrears": 1, "tax_arrears_amount": 1_800_000},
}


def _h(inn: str, salt: str) -> float:
    return int(hashlib.sha256(f"{salt}:{inn}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def egrul(inn: str) -> dict:
    fx = DEMO_FIXTURES.get(inn, {})
    age = fx.get("age_months") or int(6 + _h(inn, "age") * 150)
    return {"inn": inn, "status": fx.get("egrul_status", "active"), "age_months": age,
            "okved": "47.11", "director_changes_12m": int(_h(inn, "dir") * 2.2)}


def fns_transparent(inn: str) -> dict:
    fx = DEMO_FIXTURES.get(inn, {})
    arrears = fx.get("tax_arrears", int(_h(inn, "tax") > 0.96))
    return {"tax_arrears": arrears, "tax_arrears_amount": fx.get("tax_arrears_amount", 150_000 * arrears),
            "mass_address": _h(inn, "addr") > 0.995, "court_cases": int(_h(inn, "court") ** 6 * 3)}


def bki(inn: str) -> dict:
    fx = DEMO_FIXTURES.get(inn, {})
    if "bki_score" in fx:
        score = fx["bki_score"]
    elif _h(inn, "thin") < 0.08:
        score = None                                   # нет кредитной истории
    else:
        score = int(480 + _h(inn, "bki") * 330)
    return {"bki_score": score,
            "overdue_cnt_12m": fx.get("overdue_cnt_12m", int(_h(inn, "od") ** 4 * 3)),
            "max_overdue_days_24m": fx.get("max_overdue_days_24m", int(_h(inn, "odd") ** 5 * 60)),
            "inquiries_6m": int(_h(inn, "inq") * 4), "queried_at": date.today().isoformat()}


def enrich(inn: str) -> dict:
    """Единая точка обогащения заявки внешними данными (аналог оркестратора интеграций)."""
    return {"egrul": egrul(inn), "fns": fns_transparent(inn), "bki": bki(inn)}
