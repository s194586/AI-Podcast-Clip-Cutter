param(
    [switch]$Reset,
    [switch]$AllowDemo,
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$ArgsList = @("-m", "apps.api.tools.import_local_project")
if ($Reset) {
    $ArgsList += "--reset"
}
if ($AllowDemo) {
    $ArgsList += "--allow-demo"
}
if ($ProjectRoot) {
    $ArgsList += @("--project-root", $ProjectRoot)
}

Set-Location $RepoRoot
& $Python @ArgsList
