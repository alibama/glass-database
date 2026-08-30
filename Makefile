.PHONY: help install seed test lint run-api run-explore run-admin run-glowtbook
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Install Python dependencies
	pip install -r requirements.txt

seed:  ## Build a small synthetic demo database (no real data)
	python -m scripts.seed_demo

test:  ## Run the test suite
	pip install pytest moto boto3 >/dev/null 2>&1 || true
	python -m pytest

lint:  ## Lint with ruff (if installed)
	ruff check . || true

run-api:  ## Serve the read-only API at :8000 (/docs)
	uvicorn api.main:app --reload --port 8000

run-explore:  ## Serve the public explorer at :8502
	streamlit run explore/app.py --server.port 8502

run-admin:  ## Serve the admin console at :8501
	streamlit run admin/app.py --server.port 8501 --server.baseUrlPath admin

run-glowtbook:  ## Serve Glowtbook at :8503
	streamlit run glowtbook/app.py --server.port 8503 --server.baseUrlPath glowtbook
