"""
Adapter Merge and GGUF Quantization Pipeline.
1. Reloads base Llama 2 in FP16 (never merges into 4-bit quantized weights directly).
2. Merges LoRA adapter into base weights.
3. Exports merged FP16 model.
4. Generates instructions/script to run llama.cpp convert and quantize to Q4_K_M.
"""

import os
import torch
import argparse
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

def merge_lora_weights(
    base_model_path: str,
    adapter_path: str,
    output_merged_path: str,
    hf_token: str = None
):
    print(f"Loading base model in FP16 from: {base_model_path}")
    base_model = AutoModelForCausalLM.from_pretrained(
        base_model_path,
        torch_dtype=torch.float16,
        device_map="cpu",  # CPU merge avoids CUDA OOM during 14 GB FP16 unquantized load
        token=hf_token,
        trust_remote_code=True
    )
    tokenizer = AutoTokenizer.from_pretrained(base_model_path, token=hf_token)

    print(f"Loading LoRA adapter from: {adapter_path}")
    model = PeftModel.from_pretrained(base_model, adapter_path)

    print("Merging adapter weights into base model (merge_and_unload)...")
    merged_model = model.merge_and_unload()

    print(f"Saving merged standalone FP16 model to: {output_merged_path}")
    os.makedirs(output_merged_path, exist_ok=True)
    merged_model.save_pretrained(output_merged_path)
    tokenizer.save_pretrained(output_merged_path)
    print("Merge complete.")

    print("\n--- Next Step: Convert to GGUF using llama.cpp ---")
    print("Run the following shell commands:")
    print(f"1. python llama.cpp/convert_hf_to_gguf.py {output_merged_path} --outfile {output_merged_path}/model-f16.gguf --outtype f16")
    print(f"2. llama.cpp/llama-quantize {output_merged_path}/model-f16.gguf {output_merged_path}/mlritm-llama2-7b-q4_k_m.gguf Q4_K_M")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge LoRA and prepare for GGUF export")
    parser.add_argument("--base_model", type=str, default="meta-llama/Llama-2-7b-chat-hf")
    parser.add_argument("--adapter_path", type=str, default="./models/llama2-7b-mlritm-adapter")
    parser.add_argument("--output_merged_path", type=str, default="./models/llama2-7b-mlritm-merged-fp16")
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN", None)
    merge_lora_weights(args.base_model, args.adapter_path, args.output_merged_path, token)
