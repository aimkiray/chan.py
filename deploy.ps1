# Deployment Script
param (
    [string]$TargetDir = "./mlchan_deploy",
    # Port the production server listens on (written into run_prod.bat)
    [int]$Port = 38383,
    # URL opened by run_prod.bat. Default: http://127.0.0.1:<Port>
    # NOTE: the API has no authentication and CORS is wide open -- deploy on a
    # trusted intranet/localhost only. Set this to e.g. "http://192.168.x.x:38383"
    # for LAN use; do NOT point it at a public domain without adding auth first.
    [string]$PublicUrl = "",
    # Optional pip index mirror (e.g. "https://pypi.tuna.tsinghua.edu.cn/simple").
    # Empty = use the target pip's own default configuration.
    [string]$PipIndexUrl = "",
    # Force rebuilding the venv in the target dir instead of reusing/copying one
    [switch]$RebuildVenv
)

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

# Resolve absolute path (fail closed: refuse unresolvable paths instead of falling through)
# Deployment targets are local directories only: reject UNC/remote forms ('\\', '//') and
# wildcard characters up front, because on pwsh/.NET Core GetFullPath does NOT throw for them.
if ([string]::IsNullOrWhiteSpace($TargetDir)) {
    Write-Error "Invalid TargetDir: value must not be null or empty."
    exit 1
}
if ($TargetDir.StartsWith('\\') -or $TargetDir.StartsWith('//')) {
    Write-Error "Invalid TargetDir '$TargetDir': UNC/remote paths are not allowed; the deployment target must be a local directory."
    exit 1
}
if ($TargetDir -match '[*?]') {
    Write-Error "Invalid TargetDir '$TargetDir': wildcard characters ('*', '?') are not allowed."
    exit 1
}
$AbsTargetDir = $null
try {
    if ([System.IO.Path]::IsPathRooted($TargetDir)) {
        $AbsTargetDir = [System.IO.Path]::GetFullPath($TargetDir)
    } else {
        $AbsTargetDir = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $TargetDir))
    }
} catch {
    Write-Error "Invalid TargetDir '$TargetDir': cannot resolve an absolute path ($($_.Exception.Message))"
    exit 1
}
if ([string]::IsNullOrWhiteSpace($AbsTargetDir)) {
    Write-Error "Invalid TargetDir '$TargetDir': resolved to an empty path."
    exit 1
}
if ($AbsTargetDir.StartsWith('\\?\') -or $AbsTargetDir.StartsWith('\\.\')) {
    Write-Error "Invalid TargetDir '$TargetDir': extended-length/device paths are not allowed."
    exit 1
}
# Re-check on the resolved path: '//host/share' normalizes to '\\host\share', and a wildcard
# could survive resolution on engines where GetFullPath does not throw.
if ($AbsTargetDir.StartsWith('\\') -or $AbsTargetDir.StartsWith('//')) {
    Write-Error "Invalid TargetDir '$TargetDir': resolved path '$AbsTargetDir' is a UNC/remote path; only local directories are allowed."
    exit 1
}
if ($AbsTargetDir -match '[*?]') {
    Write-Error "Invalid TargetDir '$TargetDir': resolved path '$AbsTargetDir' contains wildcard characters."
    exit 1
}
# Reject path components ending with a space or dot: Windows silently maps 'foo '/'foo.' to
# 'foo', which would desynchronize the string-based repo-root check below from the real target
# on disk (fail closed instead of normalizing: a legitimate deploy dir never has such names).
foreach ($comp in ($AbsTargetDir -split '[\\/]')) {
    if ($comp.Length -gt 0 -and ($comp.EndsWith(' ') -or $comp.EndsWith('.'))) {
        Write-Error "Invalid TargetDir '$TargetDir': path component '$comp' ends with a space or dot, which is ambiguous on Windows filesystems."
        exit 1
    }
}

# Refuse if TargetDir already exists as a file (Get-ChildItem would return the file itself and Remove-Item would delete it)
if (Test-Path -LiteralPath $AbsTargetDir -PathType Leaf) {
    Write-Error "Unsafe TargetDir '$AbsTargetDir': path exists and is a file, not a directory."
    exit 1
}

# Refuse junctions/symlinks anywhere along the target path so the cleanup cannot be redirected
$walk = $AbsTargetDir.TrimEnd('\')
while ($walk) {
    if (Test-Path -LiteralPath $walk) {
        $walkItem = Get-Item -LiteralPath $walk -Force
        if ($walkItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            $linkDest = $walkItem.LinkTarget
            if (-not $linkDest) { $linkDest = $walkItem.Target }
            Write-Error "Unsafe TargetDir '$AbsTargetDir': '$walk' is a junction/symlink pointing to '$linkDest'; refusing to deploy through a reparse point."
            exit 1
        }
    }
    $walkParent = Split-Path -Parent $walk
    if (-not $walkParent -or $walkParent -eq $walk) { break }
    $walk = $walkParent
}

# Safety guard: refuse to clean/deploy into the repo root, one of its ancestors, or a filesystem root
$RepoRootFull = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd('\')
$TargetFull = $AbsTargetDir.TrimEnd('\')
$TargetRoot = [System.IO.Path]::GetPathRoot($AbsTargetDir).TrimEnd('\')
$IsRepoOrAncestor = $RepoRootFull.Equals($TargetFull, [System.StringComparison]::OrdinalIgnoreCase) -or
    $RepoRootFull.StartsWith($TargetFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
$IsFileSystemRoot = $TargetFull.Equals($TargetRoot, [System.StringComparison]::OrdinalIgnoreCase)
if ($IsRepoOrAncestor -or $IsFileSystemRoot) {
    Write-Error "Unsafe TargetDir '$AbsTargetDir': must not be the repository root, an ancestor of it, or a filesystem root."
    exit 1
}

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
if (Test-Path -LiteralPath $AbsTargetDir) {
    Write-Host "Cleaning existing deployment directory (keeping venv)..."
    # Get all items in the target directory except 'venv' and delete them.
    # -LiteralPath everywhere: child names containing wildcard characters must still be handled.
    Get-ChildItem -LiteralPath $AbsTargetDir | Where-Object { $_.Name -ne "venv" } | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
} else {
    New-Item -ItemType Directory -Path $AbsTargetDir | Out-Null
}

# 3. Copy Backend Files
Write-Host "Copying Backend Files..."
$foldersToCopy = @("backend", "DataAPI", "Common", "Plot", "Math", "KLine", "Seg", "Bi", "BuySellPoint", "ChanModel", "Combiner", "ZS")
foreach ($folder in $foldersToCopy) {
    if (Test-Path $folder) {
        Copy-Item -LiteralPath $folder -Destination $AbsTargetDir -Recurse
    } else {
        Write-Warning "Folder not found: $folder"
    }
}

# Copy Root Python Files
$rootFiles = @("Chan.py", "ChanConfig.py", "__init__.py")
foreach ($file in $rootFiles) {
    if (Test-Path $file) {
        Copy-Item -LiteralPath $file -Destination $AbsTargetDir
    }
}

# Copy Config Files
if (Test-Path ".env") {
    Copy-Item -LiteralPath ".env" -Destination $AbsTargetDir
} else {
    Write-Warning ".env not found; skipping."
}
if (Test-Path "requirements.txt") {
    Copy-Item -LiteralPath "requirements.txt" -Destination $AbsTargetDir
} else {
    Write-Warning "requirements.txt not found; skipping."
}

# 3.1 Setup Python Environment
$TargetVenv = Join-Path $AbsTargetDir "venv"
$PipArgs = @("install", "-r", (Join-Path $RepoRoot "requirements.txt"))
if ($PipIndexUrl) {
    $PipArgs += @("-i", $PipIndexUrl)
}
if ($RebuildVenv -and (Test-Path $TargetVenv)) {
    Write-Host "RebuildVenv requested; removing existing target venv..."
    Remove-Item -LiteralPath $TargetVenv -Recurse -Force
}
if (Test-Path $TargetVenv) {
    Write-Host "Using existing venv in target directory..."
    # Optional: Update dependencies if requirements changed, but usually fast if nothing changed
    Write-Host "Checking dependencies..."
    $PipPath = Join-Path $TargetVenv "Scripts\pip"
    if (Test-Path (Join-Path $RepoRoot "requirements.txt")) {
        & $PipPath @PipArgs
    }
} else {
    $VenvPath = Join-Path $RepoRoot "venv"
    if ((Test-Path $VenvPath) -and (-not $RebuildVenv)) {
        # NOTE: a copied venv only works via its own Scripts\python.exe (which
        # resolves pyvenv.cfg + the base interpreter relative to itself on this
        # machine). run_prod.bat therefore invokes it directly instead of going
        # through activate.bat, whose baked-in absolute VIRTUAL_ENV/PATH would
        # point back at this repository. For a different machine, use -RebuildVenv.
        Write-Host "Copying venv from repository..."
        Copy-Item -LiteralPath $VenvPath -Destination $AbsTargetDir -Recurse
    } else {
        if ($RebuildVenv) {
            Write-Host "Creating fresh venv in target directory..."
        } else {
            Write-Host "Venv not found in repository. Creating new venv..."
        }
        $RepoVenvPython = Join-Path $VenvPath "Scripts\python.exe"
        if (Test-Path $RepoVenvPython) {
            & $RepoVenvPython -m venv $TargetVenv
        } else {
            python -m venv $TargetVenv
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $TargetVenv "Scripts\python.exe"))) {
            Write-Error "Failed to create venv in target directory."
            exit 1
        }

        Write-Host "Installing dependencies..."
        $PipPath = Join-Path $TargetVenv "Scripts\pip"
        if (Test-Path (Join-Path $RepoRoot "requirements.txt")) {
            & $PipPath @PipArgs
        }
    }
}

# 4. Copy Static Files (Frontend Dist)
Write-Host "Copying Static Files..."
$StaticDir = Join-Path $AbsTargetDir "static"
New-Item -ItemType Directory -Path $StaticDir -Force | Out-Null
# backend/main.py mounts static/assets unconditionally (StaticFiles raises when
# the dir is missing), so keep creating it even though Nuxt assets actually live
# under static/_nuxt/ (served via the catchall). TODO: repoint the mount to _nuxt.
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
# Invoke venv\Scripts\python.exe directly: activate.bat bakes in the absolute
# VIRTUAL_ENV path of wherever the venv was created, which is wrong for a copied
# venv. Running the interpreter directly also needs no PATH/activation at all.
if (-not $PublicUrl) {
    $PublicUrl = "http://127.0.0.1:$Port"
}
$RunScriptPath = Join-Path $AbsTargetDir "run_prod.bat"
$RunContent = @"
@echo off
rem Reminder: the API has no authentication -- run on trusted intranet/localhost only.
cd /d %~dp0
echo Starting MLChan Production Server on port $Port ...
start "" $PublicUrl
venv\Scripts\python.exe backend\main.py --port $Port
pause
"@
Set-Content -Path $RunScriptPath -Value $RunContent

Write-Host "Deployment Complete!"
Write-Host "You can run the application from: $RunScriptPath"
