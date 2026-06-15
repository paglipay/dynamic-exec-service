"""Simple client for DeepSeek Ollama server.

Examples:
    python deepseek_ollama_client.py --server http://192.168.1.84:5002 --prompt "Hello"
    python deepseek_ollama_client.py --server http://192.168.1.84:5002 --health

If --server is omitted, DEEPSEEK_SERVER_URL is used. Final fallback is
http://127.0.0.1:5002.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse


def _normalize_chat_url(server_url: str) -> str:
    """Accept base URL or full /chat URL and return full /chat endpoint URL."""
    cleaned = server_url.strip().rstrip("/")
    parsed = urlparse(cleaned)
    path = parsed.path or ""
    if path.endswith("/chat"):
        return cleaned
    if path:
        return f"{cleaned}/chat"
    return f"{cleaned}/chat"


def _normalize_health_url(server_url: str) -> str:
    """Accept base URL or full URL and return /health endpoint URL."""
    cleaned = server_url.strip().rstrip("/")
    parsed = urlparse(cleaned)
    path = parsed.path or ""
    if path.endswith("/health"):
        return cleaned
    if path.endswith("/chat"):
        return cleaned[: -len("/chat")] + "/health"
    if path:
        return f"{cleaned}/health"
    return f"{cleaned}/health"


def query_deepseek_ollama(server_url: str, prompt: str, timeout: int = 120) -> dict:
    """Send a prompt to DeepSeek Ollama server and get response."""
    chat_url = _normalize_chat_url(server_url)
    data = json.dumps({"prompt": prompt}).encode("utf-8")
    req = urllib.request.Request(
        chat_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = resp.read().decode("utf-8")
            return json.loads(resp_data)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except TimeoutError:
        return {"error": f"Connection timed out to {chat_url}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed to {chat_url}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def check_health(server_url: str, timeout: int = 10) -> dict:
    """Call /health on the DeepSeek wrapper server."""
    health_url = _normalize_health_url(server_url)
    req = urllib.request.Request(health_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except TimeoutError:
        return {"error": f"Connection timed out to {health_url}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed to {health_url}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Client for deepseek_ollama_server.py")
    parser.add_argument(
        "--server",
        default=os.environ.get("DEEPSEEK_SERVER_URL", "http://127.0.0.1:5002"),
        help="DeepSeek wrapper server base URL or /chat URL",
    )
    parser.add_argument("--prompt", default=None, help="Prompt text. Optional if piped via stdin.")
    parser.add_argument("--timeout", type=int, default=120, help="Request timeout in seconds")
    parser.add_argument("--health", action="store_true", help="Check /health and exit")
    args = parser.parse_args()

    if args.health:
        result = check_health(args.server, timeout=min(args.timeout, 30))
        print(json.dumps(result, indent=2, sort_keys=True))
        sys.exit(0 if "error" not in result else 1)

    if args.prompt:
        prompt = args.prompt.strip()
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        print("Error: provide --prompt or pipe prompt text via stdin.")
        sys.exit(1)

    if not prompt:
        print("Error: prompt is empty.")
        sys.exit(1)

    print(f"Sending prompt to {args.server}...\n")
    result = query_deepseek_ollama(args.server, prompt, timeout=args.timeout)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    print(f"Model: {result.get('model', '')}")
    print(f"Response: {result.get('response', '')}\n")
    reasoning = result.get('reasoning')
    if reasoning:
        print(f"Reasoning: {reasoning}\n")


if __name__ == "__main__":
    main()
