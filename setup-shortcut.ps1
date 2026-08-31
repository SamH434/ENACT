# ENACT desktop shortcut installer
$ErrorActionPreference = "Stop"

# resolve paths relative to this script's own location so it works regardless
# of where the user cloned the repo
$repoRoot = $PSScriptRoot
$target   = Join-Path $repoRoot "enact.vbs"
$icon     = Join-Path $repoRoot "docs\assets\enact_taskbar.ico"
$desktop  = [Environment]::GetFolderPath("Desktop")
$shortcut = Join-Path $desktop "ENACT.lnk"

if (-not (Test-Path $target)) {
    Write-Error "enact.vbs not found at: $target"
    exit 1
}
if (-not (Test-Path $icon)) {
    Write-Error "Icon not found at: $icon"
    exit 1
}

$wsh  = New-Object -ComObject WScript.Shell
$link = $wsh.CreateShortcut($shortcut)
$link.TargetPath       = $target
$link.WorkingDirectory = $repoRoot
$link.IconLocation     = $icon
$link.Description      = "ENACT: Engine for Network Anomaly Condition and Telemetry"
$link.Save()

Write-Host ""
Write-Host "Created desktop shortcut: $shortcut" -ForegroundColor Green
Write-Host "Icon:   $icon"
Write-Host "Target: $target"
Write-Host ""
Write-Host "You can now double click the ENACT shortcut on your desktop to launch."