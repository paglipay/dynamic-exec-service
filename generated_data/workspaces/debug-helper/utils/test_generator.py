
import os
from pathlib import Path

def generate_stub_test(module_name):
    """Create a minimal test file for the given module."""
    mod_path = Path(f"{module_name}.py")
    if not mod_path.exists():
        return f"Module {module_name} not found."

    test_path = Path("tests") / f"test_{module_name}.py"
    test_path.parent.mkdir(exist_ok=True)
    content = f"\nimport pytest\n\ndef test_placeholder_{module_name}():\n    assert True  # replace with real assertions for {module_name}\n"
    test_path.write_text(content, encoding="utf-8")
    return f"Created {test_path}" 

if __name__ == "__main__":
    print(generate_stub_test("utils.linter"))
