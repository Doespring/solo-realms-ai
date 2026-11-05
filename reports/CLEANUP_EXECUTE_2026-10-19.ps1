param([Parameter(Mandatory=$true)][ValidateSet('file','directory')][string]$Kind,[switch]$RenewedOwnerRequest)
$ErrorActionPreference = 'Stop'
$cleanupRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd([char]92)
$expectedRoot = 'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI'
if (-not $cleanupRoot.Equals($expectedRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected workspace root' }
$rootPrefix = $cleanupRoot + [char]92
$planName = if ($RenewedOwnerRequest) { 'CLEANUP_RENEWED_PLAN_2026-10-19.json' } else { 'CLEANUP_PLAN_2026-10-19.json' }
$plan = Get-Content -LiteralPath (Join-Path $PSScriptRoot $planName) -Raw | ConvertFrom-Json
if (-not ([IO.Path]::GetFullPath($plan.root)).Equals($cleanupRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Plan root mismatch' }
$forbidden = @($plan.preserve_prefixes) + @($plan.previously_blocked)
$targets = @($plan.delete | Where-Object { $_.kind -eq $Kind })
foreach ($target in $targets) {
    $resolvedTarget = [IO.Path]::GetFullPath($target.path).TrimEnd([char]92)
    if (-not $resolvedTarget.StartsWith($rootPrefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Target escapes workspace' }
    foreach ($relative in $forbidden) {
        $protectedPath = [IO.Path]::GetFullPath((Join-Path $cleanupRoot $relative)).TrimEnd([char]92)
        if ($resolvedTarget.Equals($protectedPath,[StringComparison]::OrdinalIgnoreCase) -or
            $resolvedTarget.StartsWith($protectedPath + [char]92,[StringComparison]::OrdinalIgnoreCase) -or
            $protectedPath.StartsWith($resolvedTarget + [char]92,[StringComparison]::OrdinalIgnoreCase)) { throw ('Protected/previously blocked target: ' + $target.relative) }
    }
    if (Test-Path -LiteralPath $resolvedTarget) {
        $item = Get-Item -LiteralPath $resolvedTarget -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse target' }
        if (($Kind -eq 'directory') -ne $item.PSIsContainer) { throw 'Target type changed' }
        if ($item.PSIsContainer) {
            $links = @(Get-ChildItem -LiteralPath $resolvedTarget -Recurse -Force -Attributes ReparsePoint)
            if ($links.Count -ne 0) { throw ('Nested reparse point: ' + $target.relative) }
        }
    }
}
$removed = [Collections.Generic.List[object]]::new()
$failed = [Collections.Generic.List[object]]::new()
$phase = if ($RenewedOwnerRequest) { 'renewed-' } else { '' }
$resultPath = Join-Path $PSScriptRoot ('CLEANUP_RESULT_2026-10-19-' + $phase + $Kind + '.json')
foreach ($target in $targets) {
    if (-not (Test-Path -LiteralPath $target.path)) { continue }
    try {
        if ($Kind -eq 'directory') {
            $files = @(Get-ChildItem -LiteralPath $target.path -Recurse -Force -File)
            $bytes = ($files | Measure-Object -Property Length -Sum).Sum
            $count = $files.Count
            Remove-Item -LiteralPath $target.path -Recurse -Force -ErrorAction Stop
        } else {
            $bytes = (Get-Item -LiteralPath $target.path -Force).Length
            $count = 1
            Remove-Item -LiteralPath $target.path -Force -ErrorAction Stop
        }
        $removed.Add([pscustomobject]@{relative=$target.relative;bytes=[long]$bytes;files=$count})
    } catch {
        $failed.Add([pscustomobject]@{relative=$target.relative;error=$_.Exception.Message;retry_attempted=$false})
    }
    @{kind=$Kind;removed=@($removed.ToArray());failed=@($failed.ToArray())} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resultPath -Encoding utf8
}
@{kind=$Kind;removed_targets=$removed.Count;removed_bytes=($removed | Measure-Object -Property bytes -Sum).Sum;removed_files=($removed | Measure-Object -Property files -Sum).Sum;failed=$failed.Count} | ConvertTo-Json -Compress
