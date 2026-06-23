
import subprocess
from pathlib import Path

def run_lint():
    """Run flake8 and mypy on the current directory."""
    print("Running flake8...")
    subprocess.run(["flake8", "."], check=True)
    print("Running mypy...")
    subprocess.run(["mypy", "."], check=True)

if __name__ == "__main__":
    run_lint()
