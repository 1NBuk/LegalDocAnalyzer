"""Бенчмарк качества и скорости.

    python benchmarks/run_benchmark.py                       # качество на tests/gold.py
    python benchmarks/run_benchmark.py --files a.pdf b.pdf   # + скорость на реальных файлах
    python benchmarks/run_benchmark.py --files a.pdf --runs 5 --legacy-pdf   # сравнить с pdfplumber

Скорость: p50/p95 по --runs прогонам. Качество: precision/recall/F1 по полям.
Режим LLM здесь не измеряется (нужны ключ и сеть) — используйте API и поле metrics.analyze_s.
"""
import argparse
import re
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_extractor as new  # noqa: E402
from pipeline import analyze_file  # noqa: E402
from tests import legacy  # noqa: E402
from tests.gold import GOLD  # noqa: E402


def norm(s):
    return re.sub(r"[\s«»\"“”„']", "", s.lower())


def core(s):  # «ООО "Альфа"» -> «альфа»; «ИП Иванов И.И.» -> «иванови.и.»
    return norm(re.sub(r"^(ООО|АО|ПАО|ЗАО|ОАО|ИП)\s*", "", s.strip(), flags=re.I))


def match(pred, gold, lenient):
    return core(gold) in core(pred) if lenient else norm(pred) == norm(gold)


def prf(preds, golds, lenient=False):
    tp = sum(any(match(p, g, lenient) for p in preds) for g in golds)
    fp = sum(not any(match(p, g, lenient) for g in golds) for p in preds)
    fn = len(golds) - tp
    return tp, fp, fn


def quality(extract_fn):
    acc = sum(extract_fn(g["text"])["doc_type"] == g["doc_type"] for g in GOLD) / len(GOLD)
    rows = {"доктип (accuracy)": acc}
    for field, lenient in [("parties", False), ("parties", True), ("dates", False), ("amounts", False), ("risk_flags", False)]:
        T = [0, 0, 0]
        for g in GOLD:
            for i, v in enumerate(prf(extract_fn(g["text"])[field], g[field], lenient)):
                T[i] += v
        p = T[0] / (T[0] + T[1]) if T[0] + T[1] else 0.0
        r = T[0] / (T[0] + T[2]) if T[0] + T[2] else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        label = field + (" (мягко)" if lenient else "")
        rows[label] = (p, r, f)
    return rows


def legacy_pdf_text(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n".join(filter(None, (p.extract_text() for p in pdf.pages)))


def timed(fn, runs):
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t)
    ts.sort()
    return st.median(ts), ts[min(len(ts) - 1, int(round(0.95 * (len(ts) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--legacy-pdf", action="store_true", help="сравнить с pdfplumber (если установлен)")
    a = ap.parse_args()

    print(f"## Качество на {len(GOLD)} размеченных документах (только эвристика)\n")
    old, cur = quality(legacy.heuristic_extract), quality(lambda t: new.heuristic_extract(t))
    print("| Поле | Было P / R / F1 | Стало P / R / F1 |\n|---|---|---|")
    for k in cur:
        if isinstance(cur[k], float):
            print(f"| {k} | {old[k]:.0%} | {cur[k]:.0%} |")
        else:
            print(f"| {k} | " + " / ".join(f"{x:.0%}" for x in old[k]) + " | " + " / ".join(f"{x:.0%}" for x in cur[k]) + " |")

    if a.files:
        print(f"\n## Скорость (медиана / p95 за {a.runs} прогонов, секунды)\n")
        head = "| Файл | Стр. | Символов | Извлечение (новое) | Анализ (эвристика) | Всего |"
        sep = "|---|---|---|---|---|---|"
        if a.legacy_pdf:
            head += " Извлечение (pdfplumber, было) | Ускорение |"
            sep += "---|---|"
        print(head + "\n" + sep)
        for f in map(Path, a.files):
            from text_extract import extract
            ex = extract(f)
            t_ext = timed(lambda: extract(f), a.runs)
            t_an = timed(lambda: new.extract_with_llm(ex.text, force_heuristic=True), a.runs)
            t_all = timed(lambda: analyze_file(f, force_heuristic=True), a.runs)
            row = (f"| {f.name[:30]} | {ex.pages} | {len(ex.text):,} | {t_ext[0]:.3f} / {t_ext[1]:.3f} "
                   f"| {t_an[0]:.4f} / {t_an[1]:.4f} | {t_all[0]:.3f} / {t_all[1]:.3f} |")
            if a.legacy_pdf and f.suffix.lower() == ".pdf":
                t_old = timed(lambda: legacy_pdf_text(f), max(1, min(a.runs, 2)))
                row += f" {t_old[0]:.3f} | ×{t_old[0] / t_ext[0]:.0f} |"
            print(row)


if __name__ == "__main__":
    main()
