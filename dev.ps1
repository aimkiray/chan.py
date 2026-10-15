# dev.ps1 - Start local development stack (backend + frontend).
# All paths are anchored to the script directory so it works from any cwd.

$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

# Free the usual dev ports (8000/8001 backend, 3000/5173 frontend) -- but only
# kill processes that look like THIS project's dev servers, so we never kill an
# unrelated dev server that happens to share one of these common ports:
#   - backend ports: process command line must contain "main.py"
#   - frontend ports: process command line must contain "nuxt"/"nuxi" or "npm run dev"
Write-Host "Checking for existing processes..."
$portPatterns = [ordered]@{
    8000 = 'main\.py'
    8001 = 'main\.py'
    3000 = 'nuxt|nuxi|npm(\.cmd)?\s+run\s+dev'
    5173 = 'nuxt|nuxi|npm(\.cmd)?\s+run\s+dev'
}
$killed = @{}

foreach ($port in $portPatterns.Keys) {
    $pattern = $portPatterns[$port]
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $connections) {
        $procId = $conn.OwningProcess
        if (-not $procId -or $procId -le 4 -or $killed.ContainsKey($procId)) { continue }

        $cmdLine = $null
        try {
            $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction Stop).CommandLine
        } catch {
            $cmdLine = $null
        }

        if ($cmdLine -and $cmdLine -match $pattern) {
            Write-Host "Killing dev process on port $port (PID: $procId)..."
            Write-Host "  CommandLine: $cmdLine"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            $killed[$procId] = $true
        } else {
            Write-Warning "Port $port is in use by PID $procId but it is not a recognized mlchan dev process; leaving it running."
            if ($cmdLine) { Write-Host "  CommandLine: $cmdLine" }
        }
    }
}

# Start Backend (prefer the repo venv python; fall back to PATH python)
Write-Host "Starting Backend..."
$VenvPython = Join-Path $RepoRoot "venv\Scripts\python.exe"
$BackendScript = Join-Path $RepoRoot "backend\main.py"
if (Test-Path $VenvPython) {
    Start-Process -FilePath $VenvPython -ArgumentList "`"$BackendScript`"" -WorkingDirectory $RepoRoot
} else {
    Write-Warning "venv python not found at $VenvPython; falling back to 'python' on PATH."
    Start-Process -FilePath "python" -ArgumentList "`"$BackendScript`"" -WorkingDirectory $RepoRoot
}

# Start Frontend
Write-Host "Starting Frontend..."
$FrontendDir = Join-Path $RepoRoot "frontend"
if ($IsWindows -or $env:OS -like "*Windows*") {
    Start-Process -FilePath "npm.cmd" -ArgumentList "run dev" -WorkingDirectory $FrontendDir
} else {
    Start-Process -FilePath "npm" -ArgumentList "run dev" -WorkingDirectory $FrontendDir
}

Write-Host "Development environment started!"
