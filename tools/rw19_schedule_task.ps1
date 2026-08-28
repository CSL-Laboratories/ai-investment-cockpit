# rw19_schedule_task.ps1 — register / inspect / census / unregister the one-shot
# RW-19 autolaunch scheduled task. Windows PowerShell 5.1.
#
# The task is per-authorized-window, never standing: a one-time trigger with an
# EndBoundary (no starts after the late boundary), IgnoreNew multiple-instance
# policy, WakeToRun, an execution time limit covering the initial wait plus the
# collector's 4200-second wall cap, and DeleteExpiredTaskAfter cleanup.
# StartWhenAvailable is deliberately NOT set: a missed trigger must not queue a
# late start — the launcher's own hard late cutoff stays authoritative.
#
# Modes:
#   -Mode Register  -TaskName <n> -TriggerUtc <ISO8601Z> -PythonExe <abs> `
#                   -LauncherPath <abs> -ConfigPath <abs> -WorkingDirectory <abs> `
#                   [-Rehearsal] [-EndBoundaryMinutes 30] [-ExecutionLimitHours 2]
#   -Mode Inspect   -TaskName <n>   (exports XML, asserts every required setting)
#   -Mode Census    -MatchPaths <p1,p2,...>  (lists every task whose action or
#                   arguments reference any given path — repo, DB, evidence root)
#   -Mode Unregister -TaskName <n>
#
# Register refuses to overwrite an existing task of the same name.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Register", "Inspect", "Census", "Unregister")]
    [string]$Mode,

    [string]$TaskName,
    [string]$TriggerUtc,
    [string]$PythonExe,
    [string]$LauncherPath,
    [string]$ConfigPath,
    [string]$WorkingDirectory,
    [switch]$Rehearsal,
    [int]$EndBoundaryMinutes = 30,
    [int]$ExecutionLimitHours = 2,
    [string[]]$MatchPaths
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Error "REFUSED: $Message"
    exit 1
}

function Require-AbsoluteFile([string]$Path, [string]$Label) {
    if (-not $Path) { Fail "$Label is required" }
    if (-not [System.IO.Path]::IsPathRooted($Path)) { Fail "$Label must be absolute: $Path" }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Fail "$Label does not exist: $Path" }
}

function Parse-Utc([string]$Value) {
    try {
        $parsed = [datetimeoffset]::Parse(
            $Value,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal
        )
    } catch {
        Fail "TriggerUtc is not a parseable timestamp: $Value"
    }
    if ($parsed.Offset -ne [timespan]::Zero) { Fail "TriggerUtc must be UTC (Z): $Value" }
    return $parsed
}

