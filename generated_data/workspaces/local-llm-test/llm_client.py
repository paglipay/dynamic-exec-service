"""Client for the persistent local LLM inference server.

Examples:
    python llm_client.py --prompt "Hello world"
    python llm_client.py --health
    python llm_client.py --shutdown

The client uses stdlib HTTP calls so it works without adding dependencies.
"""
import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request_json(url: str, payload: dict | None = None, method: str = 'GET') -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            body = response.read().decode('utf-8')
    except HTTPError as exc:
        error_body = exc.read().decode('utf-8', errors='replace') if exc.fp else ''
        raise RuntimeError(f'HTTP {exc.code} from server: {error_body or exc.reason}') from exc
    except URLError as exc:
        raise RuntimeError(f'could not reach server at {url}: {exc.reason}') from exc

    if not body:
        return {}

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'invalid JSON from server: {body}') from exc


def _server_url(base_url: str, path: str) -> str:
    return base_url.rstrip('/') + path


def health(base_url: str) -> dict:
    return _request_json(_server_url(base_url, '/health'))


def generate(base_url: str, prompt: str, max_new_tokens: int) -> dict:
    return _request_json(
        _server_url(base_url, '/generate'),
        payload={'prompt': prompt, 'max_new_tokens': max_new_tokens},
        method='POST',
    )


def shutdown(base_url: str) -> dict:
    return _request_json(_server_url(base_url, '/shutdown'), method='POST')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Client for llm_server.py')
    parser.add_argument(
        '--server-url',
        type=str,
        default=os.environ.get('LLM_SERVER_URL', 'http://127.0.0.1:5100'),
        help='Base URL of llm_server.py',
    )
    parser.add_argument('--prompt', type=str, default=None, help='Prompt text to generate from')
    parser.add_argument('--max-new-tokens', type=int, default=80)
    parser.add_argument('--health', action='store_true', help='Check server health and exit')
    parser.add_argument('--shutdown', action='store_true', help='Ask the server to shut down and exit')
    args = parser.parse_args()

    if args.health:
        response = health(args.server_url)
        print(json.dumps(response, indent=2, sort_keys=True))
        sys.exit(0)

    if args.shutdown:
        response = shutdown(args.server_url)
        print(json.dumps(response, indent=2, sort_keys=True))
        sys.exit(0)

    if args.prompt:
        prompt = args.prompt.strip()
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        print('Error: provide a prompt via --prompt or stdin.', file=sys.stderr)
        sys.exit(1)

    if not prompt:
        print('Error: prompt is empty.', file=sys.stderr)
        sys.exit(1)

    response = generate(args.server_url, prompt, args.max_new_tokens)
    if 'error' in response:
        print(f"Error: {response['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"Prompt:   {response.get('prompt', prompt)}")
    print(f"Response: {response.get('response', '')}")