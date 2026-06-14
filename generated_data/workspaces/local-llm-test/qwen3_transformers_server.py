"""Flask server loading Qwen/Qwen2.5-7B-Instruct via HuggingFace transformers.

Requires:
    pip install flask torch transformers accelerate

Usage:
    python qwen3_transformers_server.py
    curl -X POST http://localhost:5003/chat -H "Content-Type: application/json" -d '{"prompt": "Hello"}'
    curl http://localhost:5003/health
"""
import logging
import time
from datetime import datetime

import torch
from flask import Flask, jsonify, request
from transformers import AutoModelForCausalLM, AutoTokenizer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"
MODEL_LABEL = "qwen3-transformers"  # used in API responses
PORT = 5003

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load model at startup (once)
# ---------------------------------------------------------------------------
logger.info("Loading model '%s' ...", MODEL_NAME)
_tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
_model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    torch_dtype=torch.float16,
    device_map="auto",   # requires `accelerate`; spreads across available GPUs/CPU
)
_model.eval()
logger.info("Model ready on device: %s", next(_model.parameters()).device)

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helper: run inference
# ---------------------------------------------------------------------------
def _generate(prompt: str, max_new_tokens: int = 256) -> tuple[str, float]:
    """Tokenize prompt, run model.generate, decode and return (text, duration_ms)."""
    # Build a chat-style message list and apply the chat template
    messages = [{"role": "user", "content": prompt}]
    formatted = _tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )

    # Tokenize and move to model's device
    inputs = _tokenizer(formatted, return_tensors="pt").to(next(_model.parameters()).device)

    start = time.perf_counter()
    with torch.no_grad():
        output_ids = _model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=True,
            temperature=0.7,
            top_p=0.9,
            pad_token_id=_tokenizer.eos_token_id,
        )
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Decode only the newly generated tokens (exclude the input)
    new_ids = output_ids[0][inputs["input_ids"].shape[-1]:]
    response_text = _tokenizer.decode(new_ids, skip_special_tokens=True).strip()

    return response_text, elapsed_ms


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
        response_text, duration_ms = _generate(prompt)
    except Exception as exc:
        logger.error("Generation failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    logger.info("Done    | duration_ms=%.1f", duration_ms)

    return jsonify({
        "model": MODEL_LABEL,
        "response": response_text,
        "duration_ms": round(duration_ms, 1),
    })


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    """Return device info so callers can confirm GPU/CPU usage."""
    device = str(next(_model.parameters()).device)

    device_info = {"device": device}
    if torch.cuda.is_available():
        # Report the name of every visible GPU
        device_info["gpus"] = [
            torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())
        ]
        device_info["vram_allocated_mb"] = round(
            torch.cuda.memory_allocated() / 1024 ** 2, 1
        )

    return jsonify({
        "status": "ok",
        "model": MODEL_LABEL,
        "hf_model": MODEL_NAME,
        **device_info,
    })


if __name__ == "__main__":
    logger.info("Starting %s server on port %d", MODEL_LABEL, PORT)
    app.run(host="0.0.0.0", port=PORT)
