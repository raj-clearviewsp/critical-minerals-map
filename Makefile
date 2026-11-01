SHELL := /bin/sh
.DEFAULT_GOAL := all
.NOTPARALLEL:

.PHONY: preview-data preview-data-check preview-data-receipt-check site-private site-private-preview

preview-data:
	$(RUN) python -m pipeline.preview --root . $(PREVIEW_FLAGS)

preview-data-check:
	$(RUN) python -m pipeline.preview --root . --check --local

preview-data-receipt-check:
	$(RUN) python -m pipeline.preview --root . --check

site-private:
	$(MAKE) preview-data-check
	cd site && npm run check && npm run build:private

site-private-preview:
	cd site && npm run preview:private

UV ?= uv
RELEASE_TAG ?= 2026.10.28
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
export PRE_COMMIT_HOME ?= $(CURDIR)/.cache/pre-commit
export npm_config_cache ?= $(CURDIR)/.cache/npm
RUN = $(UV) run --locked
PIPELINE = $(RUN) python -m pipeline --root . --release-tag $(RELEASE_TAG)

.PHONY: reconcile reconcile-check reconcile-receipt-check triage triage-check triage-receipt-check link-review link-review-check link-review-receipt-check all setup setup-python setup-site hooks lint test check data fetch sources vocab vocab-check pilot pilot-check pilot-receipt-check normalize-check normalize-receipt-check normalize harmonize link enrich qa publish site preview

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

vocab:
	$(RUN) python -m pipeline.vocab --root .

vocab-check:
	$(RUN) python -m pipeline.vocab --root . --check

pilot:
	$(RUN) python -m pipeline.pilot --root .

pilot-check:
	$(RUN) python -m pipeline.pilot --root . --check --raw

pilot-receipt-check:
	$(RUN) python -m pipeline.pilot --root . --check

link-review:
	$(RUN) python -m pipeline.link --root .

link-review-check:
	$(RUN) python -m pipeline.link --root . --check --raw

link-review-receipt-check:
	$(RUN) python -m pipeline.link --root . --check

triage:
	$(RUN) python -m pipeline.triage --root . $(TRIAGE_FLAGS)

triage-check:
	$(RUN) python -m pipeline.triage --root . --check --raw

triage-receipt-check:
	$(RUN) python -m pipeline.triage --root . --check

reconcile:
	$(RUN) python -m pipeline.reconciliation --root . $(RECONCILE_FLAGS)

reconcile-check:
	$(RUN) python -m pipeline.reconciliation --root . --check --local

reconcile-receipt-check:
	$(RUN) python -m pipeline.reconciliation --root . --check

normalize-check:
	$(RUN) python -m pipeline.normalize --root . --check --raw

normalize-receipt-check:
	$(RUN) python -m pipeline.normalize --root . --check

normalize harmonize link enrich qa publish:
	$(PIPELINE) $@

site:
	cd site && npm run check && npm run build

preview:
	cd site && npm run preview
