#!/bin/bash
# openHome AI setup — installs Ollama + pulls Llama 3.1 1B on Raspberry Pi
# Run once after flashing Pi OS

set -e

echo "=== openHome AI Setup ==="
echo "Installing Ollama..."

curl -fsSL https://ollama.com/install.sh | sh

echo "Starting Ollama service..."
sudo systemctl enable ollama
sudo systemctl start ollama
sleep 3

echo "Pulling Llama 3.1 1B model..."
echo "(this will take a few minutes on Pi's SD card)"
ollama pull llama3.1:1b

echo ""
echo "Testing inference..."
echo '{"model":"llama3.1:1b","prompt":"Reply with only: OK","stream":false}' \
  | curl -s -X POST http://localhost:11434/api/generate -d @- \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Model response:', d.get('response','').strip())"

echo ""
echo "=== Setup complete ==="
echo "Run the hub with: python3 hub/server.py"
echo ""
echo "OPTIONAL: To use your own fine-tuned model from HuggingFace:"
echo "  ollama create openhome -f Modelfile"
echo "  Then set MODEL_NAME = 'openhome' in ai_brain.py"
