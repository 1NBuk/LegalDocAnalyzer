"""Обучение v2 (воспроизводимое, с проверочными «воротами» перед выпуском).

1. Данные: синтетика (synth.py), хронологическое разбиение: train | early-stop | calibration | out-of-time test.
2. Подбор гиперпараметров: random search, оценка — expanding-window CV по времени (без подглядывания в будущее).
3. Модели: логрегрессия-бейзлайн vs CatBoost (монотонные ограничения) + Platt-калибровка.
4. Оценка на OOT: AUC/Gini/KS/Brier/ECE, бутстреп-доверительные интервалы, срезы (отрасль, форма, регион), стабильность по кварталам, PSI признаков.
5. Ворота выпуска: модель не публикуется как champion, если не проходит пороги (см. GATES). Результат — в model_card.json с версиями, хешами данных и артефактов.
Запуск: python -m credit_ds.train --n 150000 --trials 12 [--force]"""
import argparse, hashlib, json, platform, sys, time
import joblib
import numpy as np
import pandas as pd
import sklearn
from catboost import CatBoostClassifier, Pool
import catboost
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score, roc_curve
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from . import config
from .features import CAT, FEATURES, MONOTONE, NUM
from .monitoring import psi, psi_status
from .scoring import PlattCalibrator
from .synth import generate

GATES = {"gini_not_worse_than_baseline_by": 0.01, "max_ece": 0.02, "max_score_psi": 0.25, "pd_to_default_rate": (0.8, 1.25), "min_segment_auc": 0.70}
BASE_PARAMS = dict(iterations=1200, learning_rate=0.05, depth=5, l2_leaf_reg=6, random_strength=1.0, border_count=128)


def ks_stat(y, p):
    fpr, tpr, _ = roc_curve(y, p); return float(np.max(tpr - fpr))

def ece(y, p, bins=10):
    d = pd.DataFrame({"y": np.asarray(y, float), "p": np.asarray(p, float)}); d["b"] = pd.qcut(d["p"].rank(method="first"), bins, labels=False)
    g = d.groupby("b").agg(y=("y", "mean"), p=("p", "mean"), n=("y", "size"))
    return float((g["n"] * (g["y"] - g["p"]).abs()).sum() / len(d))

def metrics(y, p):
    auc = roc_auc_score(y, p)
    return {"auc": round(auc, 4), "gini": round(2 * auc - 1, 4), "ks": round(ks_stat(y, p), 4), "brier": round(brier_score_loss(y, p), 5),
            "ece": round(ece(y, p), 4), "mean_pd": round(float(np.mean(p)), 4), "default_rate": round(float(np.mean(y)), 4)}

def boot_ci(y, p, n=200, seed=0):
    rng = np.random.default_rng(seed); y, p = np.asarray(y), np.asarray(p); out = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() != y[i].max(): out.append(roc_auc_score(y[i], p[i]))
    lo, hi = np.percentile(out, [2.5, 97.5]); return {"auc_ci95": [round(float(lo), 4), round(float(hi), 4)], "gini_ci95": [round(float(2 * lo - 1), 4), round(float(2 * hi - 1), 4)]}

def _pool(df):
    x = df[FEATURES].copy()
    for c in CAT: x[c] = x[c].astype(str)
    return Pool(x, label=df["default_12m"] if "default_12m" in df else None, cat_features=CAT)

def _cb(params, seed, **kw):
    return CatBoostClassifier(**params, eval_metric="AUC", od_type="Iter", od_wait=50, random_seed=seed, monotone_constraints=MONOTONE, verbose=False, thread_count=-1, **kw)

def cv_auc(params, data, folds, seed):
    """Expanding window: чанки 0..k-1 -> обучение, чанк k -> валидация (k = 2..folds+1)."""
    ch = np.array_split(np.arange(len(data)), folds + 2); aucs = []
    for k in range(2, folds + 2):
        a, b = data.iloc[np.concatenate(ch[:k])], data.iloc[ch[k]]
        m = _cb(params, seed); m.fit(_pool(a), eval_set=_pool(b), use_best_model=True)
        aucs.append(roc_auc_score(b["default_12m"], m.predict_proba(_pool(b))[:, 1]))
    return float(np.mean(aucs)), float(np.std(aucs))

