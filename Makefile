PYTHON ?= python
DBT ?= dbt
PYTEST_BASETEMP ?= .pytest-tmp-lab
DATA_AS_OF ?= 2026-08-28T12:25:30Z

.PHONY: reset baseline tests acceptance gx dbt dashboard generate drill verify

reset:
	$(PYTHON) scripts/reset_lab.py

baseline:
	$(PYTHON) scripts/run_baseline.py

tests:
	$(PYTHON) -m pytest tests_public -q --basetemp $(PYTEST_BASETEMP)

acceptance:
	$(PYTHON) -m pytest tests_acceptance -q --basetemp $(PYTEST_BASETEMP)

gx:
	$(PYTHON) gx/validate_orders.py

dbt:
	$(PYTHON) scripts/sync_dbt_seeds.py
	$(DBT) build --project-dir dbt_project --profiles-dir dbt_project --no-partial-parse

dashboard:
	$(PYTHON) -m streamlit run dashboard/app.py

generate:
	$(PYTHON) scripts/generate_data.py --rows 600 --days 42 --seed 27 --as-of $(DATA_AS_OF)

drill:
	$(PYTHON) scripts/run_incident_drill.py

verify:
	$(PYTHON) scripts/verify_lab.py
