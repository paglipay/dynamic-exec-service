"""Persistent local LLM inference server.

Starts a minimal HTTP server that loads the model ONCE and answers
POST /generate requests, so the bot never needs to reload or rewrite anything.

Usage (start once):
    python llm_server.py --port 5100

Query (from bot via query_server or curl):
    POST /generate   {"prompt": "Your question", "max_new_tokens": 80}
    GET  /health     -> {"status": "ok", "model": "gpt2-medium"}
    POST /shutdown   -> shuts down the server
"""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_NAME = 'gpt2-medium'

print(f"[llm_server] Loading model '{MODEL_NAME}'...", flush=True)
_tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
_model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
print("[llm_server] Model ready.", flush=True)


def _generate(prompt: str, max_new_tokens: int = 80) -> str:
    inputs = _tokenizer(prompt, return_tensors='pt')
    outputs = _model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=True,
        temperature=0.8,
        top_p=0.95,
        pad_token_id=_tokenizer.eos_token_id,
    )
    generated_ids = outputs[0][inputs['input_ids'].shape[-1]:]
    return _tokenizer.decode(generated_ids, skip_special_tokens=True).strip()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default access log noise
        pass

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length) if length else b'{}'
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self):
        if self.path == '/health':
            self._send_json(200, {'status': 'ok', 'model': MODEL_NAME})
        else:
            self._send_json(404, {'error': 'not found'})

    def do_POST(self):
        if self.path == '/generate':
            data = self._read_body()
            prompt = (data.get('prompt') or '').strip()
            if not prompt:
                self._send_json(400, {'error': 'prompt is required'})
                return
            max_new_tokens = int(data.get('max_new_tokens', 80))
            try:
                response = _generate(prompt, max_new_tokens)
                self._send_json(200, {'prompt': prompt, 'response': response})
            except Exception as exc:
                self._send_json(500, {'error': str(exc)})

        elif self.path == '/shutdown':
            self._send_json(200, {'status': 'shutting down'})
            # Schedule server shutdown after reply is flushed
            import threading
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        else:
            self._send_json(404, {'error': 'not found'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5100)
    parser.add_argument('--host', type=str, default='127.0.0.1')
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), _Handler)
    print(f"[llm_server] Listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    print("[llm_server] Stopped.", flush=True)
