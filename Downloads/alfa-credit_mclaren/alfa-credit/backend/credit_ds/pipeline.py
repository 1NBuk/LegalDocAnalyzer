"""Оркестрация: заявка -> обогащение (моки ЕГРЮЛ/ФНС/БКИ) -> признаки -> PD -> решение -> объяснение."""
import numpy as np

from . import config
from .decision import decide
from .llm import explain_decision
from .mock_integrations import enrich
from .scoring import Scorer

_scorer = None


def get_scorer() -> Scorer:
    global _scorer
    if _scorer is None:
        if not (config.ARTIFACTS / "pd_model.cbm").exists():
            from .train import train
            train(verbose=False)
        _scorer = Scorer()
    return _scorer


def apply_enrichment(app: dict, e: dict) -> dict:
    """Данные из внешних источников имеют приоритет над заявленными клиентом."""
    a = dict(app)
    a["age_months"] = e["egrul"]["age_months"]
    for k in ("tax_arrears", "tax_arrears_amount", "court_cases"):
        a[k] = e["fns"][k]
    for k in ("bki_score", "overdue_cnt_12m", "max_overdue_days_24m", "inquiries_6m"):
        a[k] = e["bki"][k] if e["bki"][k] is not None else np.nan
    return a


def assess(app: dict, with_text: bool = True) -> dict:
    e = enrich(app["inn"])
    full = apply_enrichment(app, e)
    result = decide(full, get_scorer(), e)
    result["enrichment"] = e
    if with_text:
        result["explanation"] = explain_decision(result)
    return result
