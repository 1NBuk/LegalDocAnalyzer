import os
import re
import json
from typing import Dict

try:
    import openai
except:
    openai = None

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = "gpt-4o-mini"

def heuristic_extract(text):
    doc_type = "договор" if re.search(r"\bдоговор\b", text, re.I) else "other"
    parties = re.findall(r"([А-ЯЁ][а-яё\s]{2,60}(?:ООО|АО|ИП|ЗАО)?)", text)
    dates = re.findall(r"\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4}", text)
    amounts = re.findall(r"\d+[ \d]*[.,]?\d*\s*(?:руб|₽)", text)
    summary = " ".join([l.strip() for l in text.splitlines() if l.strip()][:3])
    risk_flags = []
    if "штраф" in text.lower():
        risk_flags.append("penalty")
    return {
        "doc_type": doc_type,
        "parties": parties[:5],
        "dates": dates[:5],
        "amounts": amounts[:5],
        "summary": summary,
        "risk_flags": risk_flags
    }

def extract_with_llm(text):
    if not OPENAI_API_KEY or not openai:
        return heuristic_extract(text)
    prompt = f"""
    Извлеки JSON поля: doc_type, parties, dates, amounts, summary, risk_flags из текста:
    {text[:3000]}
    """
    try:
        resp = openai.ChatCompletion.create(
            model=MODEL,
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )
        content = resp['choices'][0]['message']['content']
        j = json.loads(re.search(r"\{[\s\S]*\}", content).group())
        return j
    except:
        return heuristic_extract(text)
