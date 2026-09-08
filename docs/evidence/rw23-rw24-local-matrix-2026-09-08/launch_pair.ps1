param([Parameter(Mandatory=$true)][ValidateSet('311','312')][string]$Minor)
$ErrorActionPreference = 'Stop'
$commit = 'fa3cc1c0d8dc41af913a2d3fe6040d85d4e6e2e2'
$tree = 'cb8cefaa98913bc8efd77e4acb4750b572d095bc'
$archiveHash = 'da6e4ae1c375ff4e161fb6e1c6b1d75093ce7b41532297222484f125b36ffb05'
$windowsRoot = 'C:/t/r25'
$linuxRoot = '\\wsl.localhost\Cockpit-Audit-2404\audit\rw25'
$nativePython = if ($Minor -eq '311') { 'C:/t/a26/env311/Scripts/python.exe' } else { 'C:/t/a26/env312r/Scripts/python.exe' }
$winCell = "w${Minor}r"
$linuxCell = "l${Minor}r"
foreach ($root in @($windowsRoot, $linuxRoot)) {
    if ((Get-FileHash -LiteralPath "$root/source.tar" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $archiveHash) {
        throw 'Source archive does not match the frozen implementation'
    }
}
if ((Test-Path -LiteralPath "$windowsRoot/$winCell") -or (Test-Path -LiteralPath "$linuxRoot/$linuxCell")) {
    throw 'Refuse to overwrite a previous run'
}
$native = Start-Process -FilePath $nativePython -ArgumentList @(
    "$windowsRoot/run_cell.py", "$windowsRoot/source.tar", "$windowsRoot/$winCell", $commit, $tree
) -WindowStyle Hidden -RedirectStandardOutput "$windowsRoot/$winCell-launch.log" -RedirectStandardError "$windowsRoot/$winCell-launch.err" -PassThru
$linux = Start-Process -FilePath wsl.exe -ArgumentList @(
    '-d', 'Cockpit-Audit-2404', '--exec', "/audit/env$Minor/bin/python", '/audit/rw25/run_cell.py',
    '/audit/rw25/source.tar', "/audit/rw25/$linuxCell", $commit, $tree
) -WindowStyle Hidden -RedirectStandardOutput "$windowsRoot/$linuxCell-launch.log" -RedirectStandardError "$windowsRoot/$linuxCell-launch.err" -PassThru
@{native_cell=$winCell;native_pid=$native.Id;linux_cell=$linuxCell;linux_pid=$linux.Id} |
    ConvertTo-Json | Set-Content -LiteralPath "$windowsRoot/launch-pids-$Minor.json"
Write-Output "Started $winCell and $linuxCell; completion requires attestation.json and all verifier gates."
