param([switch]$PdfHotfix,[switch]$PdfWorld,[switch]$PlayableNext,[switch]$PdfPlayable)
if((@($PdfHotfix,$PdfWorld,$PlayableNext,$PdfPlayable) | Where-Object {$_}).Count -gt 1){throw 'Choose one isolated output'}
$ErrorActionPreference='Stop'
$prototypeRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $prototypeRoot
$env:SOLO_TEST='1'
$env:SOLO_PACKAGE_PROTOTYPE='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$python=Join-Path $prototypeRoot '.venv\Scripts\python.exe'
$version=& $python -B -c 'from solo.version import VERSION; print(VERSION)'
if($version -notlike '*prototype*'){throw 'Set a unique experimental prototype version before building'}
if(Test-Path -LiteralPath (Join-Path $prototypeRoot ('dist\SoloRealmsAI-Windows-'+$version+'.zip'))){throw 'Versioned ZIP already exists; no overwrite'}
$reportRelative=if($PdfPlayable){'reports/pdf-playable-release'}elseif($PlayableNext){'reports/playable-next-release'}elseif($PdfWorld){'reports/pdf-world-release'}elseif($PdfHotfix){'reports/pdf-hotfix'}else{'reports/prototype-5'}
$distRelative=if($PdfPlayable){'dist/pdf-playable'}elseif($PlayableNext){'dist/playable-next'}elseif($PdfWorld){'dist/pdf-world'}elseif($PdfHotfix){'dist/pdf-hotfix'}else{'dist'}
$owned=Join-Path $prototypeRoot ($reportRelative+'/build')
$bundleRelative=$distRelative+'/SoloRealmsAI-Prototype'
$bundle=Join-Path $prototypeRoot $bundleRelative
if(Test-Path -LiteralPath $bundle){throw 'Refusing to replace an existing executable directory; use an isolated output'}
if(-not [IO.Path]::GetFullPath($owned).StartsWith($prototypeRoot+'\reports\',[StringComparison]::OrdinalIgnoreCase)){throw 'Build path escaped own stand'}
& $python -B -m PyInstaller --noconfirm --workpath $owned --distpath (Join-Path $prototypeRoot $distRelative) packaging/SoloRealmsAI.spec
if($LASTEXITCODE -ne 0){throw 'PyInstaller failed'}
Copy-Item -LiteralPath assets/README-Prototype.txt -Destination (Join-Path $bundle 'README-Windows.txt')
Copy-Item -LiteralPath docs/PROTOTYPE_KNOWN_ISSUES.md -Destination (Join-Path $bundle 'KNOWN_ISSUES.md')
Copy-Item -LiteralPath packaging/Offline-Mock.cmd -Destination (Join-Path $bundle 'Offline-Mock.cmd')
if($PdfHotfix){Copy-Item -LiteralPath reports/PDF_SOURCE_RECOVERY_FIX.md,reports/PDF_READINESS_UI_FIX.md -Destination $bundle}
if($PdfWorld){Copy-Item -LiteralPath docs/PDF_NAT20_START.md,docs/PDF_NAT20_RULES.md,docs/PDF_NAT20_UI.md -Destination $bundle}
if($PlayableNext){Copy-Item -LiteralPath docs/PDF_NAT20_START.md,docs/PDF_NAT20_RULES.md,docs/PDF_NAT20_UI.md,docs/PDF_PREPARATION_RESUME.md,docs/PDF_PLAY_TODAY_UI.md,docs/NAT20_PLAYABLE_NEXT.md -Destination $bundle}
if($PdfPlayable){Copy-Item -LiteralPath docs/PDF_NAT20_START.md,docs/PDF_NAT20_RULES.md,docs/PDF_NAT20_UI.md,docs/PDF_PREPARATION_RESUME.md,docs/PDF_PLAY_TODAY_UI.md,docs/NAT20_PLAYABLE_NEXT.md,docs/NAT20_MONSTER_UI.md,docs/NATIVE_ACTION_PARAMETERS_UI.md,docs/PDF_FROZEN_SMOKE_FIXTURE.md -Destination $bundle}
& $python -B scripts/package_windows.py --prototype --portable-only --bundle $bundleRelative --report ($reportRelative+'/release-manifest.json')
if($LASTEXITCODE -ne 0){throw 'Prototype package verification failed'}
$archive=Join-Path $prototypeRoot ('dist\SoloRealmsAI-Windows-'+$version+'.zip')
$hash=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath ($archive+'.sha256') -Value ($hash+'  '+[IO.Path]::GetFileName($archive)) -Encoding ascii
