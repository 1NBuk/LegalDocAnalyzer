"""Единый пайплайн «файл -> результат + метрики времени». Используется API и бенчмарком."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from llm_extractor import extract_with_llm
from text_extract import extract


def analyze_file(path: Path, client=None, force_heuristic: bool = False) -> Dict[str, Any]:
    t0 = time.perf_counter()
    ex = extract(path)
    t1 = time.perf_counter()
    result = extract_with_llm(ex.text, client=client, force_heuristic=force_heuristic)
    t2 = time.perf_counter()
    return {
        "result": result,
        "metrics": {
            "pages": ex.pages,
            "chars": len(ex.text),
            "extract_method": ex.method,
            "extract_error": ex.error,
            "source": result.get("source"),
            "extract_s": round(t1 - t0, 3),
            "analyze_s": round(t2 - t1, 3),
            "total_s": round(t2 - t0, 3),
        },
    }
