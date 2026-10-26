import json
from types import SimpleNamespace

import llm_extractor as le
from tests.gold import GOLD


def test_gold_doc_type_and_fields():
    for g in GOLD:
        r = le.heuristic_extract(g["text"])
        assert r["doc_type"] == g["doc_type"], g["name"]
        assert r["dates"] == g["dates"], g["name"]
        assert r["amounts"] == g["amounts"], g["name"]
        assert r["parties"] == g["parties"], g["name"]
        assert sorted(r["risk_flags"]) == sorted(g["risk_flags"]), g["name"]


def test_invalid_dates_and_phones_are_ignored():
    assert le.extract_dates("тел 22-14-93, 31.02.2024, 29.12.10") == []


def test_amount_not_glued_to_previous_number():
    assert le.extract_amounts("Дата 2023 100 000 руб.") == ["100000 ₽"]


def test_word_rub_inside_other_words_ignored():
    assert le.extract_amounts("в рубрике 5 рублевых") == []


def _fake_client(content=None, exc=None):
    def create(**kwargs):
        if exc:
            raise exc
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_llm_path_uses_new_openai_api_and_normalizes():
    payload = {"doc_type": "ДОГОВОР", "parties": ["ООО \"А\""], "dates": ["01.01.2023"],
               "amounts": "100 ₽", "summary": "ok", "risk_flags": ["penalty", "bogus"]}
    r = le.extract_with_llm("Договор №1", client=_fake_client(json.dumps(payload)))
    assert r["source"] == "llm"
    assert r["doc_type"] == "договор"
    assert r["amounts"] == ["100 ₽"]
    assert r["risk_flags"] == ["penalty"]  # неизвестный флаг отброшен


def test_llm_failure_falls_back_and_reports_reason(caplog):
    r = le.extract_with_llm("Договор №1\nДата: 01.01.2023", client=_fake_client(exc=TimeoutError("boom")))
    assert r["source"] == "heuristic"
    assert r["llm_error"] == "TimeoutError"
    assert r["dates"] == ["01.01.2023"]
    assert "LLM-анализ не удался" in caplog.text


def test_llm_bad_json_falls_back():
    r = le.extract_with_llm("Договор №1", client=_fake_client("not json"))
    assert r["source"] == "heuristic" and "llm_error" in r


def test_digest_keeps_late_risk_clause_and_limit():
    body = "Общие положения. " * 3000 + " За нарушение сроков взимается штраф 5%. " + "Прочее. " * 3000 + " ООО «Хвост»"
    d = le.build_llm_digest(body)
    assert len(d) <= le.MAX_LLM_CHARS + 100
    assert "штраф 5%" in d
    assert "Хвост" in d


def test_empty_text():
    r = le.extract_with_llm("   ")
    assert r["doc_type"] == "other" and r["llm_error"] == "empty_text"


def test_holdout_regression():
    """Бывшая отложенная выборка (после исправления найденных на ней промахов — регрессионная)."""
    from tests.holdout import HOLDOUT
    for h in HOLDOUT:
        r = le.heuristic_extract(h["text"])
        for k in ("doc_type", "parties", "dates", "amounts", "risk_flags"):
            got, exp = r[k], h[k]
            assert (sorted(got) == sorted(exp)) if isinstance(exp, list) else got == exp, (h["name"], k)
