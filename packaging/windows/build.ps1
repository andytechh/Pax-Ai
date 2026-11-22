[CmdletBinding()]
param(
    [ValidateSet("onedir", "onefile")][string]$Mode = "onedir",
    [switch]$Installer
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $Root
$Python = "py -3.11"
Write-Host "Building PAX for Windows x64 ($Mode)" -ForegroundColor Cyan
& cmd.exe /c "$Python -m pip install --upgrade pip wheel setuptools pyinstaller"
if ($LASTEXITCODE -ne 0) { throw "Could not install build tooling" }
& cmd.exe /c "$Python -m pip install -r requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Could not install PAX dependencies" }
& cmd.exe /c "$Python packaging/windows/prepare_wakeword.py"
if ($LASTEXITCODE -ne 0) { throw "Could not prepare the wake-word model" }
$env:PAX_BUILD_MODE = $Mode
& cmd.exe /c "$Python -m PyInstaller --noconfirm --clean packaging/windows/pax.spec --distpath dist --workpath build"
Remove-Item Env:PAX_BUILD_MODE -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
$Output = if ($Mode -eq "onefile") { "dist/PAX.exe" } else { "dist/PAX/PAX.exe" }
if (!(Test-Path $Output)) { throw "Expected executable missing: $Output" }
$env:PAX_DATA_DIR = Join-Path $env:LOCALAPPDATA "PAX"
& $Output --pax-smoke-test
$SmokeExit = $LASTEXITCODE
Remove-Item Env:PAX_DATA_DIR -ErrorAction SilentlyContinue
if ($SmokeExit -ne 0) { throw "Packaged executable smoke test failed ($SmokeExit)" }
if ($Installer) {
    $ISCC = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if (!$ISCC) { throw "Install Inno Setup 6 to build PAX-Setup.exe" }
    & $ISCC.Source "/DPackageMode=$Mode" "packaging/windows/PAX.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }
}
Write-Host "PAX packaging build completed. Executable: $Output" -ForegroundColor Green
