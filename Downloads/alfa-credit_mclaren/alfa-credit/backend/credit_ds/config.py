"""Единая конфигурация DS-модуля. Всё переопределяется переменными окружения."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARTIFACTS = Path(os.getenv("DS_ARTIFACTS", ROOT.parent / "artifacts"))
SEED = 42

# --- ценообразование (все числа условные, это симуляция) ---
KEY_RATE = float(os.getenv("DS_KEY_RATE", 0.14))      # ключевая ставка ЦБ РФ (14% с 24.07.2026; сверено 21.10.2026)
BASE_RATE = float(os.getenv("DS_BASE_RATE", KEY_RATE))  # цена денег = ключ; далее фондирование, opex, маржа, спред продукта и ожидаемые потери
FUNDING_SPREAD = 0.015
OPEX_SPREAD = 0.02
TARGET_MARGIN = 0.02
NOMINAL_RATE_FEAT = 0.20      # ставка, по которой считаем DSCR в признаках модели

# --- рейтинговая шкала по PD (12 мес.) ---
GRADE_BINS = [0.02, 0.04, 0.07, 0.12]
GRADES = ["A", "B", "C", "D", "E"]

# --- LLM (любой OpenAI-совместимый сервер: vLLM / Ollama / llama.cpp) ---
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")          # пусто = LLM выключен, работают правила
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3-8b")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")                # токен провайдера (OpenAI-совместимый API)
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", 6))
