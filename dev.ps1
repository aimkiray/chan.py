# Kill processes on port 8000, 8001 (Backend) and 3000, 5173 (Frontend)
Write-Host "Checking for existing processes..."
$ports = 8000, 8001, 3000, 5173

foreach ($port in $ports) {
    $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($connections) {
        foreach ($conn in $connections) {
            $pid_val = $conn.OwningProcess
            if ($pid_val -ne 0) {
                Write-Host "Killing process on port $port (PID: $pid_val)..."
                Stop-Process -Id $pid_val -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

# Start Backend
Write-Host "Starting Backend..."
Start-Process -FilePath "python" -ArgumentList "backend/main.py" -NoNewWindow:$false

# Start Frontend
Write-Host "Starting Frontend..."
Set-Location frontend
if ($IsWindows -or $env:OS -like "*Windows*") {
    Start-Process -FilePath "npm.cmd" -ArgumentList "run dev" -NoNewWindow:$false
} else {
    Start-Process -FilePath "npm" -ArgumentList "run dev" -NoNewWindow:$false
}
Set-Location ..

Write-Host "Development environment started!"
