"""Local LLM inference — accepts prompts via CLI arg or stdin.

Usage:
    python run_llm.py --prompt "Your question here"
    echo "Your question here" | python run_llm.py

The prompt is NEVER hardcoded here. Pass it each time via --prompt or stdin.
This keeps the script stable so the bot never needs to rewrite it.
"""
import argparse
import sys

from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_NAME = 'gpt2-medium'

def load_model():
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
    return tokenizer, model

def generate_text(prompt: str, tokenizer, model, max_new_tokens: int = 80) -> str:
    inputs = tokenizer(prompt, return_tensors='pt')
    outputs = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=True,
        temperature=0.8,
        top_p=0.95,
        pad_token_id=tokenizer.eos_token_id,
    )
    generated_ids = outputs[0][inputs['input_ids'].shape[-1]:]
    return tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run a local LLM inference.')
    parser.add_argument('--prompt', type=str, default=None, help='Prompt text')
    parser.add_argument('--max-new-tokens', type=int, default=80)
    args = parser.parse_args()

    # Accept prompt from --prompt arg or stdin pipe
    if args.prompt:
        prompt = args.prompt.strip()
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        print("Error: provide a prompt via --prompt or stdin.", file=sys.stderr)
        sys.exit(1)

    if not prompt:
        print("Error: prompt is empty.", file=sys.stderr)
        sys.exit(1)

    tokenizer, model = load_model()
    response = generate_text(prompt, tokenizer, model, args.max_new_tokens)
    print(f"Prompt:   {prompt}")
    print(f"Response: {response}")
