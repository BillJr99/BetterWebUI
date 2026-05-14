param(
    [switch]$Test
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

# Build Docker images
Write-Host "[1/4] Building Docker images..."
docker compose -f "$ScriptDir\docker-compose.integration.yml" build

# Start core services
Write-Host "[2/4] Starting services..."
docker compose -f "$ScriptDir\docker-compose.integration.yml" up -d

# Wait for BetterWebUI to be healthy
Write-Host "[3/4] Waiting for services to be ready..."
$timeout = 60
$elapsed = 0
while ($elapsed -lt $timeout) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8080/api/health" -UseBasicParsing -ErrorAction Stop
        if ($response.StatusCode -eq 200) { break }
    } catch { }
    Start-Sleep -Seconds 2
    $elapsed += 2
}
Write-Host "Services ready."

# Run Playwright tests
if ($Test) {
    Write-Host "[4/4] Running Playwright tests..."
    Push-Location "$RepoRoot\tests\playwright"
    npm ci
    npx playwright install --with-deps
    docker compose -f "$ScriptDir\docker-compose.integration.yml" --profile test up -d
    npx playwright test
    docker compose -f "$ScriptDir\docker-compose.integration.yml" --profile test down
    Pop-Location
} else {
    Write-Host "[4/4] Skipping tests (pass -Test to run Playwright tests)."
}

Write-Host "Done! BetterWebUI running at http://localhost:8080"
