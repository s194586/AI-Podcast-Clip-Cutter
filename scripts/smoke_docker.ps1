param(
    [string]$ProjectName = "podcast-cutter-demo-smoke",
    [int]$WebPort = 15173,
    [int]$ApiPort = 18010,
    [int]$AirflowPort = 18080
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $repoRoot "docker-compose.yml"
$smokeParent = Join-Path $repoRoot ".docker-smoke"
$smokeRoot = Join-Path $smokeParent $ProjectName
$dataRoot = Join-Path $smokeRoot "data"
$envFile = Join-Path $smokeRoot "smoke.env"
$customCaDirectory = Join-Path $repoRoot "orchestration\airflow\secrets\custom-ca"
$customCaDirectoryExisted = Test-Path -LiteralPath $customCaDirectory
$composeAttempted = $false

function Invoke-Docker {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Get-ServiceContainerId {
    param([Parameter(Mandatory = $true)][string]$Service)
    $containerId = "$(& docker @script:composeArgs ps --all --quiet $Service)".Trim()
    if ($LASTEXITCODE -ne 0 -or -not $containerId) {
        throw "Compose service '$Service' has no container."
    }
    return $containerId
}

function Assert-Healthy {
    param([Parameter(Mandatory = $true)][string]$Service)
    $containerId = Get-ServiceContainerId -Service $Service
    $state = (& docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" $containerId).Trim()
    if ($LASTEXITCODE -ne 0 -or $state -ne "healthy") {
        throw "Compose service '$Service' is not healthy (state: '$state')."
    }
    Write-Host "healthy: $Service"
}

if ($ProjectName -notmatch '^[a-z0-9][a-z0-9_-]*$') {
    throw "ProjectName may contain only lowercase letters, digits, underscores, and hyphens."
}

Invoke-Docker info --format "{{.ServerVersion}}"

New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$resolvedSmokeRoot = (Resolve-Path -LiteralPath $smokeRoot).Path
$expectedPrefix = $repoRoot.TrimEnd('\') + "\.docker-smoke\"
if (-not $resolvedSmokeRoot.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use smoke directory outside $expectedPrefix"
}

$relativeDataPath = ".docker-smoke/$ProjectName/data"
$envLines = @(
    "AIRFLOW_API_USERNAME=smoke_admin"
    "AIRFLOW_API_PASSWORD=$([guid]::NewGuid().ToString('N'))"
    "AIRFLOW_DB_PASSWORD=$([guid]::NewGuid().ToString('N'))"
    "AIRFLOW_JWT_SECRET=$([guid]::NewGuid().ToString('N'))$([guid]::NewGuid().ToString('N'))"
    "AIRFLOW_PORT=$AirflowPort"
    "APP_API_PORT=$ApiPort"
    "WEB_PORT=$WebPort"
    "APP_DATA_HOST_PATH=$relativeDataPath"
    "AIRFLOW_API_TIMEOUT_SECONDS=10"
    "CLIP_REVIEW_MODE=local_stub"
    "GEMINI_API_KEY="
    "GEMINI_MODEL=gemini-3.5-flash"
    "GEMINI_REQUEST_TIMEOUT_SECONDS=300"
    "GEMINI_BATCH_TIMEOUT_SECONDS=1800"
    "CUSTOM_CA_REQUIRED=false"
)
[System.IO.File]::WriteAllLines($envFile, $envLines, [System.Text.UTF8Encoding]::new($false))

$script:composeArgs = @(
    "compose",
    "--project-name", $ProjectName,
    "--env-file", $envFile,
    "--file", $composeFile
)

try {
    Invoke-Docker @composeArgs config --quiet
    $composeAttempted = $true
    Invoke-Docker @composeArgs up --build --detach --wait --wait-timeout 300
    Invoke-Docker @composeArgs ps

    foreach ($service in @(
        "postgres",
        "airflow-api-server",
        "airflow-scheduler",
        "airflow-dag-processor",
        "app-api",
        "frontend"
    )) {
        Assert-Healthy -Service $service
    }

    $initContainer = Get-ServiceContainerId -Service "airflow-init"
    $initExitCode = (& docker inspect --format "{{.State.ExitCode}}" $initContainer).Trim()
    if ($LASTEXITCODE -ne 0 -or $initExitCode -ne "0") {
        throw "airflow-init did not complete successfully (exit code: '$initExitCode')."
    }

    $frontend = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$WebPort/"
    if ($frontend.StatusCode -ne 200 -or $frontend.Content -notmatch '<div id="root"></div>') {
        throw "Frontend HTML smoke check failed."
    }

    $apiHealth = Invoke-RestMethod "http://127.0.0.1:$ApiPort/health"
    $proxiedHealth = Invoke-RestMethod "http://127.0.0.1:$WebPort/api/health"
    if ($apiHealth.status -ne "ok" -or $proxiedHealth.status -ne "ok") {
        throw "API health check failed."
    }
    if ($proxiedHealth.pipeline_orchestrator -ne "airflow") {
        throw "Frontend proxy did not reach the Airflow-configured API."
    }

    $payload = @{
        source_url = "https://example.com/docker-smoke"
        title = "Docker smoke project"
        auto_review = $false
        auto_start = $false
    } | ConvertTo-Json
    $created = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:$WebPort/api/projects" `
        -ContentType "application/json" `
        -Body $payload
    $projectId = [int]$created.project.id
    $projects = Invoke-RestMethod "http://127.0.0.1:$WebPort/api/projects"
    if ($projectId -notin @($projects.projects.id)) {
        throw "Created project was not returned through the frontend API proxy."
    }

    $spaRoute = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$WebPort/projects/$projectId"
    if ($spaRoute.StatusCode -ne 200 -or $spaRoute.Content -notmatch '<div id="root"></div>') {
        throw "Frontend SPA fallback check failed."
    }

    $airflowExec = @(
        "exec",
        "--no-TTY",
        "--user", "airflow",
        "--env", "HOME=/home/airflow",
        "airflow-scheduler"
    )
    $dagList = & docker @composeArgs @airflowExec airflow dags list --output json
    if ($LASTEXITCODE -ne 0 -or "$dagList" -notmatch "podcast_clip_pipeline") {
        throw "Airflow did not list podcast_clip_pipeline."
    }
    $importErrors = & docker @composeArgs @airflowExec airflow dags list-import-errors --output json
    if ($LASTEXITCODE -ne 0) {
        throw "Airflow DAG import-error check failed."
    }
    $importErrorsJson = @($importErrors | Where-Object { $_ -match '^\s*\[' }) |
        Select-Object -Last 1
    if (-not $importErrorsJson) {
        throw "Airflow DAG import-error check did not return JSON."
    }
    $parsedImportErrors = $importErrorsJson | ConvertFrom-Json
    if (@($parsedImportErrors).Count -ne 0) {
        throw "Airflow reported DAG import errors: $importErrors"
    }
    Write-Host "DAG_PARSE_OK podcast_clip_pipeline import_errors=0"

    Write-Host "SMOKE_OK frontend=$WebPort api=$ApiPort airflow=$AirflowPort project_id=$projectId"
}
catch {
    if ($composeAttempted) {
        Write-Warning "Smoke failed; collecting Compose status and service logs before cleanup."
        & docker @composeArgs ps --all
        foreach ($service in @(
            "airflow-dag-processor",
            "postgres",
            "airflow-init",
            "airflow-api-server",
            "airflow-scheduler",
            "app-api",
            "frontend"
        )) {
            $containerId = "$(& docker @composeArgs ps --quiet $service)".Trim()
            if ($containerId) {
                & docker inspect --format "{{json .State.Health}}" $containerId
            }
        }
        & docker @composeArgs logs --no-color airflow-dag-processor
        & docker @composeArgs logs --no-color airflow-init
    }
    throw
}
finally {
    if ($composeAttempted) {
        & docker @composeArgs down --volumes --remove-orphans
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Compose cleanup failed with exit code $LASTEXITCODE."
        }
    }
    if (Test-Path -LiteralPath $resolvedSmokeRoot) {
        Remove-Item -LiteralPath $resolvedSmokeRoot -Recurse -Force
    }
    if (
        -not $customCaDirectoryExisted -and
        (Test-Path -LiteralPath $customCaDirectory) -and
        -not (Get-ChildItem -Force -LiteralPath $customCaDirectory)
    ) {
        Remove-Item -LiteralPath $customCaDirectory -Force
    }
    if (
        (Test-Path -LiteralPath $smokeParent) -and
        -not (Get-ChildItem -Force -LiteralPath $smokeParent)
    ) {
        Remove-Item -LiteralPath $smokeParent -Force
    }
}
