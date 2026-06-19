"""
OpenHome overnight CPU training script.
Run via run_overnight.py — reads hyperparams from env vars.
Fixes: correct dataset paths, left-side truncation (preserves assistant response),
       lazy per-item tokenization, proper HF token usage.
"""

import os, json, time, logging
from pathlib import Path
import torch
from torch.utils.data import Dataset
from transformers import (
    AutoModelForCausalLM, AutoTokenizer,
    TrainingArguments, Trainer,
    DataCollatorForLanguageModeling,
)
from peft import LoraConfig, get_peft_model

EPOCHS   = int(os.getenv("OHM_EPOCHS",  "2"))
LR       = float(os.getenv("OHM_LR",    "2e-4"))
MAX_LEN  = int(os.getenv("OHM_MAX_LEN", "1024"))
ATTEMPT  = int(os.getenv("OHM_ATTEMPT", "1"))

HF_TOKEN   = os.getenv("HF_TOKEN", "")
BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
TRAIN_FILE = "dataset/openhome_train.jsonl"
VAL_FILE   = "dataset/openhome_val.jsonl"
OUTPUT_DIR = "openhome-model"
LORA_DIR   = f"{OUTPUT_DIR}/lora"

# ── LOGGING ───────────────────────────────────────────────
log_file = f"training_attempt_{ATTEMPT}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

logger.info(f"=== OpenHome Training Attempt {ATTEMPT} ===")
logger.info(f"  epochs={EPOCHS}  lr={LR}  max_len={MAX_LEN}")
logger.info(f"  base={BASE_MODEL}  device=cpu  threads={torch.get_num_threads()}")

# ── TOKENIZER ─────────────────────────────────────────────
logger.info("Loading tokenizer...")
tok = AutoTokenizer.from_pretrained(BASE_MODEL, token=HF_TOKEN or None)
tok.pad_token = tok.eos_token
tok.truncation_side = "left"  # keep assistant response when truncating long contexts

# ── MODEL ─────────────────────────────────────────────────
logger.info("Loading base model (first run downloads ~1 GB)...")
t0 = time.time()
model = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL,
    torch_dtype=torch.float32,
    device_map="cpu",
    token=HF_TOKEN or None,
)
logger.info(f"Model loaded in {time.time()-t0:.1f}s")

# ── LORA ──────────────────────────────────────────────────
logger.info("Applying LoRA adapters...")
lora_cfg = LoraConfig(
    r=16, lora_alpha=16,
    target_modules=["q_proj", "v_proj", "up_proj", "down_proj"],
    task_type="CAUSAL_LM",
    lora_dropout=0.05,
    bias="none",
)
model = get_peft_model(model, lora_cfg)
model.print_trainable_parameters()

# ── DATASET ───────────────────────────────────────────────
class ChatDataset(Dataset):
    def __init__(self, path):
        self.data = [json.loads(l) for l in open(path, encoding="utf-8")]
        logger.info(f"  Loaded {len(self.data)} examples from {path}")

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        text = tok.apply_chat_template(
            self.data[idx]["messages"],
            tokenize=False,
            add_generation_prompt=False,
        )
        enc = tok(text, truncation=True, max_length=MAX_LEN, return_tensors="pt")
        return {
            "input_ids":      enc["input_ids"][0],
            "attention_mask": enc["attention_mask"][0],
        }

train_ds = ChatDataset(TRAIN_FILE)
val_ds   = ChatDataset(VAL_FILE)

# ── QUICK STEP BENCHMARK ──────────────────────────────────
logger.info("Timing one forward pass to estimate total duration...")
model.eval()
with torch.no_grad():
    sample = train_ds[0]
    t0 = time.time()
    model(
        input_ids=sample["input_ids"].unsqueeze(0),
        labels=sample["input_ids"].unsqueeze(0),
    )
step_sec = time.time() - t0
# forward ~ 1/3 of forward+backward; multiply by 3 for training step
est_step = step_sec * 3
est_hours = est_step * len(train_ds) * EPOCHS / 3600
logger.info(f"  Estimated step time: ~{est_step:.0f}s -> total: ~{est_hours:.1f}h")
model.train()

# ── TRAIN ─────────────────────────────────────────────────
trainer = Trainer(
    model=model,
    args=TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        learning_rate=LR,
        warmup_steps=20,
        logging_steps=25,
        eval_strategy="epoch",
        save_strategy="epoch",
        seed=42,
        dataloader_num_workers=0,
        dataloader_pin_memory=False,
        report_to="none",
        fp16=False,
        bf16=False,
    ),
    train_dataset=train_ds,
    eval_dataset=val_ds,
    data_collator=DataCollatorForLanguageModeling(tok, mlm=False),
)

logger.info("Starting training...")
t_start = time.time()
trainer.train()
elapsed = (time.time() - t_start) / 3600
logger.info(f"Training complete in {elapsed:.2f}h")

# ── SAVE ──────────────────────────────────────────────────
logger.info(f"Saving LoRA adapters to {LORA_DIR}/")
Path(LORA_DIR).mkdir(parents=True, exist_ok=True)
model.save_pretrained(LORA_DIR)
tok.save_pretrained(LORA_DIR)
logger.info("Done.")
