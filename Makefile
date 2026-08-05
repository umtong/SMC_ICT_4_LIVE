.PHONY: setup doctor test smoke check research

ifeq ($(SMC4_PREBUILT_ENV),1)
PYTHON := python
SMC4 := smc4
else
PYTHON := uv run python
SMC4 := uv run smc4
endif

setup:
ifeq ($(SMC4_PREBUILT_ENV),1)
	@echo "prebuilt environment is already installed"
else
	uv sync --locked
endif

doctor:
	$(SMC4) doctor

test:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

smoke:
	$(SMC4) smoke --output artifacts/smoke

check: doctor test smoke

research:
	@test -n "$(NAME)" || (echo "usage: make research NAME=candidate-a" && exit 2)
	$(PYTHON) scripts/new_research.py "$(NAME)"
