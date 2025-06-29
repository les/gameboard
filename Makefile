all: lint test download
.PHONY: all

.venv:
	uv sync

lint: .venv
	uv tool run --with pre-commit-uv -- pre-commit run -a
.PHONY: lint

test:
	uv run pytest -vv
.PHONY: test

download:
	uv run scripts/$(@).py
.PHONY: download

clean:
	find . -type d \( \
		-name 'data' -or \
		-name '__pycache__' -or \
		-name '.pytest_cache' -or \
		-name '.ruff_cache' -or \
		-name '.venv' \
	\) -prune -exec $(RM) -r {} +
	find . -type f -name '*.pyc' -exec $(RM) {} +
.PHONY: clean
