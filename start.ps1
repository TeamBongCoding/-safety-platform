[CmdletBinding()]
param(
    [switch]$WithAnalyzer
)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$backendDir = Join-Path $projectRoot "backend"
$frontendDir = Join-Path $projectRoot "frontend"
$envFile = Join-Path $projectRoot ".env"

if (Test-Path -LiteralPath $envFile) {
    foreach ($rawLine in Get-Content -LiteralPath $envFile) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }

        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }

        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
            continue
        }
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        if ($null -eq [Environment]::GetEnvironmentVariable($name, "Process")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

$backendHost = if ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { "127.0.0.1" }
$backendPort = if ($env:BACKEND_PORT) { $env:BACKEND_PORT } else { "8000" }
$frontendPort = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "5173" }
$backendHealthHost = if ($backendHost -eq "0.0.0.0") { "127.0.0.1" } else { $backendHost }
$backendHealthUrl = "http://${backendHealthHost}:${backendPort}/health"

$processes = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Start-Component {
    param(
        [string]$Title,
        [string]$WorkingDirectory,
        [string]$Command
    )

    Write-Host "[$Title] 시작"

    $childCommand = @"
`$Host.UI.RawUI.WindowTitle = '$Title'
$Command
"@

    $encodedCommand = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($childCommand)
    )

    return Start-Process `
        -FilePath "powershell.exe" `
        -WorkingDirectory $WorkingDirectory `
        -ArgumentList @("-NoProfile", "-NoExit", "-EncodedCommand", $encodedCommand) `
        -PassThru
}

function Wait-ForBackend {
    Write-Host "[Backend] 준비 상태 확인 중..."

    $deadline = (Get-Date).AddSeconds(90)

    while ((Get-Date) -lt $deadline) {
        try {
            $result = Invoke-RestMethod `
                -Uri $backendHealthUrl `
                -TimeoutSec 1

            if ($result.status -eq "ok") {
                Write-Host "[Backend] 준비 완료"
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }

    throw "90초 안에 백엔드가 시작되지 않았습니다. Safety Backend 창의 오류를 확인하세요."
}

try {
    $backendCommand = "python -m uvicorn app.main:app --host $backendHost --port $backendPort --reload"
    if ($WithAnalyzer) {
        $backendCommand = '$env:ANALYSIS_ENABLED = "1"; $env:POSE_ENABLED = "1"; ' + $backendCommand
    }

    $processes.Add(
        (Start-Component `
            -Title "Safety Backend" `
            -WorkingDirectory $backendDir `
            -Command $backendCommand)
    )

    Wait-ForBackend

    $processes.Add(
        (Start-Component `
            -Title "Safety Frontend" `
            -WorkingDirectory $frontendDir `
            -Command "npm.cmd run dev -- --host 0.0.0.0 --port $frontendPort")
    )

    Write-Host ""
    Write-Host "실행 완료"
    Write-Host "Frontend : http://localhost:$frontendPort"
    Write-Host "Backend  : http://${backendHost}:$backendPort"
    Write-Host "API Docs : http://${backendHost}:$backendPort/docs"
    Write-Host ""
    Read-Host "모든 프로세스를 종료하려면 Enter를 누르세요" | Out-Null
}
finally {
    Write-Host "프로세스를 종료합니다."

    foreach ($process in $processes) {
        if ($null -ne $process -and -not $process.HasExited) {
            taskkill.exe /PID $process.Id /T /F | Out-Null
        }
    }
}
