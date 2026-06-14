"""Flask server wrapping the Ollama API for qwen3:8b.

Usage:
    python qwen3_ollama_server.py
    curl -X POST http://localhost:5001/chat -H "Content-Type: application/json" -d '{"prompt": "Hello"}'
    curl http://localhost:5001/health
"""
import logging
import time
from datetime import datetime

import requests
from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL_NAME = "qwen3:8b"
OLLAMA_BASE = "http://localhost:11434"
PORT = 5001

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helper: call Ollama /api/generate
# ---------------------------------------------------------------------------
def _ollama_generate(prompt: str) -> tuple[str, float]:
    """Send prompt to Ollama and return (response_text, duration_ms)."""
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,  # get a single JSON response instead of a stream
    }
    start = time.perf_counter()
    resp = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=120)
    resp.raise_for_status()
    elapsed_ms = (time.perf_counter() - start) * 1000

    data = resp.json()
    return data.get("response", ""), elapsed_ms


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
        response_text, duration_ms = _ollama_generate(prompt)
    except Exception as exc:
        logger.error("Generation failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    logger.info("Done    | duration_ms=%.1f", duration_ms)

    return jsonify({
        "model": MODEL_NAME,
        "response": response_text,
        "duration_ms": round(duration_ms, 1),
    })


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
