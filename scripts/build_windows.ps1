param([switch]$SkipInstaller)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$ownedBuild=[IO.Path]::GetFullPath((Join-Path $projectRoot 'dist\SoloRealmsAI'))
if (-not $ownedBuild.StartsWith(([IO.Path]::GetFullPath($projectRoot)+'\dist\'),[StringComparison]::OrdinalIgnoreCase)) {throw 'Build path escaped project dist'}
$currentBaseline=Join-Path $projectRoot 'reports\preview10\baseline-preview9.zip'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $currentBaseline) | Out-Null
$previousPreview=Join-Path $projectRoot 'dist\SoloRealmsAI-Windows-2.1.4-preview.9-mock.zip'
# Cleanup may remove the obsolete dist ZIP; its verified rollback copy is retained.
if (Test-Path -LiteralPath $currentBaseline) {
    if ((Get-FileHash -LiteralPath $currentBaseline -Algorithm SHA256).Hash -ne '10060994c6fe56e994fe4bfd1669e22bdc34286e8514488e85c2ac7f36600101') {throw 'Preview9 rollback baseline changed'}
} elseif (Test-Path -LiteralPath $previousPreview) {
    if ((Get-FileHash -LiteralPath $previousPreview -Algorithm SHA256).Hash -ne '10060994c6fe56e994fe4bfd1669e22bdc34286e8514488e85c2ac7f36600101') {throw 'Preview9 baseline changed'}
    Copy-Item -LiteralPath $previousPreview -Destination $currentBaseline
} else {throw 'Verified Preview9 rollback copy is missing'}
$baseline=Join-Path $projectRoot 'reports\preview7\baseline-preview6.zip'
if (-not (Test-Path -LiteralPath $baseline)) {
    $previous=Join-Path $projectRoot 'dist\SoloRealmsAI-Windows-2.1.4-preview.6.zip'
    if (-not (Test-Path -LiteralPath $previous)) {throw 'Previous preview ZIP must be preserved before replacement'}
    Copy-Item -LiteralPath $previous -Destination $baseline
}
& (Join-Path $projectRoot '.venv\Scripts\python.exe') -m PyInstaller --noconfirm packaging/SoloRealmsAI.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
Copy-Item -LiteralPath (Join-Path $projectRoot 'assets\README-Windows.txt') -Destination (Join-Path $projectRoot 'dist\SoloRealmsAI\README-Windows.txt')
if (-not $SkipInstaller) {
$compilerPath = Join-Path $projectRoot 'build-tools\inno\ISCC.exe'
if (-not (Test-Path -LiteralPath $compilerPath)) { $compilerPath = (Get-Command ISCC.exe -ErrorAction Stop).Source }
& $compilerPath 'packaging\installer.iss'
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
}

if ($SkipInstaller) { & (Join-Path $projectRoot '.venv\Scripts\python.exe') scripts/package_windows.py --portable-only }
else { & (Join-Path $projectRoot '.venv\Scripts\python.exe') scripts/package_windows.py }
if ($LASTEXITCODE -ne 0) { throw "Release ZIP verification failed" }
