"""FastAPI-роутер DS-сервиса. Подключается в основное приложение: app.include_router(router)."""
from typing import Optional

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field

from .llm import extract_intent
from .news import personalize
from .pipeline import apply_enrichment, assess, get_scorer
from .mock_integrations import enrich
from .recommender import recommend

router = APIRouter(prefix="/ds", tags=["ds-ml"])


class Company(BaseModel):
    inn: str = Field(..., examples=["7700000001"])
    legal_form: str = "OOO"
    sector: str = "retail"
    region_tier: str = "million"
    employees: int = 10
    revenue: float = 24_000_000
    revenue_growth: float = 0.08
    profit_margin: float = 0.16
    avg_monthly_turnover: float = 2_000_000
    turnover_cv: float = 0.25
    neg_days_90: int = 1
    debt_to_revenue: float = 0.1
    active_loans: int = 1
    collateral_ratio: float = 0.0


class Application(Company):
    product: str = "working_capital"
    purpose: str = "working_capital"
    requested_amount: Optional[float] = 3_000_000
    term_months: Optional[int] = 24


class IntentIn(BaseModel):
    text: str


class RecommendIn(BaseModel):
    company: Company
    text: str


class NewsIn(BaseModel):
    company: Company
    stage: str = "DRAFT"
    application: Optional[dict] = None


@router.post("/intent")
def intent(body: IntentIn):
    return extract_intent(body.text)


@router.post("/recommend")
def recommend_products(body: RecommendIn):
    c = body.company.model_dump()
    profile = apply_enrichment({**c, "purpose": "working_capital", "requested_amount": 1, "term_months": 12}, enrich(c["inn"]))
    it = extract_intent(body.text)
    return {"intent": it, "products": recommend(profile, it["intent"], get_scorer())}


@router.post("/assess")
def assess_application(body: Application):
    return assess(body.model_dump())


@router.post("/news")
def news(body: NewsIn):
    return personalize(body.company.model_dump(), body.application, body.stage)


@router.get("/model-card")
def model_card():
    return get_scorer().card


app = FastAPI(title="Credit DS/ML service", version="1.0")
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
