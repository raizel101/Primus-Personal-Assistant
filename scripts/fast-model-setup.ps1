$ErrorActionPreference = 'Stop'
$rootPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$modelsPath = Join-Path $rootPath 'models\ollama'
$tempPath = Join-Path $rootPath 'temp'
$config = Get-Content -LiteralPath (Join-Path $rootPath 'config.json') -Raw -Encoding utf8 | ConvertFrom-Json
if ([IO.Path]::GetFullPath([string]$config.root).TrimEnd('\') -ne [IO.Path]::GetFullPath($rootPath).TrimEnd('\')) {
    throw 'The PRIMUS root in config.json must match this project folder.'
}
New-Item -ItemType Directory -Force -Path $modelsPath, $tempPath | Out-Null
$env:OLLAMA_HOST = '127.0.0.1:11435'
$env:OLLAMA_MODELS = $modelsPath
$env:TEMP = $tempPath
$env:TMP = $tempPath
try {
    $models = (Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/tags' -TimeoutSec 5).models
} catch {
    throw 'The private PRIMUS model server is unavailable. Run scripts\vision-setup.ps1 first.'
}
if ('qwen3:1.7b' -notin @($models | ForEach-Object name)) {
    $ollamaCommand = Get-Command ollama.exe -ErrorAction Stop
    & $ollamaCommand.Source pull 'qwen3:1.7b'
    if ($LASTEXITCODE -ne 0) { throw 'Fast model download failed' }
}
$models = (Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/tags' -TimeoutSec 5).models
if ('qwen3:1.7b' -notin @($models | ForEach-Object name)) { throw 'Fast model could not be verified' }
Write-Output "PRIMUS fast model ready at 127.0.0.1:11435; files: $modelsPath"
