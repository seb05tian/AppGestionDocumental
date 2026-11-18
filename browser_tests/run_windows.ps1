$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "No existe .venv\Scripts\python.exe. Crea el entorno virtual e instala requirements-dev.txt primero."
}

$containerName = "admisiones-browser-test-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$previousDebug = $env:DEBUG
$previousSecret = $env:DJANGO_SECRET_KEY
$previousDatabase = $env:DATABASE_URL
$started = $false
$pushedLocation = $false
$testExitCode = 1

try {
    docker run --rm --detach --name $containerName `
        -e POSTGRES_USER=reviewtest -e POSTGRES_PASSWORD=reviewtest -e POSTGRES_DB=reviewtest `
        -p 127.0.0.1::5432 postgres:17-alpine *> $null
    if ($LASTEXITCODE -ne 0) { throw "No se pudo iniciar PostgreSQL temporal para la prueba." }
    $started = $true

    $mapping = (& docker port $containerName "5432/tcp").Trim()
    if ($LASTEXITCODE -ne 0) { throw "No se pudo consultar el puerto PostgreSQL temporal." }
    $port = [int](($mapping -split ":")[-1])

    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        docker exec $containerName pg_isready -U reviewtest -d reviewtest *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw "PostgreSQL temporal no quedó disponible." }

    Push-Location $projectRoot
    $pushedLocation = $true
    $env:DEBUG = "1"
    $env:DJANGO_SECRET_KEY = "browser-test-only-key"
    $env:DATABASE_URL = "postgresql://reviewtest:reviewtest@127.0.0.1:${port}/reviewtest"
    & $python (Join-Path $projectRoot "manage.py") test browser_tests.flow
    $testExitCode = $LASTEXITCODE
}
catch {
    Write-Host "Error: $_" -ForegroundColor Red
    $testExitCode = 1
}
finally {
    if ($started) { docker stop $containerName *> $null }
    if ($pushedLocation) { Pop-Location }
    if ($null -eq $previousDebug) { Remove-Item Env:DEBUG -ErrorAction SilentlyContinue } else { $env:DEBUG = $previousDebug }
    if ($null -eq $previousSecret) { Remove-Item Env:DJANGO_SECRET_KEY -ErrorAction SilentlyContinue } else { $env:DJANGO_SECRET_KEY = $previousSecret }
    if ($null -eq $previousDatabase) { Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue } else { $env:DATABASE_URL = $previousDatabase }
}

exit $testExitCode
