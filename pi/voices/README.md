# Custom Voices for openHome

ARIA speaks through **Piper** — a fully local neural TTS that runs on the Pi.
You can use prebuilt voices or train your own from ~30 minutes of recordings.

## Quick start — use a prebuilt voice

Piper voices are hosted on Hugging Face. Drop one into `pi/voices/`:

```bash
mkdir -p pi/voices && cd pi/voices

# Default — "Amy", clear English female voice
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
```

100+ other voices at <https://huggingface.co/rhasspy/piper-voices>:
- `en_US-ryan-medium` — calm male
- `en_GB-northern_english_male-medium` — British accent
- `en_US-libritts-high` — very high quality, larger model
- Voices in 30+ other languages too

Then in the dashboard, set the voice via API:
```bash
curl -X POST http://PI:8765/users/<id> \
  -H "X-OpenHome-Key: ..." \
  -H "Content-Type: application/json" \
  -d '{"preferences":{"voice":"en_US-ryan-medium"}}'
```

Or per listener device, set it as a hub command:
```bash
curl -X POST http://PI:8765/command \
  -H "X-OpenHome-Key: ..." \
  -H "Content-Type: application/json" \
  -d '{"device_id":"listener_kitchen","voice":"en_US-ryan-medium"}'
```

## Train your own voice

You need:
- 30+ minutes of clean recordings of one person speaking (more = better)
- A transcript of every recording
- A CUDA GPU for training (Colab free tier works for small models)

### 1. Record

- Quiet room. Same mic the whole time. 22050 Hz mono WAV.
- Read 200+ short sentences (~10 seconds each = 30 min).
- Sources for sentence lists: the LJSpeech transcripts, Common Voice, or just pick a public-domain book and split it.
- Save files as `0001.wav`, `0002.wav`, etc.

### 2. Build the dataset

Create `metadata.csv`:
```
0001|This is the first recorded sentence.
0002|And here is the second one, with a different intonation.
0003|...
```

### 3. Train with Piper

```bash
pip install piper-train

python3 -m piper_train.preprocess \
  --language en-us \
  --input-dir my_recordings/ \
  --output-dir my_dataset/ \
  --dataset-format ljspeech \
  --single-speaker \
  --sample-rate 22050

python3 -m piper_train \
  --dataset-dir my_dataset/ \
  --accelerator gpu \
  --devices 1 \
  --batch-size 32 \
  --validation-split 0.0 \
  --num-test-examples 0 \
  --max_epochs 6000 \
  --checkpoint-epochs 1 \
  --precision 32
```

A small model on a single GPU takes 4–12 hours to sound usable.

### 4. Deploy

When done, export to ONNX and copy both files to `pi/voices/`:
```bash
python3 -m piper_train.export_onnx \
  my_dataset/lightning_logs/version_0/checkpoints/epoch=X.ckpt \
  my_voice.onnx
cp my_voice.onnx my_voice.onnx.json pi/voices/
```

Set `voice="my_voice"` in your listener config or as a per-user preference.

## Cloning from existing voices (alternative)

If you don't want to train from scratch, **piper-recording-studio** lets you
fine-tune a base voice with ~15 minutes of recordings. See the Piper docs:
<https://github.com/rhasspy/piper/tree/master/notebooks>

## Why not just use ElevenLabs?

- $5–22/month per voice
- Cloud-only — your voice data leaves your house
- Rate-limited
- Subscription can be cancelled

Piper voices are local, free, and yours. Quality is genuinely good — the
`libritts-high` voices are nearly indistinguishable from cloud TTS to most
ears, and they run at multiple-times real-time on a Pi 4.
