$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

Set-Location $RepoRoot
$Files = @(Get-ChildItem -Path "apps\review_agent" -Filter "*.py" | ForEach-Object { $_.FullName }) + @(
    "apps\api\main.py",
    "orchestration\airflow\pipeline_tasks.py",
    "orchestration\airflow\dags\podcast_pipeline_dag.py"
)

& $Python -m py_compile @Files
& $Python -m unittest discover -s tests
& $Python -m pip check
git diff --check
