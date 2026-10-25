param()
$ErrorActionPreference = 'Stop'
$rootPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$modelsPath = Join-Path $rootPath 'models\ollama'
$tempPath = Join-Path $rootPath 'temp'
$logsPath = Join-Path $rootPath 'logs'
New-Item -ItemType Directory -Force -Path $modelsPath, $tempPath, $logsPath | Out-Null
$ollamaCommand = Get-Command ollama.exe -ErrorAction Stop

$env:OLLAMA_HOST = '127.0.0.1:11435'
$env:OLLAMA_MODELS = $modelsPath
$env:OLLAMA_NO_CLOUD = '1'
$env:TEMP = $tempPath
$env:TMP = $tempPath

$occupied = $false
try {
    $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/tags' -TimeoutSec 2
    $occupied = $true
} catch {
    # A refused connection is expected before the private server starts.
}
if ($occupied) { throw 'Port 11435 is already in use. Stop that server before running this setup.' }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$serverOut = Join-Path $logsPath "vision-server-$stamp.out.log"
$serverErr = Join-Path $logsPath "vision-server-$stamp.err.log"
$server = Start-Process -FilePath $ollamaCommand.Source -ArgumentList 'serve' -WorkingDirectory $rootPath `
    -WindowStyle Hidden -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr -PassThru
$success = $false
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($server.HasExited) { throw "Local vision server exited; inspect $serverErr" }
        try {
            $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/tags' -TimeoutSec 2
            $ready = $true
            break
        } catch {}
    }
    if (-not $ready) { throw "Local vision server did not start; inspect $serverErr" }

    $models = (Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/tags' -TimeoutSec 5).models
    if ('qwen3-vl:4b' -notin @($models | ForEach-Object name)) {
        & $ollamaCommand.Source pull 'qwen3-vl:4b'
        if ($LASTEXITCODE -ne 0) { throw 'Vision model download failed' }
        $models = (Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/tags' -TimeoutSec 5).models
    }
    if ('qwen3-vl:4b' -notin @($models | ForEach-Object name)) {
        throw 'Vision model could not be verified on the private server'
    }
    $success = $true
    Write-Output "PRIMUS vision ready on 127.0.0.1:11435; model files: $modelsPath; server PID: $($server.Id)"
} finally {
    if (-not $success -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
}
