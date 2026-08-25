<#
    Shim. The real build script is the Class Forge's build.ps1.

    It lives there so there is ONE build script for every mod made with this toolchain.
    A copy in each project would freeze: it stops receiving fixes the moment it is made.

        .\build.ps1              # validate, compile loca, pack, deploy
        .\build.ps1 -SkipPack    # checks only
#>
[CmdletBinding()]
param(
    [switch]$SkipPack,
    [switch]$SkipDeploy,
    [switch]$SkipValidate,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = 'Stop'

# Walk up looking for forge\build.ps1 rather than assuming a fixed depth.
$dir = $PSScriptRoot
$real = $null
while ($dir -and -not $real) {
    $candidate = Join-Path $dir 'forge\build.ps1'
    if (Test-Path $candidate) { $real = $candidate; break }
    $parent = Split-Path $dir -Parent
    if ($parent -eq $dir) { break }
    $dir = $parent
}

if (-not $real) {
    throw "cannot find forge\build.ps1 at or above $PSScriptRoot. Keep this mod beside the Class Forge folder, or copy forge\build.ps1 here and edit this shim away."
}

& $real -Workspace $PSScriptRoot -SkipPack:$SkipPack -SkipDeploy:$SkipDeploy `
        -SkipValidate:$SkipValidate -SkipSelfTest:$SkipSelfTest
exit $LASTEXITCODE
