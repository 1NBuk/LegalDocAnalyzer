"""Персональная лента новостей: content-based ранжирование по теговому профилю клиента и стадии заявки.
Корпус синтетический. score = 0.5*tag_idf_overlap + 0.25*stage_match + 0.15*sector_match + 0.10*freshness"""
import math
from datetime import date

NEWS = [
    dict(id=1, title="Как подготовить выписки и отчётность, чтобы заявку одобрили быстрее", tags=["documents", "approval", "need_info"], stages=["NEED_INFO", "SUBMITTED", "DRAFT"], sectors=[], days_old=3),
    dict(id=2, title="Льготные программы господдержки МСП: кому доступны", tags=["subsidy", "msp", "rate"], stages=["DRAFT", "OFFER_READY"], sectors=[], days_old=5),
    dict(id=3, title="Сезонный спрос: как планировать закупки и кассовые разрывы", tags=["working_capital", "cash_gap", "retail", "food"], stages=["DRAFT"], sectors=["retail", "food"], days_old=2),
    dict(id=4, title="Лизинг или кредит на оборудование: что выгоднее", tags=["equipment", "leasing", "investment"], stages=["DRAFT", "OFFER_READY"], sectors=["manufacturing", "transport", "agro"], days_old=7),
    dict(id=5, title="Как улучшить кредитную историю компании за 6 месяцев", tags=["credit_history", "bki", "grade_low"], stages=["DECLINED", "NEED_INFO"], sectors=[], days_old=6),
    dict(id=6, title="Как читать кредитное предложение: ставка, платёж, переплата", tags=["offer", "rate", "overpayment"], stages=["OFFER_READY"], sectors=[], days_old=4),
    dict(id=7, title="Подписание документов онлайн: что понадобится", tags=["signing", "documents"], stages=["OFFER_ACCEPTED", "SIGNING"], sectors=[], days_old=1),
    dict(id=8, title="Рынок строительных подрядов: риски и условия финансирования", tags=["construction", "guarantee", "working_capital"], stages=["DRAFT"], sectors=["construction"], days_old=9),
    dict(id=9, title="IT-бизнес: как финансировать рост без залога", tags=["it", "unsecured", "express"], stages=["DRAFT"], sectors=["it"], days_old=8),
    dict(id=10, title="Рефинансирование: когда оно действительно снижает платёж", tags=["refinance", "rate", "overpayment"], stages=["DRAFT", "OFFER_READY"], sectors=[], days_old=4),
    dict(id=11, title="Недвижимость в залог: оценка и что влияет на ставку", tags=["real_estate", "collateral", "rate"], stages=["DRAFT", "NEED_INFO"], sectors=[], days_old=10),
    dict(id=12, title="Что происходит после отказа: как подать заявку повторно", tags=["declined", "credit_history", "grade_low"], stages=["DECLINED"], sectors=[], days_old=2),
    dict(id=13, title="Налоговый календарь МСП на ближайший квартал", tags=["tax", "calendar", "cash_gap"], stages=["DRAFT", "SUBMITTED", "OFFER_READY"], sectors=[], days_old=1),
    dict(id=14, title="Агросектор: сезонные кредиты и страхование урожая", tags=["agro", "working_capital", "seasonal"], stages=["DRAFT"], sectors=["agro"], days_old=11),
]
TAG_RU = {"documents": "документы", "approval": "одобрение заявки", "need_info": "нужны данные", "subsidy": "господдержка", "msp": "малый бизнес", "rate": "ставка",
          "working_capital": "оборотные средства", "cash_gap": "кассовые разрывы", "retail": "розничная торговля", "food": "общепит", "equipment": "оборудование",
          "leasing": "лизинг", "investment": "инвестиции", "manufacturing": "производство", "transport": "транспорт", "agro": "агросектор", "credit_history": "кредитная история",
          "bki": "БКИ", "grade_low": "повышенный риск", "offer": "кредитное предложение", "overpayment": "переплата", "signing": "подписание", "construction": "строительство",
          "guarantee": "гарантии", "it": "IT", "unsecured": "кредит без залога", "express": "быстрое решение", "refinance": "рефинансирование", "real_estate": "недвижимость",
          "collateral": "залог", "declined": "отказ по заявке", "tax": "налоги", "calendar": "календарь", "seasonal": "сезонность", "services": "услуги"}
