"""Client for deepseek_transformers_server.py.

Examples:
    python deepseek_transformers_client.py --prompt "Hello"
    python deepseek_transformers_client.py --server http://192.168.1.84:5004 --prompt "Hello"
    python deepseek_transformers_client.py --server http://192.168.1.84:5004 --health

Timeout defaults to 600 seconds for slow model inference but can be overridden.
"""

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


DEFAULT_SERVER_URL = os.environ.get("DEEPSEEK_TRANSFORMERS_SERVER_URL", "http://127.0.0.1:5004")
DEFAULT_TIMEOUT_SECONDS = 600

logger = logging.getLogger(__name__)


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

    logger.debug("POST %s | prompt_len=%d | timeout=%s", chat_url, len(prompt), timeout)
    start = time.perf_counter()
    try:
        if timeout == 0:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
        else:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        elapsed = time.perf_counter() - start
        logger.debug("POST %s completed in %.2fs | response_bytes=%d", chat_url, elapsed, len(raw))
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - start
        logger.debug("POST %s failed in %.2fs | HTTP %s", chat_url, elapsed, exc.code)
        return {"error": f"HTTP {exc.code}: {exc.reason}"}
    except TimeoutError:
        elapsed = time.perf_counter() - start
        logger.debug("POST %s timed out in %.2fs", chat_url, elapsed)
        return {"error": f"Connection timed out after {timeout}s to {chat_url}"}
    except urllib.error.URLError as exc:
        elapsed = time.perf_counter() - start
        logger.debug("POST %s failed in %.2fs | URLError: %s", chat_url, elapsed, exc.reason)
        return {"error": f"Connection failed to {chat_url}: {exc.reason}"}
    except Exception as exc:
        elapsed = time.perf_counter() - start
        logger.debug("POST %s failed in %.2fs | Exception: %s", chat_url, elapsed, exc)
        return {"error": str(exc)}


def check_health(server_url: str, timeout: int) -> dict:
    """Call /health and return parsed JSON."""
    health_url = _normalize_url(server_url, "/health")
    req = urllib.request.Request(health_url, method="GET")

    logger.debug("GET %s | timeout=%s", health_url, timeout)
    start = time.perf_counter()
    try:
        if timeout == 0:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
        else:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        elapsed = time.perf_counter() - start
        logger.debug("GET %s completed in %.2fs | response_bytes=%d", health_url, elapsed, len(raw))
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - start
        logger.debug("GET %s failed in %.2fs | HTTP %s", health_url, elapsed, exc.code)
        return {"error": f"HTTP {exc.code}: {exc.reason}"}
    except TimeoutError:
        elapsed = time.perf_counter() - start
        logger.debug("GET %s timed out in %.2fs", health_url, elapsed)
        return {"error": f"Connection timed out after {timeout}s to {health_url}"}
    except urllib.error.URLError as exc:
        elapsed = time.perf_counter() - start
        logger.debug("GET %s failed in %.2fs | URLError: %s", health_url, elapsed, exc.reason)
        return {"error": f"Connection failed to {health_url}: {exc.reason}"}
    except Exception as exc:
        elapsed = time.perf_counter() - start
        logger.debug("GET %s failed in %.2fs | Exception: %s", health_url, elapsed, exc)
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
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}). Use 0 for no client-side timeout.",
    )
    parser.add_argument("--health", action="store_true", help="Check /health and exit")
    parser.add_argument("--debug", action="store_true", help="Enable debug logs")
    args = parser.parse_args()

    if args.timeout < 0:
        print("Error: --timeout must be >= 0", file=sys.stderr)
        return 1

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.debug("Client started | server=%s | timeout=%s | health=%s", args.server, args.timeout, args.health)

    if args.health:
        health_timeout = min(args.timeout, 60) if args.timeout != 0 else 0
        result = check_health(args.server, timeout=health_timeout)
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
