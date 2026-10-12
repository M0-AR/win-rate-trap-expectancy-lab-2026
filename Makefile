.PHONY: up down run test shell
up:
	docker compose up --build
down:
	docker compose down
run:
	python experiments/run_all.py --config configs/experiment.yaml
test:
	python -m pytest tests/ -q
shell:
	docker compose run --rm research bash
