# Deployment Script
param (
    [string]$TargetDir = "./mlchan_deploy"
)

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

# Resolve absolute path
$AbsTargetDir = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $TargetDir))

Write-Host "Deploying to: $AbsTargetDir"

# 1. Build Frontend
Write-Host "Building Frontend..."
Set-Location (Join-Path $RepoRoot "frontend")
npm run generate
if ($LASTEXITCODE -ne 0) {
    Write-Error "Frontend build failed!"
    exit 1
}
Set-Location $RepoRoot

# 2. Prepare Target Directory
if (Test-Path $AbsTargetDir) {
    Write-Host "Cleaning existing deployment directory (keeping venv)..."
    # Get all items in the target directory except 'venv' and delete them
    Get-ChildItem -Path $AbsTargetDir | Where-Object { $_.Name -ne "venv" } | Remove-Item -Recurse -Force
} else {
    New-Item -ItemType Directory -Path $AbsTargetDir | Out-Null
}

# 3. Copy Backend Files
Write-Host "Copying Backend Files..."
$foldersToCopy = @("backend", "DataAPI", "Common", "Plot", "Math", "KLine", "Seg", "Bi", "BuySellPoint", "ChanModel", "Combiner", "ZS")
foreach ($folder in $foldersToCopy) {
    if (Test-Path $folder) {
        Copy-Item -Path $folder -Destination $AbsTargetDir -Recurse
    } else {
        Write-Warning "Folder not found: $folder"
    }
}

# Copy Root Python Files
$rootFiles = @("Chan.py", "ChanConfig.py", "__init__.py")
foreach ($file in $rootFiles) {
    if (Test-Path $file) {
        Copy-Item -Path $file -Destination $AbsTargetDir
    }
}

# Copy Config Files
if (Test-Path ".env") {
    Copy-Item -Path ".env" -Destination $AbsTargetDir
} else {
    Write-Warning ".env not found; skipping."
}
if (Test-Path "requirements.txt") {
    Copy-Item -Path "requirements.txt" -Destination $AbsTargetDir
} else {
    Write-Warning "requirements.txt not found; skipping."
}

# 3.1 Setup Python Environment
$TargetVenv = Join-Path $AbsTargetDir "venv"
if (Test-Path $TargetVenv) {
    Write-Host "Using existing venv in target directory..."
    # Optional: Update dependencies if requirements changed, but usually fast if nothing changed
    Write-Host "Checking dependencies..."
    $PipPath = Join-Path $TargetVenv "Scripts\pip"
    if (Test-Path (Join-Path $RepoRoot "requirements.txt")) {
        & $PipPath install -r (Join-Path $RepoRoot "requirements.txt") -i https://pypi.tuna.tsinghua.edu.cn/simple
    }
} else {
    $VenvPath = Join-Path $RepoRoot "venv"
    if (Test-Path $VenvPath) {
        Write-Host "Copying venv from repository..."
        Copy-Item -Path $VenvPath -Destination $AbsTargetDir -Recurse
    } else {
        Write-Host "Venv not found in repository. Creating new venv..."
        python -m venv $TargetVenv
        
        Write-Host "Installing dependencies..."
        $PipPath = Join-Path $TargetVenv "Scripts\pip"
        if (Test-Path (Join-Path $RepoRoot "requirements.txt")) {
            & $PipPath install -r (Join-Path $RepoRoot "requirements.txt") -i https://pypi.tuna.tsinghua.edu.cn/simple
        }
    }
}

# 4. Copy Static Files (Frontend Dist)
Write-Host "Copying Static Files..."
$StaticDir = Join-Path $AbsTargetDir "static"
New-Item -ItemType Directory -Path $StaticDir -Force | Out-Null
$AssetsDir = Join-Path $StaticDir "assets"
New-Item -ItemType Directory -Path $AssetsDir -Force | Out-Null

$FrontendStaticDir = Join-Path $RepoRoot "frontend\.output\public"
if (-not (Test-Path $FrontendStaticDir)) {
    Write-Error "Frontend static output not found: $FrontendStaticDir"
    Write-Error "Expected Nuxt generate output. Please run 'npm run generate' in frontend."
    exit 1
}

Copy-Item -Path (Join-Path $FrontendStaticDir "*") -Destination $StaticDir -Recurse -Force

# 5. Create Run Script for Production
$RunScriptPath = Join-Path $AbsTargetDir "run_prod.bat"
$RunContent = @"
@echo off
echo Starting MLChan Production Server...
start "" http://akari.debuff.top:38383
call venv\Scripts\activate.bat
python backend/main.py --port 38383
pause
"@
Set-Content -Path $RunScriptPath -Value $RunContent

Write-Host "Deployment Complete!"
Write-Host "You can run the application from: $RunScriptPath"