def search(data, trials, folds, seed, verbose):
    rng = np.random.default_rng(seed); space = dict(depth=[4, 5, 6, 7], learning_rate=[0.03, 0.05, 0.08], l2_leaf_reg=[3, 6, 10, 20], random_strength=[0.5, 1.0, 2.0], border_count=[64, 128])
    cand = [dict(BASE_PARAMS)] + [{**BASE_PARAMS, **{k: v[int(rng.integers(len(v)))] for k, v in space.items()}} for _ in range(trials)]; res = []
    for i, p in enumerate(cand):
        m, s = cv_auc(p, data, folds, seed); res.append({"trial": i, "cv_auc": round(m, 4), "cv_std": round(s, 4), **{k: p[k] for k in space}})
        if verbose: print(f"trial {i}: cv_auc={m:.4f}±{s:.4f} {p}", flush=True)
    best = max(range(len(cand)), key=lambda i: res[i]["cv_auc"]); return cand[best], sorted(res, key=lambda r: -r["cv_auc"])

def sha(path): return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]

def train(n: int = 40000, seed: int = config.SEED, out=config.ARTIFACTS, verbose=True, trials: int = 0, cv_folds: int = 3, force: bool = False):
    out.mkdir(parents=True, exist_ok=True); t0 = time.time()
    df = generate(n, seed); cut = [int(n * x) for x in (0.55, 0.70, 0.80)]
    tr, es, cal, te = df.iloc[:cut[0]], df.iloc[cut[0]:cut[1]], df.iloc[cut[1]:cut[2]], df.iloc[cut[2]:]
    data_hash = hashlib.sha256(pd.util.hash_pandas_object(df.drop(columns=["pd_true"]), index=False).values.tobytes()).hexdigest()[:16]

    pre = ColumnTransformer([("num", Pipeline([("imp", SimpleImputer(strategy="median", add_indicator=True)), ("sc", StandardScaler())]), NUM), ("cat", OneHotEncoder(handle_unknown="ignore"), CAT)])
    lr = Pipeline([("pre", pre), ("lr", LogisticRegression(C=0.5, max_iter=2000))]); fit_set = pd.concat([tr, es])
    lr.fit(fit_set[FEATURES], fit_set["default_12m"]); p_lr = lr.predict_proba(te[FEATURES])[:, 1]

    params, trials_log = (search(pd.concat([tr, es]), trials, cv_folds, seed, verbose) if trials else (dict(BASE_PARAMS), []))
    cb = _cb(params, seed); cb.fit(_pool(tr), eval_set=_pool(es), use_best_model=True)
    raw_cal = cb.predict_proba(_pool(cal))[:, 1]; plat = PlattCalibrator().fit(raw_cal, cal["default_12m"])
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.99).fit(raw_cal, cal["default_12m"])
    p_raw = cb.predict_proba(_pool(te))[:, 1]; p_te = plat.predict(p_raw); y = te["default_12m"]

    m = {"logreg_baseline": metrics(y, p_lr), "catboost_raw": metrics(y, p_raw), "catboost_calibrated": metrics(y, p_te), "catboost_isotonic_for_comparison": metrics(y, iso.predict(p_raw))}
    te = te.assign(pd_hat=p_te, q=te["app_date"].dt.to_period("Q").astype(str))
    seg = []
    for col in ("sector", "legal_form", "region_tier"):
        for v, g in te.groupby(col):
            if len(g) >= 300 and g["default_12m"].nunique() == 2:
                seg.append({"segment": col, "value": v, "n": len(g), "auc": round(roc_auc_score(g["default_12m"], g["pd_hat"]), 4), "mean_pd": round(g["pd_hat"].mean(), 4), "default_rate": round(g["default_12m"].mean(), 4)})
    quarters = [{"quarter": q, "n": len(g), "auc": round(roc_auc_score(g["default_12m"], g["pd_hat"]), 4)} for q, g in te.groupby("q") if len(g) >= 300 and g["default_12m"].nunique() == 2]
    te["decile"] = pd.qcut(te["pd_hat"].rank(method="first"), 10, labels=False)
    calib = te.groupby("decile").agg(pd_pred=("pd_hat", "mean"), default_rate=("default_12m", "mean"), n=("default_12m", "size")).round(4).reset_index().to_dict("records")
    psi_feat = {}
    for f in NUM:
        try: psi_feat[f] = round(psi(tr[f], te[f]), 4)
        except Exception: pass
    drift = psi(plat.predict(cb.predict_proba(_pool(tr))[:, 1]), p_te)

    cm = m["catboost_calibrated"]; lo, hi = GATES["pd_to_default_rate"]
    gates = {"gini_vs_baseline": m["catboost_calibrated"]["gini"] >= m["logreg_baseline"]["gini"] - GATES["gini_not_worse_than_baseline_by"],
             "ece": cm["ece"] <= GATES["max_ece"], "score_psi": drift <= GATES["max_score_psi"],
             "pd_vs_default_rate": lo <= cm["mean_pd"] / max(cm["default_rate"], 1e-9) <= hi, "segment_auc": all(s["auc"] >= GATES["min_segment_auc"] for s in seg)}
    passed = all(gates.values())
    fi = sorted(zip(FEATURES, cb.get_feature_importance(_pool(tr))), key=lambda t: -t[1])[:12]
    target = out if (passed or force) else out / "candidate"; target.mkdir(parents=True, exist_ok=True)
    cb.save_model(str(target / "pd_model.cbm")); joblib.dump(plat, target / "calibrator.joblib")
    card = {"model": "CatBoostClassifier (монотонные ограничения) + Platt-калибровка", "target": "default_12m (синтетика)", "n_train": len(tr), "n_calibration": len(cal), "n_test_oot": len(te),
            "metrics_oot": m, "bootstrap": boot_ci(y, p_te), "calibration_deciles": calib, "segments": seg, "quarterly_auc": quarters, "psi_features_train_vs_oot": psi_feat,
            "top_features": [(f, round(float(v), 2)) for f, v in fi], "monotone_constraints": MONOTONE, "grade_bins": dict(zip(config.GRADES, config.GRADE_BINS)),
            "psi_train_vs_test": {"value": round(drift, 4), "status": psi_status(drift)}, "best_iteration": int(cb.get_best_iteration()),
            "hyperparameters": params, "search_trials": trials_log[:10], "cv": {"scheme": f"expanding window, {cv_folds} folds по времени", "note": "ранняя остановка на валидационном фолде слегка завышает CV-оценку"},
            "gates": {"thresholds": {k: v for k, v in GATES.items()}, "results": {k: bool(v) for k, v in gates.items()}, "passed": bool(passed), "forced": bool(force and not passed)},
            "registry": {"version": time.strftime("%Y%m%d-%H%M") + "-" + data_hash[:6], "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"), "seed": seed, "n": n, "data_sha256": data_hash,
                         "libs": {"python": platform.python_version(), "catboost": catboost.__version__, "sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},
                         "artifacts_sha256": {"pd_model.cbm": sha(target / "pd_model.cbm"), "calibrator.joblib": sha(target / "calibrator.joblib")}, "train_seconds": round(time.time() - t0)},
            "disclaimer": "Метрики получены на синтетических данных (логистический генератор) и не переносятся на реальный портфель. Близость CatBoost и логрегрессии объясняется самим генератором."}
    (target / "model_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    df.sample(min(2000, n), random_state=seed).drop(columns=["pd_true"]).to_csv(target / "sample_applications.csv", index=False)
    if verbose:
        print(json.dumps({"metrics_oot": m, "bootstrap": card["bootstrap"], "gates": card["gates"]}, indent=1, ensure_ascii=False)); print("артефакты ->", target)
    return card


if __name__ == "__main__":
    a = argparse.ArgumentParser(); a.add_argument("--n", type=int, default=150000); a.add_argument("--trials", type=int, default=12); a.add_argument("--folds", type=int, default=3)
    a.add_argument("--seed", type=int, default=config.SEED); a.add_argument("--force", action="store_true", help="опубликовать, даже если ворота не пройдены")
    x = a.parse_args(); c = train(x.n, x.seed, trials=x.trials, cv_folds=x.folds, force=x.force); sys.exit(0 if c["gates"]["passed"] or x.force else 1)
