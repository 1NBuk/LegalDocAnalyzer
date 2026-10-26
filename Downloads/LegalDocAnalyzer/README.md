# LegalDocAnalyzer

Автоматический анализ русскоязычных юридических документов (PDF / DOCX / сканы и фото):
тип документа, стороны, даты, суммы, краткое резюме, риск-флаги.

* **Режим LLM** (OpenAI, JSON-режим) — если задан `OPENAI_API_KEY`.
* **Режим эвристики** — без ключа или при сбое LLM (причина фиксируется в `llm_error`).
* **OCR** для сканов подключается автоматически (tesseract, `rus+eng`).

## Быстрый старт

```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                  # впишите OPENAI_API_KEY (необязательно)
export $(grep -v '^#' .env | xargs)                   # или задайте переменные вручную
uvicorn app:app --reload
```

Для OCR нужен системный tesseract с русским языком: `apt install tesseract-ocr tesseract-ocr-rus`
(в Docker-образе уже установлен).

Docker: `docker compose up --build` (читает `.env`).

## API

| Метод | Путь | Описание |
|---|---|---|
| POST | `/analyze` | multipart-поле `file` (pdf, docx, png, jpg, tiff, bmp, webp) |
| GET | `/results/{id}` | сохранённый результат |
| GET | `/health` | проверка живости |

```bash
curl -F "file=@contract.pdf" http://localhost:8000/analyze
```

Ответ:

```json
{
  "id": "…uuid…",
  "filename": "contract.pdf",
  "result": {
    "doc_type": "договор",
    "parties": ["ООО \"Альфа\"", "ИП Иванов И.И."],
    "dates": ["01.01.2023"],
    "amounts": ["100000 ₽"],
    "summary": "…",
    "risk_flags": ["penalty"],
    "source": "heuristic"
  },
  "metrics": {"pages": 1, "chars": 88, "extract_method": "pdf-text",
              "extract_s": 0.003, "analyze_s": 0.0, "total_s": 0.003, "request_s": 0.01}
}
```

Коды ошибок: `413` — файл больше лимита, `415` — формат не поддерживается,
`422` — не удалось извлечь текст.

`doc_type`: договор, соглашение, акт, счёт, доверенность, исковое заявление, претензия, решение, приказ, other.
`risk_flags`: `penalty`, `unilateral_termination`, `auto_renewal`, `prepayment`, `indemnity`.

## Настройки (переменные окружения)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `OPENAI_API_KEY` | — | включает LLM-режим |
| `OPENAI_MODEL` | `gpt-4o-mini` | модель |
| `OPENAI_TIMEOUT` | `30` | таймаут запроса, с (2 автоповтора) |
| `MAX_UPLOAD_MB` | `25` | лимит загрузки |
| `OCR_LANG` | `rus+eng` | языки tesseract (недоступные отбрасываются) |
| `OCR_MAX_PAGES` | `30` | максимум страниц для OCR |
| `KEEP_UPLOADS` | `0` | `1` — не удалять загруженные документы после анализа |

По умолчанию исходный документ **удаляется** после обработки (конфиденциальность), сохраняется только JSON-результат.

## Тесты и бенчмарк

```bash
pytest -q
python benchmarks/run_benchmark.py --files path/to/doc.pdf --runs 5 --legacy-pdf
```

Результаты последнего прогона — `benchmarks/RESULTS.md`.
Режим LLM в бенчмарке не измеряется: задержку смотрите в `metrics.analyze_s` ответа API.

## Структура

```
app.py            FastAPI: валидация, лимиты, пул потоков, сохранение результатов
pipeline.py       файл -> результат + метрики времени
text_extract.py   PDF (PyMuPDF) / DOCX / OCR, нормализация текста
llm_extractor.py  LLM-анализ + эвристика, выжимка для длинных текстов
ml_models.py      опциональный TF-IDF + RandomForest (не используется в API)
tests/            pytest, размеченная выборка (gold.py), отложенная (holdout.py)
benchmarks/       скрипт бенчмарка и его последний результат
```

## Известные ограничения

* Формат `.doc` (старый Word) не поддерживается — конвертируйте в `.docx`.
* Эвристика — правила, а не понимание текста: нестандартные формулировки сторон она пропустит.
  Для production используйте LLM-режим и оценивайте его на своей размеченной выборке.
* Для документов > ~12 000 символов в LLM уходит выжимка (начало, фрагменты вокруг рисковых слов, конец).
