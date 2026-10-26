"""Онлайн-скоринг: PD (вероятность дефолта за 12 мес.), рейтинг, причины решения."""
import json
import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool

from . import config
from .features import CAT, FEATURES

REASONS = {  # feature -> (текст-риск, текст-плюс)
    "debt_to_revenue": ("Высокая долговая нагрузка: действующие обязательства {v:.0%} годовой выручки", "Низкая долговая нагрузка"),
    "overdue_cnt_12m": ("Просрочки по обязательствам за 12 мес.: {v:.0f}", "Нет просрочек за последний год"),
    "max_overdue_days_24m": ("Максимальная просрочка за 24 мес.: {v:.0f} дн.", "Платёжная дисциплина без длительных просрочек"),
    "bki_score": ("Невысокий кредитный скор БКИ ({v:.0f})", "Хорошая кредитная история (скор {v:.0f})"),
    "profit_margin": ("Низкая операционная маржа ({v:.0%})", "Устойчивая операционная маржа ({v:.0%})"),
    "revenue_growth": ("Снижение выручки ({v:+.0%} за год)", "Рост выручки ({v:+.0%} за год)"),
    "dscr": ("Недостаточное покрытие платежей денежным потоком (DSCR {v:.2f})", "Платежи комфортно покрываются денежным потоком (DSCR {v:.2f})"),
    "loan_to_revenue": ("Запрошенная сумма велика относительно выручки ({v:.0%})", "Сумма соразмерна обороту бизнеса ({v:.0%})"),
    "age_months": ("Небольшой срок работы бизнеса ({v:.0f} мес.)", "Бизнес работает давно ({v:.0f} мес.)"),
    "tax_arrears": ("Есть задолженность по налогам", "Нет налоговой задолженности"),
    "court_cases": ("Судебные дела с участием компании: {v:.0f}", "Нет судебных дел"),
    "turnover_cv": ("Нестабильные обороты по счёту", "Стабильные обороты по счёту"),
    "neg_days_90": ("Дни с отрицательным остатком на счёте за 90 дн.: {v:.0f}", "Нет кассовых разрывов"),
    "collateral_ratio": ("Недостаточное обеспечение", "Обеспечение покрывает кредит ({v:.0%})"),
    "inquiries_6m": ("Много запросов в БКИ за полгода: {v:.0f}", "Мало запросов в БКИ"),
}


class PlattCalibrator:
    """Гладкая монотонная калибровка: логрегрессия по logit(сырой вероятности).
    В отличие от изотоники не даёт «ступенек» -> непрерывное ценообразование."""
    def __init__(self):
        self.a = self.b = None   # коэффициенты хранятся числами: артефакт не зависит от версии scikit-learn
        self.lr = None

    @staticmethod
    def _x(raw):
        r = np.clip(np.asarray(raw, float), 1e-6, 1 - 1e-6)
        return np.log(r / (1 - r)).reshape(-1, 1)

    def fit(self, raw, y):
        from sklearn.linear_model import LogisticRegression
        lr = LogisticRegression(C=1e6, max_iter=500).fit(self._x(raw), y)
        self.a, self.b = float(np.ravel(lr.coef_)[0]), float(np.ravel(lr.intercept_)[0])
        return self

    def predict(self, raw):
        a = getattr(self, "a", None)
        b = getattr(self, "b", None)
        if a is None:   # старый артефакт: в pickle только lr
            a = float(np.ravel(self.lr.coef_)[0])
            b = float(np.ravel(self.lr.intercept_)[0])
        return 1.0 / (1.0 + np.exp(-(a * self._x(raw).ravel() + b)))


def grade_of(pd_value: float) -> str:
    for g, thr in zip(config.GRADES, config.GRADE_BINS):
        if pd_value < thr:
            return g
    return config.GRADES[-1]


class Scorer:
    def __init__(self, path=config.ARTIFACTS):
        self.model = CatBoostClassifier()
        self.model.load_model(str(path / "pd_model.cbm"))
        self.calibrator = joblib.load(path / "calibrator.joblib")
        self.card = json.loads((path / "model_card.json").read_text(encoding="utf-8"))

    def _pool(self, df: pd.DataFrame) -> Pool:
        x = df[FEATURES].copy()
        for c in CAT:
            x[c] = x[c].astype(str)
        return Pool(x, cat_features=CAT)

    def predict_pd(self, df: pd.DataFrame) -> np.ndarray:
        raw = self.model.predict_proba(self._pool(df))[:, 1]
        return np.clip(self.calibrator.predict(raw), 0.003, 0.95)

    def explain(self, row: pd.DataFrame, k: int = 3):
        """Топ факторов риска и сильных сторон по SHAP (вклад в log-odds)."""
        shap = self.model.get_feature_importance(self._pool(row), type="ShapValues")[0][:-1]
        order = np.argsort(shap)
        out = {"risk": [], "strengths": []}
        r = row.iloc[0]
        for idx in order[::-1]:
            f = FEATURES[idx]
            if shap[idx] > 0.05 and f in REASONS and len(out["risk"]) < k:
                v = r[f]
                out["risk"].append({"feature": f, "impact": round(float(shap[idx]), 3),
                                    "text": REASONS[f][0].format(v=v) if pd.notna(v) else "Нет данных БКИ (тонкая кредитная история)"})
        for idx in order:
            f = FEATURES[idx]
            if shap[idx] < -0.05 and f in REASONS and len(out["strengths"]) < k:
                out["strengths"].append({"feature": f, "impact": round(float(shap[idx]), 3),
                                         "text": REASONS[f][1].format(v=r[f]) if pd.notna(r[f]) else REASONS[f][1]})
        return out
