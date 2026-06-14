from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

# GPT-J 6B model by EleutherAI
MODEL_NAME = "EleutherAI/gpt-j-6B"

def load_model():
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME, torch_dtype=torch.float16, revision='float16', low_cpu_mem_usage=True)
    # Move model to GPU if available
    if torch.cuda.is_available():
        model = model.to('cuda')
    else:
        print("Warning: CUDA GPU not available. Running on CPU will be slow and may require lots of RAM.")
    return tokenizer, model

def generate_text(prompt: str, tokenizer, model, max_new_tokens: int = 80) -> str:
    inputs = tokenizer(prompt, return_tensors='pt').to(model.device)
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
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Run GPT-J 6B local inference')
    parser.add_argument('--prompt', type=str, default=None, help='Prompt text')
    parser.add_argument('--max-new-tokens', type=int, default=80)
    args = parser.parse_args()

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
