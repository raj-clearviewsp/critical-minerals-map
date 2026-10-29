SHELL := /bin/sh
.DEFAULT_GOAL := all
.NOTPARALLEL:

UV ?= uv
RELEASE_TAG ?= 2026.10.28
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
export PRE_COMMIT_HOME ?= $(CURDIR)/.cache/pre-commit
export npm_config_cache ?= $(CURDIR)/.cache/npm
RUN = $(UV) run --locked
PIPELINE = $(RUN) python -m pipeline --root . --release-tag $(RELEASE_TAG)

.PHONY: all setup setup-python setup-site hooks lint test check data fetch sources normalize harmonize link enrich qa publish site preview

all: setup check data site

setup: setup-python setup-site

setup-python:
	$(UV) sync --locked

setup-site:
	cd site && npm ci --no-audit --no-fund

hooks: setup-python
	$(RUN) pre-commit install

lint:
	$(RUN) ruff check .
	$(RUN) ruff format --check .

test:
	$(RUN) pytest

check: lint test

data: fetch normalize harmonize link enrich qa publish

fetch:
ifdef SOURCE
	$(RUN) python -m pipeline.fetch --root . --source $(SOURCE) $(FETCH_FLAGS)
else
	$(PIPELINE) fetch
endif

sources:
	$(RUN) python -m pipeline.fetch --root . --list

normalize harmonize link enrich qa publish:
	$(PIPELINE) $@

site:
	cd site && npm run check && npm run build

preview:
	cd site && npm run preview -- --host 127.0.0.1
