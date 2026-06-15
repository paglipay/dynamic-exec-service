"""Client for deepseek_transformers_server.py.

Examples:
    python deepseek_transformers_client.py --prompt "Hello"
    python deepseek_transformers_client.py --server http://192.168.1.84:5004 --prompt "Hello"
    python deepseek_transformers_client.py --server http://192.168.1.84:5004 --health

Timeout defaults to 600 seconds for slow model inference but can be overridden.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse


DEFAULT_SERVER_URL = os.environ.get("DEEPSEEK_TRANSFORMERS_SERVER_URL", "http://127.0.0.1:5004")
DEFAULT_TIMEOUT_SECONDS = 600


def _normalize_url(server_url: str, endpoint: str) -> str:
    """Accept base URL or endpoint URL and return normalized endpoint URL."""
    cleaned = server_url.strip().rstrip("/")
    parsed = urlparse(cleaned)
    path = parsed.path or ""

    if path.endswith(endpoint):
        return cleaned

    if endpoint == "/health" and path.endswith("/chat"):
        return cleaned[: -len("/chat")] + "/health"

    return f"{cleaned}{endpoint}"


def query_server(server_url: str, prompt: str, timeout: int) -> dict:
    """Send a prompt to /chat and return parsed JSON."""
    chat_url = _normalize_url(server_url, "/chat")
    payload = json.dumps({"prompt": prompt}).encode("utf-8")
    req = urllib.request.Request(
        chat_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}"}
    except TimeoutError:
        return {"error": f"Connection timed out after {timeout}s to {chat_url}"}
    except urllib.error.URLError as exc:
        return {"error": f"Connection failed to {chat_url}: {exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}


def check_health(server_url: str, timeout: int) -> dict:
    """Call /health and return parsed JSON."""
    health_url = _normalize_url(server_url, "/health")
    req = urllib.request.Request(health_url, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}"}
    except TimeoutError:
        return {"error": f"Connection timed out after {timeout}s to {health_url}"}
    except urllib.error.URLError as exc:
        return {"error": f"Connection failed to {health_url}: {exc.reason}"}
    except Exception as exc:
        return {"error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Client for deepseek_transformers_server.py")
    parser.add_argument(
        "--server",
        default=DEFAULT_SERVER_URL,
        help="Base URL or /chat URL for deepseek_transformers_server.py",
    )
    parser.add_argument("--prompt", default=None, help="Prompt text. Optional if piped via stdin.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument("--health", action="store_true", help="Check /health and exit")
    args = parser.parse_args()

    if args.timeout <= 0:
        print("Error: --timeout must be a positive integer", file=sys.stderr)
        return 1

    if args.health:
        result = check_health(args.server, timeout=min(args.timeout, 60))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if "error" not in result else 1

    if args.prompt:
        prompt = args.prompt.strip()
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        print("Error: provide --prompt or pipe prompt text via stdin.", file=sys.stderr)
        return 1

    if not prompt:
        print("Error: prompt is empty.", file=sys.stderr)
        return 1

    print(f"Sending prompt to {args.server} with timeout={args.timeout}s...\n")
    result = query_server(args.server, prompt, timeout=args.timeout)

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    print(f"Model: {result.get('model', '')}")
    print(f"Response: {result.get('response', '')}\n")

    reasoning = result.get("reasoning")
    if reasoning:
        print(f"Reasoning: {reasoning}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
