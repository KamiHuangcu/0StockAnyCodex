$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourceFile = Join-Path $ProjectDir "stock_scanner.py"
$VenvDir = Join-Path $ProjectDir ".venv-build"
$WindowsVenvPython = Join-Path $VenvDir "Scripts\python.exe"
$MsysVenvPython = Join-Path $VenvDir "bin\python.exe"
$DistDir = Join-Path $ProjectDir "dist"
$ExePath = Join-Path $DistDir "NetworkPingTest.exe"

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

$LocalStockList = Join-Path $ProjectDir "stock_list.txt"
$ParentStockList = Join-Path (Split-Path -Parent $ProjectDir) "stock_list.txt"

if (Test-Path $LocalStockList) {
    Copy-Item -Force $LocalStockList $DistDir
    Write-Host "Copied stock_list.txt from project folder."
} elseif (Test-Path $ParentStockList) {
    Copy-Item -Force $ParentStockList $DistDir
    Write-Host "Copied stock_list.txt from parent folder."
} else {
    Write-Host "No stock_list.txt found. Put stock_list.txt next to the exe before scanning."
}

if (-not (Test-Path $ExePath)) {
    throw "Build finished but exe was not found: $ExePath"
}

Write-Host ""
Write-Host "Build complete:"
Write-Host $ExePath
