
import subprocess

def generate_graph():
    """Generate a dependency graph image (pydeps)."""
    try:
        subprocess.run(["pydeps", ".", "-o", "depgraph.png"], check=True)
        return "Graph generated: depgraph.png"
    except Exception as e:
        return f"Failed to generate graph: {e}"

if __name__ == "__main__":
    print(generate_graph())
