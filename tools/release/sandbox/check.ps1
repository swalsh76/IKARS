# The clean-machine checks for M1.9a, run inside Windows Sandbox (a fresh Windows each time). The
# build's releases are mapped read-only at C:\IKARS-releases. Each step writes yes/no lines to the
# Sandbox's desktop (IKARS-check.txt). Steps, in order:
#   check.ps1 install       install Setup.exe silently and check what it made
#   check.ps1 uninstalled   after uninstalling from Settings > Apps: the data is still there
#   check.ps1 portable      run the portable zip from a folder of its own
param([Parameter(Mandatory)][ValidateSet("install", "uninstalled", "portable")][string]$Step)

$releases = "C:\IKARS-releases"
$report = Join-Path ([Environment]::GetFolderPath("Desktop")) "IKARS-check.txt"
$program = Join-Path $env:LOCALAPPDATA "IKARS-App"
$data = Join-Path $env:LOCALAPPDATA "IKARS"
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\IKARS.lnk"

function Say([string]$what, [bool]$ok) {
    $line = "{0,-70} {1}" -f $what, $(if ($ok) { "yes" } else { "NO" })
    Add-Content -Path $report -Value $line
    Write-Host $line
}

function WaitFor([scriptblock]$test, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) { if (& $test) { return $true }; Start-Sleep -Seconds 2 }
    return $false
}

Add-Content -Path $report -Value "`n== $Step ($(Get-Date -Format s)) =="
switch ($Step) {
    "install" {
        Say "Python is not on this machine (a clean Windows)" (-not (Get-Command python -ErrorAction SilentlyContinue))
        Start-Process -FilePath (Join-Path $releases "IKARS-App-win-Setup.exe") -ArgumentList "--silent" -Wait
        Say "installed to %LOCALAPPDATA%\IKARS-App" (Test-Path (Join-Path $program "current\IKARS.exe"))
        Say "Start-menu entry IKARS" (Test-Path $menu)
        Say "no desktop shortcut" (-not (Test-Path (Join-Path ([Environment]::GetFolderPath("Desktop")) "IKARS.lnk")))
        # a silent install starts nothing: start IKARS as the owner would, from its Start-menu entry
        Start-Process -FilePath $menu
        $started = WaitFor { Test-Path (Join-Path $data "data\api.json") } 180
        Say "the engine started from the Start menu (setup mode: no key here)" $started
        Say "an IKARS window is open in Edge" ($null -ne (Get-Process msedge -ErrorAction SilentlyContinue))
        if (-not $started) {  # a clean machine has no key or account: these hold nothing private
            $logs = @((Join-Path $data "logs\ikars.jsonl"), (Join-Path $program "velopack.log"))
            foreach ($log in $logs) {
                if (Test-Path $log) { Write-Host "-- $log"; Get-Content $log -Tail 8 | Tee-Object -FilePath $report -Append }
            }
            Write-Host "-- python processes:"; Get-Process python* -ErrorAction SilentlyContinue | Format-Table Id, Path -AutoSize
            Write-Host "-- opening by hand:"
            & (Join-Path $program "current\python\python.exe") -I -m ikars_engine open 2>&1 | Select-Object -Last 8
        }
        Say "the data folder is apart from the program's" (-not (Test-Path (Join-Path $program "IKARS-data")))
        Write-Host "Now: walk the wizard in the Edge window, then uninstall IKARS from Settings > Apps, then run: check.ps1 uninstalled"
    }
    "uninstalled" {
        Say "the program folder is gone" (WaitFor { -not (Test-Path (Join-Path $program "current")) } 60)
        Say "the Start-menu entry is gone" (-not (Test-Path $menu))
        Say "the data folder is kept" (Test-Path $data)
        Say "the settings are kept" (Test-Path (Join-Path $data "settings.toml"))
        Say "start at sign-in is removed" (-not (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "IKARS Engine" -ErrorAction SilentlyContinue))
        Say "the watchdog task is removed" (-not (Get-ScheduledTask -TaskName "IKARS Watchdog" -ErrorAction SilentlyContinue))
    }
    "portable" {
        $folder = Join-Path ([Environment]::GetFolderPath("Desktop")) "IKARS portable"
        Expand-Archive (Join-Path $releases "IKARS-App-win-Portable.zip") -DestinationPath $folder -Force
        Start-Process -FilePath (Join-Path $folder "IKARS.exe")
        Say "the portable data folder appeared beside it" (WaitFor { Test-Path (Join-Path $folder "IKARS-data\data\api.json") } 180)
        Say "nothing written inside its program folder (current)" (-not (Test-Path (Join-Path $folder "current\IKARS-data")))
    }
}
Write-Host "Report: $report"
