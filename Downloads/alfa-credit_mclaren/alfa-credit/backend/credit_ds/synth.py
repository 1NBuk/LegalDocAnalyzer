"""Генератор синтетических заявок малого бизнеса.

Идея: скрытое «качество бизнеса» q порождает коррелированные наблюдаемые признаки
(маржа, просрочки, БКИ-скор...), а вероятность дефолта строится из признаков + ненаблюдаемый шум.
Так модель решает реалистичную задачу, а не восстанавливает формулу 1:1.
Метрики на таких данных НЕ переносятся на реальный портфель — это стенд для проверки пайплайна.
"""
import numpy as np
import pandas as pd

from .config import SEED
from .features import add_derived

SECTOR_EFFECT = {"retail": 0.10, "food": 0.15, "services": 0.0, "manufacturing": -0.05,
                 "construction": 0.30, "it": -0.15, "transport": 0.10, "agro": 0.20}
SECTOR_P = [0.22, 0.12, 0.20, 0.10, 0.10, 0.08, 0.10, 0.08]
PURPOSES = ["working_capital", "equipment", "expansion", "refinance", "real_estate"]
PURPOSE_P = [0.45, 0.20, 0.15, 0.10, 0.10]
PURPOSE_TERMS = {"working_capital": [12, 24, 36], "equipment": [24, 36, 60], "expansion": [24, 36, 60],
                 "refinance": [12, 24, 36], "real_estate": [60, 84, 120]}


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def _logit(df: pd.DataFrame, rng, noise=0.5):
    bki = df["bki_score"].fillna(640)
    z = (-3.1
         - 6.0 * (df["profit_margin"] - 0.15)
         - 1.5 * df["revenue_growth"].clip(-0.5, 0.5)
         + 1.8 * df["debt_to_revenue"]
         + 0.6 * df["overdue_cnt_12m"].clip(0, 5)
         + 0.012 * df["max_overdue_days_24m"].clip(0, 180)
         - 0.008 * (bki - 640) + 0.3 * df["bki_score"].isna()
         + 0.7 * df["tax_arrears"] + 0.35 * df["court_cases"].clip(0, 4)
         + 1.2 * (df["turnover_cv"] - 0.25) + 0.05 * df["neg_days_90"].clip(0, 40)
         - 0.6 * np.log(df["age_months"].clip(3, 120) / 24)
         + 0.9 * (df["loan_to_revenue"].clip(0, 1.5) - 0.2)
         - 0.45 * df["dscr"].clip(0, 4)
         - 0.4 * df["collateral_ratio"].clip(0, 1.2)
         + df["sector"].map(SECTOR_EFFECT))
    return z + rng.normal(0, noise, len(df))


def generate(n: int = 30000, seed: int = SEED, target_default_rate: float = 0.07) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    q = rng.normal(size=n)
    legal = rng.choice(["IP", "OOO"], n, p=[0.45, 0.55])
    sector = rng.choice(list(SECTOR_EFFECT), n, p=SECTOR_P)
    region = rng.choice(["msk_spb", "million", "other"], n, p=[0.25, 0.30, 0.45])
    age = np.clip(rng.gamma(1.8, 32, n) + 1, 2, 360).round()
    revenue = np.exp(rng.normal(15.6 + 0.5 * (legal == "OOO") + 0.1 * q, 0.9, n))
    employees = np.where(legal == "IP", rng.poisson(1.5, n), rng.poisson(np.maximum(revenue / 1.2e6, 1)))
    employees = np.minimum(employees, 100)
    margin = np.clip(0.16 + 0.05 * q + rng.normal(0, 0.05, n), -0.2, 0.4)
    growth = np.clip(0.06 + 0.08 * q + rng.normal(0, 0.2, n), -0.6, 1.0)
    turnover = revenue / 12 * np.exp(rng.normal(0, 0.1, n))
    cv = np.clip(0.28 - 0.06 * q + np.abs(rng.normal(0, 0.1, n)), 0.03, 1.5)
    neg_days = np.minimum(rng.poisson(np.exp(0.9 - 0.7 * q)), 90)

    has_debt = rng.random(n) < 0.65
    d2r = np.where(has_debt, np.clip(rng.beta(1.3, 4, n) * np.exp(-0.15 * q), 0, 1.5), 0.0)
    active = np.where(has_debt, 1 + rng.poisson(0.8, n), 0)
    overdue = np.where(has_debt | (rng.random(n) < 0.1), rng.poisson(np.exp(-1.6 - 0.8 * q)), 0)
    max_od = np.where(overdue > 0, np.minimum(rng.gamma(1.5, 12, n) * (1 + 0.3 * overdue), 360), 0).round()
    thin = rng.random(n) < (0.08 + 0.25 * (age < 12))
    bki = np.clip(650 + 55 * q - 22 * overdue - 0.25 * max_od + rng.normal(0, 25, n), 300, 850).round()
    bki = np.where(thin, np.nan, bki)
    inquiries = rng.poisson(np.exp(0.2 - 0.2 * q))
    tax = (rng.random(n) < _sigmoid(-3.2 - 0.7 * q)).astype(int)
    court = rng.poisson(np.exp(-2.4 - 0.4 * q))

    purpose = rng.choice(PURPOSES, n, p=PURPOSE_P)
    term = np.array([rng.choice(PURPOSE_TERMS[p]) for p in purpose])
    frac = (rng.beta(1.8, 10, n) + 0.02) * np.where(purpose == "real_estate", 1.6, 1.0)
    amount = np.clip((revenue * frac / 10_000).round() * 10_000, 100_000, 150_000_000)
    own_collateral = np.where(rng.random(n) < 0.35, rng.uniform(0.3, 1.2, n), 0.0)
    collateral = np.where(np.isin(purpose, ["equipment", "real_estate"]), 1.0, own_collateral)

    df = pd.DataFrame(dict(
        app_date=pd.Timestamp("2023-01-01") + pd.to_timedelta(rng.integers(0, 730, n), unit="D"),
        legal_form=legal, sector=sector, region_tier=region, age_months=age, employees=employees,
        revenue=revenue.round(), revenue_growth=growth.round(3), profit_margin=margin.round(3),
        avg_monthly_turnover=turnover.round(), turnover_cv=cv.round(3), neg_days_90=neg_days,
        debt_to_revenue=d2r.round(3), active_loans=active, overdue_cnt_12m=overdue,
        max_overdue_days_24m=max_od, bki_score=bki, inquiries_6m=inquiries, tax_arrears=tax,
        court_cases=court, purpose=purpose, term_months=term, requested_amount=amount,
        collateral_ratio=collateral.round(2)))
    df = add_derived(df)

    z = _logit(df, rng)
    lo, hi = -6.0, 6.0                       # подбираем сдвиг, чтобы средний уровень дефолта был целевым
    for _ in range(50):
        c = (lo + hi) / 2
        lo, hi = (c, hi) if _sigmoid(z + c).mean() < target_default_rate else (lo, c)
    df["pd_true"] = _sigmoid(z + c)
    df["default_12m"] = (rng.random(n) < df["pd_true"]).astype(int)
    return df.sort_values("app_date").reset_index(drop=True)


if __name__ == "__main__":
    d = generate(5000)
    print(d.shape, d["default_12m"].mean().round(4))
    print(d.describe().T[["mean", "min", "max"]].round(2))
