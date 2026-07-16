Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$webRoot = Join-Path $repoRoot "apps\web"

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "$Name is required but was not found in PATH."
    }
}

Require-Command "node"
Require-Command "npm"

Write-Host "Node: $(node --version)"
Write-Host "npm:  $(npm --version)"

Set-Location $webRoot

if (-not (Test-Path -LiteralPath "node_modules")) {
    Write-Host "Installing frontend dependencies..."
    npm install
}

npm run dev
