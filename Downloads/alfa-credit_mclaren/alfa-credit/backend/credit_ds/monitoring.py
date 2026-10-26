"""Мониторинг дрейфа: PSI по скору и признакам."""
import numpy as np


def psi(expected, actual, bins: int = 10) -> float:
    expected, actual = np.asarray(expected, float), np.asarray(actual, float)
    expected, actual = expected[~np.isnan(expected)], actual[~np.isnan(actual)]
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_status(v: float) -> str:
    return "stable" if v < 0.1 else "watch" if v < 0.25 else "retrain"
