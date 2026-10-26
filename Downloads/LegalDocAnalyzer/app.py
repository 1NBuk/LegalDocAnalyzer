"""FastAPI-сервис анализа юридических документов."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from pipeline import analyze_file

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("legaldoc")

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BASE_DIR / "data"))
RESULTS_DIR = Path(os.environ.get("RESULTS_DIR", BASE_DIR / "results"))
MAX_UPLOAD_BYTES = int(float(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024)
KEEP_UPLOADS = os.environ.get("KEEP_UPLOADS", "0") == "1"
ALLOWED_EXT = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="LegalDocAnalyzer", version="2.0")


@app.get("/health")
def health():
    return {"status": "ok"}


async def _save_upload(file: UploadFile, dest: Path) -> int:
    """Потоково сохраняет файл, прерываясь при превышении лимита."""
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"Файл больше {MAX_UPLOAD_BYTES // 1024 // 1024} МБ")
            out.write(chunk)
    return size


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    original_name = Path(file.filename or "upload").name  # защита от ../ в имени
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, f"Неподдерживаемый формат {ext or '(нет расширения)'}. Допустимо: {sorted(ALLOWED_EXT)}")

    uid = str(uuid.uuid4())
    path = UPLOAD_DIR / f"{uid}{ext}"  # исходное имя на диск не попадает
    t0 = time.perf_counter()
    size = await _save_upload(file, path)
    try:
        # тяжёлая синхронная работа — в пуле потоков, event loop не блокируется
        out = await run_in_threadpool(analyze_file, path)
    finally:
        if not KEEP_UPLOADS:
            path.unlink(missing_ok=True)

    metrics = {**out["metrics"], "upload_bytes": size, "request_s": round(time.perf_counter() - t0, 3)}
    payload = {"id": uid, "filename": original_name, "result": out["result"], "metrics": metrics}
    (RESULTS_DIR / f"{uid}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("analyzed %s id=%s %s", original_name, uid, metrics)

    if metrics["extract_error"] and not out["result"]["summary"]:
        raise HTTPException(422, f"Не удалось извлечь текст: {metrics['extract_error']}")
    return payload


@app.get("/results/{uid}")
def get_result(uid: str):
    if not UUID_RE.match(uid):  # не даём читать произвольные пути
        raise HTTPException(400, "Некорректный id")
    p = RESULTS_DIR / f"{uid}.json"
    if not p.exists():
        raise HTTPException(404, "Результат не найден")
    return json.loads(p.read_text(encoding="utf-8"))
