param([Parameter(Mandatory=$true)][ValidateSet('311','312')][string]$Minor)
$ErrorActionPreference = 'Stop'
$commit = '6d7d7df73b4f480818c64a3a2b8fa8df39c6fdf9'
$tree = '5a9b5b5439a3a4bfb4e4203ddb37597fdc313449'
$archiveHash = '8247c677f1f055321bce2ee7d8132d0b28949aa0085e7ddbe995062ee3a670ab'
$windowsRoot = 'C:/t/r24'
$linuxRoot = '\\wsl.localhost\Cockpit-Audit-2404\audit\rw24'
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
    '-d', 'Cockpit-Audit-2404', '--exec', "/audit/env$Minor/bin/python", '/audit/rw24/run_cell.py',
    '/audit/rw24/source.tar', "/audit/rw24/$linuxCell", $commit, $tree
) -WindowStyle Hidden -RedirectStandardOutput "$windowsRoot/$linuxCell-launch.log" -RedirectStandardError "$windowsRoot/$linuxCell-launch.err" -PassThru
@{native_cell=$winCell;native_pid=$native.Id;linux_cell=$linuxCell;linux_pid=$linux.Id} |
    ConvertTo-Json | Set-Content -LiteralPath "$windowsRoot/launch-pids-$Minor.json"
Write-Output "Started $winCell and $linuxCell; completion requires attestation.json and all verifier gates."
