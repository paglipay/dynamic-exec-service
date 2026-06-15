"""Flask server wrapping the Ollama API for deepseek-r1:7b.

DeepSeek-R1 models wrap their chain-of-thought in <think>...</think> tags.
This server parses that block out and returns it as a separate "reasoning" field.

Usage:
    python deepseek_ollama_server.py
    curl -X POST http://localhost:5002/chat -H "Content-Type: application/json" -d '{"prompt": "Hello"}'
    curl http://localhost:5002/health
"""
import logging
import os
import re
import time
from datetime import datetime

import requests
from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL_NAME = "deepseek-r1:7b"
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
PORT = int(os.environ.get("DEEPSEEK_OLLAMA_SERVER_PORT", "5002"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Regex to extract the <think>...</think> reasoning block (non-greedy, dotall)
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


# ---------------------------------------------------------------------------
# Helper: call Ollama /api/generate and split out reasoning
# ---------------------------------------------------------------------------
def _ollama_generate(prompt: str) -> tuple[str, str, float]:
    """Return (response_text, reasoning_text, duration_ms).

    reasoning_text is the content of the <think> block if present, else "".
    response_text has the <think> block stripped out.
    """
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
    }
    start = time.perf_counter()
    resp = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=120)
    resp.raise_for_status()
    elapsed_ms = (time.perf_counter() - start) * 1000

    raw = resp.json().get("response", "")

    # Extract <think> block if present
    match = _THINK_RE.search(raw)
    reasoning = match.group(1).strip() if match else ""

    # Remove the <think> block from the visible response
    response_text = _THINK_RE.sub("", raw).strip()

    return response_text, reasoning, elapsed_ms


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------
@app.route("/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()

    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    logger.info("Request | prompt_len=%d | %s", len(prompt), datetime.utcnow().isoformat())

    try:
        response_text, reasoning, duration_ms = _ollama_generate(prompt)
    except Exception as exc:
        logger.error("Generation failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    logger.info("Done    | duration_ms=%.1f | reasoning_len=%d", duration_ms, len(reasoning))

    result = {
        "model": MODEL_NAME,
        "response": response_text,
        "duration_ms": round(duration_ms, 1),
    }
    # Only include reasoning if the model produced a <think> block
    if reasoning:
        result["reasoning"] = reasoning

    return jsonify(result)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    """Ping Ollama and confirm the model is available."""
    try:
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        resp.raise_for_status()
        tags = resp.json()
        available_models = [m["name"] for m in tags.get("models", [])]
        model_available = any(MODEL_NAME in name for name in available_models)
    except Exception as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 503

    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "model_available": model_available,
        "ollama_url": OLLAMA_BASE,
    })


if __name__ == "__main__":
    logger.info("Starting %s server on port %d", MODEL_NAME, PORT)
    app.run(host="0.0.0.0", port=PORT)
