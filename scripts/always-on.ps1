param(
    [ValidateSet('Status', 'Enable', 'Disable')][string]$Mode = 'Status',
    [switch]$Apply,
    [string]$Pythonw = ''
)
$ErrorActionPreference = 'Stop'
$taskName = 'PRIMUS Always On'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$config = Get-Content -LiteralPath (Join-Path $root 'config.json') -Raw -Encoding utf8 | ConvertFrom-Json
if ([IO.Path]::GetFullPath([string]$config.root).TrimEnd('\') -ne [IO.Path]::GetFullPath($root).TrimEnd('\')) {
    throw 'The PRIMUS root in config.json must match this project folder.'
}
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if ($Mode -eq 'Status') {
    if ($existing) {
        Write-Output "Task: $taskName"
        Write-Output "State: $($existing.State)"
        Write-Output "Executable: $($existing.Actions[0].Execute)"
        Write-Output "Arguments: $($existing.Actions[0].Arguments)"
    } else {
        Write-Output "Task: $taskName (not registered)"
    }
    $statusFile = Join-Path $root 'data\voice-status.json'
    if (Test-Path -LiteralPath $statusFile) {
        Write-Output ('Voice: ' + (Get-Content -LiteralPath $statusFile -Raw -Encoding utf8))
    }
    return
}

if ($Mode -eq 'Disable') {
    Write-Output "Remove only the current-user scheduled task '$taskName'. A currently running tray/voice process will keep running until stopped from the tray."
    if (-not $Apply) { Write-Output 'Preview only. Add -Apply to make this change.'; return }
    if ($existing) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
    Write-Output "Sign-in startup removed for '$taskName'."
    return
}

if (-not $config.tray.auto_start_voice) { throw 'Set tray.auto_start_voice to true before enabling sign-in listening.' }
if (-not $Pythonw) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe'
    if (Test-Path -LiteralPath $bundled -PathType Leaf) { $Pythonw = $bundled }
}
if (-not $Pythonw) {
    $installed = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($installed) { $Pythonw = $installed.Source }
}
if (-not (Test-Path -LiteralPath $Pythonw -PathType Leaf)) { throw "Pythonw not found: $Pythonw" }
$Pythonw = (Resolve-Path -LiteralPath $Pythonw).Path
$script = Join-Path $root 'primus.py'
$arguments = '"' + $script + '" tray'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Write-Output "At sign-in for $user, run: '$Pythonw' $arguments"
Write-Output "Working directory: '$root'"
Write-Output 'Task: current-user, limited privileges, no network requirement, battery operation allowed, at most one task instance.'
Write-Output 'Effect: PRIMUS tray, localhost API, scheduler, and microphone wake listening start in the signed-in desktop session.'
Write-Output 'PRIMUS files remain under its root; Windows stores the task definition in Task Scheduler settings.'
if (-not $Apply) { Write-Output 'Preview only. Add -Apply to register and start the task now.'; return }

if ($existing) {
    if ($existing.Actions.Count -ne 1 -or $existing.Actions[0].Execute -ne $Pythonw -or $existing.Actions[0].Arguments -ne $arguments) {
        throw "A different task named '$taskName' already exists; inspect it manually."
    }
    Write-Output "Matching task '$taskName' already registered."
} else {
    $action = New-ScheduledTaskAction -Execute $Pythonw -Argument $arguments -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
        -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Description 'Start local PRIMUS tray and offline wake listening at user sign-in.' | Out-Null
    Write-Output "Registered '$taskName'."
}
Start-ScheduledTask -TaskName $taskName
Write-Output 'Started task now; check voice status after a few seconds.'
