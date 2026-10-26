up:      ; docker compose up --build
test:    ; cd backend && pytest -q
retrain: ; cd backend && python -m credit_ds.train
dev:     ; cd backend && uvicorn app.main:app --reload
train:   ; cd backend && python -m credit_ds.train --n 150000 --trials 12
prod:    ; docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
