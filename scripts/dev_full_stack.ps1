param(
    [switch]$OpenWindows
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$apiCommand = "Set-Location '$repoRoot'; .\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --port 8010"
$webCommand = "Set-Location '$repoRoot'; .\scripts\dev_web.ps1"

Write-Host "FastAPI backend:"
Write-Host "  $apiCommand"
Write-Host ""
Write-Host "React frontend:"
Write-Host "  $webCommand"
Write-Host ""
Write-Host "Backend URL: http://127.0.0.1:8010"
Write-Host "Vite URL:    printed by npm run dev"

if ($OpenWindows) {
    Start-Process powershell.exe -ArgumentList @("-NoExit", "-Command", $apiCommand) -WindowStyle Normal
    Start-Process powershell.exe -ArgumentList @("-NoExit", "-Command", $webCommand) -WindowStyle Normal
}
