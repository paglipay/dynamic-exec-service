"""Flask server loading deepseek-ai/DeepSeek-R1-Distill-Qwen-7B via HuggingFace transformers.

Requires:
    pip install flask torch transformers accelerate

Usage:
    python deepseek_transformers_server.py
    curl -X POST http://localhost:5004/chat -H "Content-Type: application/json" -d '{"prompt": "Hello"}'
    curl http://localhost:5004/health
"""
import logging
import re
import time
from datetime import datetime

import torch
from flask import Flask, jsonify, request
from transformers import AutoModelForCausalLM, AutoTokenizer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL_NAME = "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
MODEL_LABEL = "deepseek-transformers"  # used in API responses
PORT = 5004

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Regex to extract the <think>...</think> reasoning block
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

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
# Helper: run inference and split reasoning from response
# ---------------------------------------------------------------------------
def _generate(prompt: str, max_new_tokens: int = 512) -> tuple[str, str, float]:
    """Return (response_text, reasoning_text, duration_ms).

    DeepSeek-R1 wraps chain-of-thought in <think>...</think>.
    reasoning_text contains that block (stripped); response_text has it removed.
    """
    # Apply the chat template if the tokenizer supports it, otherwise use raw prompt
    if hasattr(_tokenizer, "apply_chat_template") and _tokenizer.chat_template:
        messages = [{"role": "user", "content": prompt}]
        formatted = _tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    else:
        formatted = prompt

    inputs = _tokenizer(formatted, return_tensors="pt").to(next(_model.parameters()).device)

    start = time.perf_counter()
    with torch.no_grad():
        output_ids = _model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=True,
            temperature=0.6,   # lower temp encourages tighter reasoning
            top_p=0.9,
            pad_token_id=_tokenizer.eos_token_id,
        )
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Decode only the newly generated tokens
    new_ids = output_ids[0][inputs["input_ids"].shape[-1]:]
    raw = _tokenizer.decode(new_ids, skip_special_tokens=True).strip()

    # Split out <think> block if present
    match = _THINK_RE.search(raw)
    reasoning = match.group(1).strip() if match else ""
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
        response_text, reasoning, duration_ms = _generate(prompt)
    except Exception as exc:
        logger.error("Generation failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    logger.info("Done    | duration_ms=%.1f | reasoning_len=%d", duration_ms, len(reasoning))

    result = {
        "model": MODEL_LABEL,
        "response": response_text,
        "duration_ms": round(duration_ms, 1),
    }
    if reasoning:
        result["reasoning"] = reasoning

    return jsonify(result)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    """Return device info so callers can confirm GPU/CPU usage."""
    device = str(next(_model.parameters()).device)

    device_info = {"device": device}
    if torch.cuda.is_available():
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
