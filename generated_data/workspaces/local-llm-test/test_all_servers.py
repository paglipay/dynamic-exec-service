"""Send a test prompt to all 4 LLM servers and print results side by side.

Usage:
    python test_all_servers.py

Requires:
    pip install requests
"""
import sys

import requests

# ---------------------------------------------------------------------------
# Server registry
# ---------------------------------------------------------------------------
SERVERS = [
    {"label": "qwen3 (Ollama)",          "url": "http://localhost:5001/chat"},
    {"label": "deepseek-r1 (Ollama)",    "url": "http://localhost:5002/chat"},
    {"label": "qwen3 (transformers)",    "url": "http://localhost:5003/chat"},
    {"label": "deepseek (transformers)", "url": "http://localhost:5004/chat"},
]

TEST_PROMPT = "Explain what a Python decorator is in 2 sentences."
TIMEOUT = 60  # seconds

# ---------------------------------------------------------------------------
# Width helpers
# ---------------------------------------------------------------------------
COL_LABEL = 28
COL_DUR   = 12
RESPONSE_WRAP = 90


def _wrap(text: str, width: int, indent: int = 2) -> str:
    """Naive word-wrap for console output."""
    words = text.split()
    lines, current = [], []
    length = 0
    for word in words:
        if length + len(word) + 1 > width and current:
            lines.append(" " * indent + " ".join(current))
            current, length = [], 0
        current.append(word)
        length += len(word) + 1
    if current:
        lines.append(" " * indent + " ".join(current))
    return "\n".join(lines)


def _divider(char: str = "─", width: int = 80) -> str:
    return char * width


# ---------------------------------------------------------------------------
# Query a single server
# ---------------------------------------------------------------------------
def query_server(server: dict) -> dict:
    """POST to /chat and return the parsed JSON or an error dict."""
    try:
        resp = requests.post(
            server["url"],
            json={"prompt": TEST_PROMPT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"error": f"Connection refused — is the server running at {server['url']}?"}
    except requests.exceptions.Timeout:
        return {"error": f"Timed out after {TIMEOUT}s"}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print(_divider("═"))
    print(f"  Test prompt: \"{TEST_PROMPT}\"")
    print(_divider("═"))
    print()

    results = []
    for server in SERVERS:
        print(f"  Querying {server['label']} ...", end="", flush=True)
        data = query_server(server)
        results.append((server["label"], data))
        status = f"{data.get('duration_ms', '—')} ms" if "error" not in data else "ERROR"
        print(f" {status}")

    print()
    print(_divider("─"))
    print(f"  {'Model':<{COL_LABEL}}  {'Duration':>{COL_DUR}}  Response")
    print(_divider("─"))

    all_ok = True
    for label, data in results:
        if "error" in data:
            all_ok = False
            print(f"  {label:<{COL_LABEL}}  {'ERROR':>{COL_DUR}}")
            print(_wrap(data["error"], RESPONSE_WRAP))
        else:
            duration = f"{data.get('duration_ms', '—')} ms"
            response = data.get("response", "")
            print(f"  {label:<{COL_LABEL}}  {duration:>{COL_DUR}}")
            print(_wrap(response, RESPONSE_WRAP))

            # Print reasoning if present (deepseek models)
            reasoning = data.get("reasoning")
            if reasoning:
                print("  [reasoning]")
                print(_wrap(reasoning[:300] + ("..." if len(reasoning) > 300 else ""), RESPONSE_WRAP))

        print(_divider("─"))

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
