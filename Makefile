.PHONY: setup doctor test smoke check research

setup:
	uv sync --locked

doctor:
	uv run smc4 doctor

test:
	uv run python -m unittest discover -s tests -p 'test_*.py'

smoke:
	uv run smc4 smoke --output artifacts/smoke

check: doctor test smoke

research:
	@test -n "$(NAME)" || (echo "usage: make research NAME=candidate-a" && exit 2)
	uv run python scripts/new_research.py "$(NAME)"
