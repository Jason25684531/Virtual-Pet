VirtualPet Release deployment guide
===================================

1. Copy the whole VirtualPet_Release folder to the new machine (any path is fine; Chinese characters/spaces OK).
2. Copy .env.example to .env and fill in the API keys and the ComfyUI / Ollama URLs.
3. Runtime data to copy from the old machine (optional; skipping it = fresh start):
     data\pet_state.db
     data\characters\        (personal memory, per-character state)
     data\saves\             (saves)
     data\runtime\provider_config.json
     runtime_cache\qdrant\   (memory vector store; model cache can be re-downloaded)
     runtime_cache\whisper\  (STT model, ~1.6GB; skipping it = re-download on first launch)
4. External services (installed separately; not in this folder):
     - ComfyUI + checkpoints / LoRA / VAE (set the URL in .env COMFYUI_BASE_URL)
     - Ollama + models (OLLAMA_BASE_URL)
     - NVIDIA driver (STT uses CUDA; the CUDA runtime DLLs are already in nvidia\)
     - Playwright Chromium (music/news skills): on a machine with Python, run
         python -m playwright install chromium
       or copy %LOCALAPPDATA%\ms-playwright\ from the old machine
5. Double-click VirtualPet.exe to launch. Logs: logs\echoes.log, logs\stdout.log, logs\stderr.log

Notes
- assets\ is a hidden folder (character visuals); ComfyUI-generated assets are also written here.
- Skills, prompts, and ComfyUI workflows are encrypted and embedded in the binary; no plaintext files ship.
- ffmpeg\ffplay.exe is a GPLv3 build (gyan.dev); see ffmpeg\LICENSE.txt for the license.
