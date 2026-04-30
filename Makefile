PYTHON ?= python3
PACKAGE = harness_engineering
PYTHONPATH_RUN = PYTHONPATH=src

.PHONY: install test demo demo-interactive clean secrets run-sample check

install:
	@echo "For isolated environments, prefer a virtualenv or uv."
	$(PYTHON) -m pip install -e .

test:
	$(PYTHONPATH_RUN) $(PYTHON) -m unittest discover -s tests -v

secrets:
	$(PYTHON) scripts/secret_scan.py

demo:
	$(PYTHONPATH_RUN) $(PYTHON) -m $(PACKAGE).cli start --topic "Agentic harness engineering" --source-file sample_data/sources.json

demo-interactive:
	$(PYTHONPATH_RUN) $(PYTHON) -m $(PACKAGE).cli interactive --topic "Agentic harness engineering" --source-file sample_data/sources.json

run-sample:
	$(PYTHONPATH_RUN) $(PYTHON) -m $(PACKAGE).cli start --topic "Agentic harness engineering" --source-file sample_data/sources.json && \
	$(PYTHONPATH_RUN) $(PYTHON) -m $(PACKAGE).cli list && \
	$(PYTHONPATH_RUN) $(PYTHON) -m $(PACKAGE).cli inspect --latest

check:
	$(MAKE) test
	$(MAKE) secrets

clean:
	rm -rf .runs build dist *.egg-info src/*.egg-info src/$(PACKAGE).egg-info
