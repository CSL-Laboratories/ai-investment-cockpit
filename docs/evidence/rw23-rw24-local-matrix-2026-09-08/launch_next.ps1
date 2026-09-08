param([Parameter(Mandatory=$true)][ValidateSet('w312','l312')][string]$Cell)
$ErrorActionPreference = 'Stop'
$binding = Get-Content -LiteralPath "$PSScriptRoot/source-binding.json" -Raw | ConvertFrom-Json
$root = if ($Cell -eq 'w312') { 'C:/t/r25' } else { '\\wsl.localhost\Cockpit-Audit-2404\audit\rw25' }
$prior = if ($Cell -eq 'w312') { 'w311r' } else { 'l311r' }
$att = Get-Content -LiteralPath "$root/$prior/attestation.json" -Raw | ConvertFrom-Json
if (-not $att.completed_utc -or $att.checks.Count -ne 5 -or
    @($att.checks | Where-Object { $_.returncode -ne 0 }).Count -ne 0 -or
    $null -eq $att.tracked_source_changed -or $att.tracked_source_changed.Count -ne 0) {
    throw 'Prior platform cell did not finish cleanly'
}
if ((Get-FileHash -LiteralPath "$root/source.tar" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $binding.archive_sha256) {
    throw 'Archive differs from frozen source'
}
$run = "${Cell}r"
if (Test-Path -LiteralPath "$root/$run") { throw 'Refuse to overwrite a prior run' }
if ($Cell -eq 'w312') {
    $exe = 'C:/t/a26/env312r/Scripts/python.exe'
    $arguments = @('C:/t/r25/run_cell.py', 'C:/t/r25/source.tar', "C:/t/r25/$run", $binding.implementation_commit, $binding.tree)
} else {
    $exe = 'wsl.exe'
    $arguments = @('-d', 'Cockpit-Audit-2404', '--exec', '/audit/env312/bin/python', '/audit/rw25/run_cell.py', '/audit/rw25/source.tar', "/audit/rw25/$run", $binding.implementation_commit, $binding.tree)
}
$process = Start-Process -FilePath $exe -ArgumentList $arguments -WindowStyle Hidden -RedirectStandardOutput "C:/t/r25/$run-launch.log" -RedirectStandardError "C:/t/r25/$run-launch.err" -PassThru
@{cell=$Cell;pid=$process.Id;prior_cell=$prior} | ConvertTo-Json | Set-Content -LiteralPath "C:/t/r25/launch-$Cell.json"
Write-Output "Started $run after verified completion of $prior (PID $($process.Id))."
