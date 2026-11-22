.PHONY: up down logs test restart

up:
	cp -n .env.example .env || true
	docker-compose up --build

down:
	docker-compose down

logs:
	docker-compose logs -f api worker mock-dms

test:
	docker-compose exec api pytest -q

restart:
	docker-compose restart api worker
