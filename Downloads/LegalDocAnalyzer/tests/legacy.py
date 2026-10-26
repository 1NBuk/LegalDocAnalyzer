"""Эвристика из первой версии проекта — оставлена только для сравнения в бенчмарке."""
import re


def heuristic_extract(text):
    doc_type = "договор" if re.search(r"\bдоговор\b", text, re.I) else "other"
    parties = re.findall(r"([А-ЯЁ][а-яё\s]{2,60}(?:ООО|АО|ИП|ЗАО)?)", text)
    dates = re.findall(r"\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4}", text)
    amounts = re.findall(r"\d+[ \d]*[.,]?\d*\s*(?:руб|₽)", text)
    summary = " ".join([l.strip() for l in text.splitlines() if l.strip()][:3])
    risk_flags = []
    if "штраф" in text.lower():
        risk_flags.append("penalty")
    return {"doc_type": doc_type, "parties": parties[:5], "dates": dates[:5],
            "amounts": amounts[:5], "summary": summary, "risk_flags": risk_flags}
