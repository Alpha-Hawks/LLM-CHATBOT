"""
QLoRA Fine-Tuning Script for Llama 2 7B on MLRITM Academic Advising Dataset.
Optimized for single 16 GB T4 GPU (Google Colab / Kaggle).
Uses 4-bit NF4 quantization, paged_adamw_8bit, and TRL SFTTrainer.
"""

import os
import torch
from dataclasses import dataclass, field
from typing import Optional
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
    HfArgumentParser
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer

@dataclass
class ModelArguments:
    model_name_or_path: str = field(
        default="meta-llama/Llama-2-7b-chat-hf",
        metadata={"help": "Path to base model or Hugging Face repo ID"}
    )
    use_auth_token: Optional[str] = field(
        default=os.environ.get("HF_TOKEN", None),
        metadata={"help": "Hugging Face access token for gated Llama 2 model"}
    )

@dataclass
class DataArguments:
    train_file: str = field(default="data/qa_dataset/train.jsonl")
    val_file: str = field(default="data/qa_dataset/val.jsonl")
    dataset_text_field: str = field(default="text")
    max_seq_length: int = field(default=1024)

@dataclass
class TrainingArgs(TrainingArguments):
    output_dir: str = field(default="./models/llama2-7b-mlritm-adapter")
    num_train_epochs: float = field(default=3.0)
    per_device_train_batch_size: int = field(default=2)
    gradient_accumulation_steps: int = field(default=8)
    learning_rate: float = field(default=2e-4)
    lr_scheduler_type: str = field(default="cosine")
    warmup_ratio: float = field(default=0.03)
    optim: str = field(default="paged_adamw_8bit")
    fp16: bool = field(default=True)
    bf16: bool = field(default=False)  # T4 does not support native bf16
    logging_steps: int = field(default=10)
    evaluation_strategy: str = field(default="steps")
    eval_steps: int = field(default=50)
    save_strategy: str = field(default="steps")
    save_steps: int = field(default=50)
    save_total_limit: int = field(default=2)
    gradient_checkpointing: bool = field(default=True)
    report_to: str = field(default="tensorboard")
    seed: int = field(default=42)


def main():
    parser = HfArgumentParser((ModelArguments, DataArguments, TrainingArgs))
    model_args, data_args, training_args = parser.parse_args_into_dataclasses()

    # 1. Configure 4-bit NF4 Quantization
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.float16
    )

    # 2. Load Tokenizer
    tokenizer = AutoTokenizer.from_pretrained(
        model_args.model_name_or_path,
        token=model_args.use_auth_token,
        trust_remote_code=True
    )
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    # 3. Load 4-bit Base Model
    model = AutoModelForCausalLM.from_pretrained(
        model_args.model_name_or_path,
        quantization_config=bnb_config,
        device_map="auto",
        token=model_args.use_auth_token,
        trust_remote_code=True
    )
    model = prepare_model_for_kbit_training(model)

    # 4. LoRA Configuration
    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        bias="none",
        task_type="CAUSAL_LM"
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # 5. Load Datasets
    data_files = {"train": data_args.train_file, "validation": data_args.val_file}
    dataset = load_dataset("json", data_files=data_files)

    # 6. Initialize SFTTrainer
    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        peft_config=lora_config,
        dataset_text_field=data_args.dataset_text_field,
        max_seq_length=data_args.max_seq_length,
        tokenizer=tokenizer,
        args=training_args
    )

    # 7. Train & Save LoRA Adapter
    print("Starting QLoRA Fine-Tuning on T4 GPU...")
    trainer.train()

    print(f"Saving fine-tuned adapter to {training_args.output_dir}")
    trainer.model.save_pretrained(training_args.output_dir)
    tokenizer.save_pretrained(training_args.output_dir)
    print("Training successfully completed.")


if __name__ == "__main__":
    main()
