param([string]$Python = "", [switch]$InstallPhase2, [switch]$InstallVoice, [switch]$InstallBrowser)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if ($candidate) {
        try {
            & $candidate.Source -c "import sys; assert sys.version_info >= (3,11)" 2>$null
            if ($LASTEXITCODE -eq 0) { $Python = $candidate.Source }
        } catch {}
    }
}
if (-not $Python) {
    $bundled = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $bundled) { $Python = $bundled }
}
if (-not $Python) { throw "Python 3.11+ is required. Provide -Python with an existing Python executable." }
& $Python -c "import sys; assert sys.version_info >= (3,11)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11+ is required" }
& $Python (Join-Path $root "primus.py") init
if ($LASTEXITCODE -ne 0) { throw "PRIMUS initialization failed" }
if ($InstallPhase2) {
    $answer = Read-Host "Install pinned keyring, pystray, pynput, Pillow into '$root\vendor' using PyPI? This downloads packages but writes cache/temp under the PRIMUS root. Type YES"
    if ($answer -cne "YES") { throw "Phase 2 dependency install not approved" }
    $env:PIP_CACHE_DIR = Join-Path $root "temp\pip-cache"
    $env:TEMP = Join-Path $root "temp"
    $env:TMP = $env:TEMP
    $env:PYTHONPYCACHEPREFIX = Join-Path $root "temp\pycache"
    & $Python -m pip --disable-pip-version-check install --target (Join-Path $root "vendor") -r (Join-Path $root "requirements-phase2.txt")
    if ($LASTEXITCODE -ne 0) { throw "Phase 2 dependency install failed" }
}
if ($InstallVoice) {
    $answer = Read-Host "Install pinned faster-whisper, sounddevice, Piper and download the ~76 MB STT and ~63 MB TTS models into '$root'? Packages and caches stay under PRIMUS. Type YES"
    if ($answer -cne "YES") { throw "Phase 3 voice install not approved" }
    $env:PIP_CACHE_DIR = Join-Path $root "temp\pip-cache"
    $env:TEMP = Join-Path $root "temp"
    $env:TMP = $env:TEMP
    $env:PYTHONPYCACHEPREFIX = Join-Path $root "temp\pycache"
    $env:HF_HOME = Join-Path $root "temp\huggingface"
    $env:XDG_CACHE_HOME = Join-Path $root "temp\xdg-cache"
    & $Python -m pip --disable-pip-version-check install --target (Join-Path $root "vendor") -r (Join-Path $root "requirements-phase3.txt")
    if ($LASTEXITCODE -ne 0) { throw "Phase 3 dependency install failed" }
    $env:PYTHONPATH = Join-Path $root "vendor"
    & $Python (Join-Path $root "scripts\setup_voice_models.py")
    if ($LASTEXITCODE -ne 0) { throw "Phase 3 model download failed" }
    & $Python (Join-Path $root "primus.py") voice-doctor
    if ($LASTEXITCODE -ne 0) { throw "Voice check failed" }
}
if ($InstallBrowser) {
    $answer = Read-Host "Install pinned Playwright into '$root\vendor' from PyPI? This uses an existing Chrome installation and keeps pip cache/temp under PRIMUS. Type YES"
    if ($answer -cne "YES") { throw "Browser dependency install not approved" }
    $env:PIP_CACHE_DIR = Join-Path $root "temp\pip-cache"
    $env:TEMP = Join-Path $root "temp"
    $env:TMP = $env:TEMP
    $env:PYTHONPYCACHEPREFIX = Join-Path $root "temp\pycache"
    & $Python -m pip --disable-pip-version-check install --target (Join-Path $root "vendor") -r (Join-Path $root "requirements-browser.txt")
    if ($LASTEXITCODE -ne 0) { throw "Browser dependency install failed" }
}
& $Python (Join-Path $root "primus.py") doctor
if ($LASTEXITCODE -ne 0) { throw "PRIMUS doctor failed" }
