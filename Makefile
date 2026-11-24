PYTHON ?= .venv/bin/python
RUFF ?= .venv/bin/ruff
SHELLCHECK ?= .venv/bin/shellcheck

.PHONY: test lint check package smoke-live

test:
	$(PYTHON) -m pytest -q

lint:
	$(RUFF) check .
	$(RUFF) format --check .
	$(SHELLCHECK) install.sh uninstall.sh
	$(PYTHON) -m compileall -q src

check: lint test

package:
	$(PYTHON) -m build
	$(PYTHON) -m twine check dist/*

# Deliberately performs a real request; denied locations exit 2.
smoke-live:
	$(PYTHON) -I run.py check

.PHONY: install uninstall install-dry-run help
install:
	./install.sh $(INSTALL_ARGS)
uninstall:
	./uninstall.sh
install-dry-run:
	./install.sh --dry-run $(INSTALL_ARGS)
help:
	@echo 'install install-dry-run uninstall check package; pass INSTALL_ARGS for installer flags'