if ($Mode -eq "Register") {
    if (-not $TaskName) { Fail "TaskName is required" }
    Require-AbsoluteFile $PythonExe "PythonExe"
    Require-AbsoluteFile $LauncherPath "LauncherPath"
    Require-AbsoluteFile $ConfigPath "ConfigPath"
    if (-not $WorkingDirectory) { Fail "WorkingDirectory is required" }
    if (-not [System.IO.Path]::IsPathRooted($WorkingDirectory)) { Fail "WorkingDirectory must be absolute" }
    if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) { Fail "WorkingDirectory does not exist" }

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $existing) { Fail "task '$TaskName' already exists; unregister it explicitly first" }

    $triggerUtcParsed = Parse-Utc $TriggerUtc
    $triggerLocal = $triggerUtcParsed.ToLocalTime().DateTime
    $endLocal = $triggerUtcParsed.AddMinutes($EndBoundaryMinutes).ToLocalTime().DateTime
    if ($triggerUtcParsed -le [datetimeoffset]::UtcNow) { Fail "TriggerUtc is in the past" }

    $arguments = "`"$LauncherPath`" --config `"$ConfigPath`""
    if ($Rehearsal) { $arguments = "$arguments --rehearsal" }

    $action = New-ScheduledTaskAction -Execute $PythonExe -Argument $arguments -WorkingDirectory $WorkingDirectory
    $trigger = New-ScheduledTaskTrigger -Once -At $triggerLocal
    $trigger.EndBoundary = $endLocal.ToString("yyyy-MM-dd'T'HH:mm:ss")
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -WakeToRun `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours $ExecutionLimitHours) `
        -DeleteExpiredTaskAfter (New-TimeSpan -Hours 6) `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    # StartWhenAvailable defaults to $false and MUST stay false (no queued late starts).

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings | Out-Null
    Write-Output "registered: $TaskName trigger_local=$triggerLocal end_local=$endLocal rehearsal=$($Rehearsal.IsPresent)"
    # Immediately self-inspect so registration and verification are one step.
    $Mode = "Inspect"
}

if ($Mode -eq "Inspect") {
    if (-not $TaskName) { Fail "TaskName is required" }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { Fail "task '$TaskName' is not registered" }
    $xml = Export-ScheduledTask -TaskName $TaskName
    $parsed = [xml]$xml

    $problems = @()
    $settings = $parsed.Task.Settings
    if ($settings.WakeToRun -ne "true") { $problems += "WakeToRun is not true" }
    if ($settings.MultipleInstancesPolicy -ne "IgnoreNew") { $problems += "MultipleInstancesPolicy is not IgnoreNew" }
    if ($settings.StartWhenAvailable -eq "true") { $problems += "StartWhenAvailable must not be set" }
    if (-not $settings.ExecutionTimeLimit -or $settings.ExecutionTimeLimit -eq "PT0S") {
        $problems += "ExecutionTimeLimit is unbounded"
    }
    if (-not $settings.DeleteExpiredTaskAfter) { $problems += "DeleteExpiredTaskAfter is not set" }
    if ($parsed.Task.Principals.Principal.LogonType -ne "InteractiveToken") {
        $problems += "LogonType is not InteractiveToken"
    }
    $trigger = $parsed.Task.Triggers.TimeTrigger
    if ($null -eq $trigger) { $problems += "no one-time TimeTrigger present" }
    elseif (-not $trigger.EndBoundary) { $problems += "trigger has no EndBoundary" }
    $exec = $parsed.Task.Actions.Exec
    if ($null -eq $exec) { $problems += "no Exec action present" }
    elseif (-not [System.IO.Path]::IsPathRooted($exec.Command)) { $problems += "action executable is not absolute" }

    Write-Output "--- task XML ---"
    Write-Output $xml
    Write-Output "--- inspection ---"
    if ($problems.Count -gt 0) {
        $problems | ForEach-Object { Write-Output "PROBLEM: $_" }
        Fail "inspection found $($problems.Count) problem(s)"
    }
    Write-Output ("inspection OK: trigger_start={0} end_boundary={1} exec={2} args={3} cwd={4}" -f `
        $trigger.StartBoundary, $trigger.EndBoundary, $exec.Command, $exec.Arguments, $exec.WorkingDirectory)
}

if ($Mode -eq "Census") {
    if (-not $MatchPaths -or $MatchPaths.Count -eq 0) { Fail "MatchPaths is required for census" }
    $hits = @()
    foreach ($task in Get-ScheduledTask) {
        foreach ($action in $task.Actions) {
            $blob = "$($action.Execute) $($action.Arguments) $($action.WorkingDirectory)"
            foreach ($needle in $MatchPaths) {
                if ($blob -and $blob.ToLowerInvariant().Contains($needle.ToLowerInvariant())) {
                    $info = $task | Get-ScheduledTaskInfo
                    $hits += [pscustomobject]@{
                        TaskName    = $task.TaskName
                        Path        = $task.TaskPath
                        State       = $task.State
                        NextRunTime = $info.NextRunTime
                        LastRunTime = $info.LastRunTime
                        LastResult  = $info.LastTaskResult
                        Action      = $blob
                    }
                    break
                }
            }
        }
    }
    if ($hits.Count -eq 0) {
        Write-Output "census: no scheduled task references the given paths"
    } else {
        Write-Output "census: $($hits.Count) task(s) reference the given paths"
        $hits | Format-List
    }
}

if ($Mode -eq "Unregister") {
    if (-not $TaskName) { Fail "TaskName is required" }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { Fail "task '$TaskName' is not registered" }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "unregistered: $TaskName"
}
