#!/usr/bin/env bash
set -euo pipefail

# Linting
flake8 .
mypy .

# Testing & coverage
pytest --cov=. --cov-report term-missing

# Dependency graph (optional)
# pydeps . -o depgraph.png 2>/dev/null || true
