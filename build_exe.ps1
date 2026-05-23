$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourceFile = Join-Path $ProjectDir "stock_scanner.py"
$VenvDir = Join-Path $ProjectDir ".venv-build"
$WindowsVenvPython = Join-Path $VenvDir "Scripts\python.exe"
$MsysVenvPython = Join-Path $VenvDir "bin\python.exe"
$DistDir = Join-Path $ProjectDir "dist"
$ExePath = Join-Path $DistDir "NetworkPingTest.exe"
$RootExePath = Join-Path $ProjectDir "NetworkPingTest.exe"

Write-Host "Project: $ProjectDir"

if (-not (Test-Path $SourceFile)) {
    throw "Cannot find stock_scanner.py in $ProjectDir"
}

if (-not (Test-Path $WindowsVenvPython) -and -not (Test-Path $MsysVenvPython)) {
    Write-Host "Creating local build virtual environment..."
    python -m venv $VenvDir
}

$PythonExe = $WindowsVenvPython
if (-not (Test-Path $PythonExe)) {
    $PythonExe = $MsysVenvPython
}
if (-not (Test-Path $PythonExe)) {
    throw "Virtual environment was created, but python.exe was not found under Scripts or bin."
}

$RuntimeArtifacts = @(
    "stock_scanner.db",
    "stock_data.db",
    "snapshot_aftermarket.csv",
    "snapshot_intraday.csv",
    "ai_training.csv"
)

Push-Location $ProjectDir
try {
Write-Host "Installing/updating build dependencies..."
& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install --upgrade pyinstaller requests

Write-Host "Checking Python syntax..."
& $PythonExe -m py_compile $SourceFile

Write-Host "Building NetworkPingTest.exe..."
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name NetworkPingTest `
    --distpath $DistDir `
    --workpath (Join-Path $ProjectDir "build") `
    --specpath $ProjectDir `
    $SourceFile
} finally {
    Pop-Location
}

foreach ($artifact in $RuntimeArtifacts) {
    $artifactPath = Join-Path $DistDir $artifact
    if (Test-Path $artifactPath) {
        Remove-Item -Force $artifactPath
        Write-Host "Removed stale dist artifact: $artifact"
    }
}

function Copy-BuildAsset {
    param(
        [string]$SourcePath,
        [string]$DestinationDir,
        [string]$Label
    )

    if (Test-Path $SourcePath) {
        Copy-Item -Force $SourcePath $DestinationDir
        Write-Host "Copied $Label to dist."
        return $true
    }

    Write-Host "Missing ${Label}: $SourcePath"
    return $false
}

$LocalStockList = Join-Path $ProjectDir "stock_list.txt"
$ParentStockList = Join-Path (Split-Path -Parent $ProjectDir) "stock_list.txt"
$ScannerConfig = Join-Path $ProjectDir "scanner_config.ini"
$DotEnv = Join-Path $ProjectDir ".env"

if (-not (Copy-BuildAsset -SourcePath $LocalStockList -DestinationDir $DistDir -Label "stock_list.txt")) {
    if (-not (Copy-BuildAsset -SourcePath $ParentStockList -DestinationDir $DistDir -Label "stock_list.txt (parent copy)")) {
        Write-Host "No stock_list.txt found. Put stock_list.txt next to the exe before scanning."
    }
}

Copy-BuildAsset -SourcePath $ScannerConfig -DestinationDir $DistDir -Label "scanner_config.ini" | Out-Null
Copy-BuildAsset -SourcePath $DotEnv -DestinationDir $DistDir -Label ".env" | Out-Null

if (-not (Test-Path $ExePath)) {
    throw "Build finished but exe was not found: $ExePath"
}

Copy-Item -Force $ExePath $RootExePath
Write-Host "Copied NetworkPingTest.exe to project root."

Write-Host ""
Write-Host "Build complete:"
Write-Host $ExePath
Write-Host $RootExePath
