import torch
import json
from torch.utils.data import DataLoader, Dataset as TorchDataset
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer, DataCollatorForLanguageModeling
from peft import LoraConfig, get_peft_model

base = "Qwen/Qwen2.5-0.5B-Instruct"
tok = AutoTokenizer.from_pretrained(base)
tok.pad_token = tok.eos_token
model = AutoModelForCausalLM.from_pretrained(base, torch_dtype="auto", device_map="cpu")

lora = LoraConfig(r=16, lora_alpha=16, target_modules=["q_proj", "v_proj", "up_proj", "down_proj"], task_type="CAUSAL_LM", lora_dropout=0.05)
model = get_peft_model(model, lora)

# Load data
train_data = [json.loads(line) for line in open("openhome_train.jsonl")]
val_data = [json.loads(line) for line in open("openhome_val.jsonl")]

def fmt_data(data):
    texts = []
    for e in data:
        text = tok.apply_chat_template(e["messages"], tokenize=False)
        texts.append(text)
    
    tokens = tok(texts, truncation=True, max_length=2048, padding=True, return_tensors="pt")
    tokens["labels"] = tokens["input_ids"].clone()
    return tokens

train_tokens = fmt_data(train_data)
val_tokens = fmt_data(val_data)

class SimpleDataset(TorchDataset):
    def __init__(self, tokens):
        self.input_ids = tokens["input_ids"]
        self.attention_mask = tokens["attention_mask"]
        self.labels = tokens["labels"]
    
    def __len__(self):
        return len(self.input_ids)
    
    def __getitem__(self, idx):
        return {
            "input_ids": self.input_ids[idx],
            "attention_mask": self.attention_mask[idx],
            "labels": self.labels[idx]
        }

train_ds = SimpleDataset(train_tokens)
val_ds = SimpleDataset(val_tokens)

trainer = Trainer(
    model=model,
    args=TrainingArguments(
        output_dir="openhome-model",
        num_train_epochs=3,
        per_device_train_batch_size=2,
        gradient_accumulation_steps=2,
        learning_rate=2e-4,
        warmup_steps=10,
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        seed=42,
        disable_tqdm=False
    ),
    train_dataset=train_ds,
    eval_dataset=val_ds,
    data_collator=DataCollatorForLanguageModeling(tok, mlm=False),
)

print("Starting training with Qwen 0.5B...")
trainer.train()

model.save_pretrained("openhome-model/lora")
tok.save_pretrained("openhome-model/lora")
print("Done!")
