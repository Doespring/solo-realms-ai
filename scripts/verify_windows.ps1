$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$qaInstall = [IO.Path]::GetFullPath((Join-Path $projectRoot 'data\windows-installer-qa'))
$qaProfile = [IO.Path]::GetFullPath((Join-Path $projectRoot 'data\windows-profile-qa'))
$expectedParent = [IO.Path]::GetFullPath((Join-Path $projectRoot 'data')) + '\'
if (-not $qaInstall.StartsWith($expectedParent,[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe installation test path' }
$setupPath = Join-Path $projectRoot 'dist\SoloRealmsAI-Setup-2.1.4-preview.6.exe'
$registryPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{C7847A67-44A0-4B1D-9168-8413D9BC8147}_is1'
if (Test-Path -LiteralPath $registryPath) {
    $priorInstall = Get-ItemProperty -LiteralPath $registryPath
    if ([IO.Path]::GetFullPath($priorInstall.InstallLocation).TrimEnd('\') -ne $qaInstall) { throw 'A user installation exists; isolated installer test will not modify it' }
}
function Install-TestBuild {
    $installerProcess = Start-Process -FilePath $setupPath -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS',('/DIR="'+$qaInstall+'"'),('/LOG="'+(Join-Path $projectRoot 'data\windows-installer.log')+'"')) -WindowStyle Hidden -PassThru
    if (-not $installerProcess.WaitForExit(55000)) { throw 'Installer test timed out' }
    $installerProcess.Refresh();if ($installerProcess.ExitCode -ne 0) { throw ('Installer failed: '+$installerProcess.ExitCode) }
}
Install-TestBuild
$installedExe = Join-Path $qaInstall 'SoloRealmsAI.exe'
$smokeReport = Join-Path $projectRoot 'reports\first-playable\installed-smoke.json'
$oldPath = $env:PATH;$oldPythonHome=$env:PYTHONHOME;$oldPythonPath=$env:PYTHONPATH
try {
    $env:PATH="$env:SystemRoot\System32;$env:SystemRoot";$env:PYTHONHOME=$null;$env:PYTHONPATH=$null
    $smokeProcess = Start-Process -FilePath $installedExe -ArgumentList @('--data-dir',('"'+$qaProfile+'"'),'--smoke-test',('"'+$smokeReport+'"')) -WindowStyle Hidden -PassThru
    if (-not $smokeProcess.WaitForExit(55000)) { throw 'Installed smoke test timed out' }
    $smokeProcess.Refresh();if ($smokeProcess.ExitCode -ne 0) { throw 'Installed smoke failed' }
} finally { $env:PATH=$oldPath;$env:PYTHONHOME=$oldPythonHome;$env:PYTHONPATH=$oldPythonPath }
$beforeDatabase = (Get-FileHash -LiteralPath (Join-Path $qaProfile 'solo.sqlite3')).Hash
$beforeLibrary = (Get-FileHash -LiteralPath (Join-Path $qaProfile 'adventure.sqlite3')).Hash
$beforeSources = @(Get-ChildItem -LiteralPath (Join-Path $qaProfile 'adventure-files') -File | Sort-Object Name | ForEach-Object { $_.Name+':'+(Get-FileHash -LiteralPath $_.FullName).Hash }) -join '|'
Install-TestBuild
$afterUpdate = (Get-FileHash -LiteralPath (Join-Path $qaProfile 'solo.sqlite3')).Hash
if ($beforeDatabase -ne $afterUpdate) { throw 'Reinstall changed profile database' }
if ($beforeLibrary -ne (Get-FileHash -LiteralPath (Join-Path $qaProfile 'adventure.sqlite3')).Hash) {throw 'Reinstall changed adventure library'}
$ownedUninstaller = [IO.Path]::GetFullPath((Join-Path $qaInstall 'unins000.exe'))
if (-not $ownedUninstaller.StartsWith($qaInstall+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe uninstaller test target' }
$uninstallProcess = Start-Process -FilePath $ownedUninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -WindowStyle Hidden -PassThru
if (-not $uninstallProcess.WaitForExit(55000)) { throw 'Uninstaller timed out' }
$uninstallProcess.Refresh();if ($uninstallProcess.ExitCode -ne 0) { throw 'Uninstaller failed' }
$afterUninstall = (Get-FileHash -LiteralPath (Join-Path $qaProfile 'solo.sqlite3')).Hash
if ($beforeDatabase -ne $afterUninstall) { throw 'Uninstall changed saved database' }
$afterSources = @(Get-ChildItem -LiteralPath (Join-Path $qaProfile 'adventure-files') -File | Sort-Object Name | ForEach-Object { $_.Name+':'+(Get-FileHash -LiteralPath $_.FullName).Hash }) -join '|'
if ($beforeLibrary -ne (Get-FileHash -LiteralPath (Join-Path $qaProfile 'adventure.sqlite3')).Hash -or $beforeSources -ne $afterSources) {throw 'Installer/uninstaller changed private adventure sources'}
@{installed_exe_smoke=$true;reinstall_preserved_database=($beforeDatabase -eq $afterUpdate);uninstall_preserved_database=($beforeDatabase -eq $afterUninstall);adventure_library_and_files_preserved=$true;executable_removed=(-not (Test-Path -LiteralPath $installedExe));system_python_removed_from_PATH=$true;clean_windows_VM_tested=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'reports\first-playable\installer-verification.json') -Encoding utf8
Write-Output 'Installer, embedded EXE, reinstall and uninstaller checks passed; test profile retained.'
