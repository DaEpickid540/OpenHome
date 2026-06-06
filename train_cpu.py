import torch
import json
from torch.utils.data import Dataset as TorchDataset
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer, DataCollatorForLanguageModeling
from peft import LoraConfig, get_peft_model

base = "Qwen/Qwen2.5-0.5B-Instruct"
tok = AutoTokenizer.from_pretrained(base)
tok.pad_token = tok.eos_token
model = AutoModelForCausalLM.from_pretrained(base, torch_dtype="auto", device_map="cpu")

lora = LoraConfig(r=16, lora_alpha=16, target_modules=["q_proj", "v_proj", "up_proj", "down_proj"], task_type="CAUSAL_LM", lora_dropout=0.05)
model = get_peft_model(model, lora)

train_data = [json.loads(line) for line in open("openhome_train.jsonl")]
val_data = [json.loads(line) for line in open("openhome_val.jsonl")]

class ChatDataset(TorchDataset):
    def __init__(self, data):
        self.data = data
    def __len__(self):
        return len(self.data)
    def __getitem__(self, idx):
        text = tok.apply_chat_template(self.data[idx]["messages"], tokenize=False)
        tokens = tok(text, truncation=True, max_length=512, return_tensors="pt")
        return {"input_ids": tokens["input_ids"][0], "attention_mask": tokens["attention_mask"][0]}

train_ds = ChatDataset(train_data)
val_ds = ChatDataset(val_data)

trainer = Trainer(
    model=model,
    args=TrainingArguments(
        output_dir="openhome-model",
        num_train_epochs=3,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        learning_rate=2e-4,
        warmup_steps=10,
        logging_steps=5,
        eval_strategy="epoch",
        save_strategy="epoch",
        seed=42,
        dataloader_pin_memory=False,
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
