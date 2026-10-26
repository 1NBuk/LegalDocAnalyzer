"""LLM-слой. Принцип: LLM НИКОГДА не принимает кредитное решение и не считает числа.
Он делает две вещи: (1) превращает свободный текст клиента в структурированную потребность,
(2) переписывает уже принятое решение понятным языком. Оба шага имеют детерминированный fallback,
поэтому система работает без LLM (и на обычном ПК жюри без GPU)."""
import hashlib
import json
import re
from functools import lru_cache
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError

from . import config

Purpose = Literal["working_capital", "equipment", "expansion", "refinance", "real_estate"]


class Intent(BaseModel):
    purpose: Optional[Purpose] = None
    amount: Optional[float] = Field(None, ge=0, le=1_000_000_000)
    term_months: Optional[int] = Field(None, ge=1, le=240)
    has_collateral: Optional[bool] = None
    urgent: Optional[bool] = None


# ---------------- детерминированный разбор (всегда работает) ----------------
PURPOSE_RULES = [
    ("refinance", r"рефинанс|перекредит|закрыть (?:другие )?кредит|объедин\w+ кредит"),
    ("real_estate", r"помещени|недвижим|офис|склад|здани|участок|ангар"),
    ("equipment", r"оборудован|станок|станк|печь|печи|техник|автомобил|грузовик|фургон|линию|линия|холодильник"),
    ("expansion", r"расшир|открыть|открыт\w+ (?:точк|филиал|магазин)|филиал|новое направлени|новую точку|франшиз"),
    ("working_capital", r"оборот|закуп|товар|сырь|зарплат|кассов\w+ разрыв|пополн|сезон|предоплат"),
]
_NUM = r"(\d{1,3}(?:[ \u00a0]\d{3})+|\d+(?:[.,]\d+)?)"


def parse_amount(text: str) -> Optional[float]:
    t = text.lower()
    m = re.search(_NUM + r"\s*(млрд|миллиард\w*|млн|миллион\w*|тыс\w*|т\.?\s?р|к)\b", t)
    if m:
        v = float(re.sub(r"[ \u00a0]", "", m.group(1)).replace(",", "."))
        u = m.group(2)
        mult = 1e9 if u.startswith(("млрд", "миллиард")) else 1e6 if u.startswith(("млн", "миллион")) else 1e3
        return v * mult
    m = re.search(r"(\d{1,3}(?:[ \u00a0]\d{3})+|\d{5,})\s*(?:₽|руб|р\b)?", t)
    return float(re.sub(r"[ \u00a0]", "", m.group(1))) if m else None


def parse_term(text: str) -> Optional[int]:
    t = text.lower()
    m = re.search(r"(\d+)\s*(?:-|–)?\s*(лет|года|год|г\.)", t)
    if m:
        return int(m.group(1)) * 12
    m = re.search(r"(\d+)\s*(?:мес|месяц)", t)
    if m:
        return int(m.group(1))
    return {"полгода": 6, "год": 12}.get(next((w for w in ("полгода", "на год") if w in t), ""), None) or (12 if "на год" in t else None)


def rules_intent(text: str) -> Intent:
    t = text.lower()
    purpose = next((p for p, rx in PURPOSE_RULES if re.search(rx, t)), None)
    collateral = True if re.search(r"под залог|есть залог|в залог", t) else False if re.search(r"без залога", t) else None
    return Intent(purpose=purpose, amount=parse_amount(t), term_months=parse_term(t), has_collateral=collateral,
                  urgent=True if re.search(r"срочн|быстро|сегодня|завтра", t) else None)


# ---------------- LLM (опционально) ----------------
def _chat(messages, max_tokens=300, json_mode=False) -> Optional[str]:
    if not config.LLM_BASE_URL:
        return None
    body = {"model": config.LLM_MODEL, "messages": messages, "temperature": 0, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False}}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions", json=body, timeout=config.LLM_TIMEOUT,
                       headers={"Authorization": f"Bearer {config.LLM_API_KEY}"} if config.LLM_API_KEY else None)
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
    except Exception:
        return None


