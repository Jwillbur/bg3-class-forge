<#
    undeploy.ps1 - remove a .pak from the game's Mods folder, from OUTSIDE the
    app container.

    ⚠ WHY THIS IS NOT `Remove-Item`. The Mods folder lives under %LOCALAPPDATA%,
      which is MSIX-redirected for a packaged app: writes and deletes land in this
      app's private VFS while READS merge the VFS over the real folder. So a
      Remove-Item here reports success, a following Test-Path agrees the file is
      gone, and the real file is untouched - the game still loads it. That is the
      same lie build.ps1's step 6 exists to avoid, and this is its mirror image.

      The escape is the same one: anything launched through explorer.exe is not a
      child of the packaged app. This writes a shim .cmd, runs it via explorer,
      and reads back a report the shim wrote to a NON-redirected path.

    Usage:
      pwsh forge/undeploy.ps1 -Names 'OathOfTheWeave','Kiln'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string[]]$Names,
    [string]$ModsDir = "$env:LOCALAPPDATA\Larian Studios\Baldur's Gate 3\Mods",
    # Must live somewhere that is NOT virtualised. C:\Modding is not.
    [string]$WorkDir = "C:\Modding\staging\undeploy"
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$shim = Join-Path $WorkDir 'undeploy.cmd'
$report = Join-Path $WorkDir 'undeploy-report.txt'
if (Test-Path $report) { Remove-Item $report -Force }

$lines = @('@echo off', "if exist `"$report`" del /q `"$report`"")
foreach ($n in $Names) {
    $target = Join-Path $ModsDir "$n.pak"
    $lines += "echo ===== $n ===== >> `"$report`""
    $lines += "if exist `"$target`" (del /f /q `"$target`" && echo DELETED >> `"$report`") else (echo NOT PRESENT >> `"$report`")"
    $lines += "if exist `"$target`" (echo STILL THERE >> `"$report`") else (echo CONFIRMED GONE >> `"$report`")"
}
$lines += "echo ===== REMAINING ===== >> `"$report`""
$lines += "dir /b `"$ModsDir\*.pak`" >> `"$report`""
$lines += "echo DONE >> `"$report`""
Set-Content -Path $shim -Value $lines -Encoding ascii

Start-Process explorer.exe -ArgumentList "`"$shim`""

$waited = 0
while (-not (Test-Path $report) -and $waited -lt 20) { Start-Sleep -Milliseconds 500; $waited++ }
Start-Sleep -Milliseconds 800

if (-not (Test-Path $report)) {
    throw "Undeploy shim produced no report. Nothing was verified - delete by hand in $ModsDir."
}
Get-Content $report
