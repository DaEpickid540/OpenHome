"""
openHome Model Fine-Tuning (Unsloth + LoRA)
────────────────────────────────────────────
Fine-tunes a small base model (Qwen2.5-0.5B or Llama-3.2-1B) to be the
openHome AI brain, using the JSONL data from generate_training_data.py.

Hardware note (Sarvin's rig — RX 6700 XT / Ryzen 7 5800X3D):
  Unsloth officially targets CUDA. On AMD you have two realistic paths:
    A) Run this on a CUDA box / Colab (free T4 works fine for 0.5B-1B), OR
    B) Use the ROCm fork of Unsloth, OR
    C) Swap to plain `transformers + peft` (a fallback block is at the bottom).
  For a 0.5B model, Colab's free T4 trains this dataset in ~10-15 min.

Install (CUDA):
  pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
  pip install --no-deps trl peft accelerate bitsandbytes

Run:
  python3 train_unsloth.py
"""

from unsloth import FastLanguageModel
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments
import torch

# ── CONFIG ────────────────────────────────────────────────
# Pick your base. Qwen 0.5B = smallest/fastest. Llama 3.2 1B = better quality.
MODEL = "unsloth/Qwen2.5-0.5B-Instruct"   # or "unsloth/Llama-3.2-1B-Instruct"
MAX_SEQ_LEN = 2048
OUTPUT_DIR  = "openhome-model"

# ── LOAD MODEL ────────────────────────────────────────────
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name     = MODEL,
    max_seq_length = MAX_SEQ_LEN,
    dtype          = None,      # auto-detect (bf16 on Ampere+, fp16 otherwise)
    load_in_4bit   = True,      # fits easily in 12GB VRAM
)

# ── ADD LoRA ADAPTERS ─────────────────────────────────────
model = FastLanguageModel.get_peft_model(
    model,
    r              = 16,        # LoRA rank — 16 is plenty for this task
    lora_alpha     = 16,
    lora_dropout   = 0,
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
                      "gate_proj", "up_proj", "down_proj"],
    bias           = "none",
    use_gradient_checkpointing = "unsloth",
    random_state   = 42,
)

# ── LOAD DATA ─────────────────────────────────────────────
dataset = load_dataset("json", data_files={
    "train": "dataset/openhome_train.jsonl",
    "validation": "dataset/openhome_val.jsonl"
})

def format_chat(example):
    """Apply the model's chat template to the messages."""
    text = tokenizer.apply_chat_template(
        example["messages"], tokenize=False, add_generation_prompt=False)
    return {"text": text}

dataset = dataset.map(format_chat)

# ── TRAIN ─────────────────────────────────────────────────
trainer = SFTTrainer(
    model           = model,
    tokenizer       = tokenizer,
    train_dataset   = dataset["train"],
    eval_dataset    = dataset["validation"],
    dataset_text_field = "text",
    max_seq_length  = MAX_SEQ_LEN,
    args = TrainingArguments(
        per_device_train_batch_size = 4,
        gradient_accumulation_steps = 4,   # effective batch = 16
        warmup_steps    = 10,
        num_train_epochs = 3,              # 3 epochs is good for 600 examples
        learning_rate   = 2e-4,
        fp16            = not torch.cuda.is_bf16_supported(),
        bf16            = torch.cuda.is_bf16_supported(),
        logging_steps   = 10,
        eval_strategy   = "epoch",
        optim           = "adamw_8bit",
        weight_decay    = 0.01,
        lr_scheduler_type = "linear",
        seed            = 42,
        output_dir      = OUTPUT_DIR,
        save_strategy   = "epoch",
    ),
)

print("Starting training...")
trainer.train()

# ── SAVE ──────────────────────────────────────────────────
# 1) LoRA adapters only (small)
model.save_pretrained(f"{OUTPUT_DIR}/lora")
tokenizer.save_pretrained(f"{OUTPUT_DIR}/lora")

# 2) Merged + GGUF for Ollama on the Pi
model.save_pretrained_gguf(f"{OUTPUT_DIR}/gguf", tokenizer, quantization_method="q4_k_m")

print(f"""
Done! Outputs:
  {OUTPUT_DIR}/lora/   — LoRA adapters (for HuggingFace upload)
  {OUTPUT_DIR}/gguf/   — q4_k_m GGUF (for Ollama on the Pi)

Next steps:
  1. Test:    ollama create openhome -f Modelfile  (point FROM at the .gguf)
  2. Upload:  huggingface-cli upload <your-username>/openhome-llm {OUTPUT_DIR}/lora
  3. Deploy:  set MODEL_NAME = "openhome" in hub/ai_brain.py
""")

# ──────────────────────────────────────────────────────────
# FALLBACK: plain transformers + peft (if Unsloth won't run on your AMD card)
# ──────────────────────────────────────────────────────────
"""
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
from peft import LoraConfig, get_peft_model
from trl import SFTTrainer
from datasets import load_dataset

base = "Qwen/Qwen2.5-0.5B-Instruct"
tok = AutoTokenizer.from_pretrained(base)
model = AutoModelForCausalLM.from_pretrained(base, torch_dtype="auto", device_map="auto")

lora = LoraConfig(r=16, lora_alpha=16, target_modules=["q_proj","v_proj"], task_type="CAUSAL_LM")
model = get_peft_model(model, lora)

ds = load_dataset("json", data_files={"train":"dataset/openhome_train.jsonl"})
def fmt(e): return {"text": tok.apply_chat_template(e["messages"], tokenize=False)}
ds = ds.map(fmt)

SFTTrainer(model=model, tokenizer=tok, train_dataset=ds["train"],
           dataset_text_field="text", max_seq_length=2048,
           args=TrainingArguments(output_dir="openhome-model", num_train_epochs=3,
               per_device_train_batch_size=2, learning_rate=2e-4)).train()
model.save_pretrained("openhome-model/lora")
"""
