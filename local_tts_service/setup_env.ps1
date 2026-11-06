# 建立單一 backend 的獨立 venv；加 -Download 才下載模型到 models\。
# 用法：powershell -ExecutionPolicy Bypass -File setup_env.ps1 -Backend qwen3tts [-Download]
param(
    [Parameter(Mandatory = $true)][ValidateSet('qwen3tts', 'omnivoice')][string]$Backend,
    [switch]$Download
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root ".venv-$Backend"
$py = Join-Path $venv 'Scripts\python.exe'

# PowerShell 5.1 的 ErrorActionPreference 不管外部程式的結束碼，每一步自己檢查
function Invoke-Py { & $py @args; if ($LASTEXITCODE -ne 0) { throw "失敗（exit $LASTEXITCODE）：python $args" } }

if (-not (Test-Path $py)) { py -3.11 -m venv $venv }
Invoke-Py -m pip install --upgrade pip
# 先從 cu128 index 裝 torch；全域 pip 設定可能指向只有 CPU 版的鏡像
Invoke-Py -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
Invoke-Py -m pip install -r (Join-Path $root "requirements-$Backend.txt")
Invoke-Py -c "import torch, sys; print('torch', torch.__version__, 'cuda', torch.version.cuda); sys.exit(0 if torch.cuda.is_available() else 1)"
& $py -m pip freeze | Set-Content (Join-Path $root "requirements-$Backend.lock.txt") -Encoding utf8

if ($Download) {
    # Base 模型已內含 speech_tokenizer/，不另下載 Qwen3-TTS-Tokenizer-12Hz
    $repos = @{
        qwen3tts  = @('Qwen/Qwen3-TTS-12Hz-0.6B-Base', 'Qwen/Qwen3-TTS-12Hz-1.7B-Base', 'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign')
        omnivoice = @('k2-fsa/OmniVoice')
    }[$Backend]
    foreach ($repo in $repos) {
        $dest = Join-Path $root ("models\" + $repo.Split('/')[1])
        Invoke-Py -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1], local_dir=sys.argv[2])" $repo $dest
    }
}
