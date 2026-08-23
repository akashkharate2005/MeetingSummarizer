install:
	cd backend && python -m pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && uvicorn app.main:app --reload

frontend:
	cd frontend && npm run dev

up:
	docker compose up --build

down:
	docker compose down
