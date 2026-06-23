# Debug‑Assistant

This workspace provides a lightweight toolset for **debugging and maintaining** large Python projects.

## Features
- Linting with **flake8** + **mypy** (static type checks)
- Unit testing & coverage with **pytest** + **coverage.py**
- Dependency graph generation via **pydeps**
- Simple CLI wrapper for running the CI pipeline (`run-ci.sh`)
- Reusable helper scripts that can be imported into future projects

## Getting started
```bash
cd generated_data/workspaces/debug-helper
pip install -r requirements.txt    # installs linters, test tools, etc.
./run-ci.sh                        # run lint + tests + coverage
```
