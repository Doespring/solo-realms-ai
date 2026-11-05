# Подключение исходников Nat20 к локальному окружению
#
# Зачем: пакеты dnd5e-engine и dnd5e-srd-data раньше лежали в .venv копией,
# и правки в vendor/ движок не видел, пока их не скопируют руками.
# Здесь они подключаются к vendor/ напрямую.
#
# Почему не `pip install -e`: в этой среде pip зависает на сборке
# (проверено, >25 мин, ~960 с CPU). Механизм ниже стандартный для Python:
# .pth добавляет пути в sys.path, dist-info даёт importlib.metadata версию,
# которую проверяет solo/nat20_content.py:release().
#
# Это НЕ проверка упакованной поставки: package data (canonical JSON)
# читается из vendor/ по тем же правилам, что и из wheel.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$sp = Join-Path $root '.venv/Lib/site-packages'
$py = Join-Path $root '.venv/Scripts/python.exe'

if (-not (Test-Path $sp)) { throw "нет окружения: $sp" }

# 1. Убрать ручные копии, если остались от прошлой установки.
foreach ($name in @('dnd5e_engine','dnd5e_srd_data','dnd5e_engine-0.7.0.dist-info','dnd5e_srd_data-0.7.0.dist-info')) {
    $p = Join-Path $sp $name
    if (Test-Path $p) { Write-Host "удаляю копию: $name"; Remove-Item -Recurse -Force $p }
}

# 2. Подключить исходники одним .pth.
$engine = Join-Path $root 'vendor/nat20/packages/dnd5e-engine/src'
$srd    = Join-Path $root 'vendor/nat20/packages/dnd5e-srd-data/src'
Set-Content -Path (Join-Path $sp 'nat20_sources.pth') -Value @($engine, $srd) -Encoding utf8

# 3. Метаданные рядом с исходниками: release() сверяет версию пакета.
foreach ($dist in @('dnd5e-engine','dnd5e-srd-data')) {
    $mod = $dist.Replace('-','_')
    $di = Join-Path $sp "$mod-0.7.0.dist-info"
    New-Item -ItemType Directory -Force -Path $di | Out-Null
    Set-Content -Path (Join-Path $di 'METADATA') -Value @('Metadata-Version: 2.1', "Name: $dist", 'Version: 0.7.0') -Encoding utf8
    Set-Content -Path (Join-Path $di 'INSTALLER') -Value 'nat20-sources' -Encoding utf8
    Set-Content -Path (Join-Path $di 'RECORD') -Value '' -Encoding utf8
    $url = 'file:///' + (Join-Path $root "vendor/nat20/packages/$dist").Replace('\','/')
    Set-Content -Path (Join-Path $di 'direct_url.json') -Value ('{"url": "' + $url + '", "dir_info": {}}') -Encoding utf8
}

# 4. Проверка: импорт идёт из vendor, release() проходит.
& $py -c @'
import sys
import dnd5e_engine, dnd5e_srd_data
print('engine:', dnd5e_engine.__file__)
print('srd  :', dnd5e_srd_data.__file__)
assert 'vendor' in dnd5e_engine.__file__, 'engine не из vendor'
assert 'vendor' in dnd5e_srd_data.__file__, 'srd не из vendor'
from solo.nat20_content import release
loader, fingerprint, profile = release()
print('release OK, fingerprint:', fingerprint[:16])
'@
