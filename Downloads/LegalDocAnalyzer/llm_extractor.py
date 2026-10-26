"""Извлечение структурированных данных из текста юридического документа.

Два режима:
* LLM (OpenAI >= 1.0, JSON-режим) — если задан OPENAI_API_KEY;
* эвристика на правилах — если ключа нет или LLM недоступен.

Исправлено относительно первой версии:
* вместо удалённого `openai.ChatCompletion` используется новый клиент `OpenAI`;
* ошибки LLM больше не глотаются молча (`except:`): они логируются и
  попадают в результат (`source`, `llm_error`);
* в LLM уходит не первые 3000 символов, а «выжимка» — начало документа,
  фрагменты вокруг рисковых формулировок и конец (реквизиты, подписи);
* ответ LLM валидируется и приводится к единой схеме;
* эвристика переписана: тип документа определяется по заголовку,
  стороны — по организационно-правовым формам, даты проверяются календарём,
  суммы понимают «1 250 000 (прописью) рублей».
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = float(os.environ.get("OPENAI_TIMEOUT", "30"))
MAX_LLM_CHARS = 12000

DOC_TYPES = [
    "договор", "соглашение", "акт", "счёт", "доверенность",
    "исковое заявление", "претензия", "решение", "приказ", "other",
]
RISK_FLAGS = {
    "penalty": r"штраф|\bпен[ияю]\b|неустойк",
    "unilateral_termination": r"односторонн\w*\s+(?:внесудебн\w*\s+)?(?:порядк\w*\s+)?(?:отказ|расторж|расторг)",
    "auto_renewal": r"автоматическ\w*\s+пролонг|продлева\w*\s+на\s+(?:тот\s+же|такой\s+же)\s+срок",
    "prepayment": r"предоплат|\bаванс",
    "indemnity": r"возмещ\w*\s+(?:\w+\s+)?убытк|возмест\w*\s+(?:\w+\s+)?убытк",
}


# --------------------------------------------------------------------------- #
# Эвристика
# --------------------------------------------------------------------------- #
_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

_TITLE_PATTERNS = [
    ("соглашение", r"^\s*дополнительное\s+соглашение\b|^\s*соглашение\b"),
    ("договор", r"^\s*(?:\w+(?:ый|ий|ой|ая|яя|ое|ее)\s+){0,2}(?:договор|контракт)\b"),
    ("акт", r"^\s*акт\b"),
    ("счёт", r"^\s*сч[её]т(?:[\s-]+фактура|[\s-]+оферта)?\b"),
    ("доверенность", r"^\s*доверенность\b"),
    ("исковое заявление", r"^\s*исков\w+\s+заявлени\w+"),
    ("претензия", r"^\s*(?:досудебная\s+)?претензия\b"),
    ("решение", r"^\s*решение\b"),
    ("приказ", r"^\s*приказ\b"),
]

_QUOTE = r"[«\"“„']"
_UNQUOTE = r"[»\"”“']"
_ORG_CANON = {
    "общество с ограниченной ответственностью": "ООО",
    "акционерное общество": "АО",
    "публичное акционерное общество": "ПАО",
    "закрытое акционерное общество": "ЗАО",
    "открытое акционерное общество": "ОАО",
}
_ORG_ABBR = ["ООО", "ПАО", "ЗАО", "ОАО", "АНО", "НКО", "ФГУП", "МУП", "ГУП", "АО"]
_ORG_RE = re.compile(
    r"\b(?P<form>" + "|".join(_ORG_ABBR + [w.replace(" ", r"\s+") for w in _ORG_CANON]) + r")\s*"
    + _QUOTE + r"(?P<name>[^«»\"“”„'\n]{2,80}?)" + _UNQUOTE,
    re.I,
)
_FIO = r"[А-ЯЁ][а-яё]+"
_IP_RE = re.compile(
    r"\b(?:ИП|Индивидуальный\s+предприниматель)\s+"
    r"(?P<name>" + _FIO + r"\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.|" + _FIO + r"(?:\s+" + _FIO + r"){1,2})"
)
_PERSON_RE = re.compile(
    r"\b[Гг]раждани(?:н|нка)(?:\s+РФ)?\s+"
    r"(?P<name>" + _FIO + r"\s+" + _FIO + r"\s+[А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична|кызы|оглы))"
)

_NUM = r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_AMOUNT_RE = re.compile(
    r"(?<![\d.,])(?P<num>" + _NUM + r")\s*"
    r"(?:\([^)\n]{0,150}\)\s*)?"
    r"(?P<mult>тыс(?:яч\w*|\.)?\s*|млн\.?\s*|миллион\w*\s*)?"
    r"(?:руб(?:лей|ля|ль|\.)?(?!\w)|₽|р\.)",
    re.I,
)
_DATE_NUM_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
_DATE_TXT_RE = re.compile(
    r"[«\"“]?(\d{1,2})[»\"”]?\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\s*(?:г\b|года)?", re.I
)


def detect_doc_type(text: str) -> str:
    """Тип определяется по заголовку (первые строки), а не по любому слову в тексте."""
    header = text[:1500]
    for label, pattern in _TITLE_PATTERNS:
        if re.search(pattern, header, re.I | re.M):
            return label
    low = header.lower()
    if "договор" in low and re.search(r"именуем\w*\s+(?:в\s+дальнейшем|далее)", low):
        return "договор"
    return "other"


def _dedupe(items: List[str], limit: int) -> List[str]:
    seen, out = set(), []
    for it in items:
        key = re.sub(r"\s+", " ", it).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(re.sub(r"\s+", " ", it).strip())
        if len(out) >= limit:
            break
    return out


def extract_parties(text: str, limit: int = 6) -> List[str]:
    found = []  # (позиция, строка)
    for m in _ORG_RE.finditer(text):
        raw = re.sub(r"\s+", " ", m.group("form")).lower()
        form = _ORG_CANON.get(raw, m.group("form").upper())
        found.append((m.start(), f'{form} "{m.group("name").strip()}"'))
    for m in _IP_RE.finditer(text):
        found.append((m.start(), f'ИП {m.group("name").strip()}'))
    for m in _PERSON_RE.finditer(text):
        found.append((m.start(), m.group("name").strip()))
    found.sort(key=lambda x: x[0])
    return _dedupe([s for _, s in found], limit)


def extract_dates(text: str, limit: int = 10) -> List[str]:
    found = []
    for m in _DATE_NUM_RE.finditer(text):
        d, mo, y = map(int, m.groups())
        found.append((m.start(), d, mo, y))
    for m in _DATE_TXT_RE.finditer(text):
        found.append((m.start(), int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))))
    found.sort()
    out = []
    for _, d, mo, y in found:
        try:
            date(y, mo, d)
        except ValueError:
            continue
        if 1990 <= y <= 2100:
            out.append(f"{d:02d}.{mo:02d}.{y}")
    return _dedupe(out, limit)


def extract_amounts(text: str, limit: int = 10) -> List[str]:
    out = []
    for m in _AMOUNT_RE.finditer(text):
        try:
            value = Decimal(re.sub(r"[ \u00a0]", "", m.group("num")).replace(",", "."))
        except InvalidOperation:
            continue
        mult = (m.group("mult") or "").strip().lower()
        if mult.startswith("тыс"):
            value *= 1000
        elif mult.startswith("млн") or mult.startswith("миллион"):
            value *= 1_000_000
        out.append(f"{int(value) if value == value.to_integral() else value} ₽")
    return _dedupe(out, limit)


def detect_risk_flags(text: str) -> List[str]:
    low = text.lower()
    return [name for name, pattern in RISK_FLAGS.items() if re.search(pattern, low)]


def make_summary(text: str, max_len: int = 300) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()][:3]
    s = " ".join(lines)
    return s if len(s) <= max_len else s[: max_len - 1].rstrip() + "…"


def heuristic_extract(text: str) -> Dict[str, Any]:
    return {
        "doc_type": detect_doc_type(text),
        "parties": extract_parties(text),
        "dates": extract_dates(text),
        "amounts": extract_amounts(text),
        "summary": make_summary(text),
        "risk_flags": detect_risk_flags(text),
    }


# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = f"""Ты анализируешь русскоязычные юридические документы.
Верни ТОЛЬКО JSON-объект с полями:
- doc_type: одно из {DOC_TYPES}
- parties: список сторон, например ["ООО \\"Альфа\\"", "ИП Иванов И.И."]
- dates: список дат в формате ДД.ММ.ГГГГ (только значимые: заключение, сроки, платежи)
- amounts: список денежных сумм в формате "100000 ₽" (цифрами, без пробелов)
- summary: краткое резюме на русском, 1-3 предложения
- risk_flags: подмножество {list(RISK_FLAGS)}
Не придумывай данные, которых нет в тексте. Если поле не найдено — пустой список/строка."""


def build_llm_digest(text: str, limit: int = MAX_LLM_CHARS) -> str:
    """Для длинных документов: начало + окна вокруг рисков + конец, чтобы уложиться в `limit`."""
    if len(text) <= limit:
        return text
    head, tail = text[: limit * 6 // 12], text[-limit // 8:]
    budget = limit - len(head) - len(tail)
    windows, used = [], 0
    pattern = re.compile("|".join(RISK_FLAGS.values()), re.I)
    last_end = len(head)
    for m in pattern.finditer(text, len(head), len(text) - len(tail)):
        start, end = max(m.start() - 200, last_end), min(m.end() + 200, len(text) - len(tail))
        if end - start < 50:
            continue
        if used + (end - start) > budget:
            break
        windows.append(text[start:end])
        used += end - start
        last_end = end
    return head + "\n[...]\n" + "\n[...]\n".join(windows) + ("\n[...]\n" if windows else "") + tail


def normalize_result(raw: Any) -> Dict[str, Any]:
    """Приводит ответ LLM к схеме и отбрасывает недопустимые значения."""
    raw = raw if isinstance(raw, dict) else {}

    def as_list(v):
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [str(v).strip()] if v else []

    doc_type = str(raw.get("doc_type", "other")).strip().lower()
    return {
        "doc_type": doc_type if doc_type in DOC_TYPES else "other",
        "parties": _dedupe(as_list(raw.get("parties")), 10),
        "dates": _dedupe(as_list(raw.get("dates")), 10),
        "amounts": _dedupe(as_list(raw.get("amounts")), 10),
        "summary": str(raw.get("summary") or "").strip()[:600],
        "risk_flags": [f for f in as_list(raw.get("risk_flags")) if f in RISK_FLAGS],
    }


_client = None


def _get_client():
    """Ленивая инициализация клиента (ключ читается из окружения в момент вызова)."""
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(timeout=OPENAI_TIMEOUT, max_retries=2)
    return _client


def llm_extract(text: str, client=None) -> Dict[str, Any]:
    client = client or _get_client()
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_llm_digest(text)},
        ],
    )
    return normalize_result(json.loads(resp.choices[0].message.content))


def extract_with_llm(text: str, client=None, force_heuristic: bool = False) -> Dict[str, Any]:
    """Возвращает результат анализа. Поле `source` = 'llm' | 'heuristic'.

    При сбое LLM результат строится эвристикой, а причина сохраняется в `llm_error`.
    """
    if not text or not text.strip():
        return {**heuristic_extract(""), "source": "heuristic", "llm_error": "empty_text"}
    use_llm = not force_heuristic and (client is not None or os.environ.get("OPENAI_API_KEY"))
    if use_llm:
        try:
            return {**llm_extract(text, client), "source": "llm"}
        except Exception as e:
            logger.warning("LLM-анализ не удался, использую эвристику: %s: %s", type(e).__name__, e)
            return {**heuristic_extract(text), "source": "heuristic", "llm_error": type(e).__name__}
    return {**heuristic_extract(text), "source": "heuristic"}
