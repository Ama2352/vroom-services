# build-push-local.ps1
# Build and push Vroom services directly from your local machine to save GitLab CI minutes.

$DockerUser = $env:DOCKER_USER
if (-not $DockerUser) {
    Write-Error "DOCKER_USER environment variable is not set. Please set it (e.g. `$env:DOCKER_USER='yourname'`) and try again."
    exit 1
}

$Registry = "vroom-mvp"
$Services = @{
    "user"         = "user-service"
    "ride"         = "ride-service"
    "notification" = "notification-service"
    "dispatch"     = "dispatch-service"
    "frontend"     = "frontend"
    "ai-reporter"  = "ai-reporter"
}

$CommitHash = git rev-parse --short HEAD
Write-Host "Starting build and push for commit: $CommitHash" -ForegroundColor Cyan

foreach ($path in $Services.Keys) {
    $name = $Services[$path]
    $imgBase = "$DockerUser/$Registry-$name"
    $imgTag = "$imgBase:ci-$CommitHash"
    $imgLatest = "$imgBase:latest"

    Write-Host "`n>>> Building $name..." -ForegroundColor Yellow
    docker build -t $imgTag -t $imgLatest ./$path

    Write-Host ">>> Pushing $name..." -ForegroundColor Green
    docker push $imgTag
    docker push $imgLatest
}

Write-Host "`nDone! All images pushed to DockerHub." -ForegroundColor Cyan