INTENT_SYSTEM = ("Ты извлекаешь параметры кредитной потребности малого бизнеса из текста. Верни ТОЛЬКО JSON с ключами: "
                 "purpose (working_capital|equipment|expansion|refinance|real_estate|null), amount (рубли, число или null), "
                 "term_months (целое или null), has_collateral (true|false|null), urgent (true|false|null). "
                 "Не выдумывай значения: если в тексте нет — null.")


def extract_intent(text: str) -> dict:
    base = rules_intent(text)
    out, source = base, "rules"
    raw = _chat([{"role": "system", "content": INTENT_SYSTEM}, {"role": "user", "content": text}], json_mode=True)
    if raw:
        try:
            llm = Intent.model_validate(json.loads(raw))
            merged = llm.model_dump()
            # числа, найденные детерминированно, надёжнее LLM
            for k in ("amount", "term_months", "has_collateral"):
                if getattr(base, k) is not None:
                    merged[k] = getattr(base, k)
            out, source = Intent(**merged), "llm+rules"
        except (ValidationError, ValueError, TypeError):
            pass
    return {"intent": out.model_dump(), "source": source}


# ---------------- объяснение решения ----------------
def _rub(x) -> str:
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def template_explanation(res: dict) -> str:
    o, off = res["outcome"], res.get("offer")
    good = "; ".join(s["text"].lower() for s in res["drivers"]["strengths"][:2])
    bad = "; ".join(s["text"].lower() for s in res["drivers"]["risk"][:2])
    if o == "APPROVED":
        s = (f"Заявка одобрена на запрошенных условиях: {_rub(off['amount'])} на {off['term_months']} мес. под {off['rate']:.1%} годовых, "
             f"платёж около {_rub(off['monthly_payment'])} в месяц.")
        return s + (f" В вашу пользу: {good}." if good else "")
    if o == "COUNTER_OFFER":
        s = (f"Запрошенные условия мы одобрить не можем, но готовы предложить: {off['product']}, {_rub(off['amount'])} на "
             f"{off['term_months']} мес. под {off['rate']:.1%}, платёж около {_rub(off['monthly_payment'])} в месяц.")
        return s + (f" Что ограничило сумму: {bad}." if bad else "")
    if o == "NEED_INFO":
        return "Для рассмотрения заявки нужно дополнить данные: " + ", ".join(res["missing_info"]) + "."
    if o == "MANUAL_REVIEW":
        return "Заявка передана кредитному специалисту банка для детального рассмотрения. Статус обновится в кабинете."
    return "К сожалению, по заявке принят отказ. " + (res["reasons"][0] + "." if res["reasons"] else "")


def _numbers(s: str) -> set:
    return {re.sub(r"\D", "", n) for n in re.findall(r"\d[\d \u00a0.,]*", s) if re.sub(r"\D", "", n)}


@lru_cache(maxsize=2048)
def _explain_cached(key: str, payload: str) -> str:
    res = json.loads(payload)
    fallback = template_explanation(res)
    facts = {k: res.get(k) for k in ("outcome", "offer", "reasons", "missing_info")}
    facts["drivers"] = [d["text"] for d in res["drivers"]["risk"] + res["drivers"]["strengths"]]
    txt = _chat([{"role": "system", "content":
                  "Ты помощник предпринимателя в банковском кабинете. Объясни решение по заявке простым русским языком "
                  "в 2–4 предложениях, без банковского жаргона. Используй ТОЛЬКО факты и числа из данных, ничего не добавляй "
                  "и не обещай. Не упоминай внутренние баллы и PD."},
                 {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}], max_tokens=220)
    # guardrail: числа в ответе должны быть подмножеством чисел из фактов
    if txt and _numbers(txt) <= _numbers(json.dumps(facts, ensure_ascii=False)) and len(txt) < 900:
        return txt
    return fallback


def explain_decision(res: dict) -> str:
    payload = json.dumps({k: res[k] for k in ("outcome", "offer", "reasons", "missing_info", "drivers")}, ensure_ascii=False, sort_keys=True)
    return _explain_cached(hashlib.md5(payload.encode()).hexdigest(), payload)
