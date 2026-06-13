from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

# Use a smarter larger model: GPT-2 medium
model_name = 'gpt2-medium'
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(model_name)

def generate_text(prompt: str) -> str:
    inputs = tokenizer(prompt, return_tensors='pt')
    outputs = model.generate(**inputs, max_length=100)
    text = tokenizer.decode(outputs[0], skip_special_tokens=True)
    return text

if __name__ == '__main__':
    # Demo prompt
    prompt = "What is the capital of France?"
    response = generate_text(prompt)
    print(f"Prompt: {prompt}")
    print(f"Response: {response}")