SUMMARY = {
    1: "Какие выписки и справки нужны банку и как оформить их так, чтобы проверка прошла с первого раза.",
    2: "Обзор программ с пониженной ставкой и гарантийной поддержкой для малого бизнеса и условия участия.",
    3: "Как заранее рассчитать закупки к высокому сезону и закрыть кассовые разрывы оборотным кредитом или овердрафтом.",
    4: "Сравнение платежа, налоговой нагрузки и требований к залогу: когда выгоднее лизинг, а когда кредит.",
    5: "Пошаговый план: закрыть просрочки, снизить долговую нагрузку и сократить число запросов в БКИ.",
    6: "Из чего складывается платёж, как считается переплата и на что смотреть, кроме процентной ставки.",
    7: "Как проходит подписание договора онлайн: что проверить в условиях и какой код потребуется.",
    8: "Особенности финансирования подрядчиков: банковские гарантии, авансы и график платежей по этапам работ.",
    9: "Варианты роста без залога: оборотное финансирование под выручку и быстрые решения по небольшим суммам.",
    10: "Когда замена нескольких кредитов одним действительно снижает ежемесячный платёж, а когда увеличивает переплату.",
    11: "Как банк оценивает залоговую недвижимость и как обеспечение влияет на ставку и лимит.",
    12: "Что можно изменить в заявке после отказа — сумму, срок, обеспечение — и когда повторное обращение имеет смысл.",
    13: "Ключевые даты налоговых платежей и отчётности, чтобы не получить кассовый разрыв в день уплаты.",
    14: "Как сезонные кредиты согласуются с графиком урожая и зачем нужно страхование урожая.",
}
_DF = {}
for n in NEWS:
    for t in set(n["tags"]):
        _DF[t] = _DF.get(t, 0) + 1
IDF = {t: math.log((1 + len(NEWS)) / (1 + c)) + 1 for t, c in _DF.items()}


def user_tags(profile: dict, application: dict | None) -> set:
    tags = {profile.get("sector", ""), "msp"}
    app = application or {}
    if app.get("purpose"):
        tags.add(app["purpose"])
        tags |= {"working_capital": {"cash_gap", "seasonal"}, "equipment": {"leasing", "investment"},
                 "real_estate": {"collateral"}, "refinance": {"overpayment"}, "expansion": {"investment"}}.get(app["purpose"], set())
    if app.get("outcome") == "NEED_INFO":
        tags |= {"documents", "need_info"}
    if app.get("outcome") == "DECLINED":
        tags |= {"declined", "credit_history"}
    if app.get("grade") in ("D", "E"):
        tags |= {"grade_low", "credit_history", "bki"}
    if app.get("outcome") in ("APPROVED", "COUNTER_OFFER"):
        tags |= {"offer", "rate", "overpayment"}
    if not profile.get("collateral_ratio"):
        tags.add("unsecured")
    return tags


def personalize(profile: dict, application: dict | None = None, stage: str = "DRAFT", k: int = 5) -> list:
    tags = user_tags(profile, application)
    scored = []
    for n in NEWS:
        overlap = sum(IDF[t] for t in n["tags"] if t in tags) / sum(IDF[t] for t in n["tags"])
        s = (0.5 * overlap + 0.25 * (stage in n["stages"]) + 0.15 * (profile.get("sector") in n["sectors"])
             + 0.10 * math.exp(-n["days_old"] / 7))
        why = sorted(set(n["tags"]) & tags)
        scored.append({"id": n["id"], "title": n["title"], "score": round(s, 3),
                       "summary": SUMMARY.get(n["id"], ""),
                       "why": ("Показано, потому что: " + ", ".join(TAG_RU.get(t, t) for t in why)) if why else "Актуально для вашей стадии заявки"})
    return sorted(scored, key=lambda x: -x["score"])[:k]
