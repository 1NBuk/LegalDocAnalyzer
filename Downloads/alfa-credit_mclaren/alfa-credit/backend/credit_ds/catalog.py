"""Каталог условных кредитных продуктов (синтетический, не отражает реальные продукты банка)."""

PRODUCTS = {
    "express": dict(
        name="Экспресс-кредит", min_amount=100_000, max_amount=3_000_000, min_term=3, max_term=24,
        purposes=["working_capital", "refinance"], spread=0.02, pd_approve=0.045, pd_decline=0.09,
        min_dscr=1.35, max_ltr=0.25, min_collateral=0.0, min_age=6, manual_above=3_000_000,
        pitch="Быстрое решение без залога, подходит для небольших сумм"),
    "working_capital": dict(
        name="Кредит на пополнение оборотных средств", min_amount=300_000, max_amount=50_000_000,
        min_term=6, max_term=36, purposes=["working_capital"], spread=0.01, pd_approve=0.06,
        pd_decline=0.11, min_dscr=1.25, max_ltr=0.50, min_collateral=0.0, min_age=6, manual_above=20_000_000,
        pitch="Закупка товара, сырья, выплата зарплат в сезонный пик"),
    "overdraft": dict(
        name="Овердрафт", min_amount=100_000, max_amount=10_000_000, min_term=3, max_term=12,
        purposes=["working_capital"], spread=0.015, pd_approve=0.05, pd_decline=0.10,
        min_dscr=1.3, max_ltr=0.20, min_collateral=0.0, min_age=6, manual_above=10_000_000,
        pitch="Закрытие кассовых разрывов: платите проценты только за использованные дни"),
    "investment": dict(
        name="Инвестиционный кредит", min_amount=1_000_000, max_amount=100_000_000,
        min_term=12, max_term=84, purposes=["equipment", "expansion"], spread=0.005,
        pd_approve=0.065, pd_decline=0.12, min_dscr=1.2, max_ltr=0.80, min_collateral=0.0, min_age=12,
        manual_above=20_000_000, pitch="Оборудование, запуск новой точки или направления"),
    "refinance": dict(
        name="Рефинансирование", min_amount=500_000, max_amount=50_000_000, min_term=12, max_term=60,
        purposes=["refinance"], spread=0.0, pd_approve=0.06, pd_decline=0.11, min_dscr=1.2,
        max_ltr=0.70, min_collateral=0.0, min_age=12, manual_above=20_000_000,
        pitch="Объединить и удешевить действующие кредиты"),
    "secured": dict(
        name="Кредит под залог недвижимости", min_amount=3_000_000, max_amount=150_000_000,
        min_term=12, max_term=120, purposes=["real_estate", "expansion", "equipment", "working_capital", "refinance"],
        spread=-0.005, pd_approve=0.08, pd_decline=0.14, min_dscr=1.1, max_ltr=1.2, min_collateral=0.7,
        min_age=12, manual_above=30_000_000, pitch="Больше сумма и срок, ниже ставка за счёт залога"),
}

PURPOSE_LABELS = {
    "working_capital": "пополнение оборотных средств", "equipment": "покупка оборудования",
    "expansion": "расширение бизнеса", "refinance": "рефинансирование", "real_estate": "покупка недвижимости",
}
# real_estate как цель обслуживается только залоговым продуктом
PRODUCTS["secured"]["purposes"] = ["real_estate", "expansion", "equipment", "working_capital", "refinance"]
