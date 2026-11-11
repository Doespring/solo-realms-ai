param([switch]$Apply,[switch]$CachesOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$profileRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'SoloRealmsAI'))
$ownedProcess = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'SoloRealmsAI.exe' -or
    ($_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and ($_.CommandLine.Contains($projectRoot) -or $_.CommandLine.Contains($profileRoot)))
}
if ($ownedProcess) { throw 'Close Solo Realms AI and its backend before cleaning.' }
if ($CachesOnly) {
    $caches = @((Join-Path $projectRoot '__pycache__'),(Join-Path $projectRoot '.pytest_cache'))
    foreach ($folder in @('solo','scripts','tests')) { $caches += @(Get-ChildItem -LiteralPath (Join-Path $projectRoot $folder) -Directory -Recurse -Force -Filter '__pycache__' | Select-Object -ExpandProperty FullName) }
    foreach ($cache in $caches) {
        $resolved = [IO.Path]::GetFullPath($cache)
        if (-not $resolved.StartsWith($projectRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe cache path' }
        if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
    }
    'Project caches cleared.'
    exit
}
$targets = [Collections.Generic.List[string]]::new()
function Add-Target([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not ($resolved.StartsWith($projectRoot+'\',[StringComparison]::OrdinalIgnoreCase) -or $resolved.StartsWith($profileRoot+'\',[StringComparison]::OrdinalIgnoreCase))) { throw ('Unsafe cleanup target: '+$resolved) }
    if ((Get-Item -LiteralPath $resolved -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ('Refusing linked cleanup target: '+$resolved) }
    $targets.Add($resolved)
}
foreach ($name in @('build','.pytest_cache','__pycache__','reports','AI-TUNING-BASELINE.jsonl')) { Add-Target (Join-Path $projectRoot $name) }
$keepDocuments = @('README.md','ARCHITECTURE.md','FIRST-PLAYABLE-PREVIEW6.md','THIRD_PARTY.md','CLEANUP-REPORT.md')
Get-ChildItem -LiteralPath $projectRoot -File -Filter '*.md' | Where-Object Name -NotIn $keepDocuments | ForEach-Object { Add-Target $_.FullName }
$keepScripts = @('build_windows.ps1','collect_licenses.py','import_rulebooks.py','import_visual_assets.py','package_source.py','package_windows.py','start_cloud.py','stop.py','verify_windows.ps1','verify_adventure_integrity.py','verify_adventure_sources.py','clean_project.ps1','package_clean.py')
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'scripts') -File | Where-Object Name -NotIn $keepScripts | ForEach-Object { Add-Target $_.FullName }
foreach ($folder in @('solo','tests','scripts')) {
    Get-ChildItem -LiteralPath (Join-Path $projectRoot $folder) -Directory -Recurse -Force -Filter '__pycache__' | ForEach-Object { Add-Target $_.FullName }
}
$keepRelease = @('SoloRealmsAI','SoloRealmsAI-Setup-2.1.4-preview.6.exe','SoloRealmsAI-Windows-2.1.4-preview.6.zip')
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'dist') -Force | Where-Object Name -NotIn $keepRelease | ForEach-Object { Add-Target $_.FullName }
$keepData = @('cloud-profiles.json','cloud-secrets','cloud-usage.sqlite3','cloud-usage.sqlite3-wal','cloud-usage.sqlite3-shm','rules-knowledge.sqlite3','rules-knowledge.sqlite3-wal','rules-knowledge.sqlite3-shm','desktop.json','app-version.json')
foreach ($folder in @((Join-Path $projectRoot 'data'),$profileRoot)) {
    if (Test-Path -LiteralPath $folder) { Get-ChildItem -LiteralPath $folder -Force | Where-Object Name -NotIn $keepData | ForEach-Object { Add-Target $_.FullName } }
}
$totalBytes = [long]0
foreach ($target in $targets) {
    $item = Get-Item -LiteralPath $target -Force
    if ($item.PSIsContainer) { $totalBytes += [long](Get-ChildItem -LiteralPath $target -Recurse -Force -File | Measure-Object -Property Length -Sum).Sum }
    else { $totalBytes += $item.Length }
}
if (-not $Apply) {
    [pscustomobject]@{targets=$targets.Count;bytes=$totalBytes;delete_paths=$targets} | ConvertTo-Json -Depth 4
    exit
}
# Every absolute target above was checked against the two explicitly owned roots.
# Stay in PowerShell end-to-end; never interpolate paths into another shell.
foreach ($target in $targets) { Remove-Item -LiteralPath $target -Force -Recurse }
$reportPath = Join-Path $projectRoot 'reports\cleanup'
New-Item -ItemType Directory -Force -Path $reportPath | Out-Null
[pscustomobject]@{deleted_targets=$targets.Count;deleted_bytes=$totalBytes;deleted_gib=[Math]::Round($totalBytes/1GB,3);project_root=$projectRoot;profile_root=$profileRoot;kept_version='2.1.4-preview.6';campaigns_removed=$true;api_settings_kept=$true;billing_ledger_kept=$true;deleted_paths=$targets} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $reportPath 'deleted.json') -Encoding utf8
[pscustomobject]@{deleted_targets=$targets.Count;deleted_gib=[Math]::Round($totalBytes/1GB,3);kept_version='2.1.4-preview.6'} | ConvertTo-Json
