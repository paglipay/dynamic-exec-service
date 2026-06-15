"""Simple client for DeepSeek Ollama server at /chat endpoint."""
import json
import sys
import urllib.request


def query_deepseek_ollama(server_url: str, prompt: str, timeout: int = 120) -> dict:
    """Send a prompt to DeepSeek Ollama server and get response."""
    data = json.dumps({"prompt": prompt}).encode("utf-8")
    req = urllib.request.Request(
        server_url,
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
    except Exception as e:
        return {"error": str(e)}


def main():
    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} <server_url> <prompt>")
        sys.exit(1)

    server_url = sys.argv[1]
    prompt = " ".join(sys.argv[2:])

    print(f"Sending prompt to {server_url}...\n")
    result = query_deepseek_ollama(server_url, prompt)

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
