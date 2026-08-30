<#
    Headless build script for a BG3 class/subclass mod.
    The mod name comes from forge.json, so this file is not project-specific.

    Does the whole pipeline with no GUI tool and no Toolkit:
      1. XML/LSX well-formedness check
      2. handle cross-reference check (every handle used must be declared)
      3. Localization .xml -> .loca
      4. pack to .pak under dist\
      5. list the archive back so you can eyeball the layout
      6. deploy dist\*.pak into the game's Mods folder, and VERIFY it landed

    Set $Divine below, then:  .\build.ps1
    Add -SkipPack to run checks only. Add -SkipDeploy to build without installing.

    ---------------------------------------------------------------------------
    WHY STEP 6 LOOKS SO CONVOLUTED  (read before "simplifying" it)

    Claude Desktop is a packaged (MSIX) app. Windows redirects any write it makes
    under %LOCALAPPDATA% into

        %LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\...

    and then MERGES the two locations on read. So packing straight to
    %LOCALAPPDATA%\Larian Studios\...\Mods\ appears to succeed, `Test-Path`
    says the pak is there, and the game sees nothing - because it isn't there.
    This silently cost a whole session (2026-08-07): three "installed" mods that
    were never on disk, and every verification agreed with itself because every
    check ran inside the same redirect.

    Anything launched via explorer.exe is NOT a child of the packaged app, so it
    escapes the redirect. Step 6 therefore writes a shim .cmd, runs it through
    explorer.exe, and reads back a report the shim wrote to a NON-redirected path
    (dist\ lives under C:\Modding, which is not virtualised). That report is the
    only trustworthy evidence the deploy worked. Do not replace it with a
    Copy-Item + Test-Path - that is precisely the check that lies.
    ---------------------------------------------------------------------------
#>

[CmdletBinding()]
param(
    # The mod to build. Defaults to this script's own folder, which is how it behaved
    # when it lived inside one mod. It moved here so there is ONE build script for every
    # mod made with this toolchain instead of a frozen copy per project - a copy stops
    # receiving fixes the moment it is made.
    [string]$Workspace,
    [switch]$SkipPack,
    [switch]$SkipDeploy,
    [switch]$SkipValidate,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- CONFIG ----
$Divine    = "C:\Modding\tools\lslib\Packed\Tools\Divine.exe"   # LSLib v1.20.4
if ([string]::IsNullOrWhiteSpace($Workspace)) { $Workspace = $PSScriptRoot }
if (-not (Test-Path $Workspace)) { throw "no such workspace: $Workspace" }

# The mod's name comes from forge.json, same source the Python tools read, so this
# script works unchanged in any mod built with this toolchain. Refuse rather than fall
# back to the folder name: a wrong name here packs a pak the game will not load, and
# names nothing in the error.
$ForgeJson = Join-Path $Workspace 'forge.json'
if (-not (Test-Path $ForgeJson)) {
    throw "no forge.json in $Workspace - every tool here reads the mod's name and layout from it. Run 'py forge.py init' in the mod root."
}
$ModName = (Get-Content $ForgeJson -Raw | ConvertFrom-Json).name
if ([string]::IsNullOrWhiteSpace($ModName)) {
    throw "$ForgeJson has no 'name'. That name IS the folder under Public/ and Mods/, so nothing can be located without it."
}

# Pack under the workspace (C:\Modding - NOT virtualised), never straight into
# %LOCALAPPDATA%. See the header note on MSIX redirection.
$DistDir   = Join-Path $Workspace 'dist'
$OutPak    = Join-Path $DistDir "$ModName.pak"
$ModsDir   = Join-Path $env:LOCALAPPDATA "Larian Studios\Baldur's Gate 3\Mods"
# -----------------------------------------------------------------------------

Write-Host "`n=== $ModName build ===" -ForegroundColor Cyan
Write-Host "workspace : $Workspace"
Write-Host "output    : $OutPak"
Write-Host "deploy to : $ModsDir`n"

# --- 0a. self-test -----------------------------------------------------------
# Run every acceptance harness before running the checks that DEPEND on them. The
# order matters: validate.py, the board and feature_sig are only worth their exit
# codes if their own tests still pass, and four harnesses had been sitting across two
# repos with nothing running them together. A harness nobody runs rots silently and
# you find out on the day you finally need it to be right.
# Fast (seconds, not minutes) because it is subprocesses, not game data. No count
# quoted here on purpose - selftest.py prints the live one, and a number written
# into a comment is a number nobody remembers to update.
# The guard matters as much as the gate. The board runs this script as a status probe
# and one of the harnesses builds the board, so without this the two call each other
# forever - see selftest.py's GUARD comment for the full cycle.
if (-not $SkipSelfTest -and -not $env:FORGE_SELFTEST_RUNNING) {
    $selftest = Join-Path $Workspace 'tools\selftest.py'
    if (Test-Path $selftest) {
        # Skip a re-run whose answer is already known. The stamp hashes every .py the
        # suite runs OR exercises, so it only matches when nothing under test moved.
        # ⚠ A GREEN STAMP IS THE ONLY SKIPPABLE ONE - a red result always re-runs, so a
        #   failing suite can never be cached into a pass.
        $stamp = Join-Path $Workspace 'tools\out\selftest.json'
        $skip = $false
        if (Test-Path $stamp) {
            try {
                $prev = Get-Content $stamp -Raw | ConvertFrom-Json
                $now = & py $selftest --fingerprint 2>$null
                if ($prev.ok -and $prev.fingerprint -and $now -and $prev.fingerprint -eq $now.Trim()) {
                    Write-Host "[0a/6] Self-test SKIPPED - $($prev.checks) checks across $($prev.harnesses) harnesses were green at $($prev.when) and no tool has changed since." -ForegroundColor DarkGray
                    $skip = $true
                }
            } catch { $skip = $false }
        }
        if (-not $skip) {
            Write-Host "[0a/6] Self-testing the toolchain..." -ForegroundColor Yellow
            & py $selftest
            if ($LASTEXITCODE -ne 0) {
                throw "A tool's own acceptance harness is failing. Fix that before trusting anything else this build reports, or re-run with -SkipSelfTest."
            }
        }
    }
}

# --- 0. static validation ----------------------------------------------------
# Gate the whole build on tools/validate.py. It checks the mod against the real
# shipped game data (invented functors, dangling references, functors used in a
# field vanilla never uses them in, malformed SpellAnimation, broken UUID wiring).
# Runs with -SkipFreshness because the pak is about to be rebuilt by definition -
# the staleness check is for the pre-launch run, not the build itself.
# Known-and-accepted findings live in tools/validate-baseline.txt.
if (-not $SkipValidate) {
    Write-Host "[0/6] Validating against shipped game data..." -ForegroundColor Yellow
    $validator = Join-Path $Workspace 'tools\validate.py'
    if (-not (Test-Path $validator)) {
        Write-Host "  validate.py not found - skipping" -ForegroundColor DarkYellow
    } else {
        & py $validator --skip-freshness
        if ($LASTEXITCODE -ne 0) {
            throw "Validation failed. Fix the ERRORs above, or re-run with -SkipValidate to pack anyway."
        }
        Write-Host "  ok" -ForegroundColor Green
    }

    # fx_audit's text-key check, gated here for the same reason validate.py is: it
    # catches a failure mode that is invisible until playtest. An EffectInfo keyed to a
    # text event its animation does not contain simply never fires - no error, no log -
    # and if it detaches it is left behind where the caster was standing. Warp Assault
    # shipped four of those for two weeks and took three live reports to find. ERRORs
    # only; the WARNs (a key some weapon rigs lack) are reported but do not block.
    $fx = Join-Path $Workspace 'tools/fx_audit.py'
    if (Test-Path $fx) {
        Write-Host "[0b/6] Auditing effects, sounds and animation text keys..." -ForegroundColor Yellow
        & py $fx
        if ($LASTEXITCODE -ne 0) {
            throw "fx_audit failed. Fix the ERRORs above, or re-run with -SkipValidate to pack anyway."
        }
        Write-Host "  ok" -ForegroundColor Green
    }
}

# --- 1. XML well-formedness --------------------------------------------------
Write-Host "[1/6] Checking XML/LSX well-formedness..." -ForegroundColor Yellow
$badXml = 0
Get-ChildItem $Workspace -Recurse -Include *.lsx,*.xml | ForEach-Object {
    # capture the path first - inside catch, $_ rebinds to the ErrorRecord
    $path = $_.FullName
    try { [xml](Get-Content $path -Raw) | Out-Null }
    catch {
        $msg = $_.Exception.Message
        if ($msg.Length -gt 300) { $msg = $msg.Substring($msg.Length - 300) }
        Write-Host "  INVALID: $path" -ForegroundColor Red
        Write-Host "           $msg" -ForegroundColor Red
        $script:badXml++
    }
}
if ($badXml -gt 0) { throw "$badXml malformed XML file(s). Fix before packing." }
Write-Host "  ok" -ForegroundColor Green

# --- 2. handle cross-reference ----------------------------------------------
Write-Host "[2/6] Cross-referencing localisation handles..." -ForegroundColor Yellow
$locaFiles = Get-ChildItem (Join-Path $Workspace 'Localization') -Recurse -Filter *.xml
$declared = @{}
foreach ($f in $locaFiles) {
    [regex]::Matches((Get-Content $f.FullName -Raw), 'contentuid="(h[0-9a-fg]+)"') |
        ForEach-Object { $declared[$_.Groups[1].Value] = $true }
}
$missing = @{}
Get-ChildItem (Join-Path $Workspace 'Public'), (Join-Path $Workspace 'Mods') -Recurse -Include *.lsx,*.txt |
    ForEach-Object {
        $file = $_.FullName
        [regex]::Matches((Get-Content $file -Raw), '\b(h[0-9a-f]{8}g[0-9a-fg]{27})\b') |
            ForEach-Object {
                $h = $_.Groups[1].Value
                if (-not $declared.ContainsKey($h)) { $missing[$h] = $file }
            }
    }
if ($missing.Count -gt 0) {
    foreach ($k in $missing.Keys) { Write-Host "  MISSING HANDLE: $k  (used in $($missing[$k]))" -ForegroundColor Red }
    throw "$($missing.Count) handle(s) referenced but not declared in Localization."
}
Write-Host "  ok - $($declared.Count) handles declared, all references resolve" -ForegroundColor Green

if ($SkipPack) { Write-Host "`n-SkipPack set. Stopping after checks.`n" -ForegroundColor Cyan; return }

if (-not (Test-Path $Divine)) { throw "Divine.exe not found at '$Divine'. Edit `$Divine at the top of this script." }

# --- 3. localisation ---------------------------------------------------------
Write-Host "[3/6] Compiling localisation..." -ForegroundColor Yellow
foreach ($f in $locaFiles) {
    $dest = [IO.Path]::ChangeExtension($f.FullName, 'loca')
    & $Divine -g bg3 -a convert-loca -s $f.FullName -d $dest
    if ($LASTEXITCODE -ne 0) { throw "convert-loca failed for $($f.Name)" }
    Write-Host "  $($f.Name) -> $(Split-Path $dest -Leaf)" -ForegroundColor Green
}

# --- 4. stage + pack ---------------------------------------------------------
# Pack from a STAGING copy, not the workspace. Divine packs everything under
# -s, so packing the workspace directly ships build.ps1, README.md, DEVELOPMENT.md, tools/ and
# the loca .xml source inside the mod. Only three folders belong in the pak, and
# localisation ships compiled (.loca) only.
Write-Host "[4/6] Staging + packing..." -ForegroundColor Yellow
# stage under the workspace, not %TEMP% - %TEMP% is under %LOCALAPPDATA% and is
# therefore redirected too (see header note)
$stage = Join-Path $Workspace 'obj\stage'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

foreach ($folder in 'Mods','Public') {
    Copy-Item (Join-Path $Workspace $folder) -Destination $stage -Recurse -Force
}
# localisation: compiled .loca only
$locDest = Join-Path $stage 'Localization\English'
New-Item -ItemType Directory -Force -Path $locDest | Out-Null
Get-ChildItem (Join-Path $Workspace 'Localization') -Recurse -Filter *.loca |
    ForEach-Object { Copy-Item $_.FullName $locDest -Force }

# The game reads .DDS. The .png beside each one is make_icons.py's editable
# master, and it was shipping too - 245 KB, 22% of the extracted payload, for
# files BG3 never opens. Found in the 2026-08-29 pak audit. Dropped from the
# STAGE, never from the workspace: the pngs are the source art and regenerating
# the DDS needs them.
$png = @(Get-ChildItem $stage -Recurse -File -Filter *.png)
if ($png.Count -gt 0) {
    $pngKB = [Math]::Round(($png | Measure-Object Length -Sum).Sum / 1KB)
    $png | Remove-Item -Force
    Write-Host "  dropped $($png.Count) .png ($pngKB KB) - the game reads the .DDS" -ForegroundColor DarkGray
}

# A staging step that silently stages nothing would pack an empty mod, and
# "0 errors" over an empty pak is the failure this repo spent 2026-08-29 finding.
$staged = (Get-ChildItem $stage -Recurse -File).Count
if ($staged -lt 20) {
    throw "Only $staged file(s) staged - that is not a Warpblade build. Refusing to pack."
}
Write-Host "  staged $staged files" -ForegroundColor Green

$outDir = Split-Path $OutPak -Parent
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
& $Divine -g bg3 -a create-package -s $stage -d $OutPak -c lz4hc
if ($LASTEXITCODE -ne 0) { throw "create-package failed" }
Remove-Item $stage -Recurse -Force
Write-Host "  ok -> $OutPak ($([Math]::Round((Get-Item $OutPak).Length/1KB)) KB)" -ForegroundColor Green

# --- 5. verify archive layout ------------------------------------------------
Write-Host "[5/6] Archive contents (expect Localization/, Mods/, Public/ at root):" -ForegroundColor Yellow
& $Divine -g bg3 -a list-package -s $OutPak

# --- 5b. regenerate info.json ------------------------------------------------
# BG3MM and mod.io read this; it ships alongside the pak, not inside it. Generated
# from meta.lsx every build so it can never disagree with the mod's own metadata,
# and so its MD5 always matches the pak that was just written.
# --- feature drift ------------------------------------------------------------
# The comment at the top of this file has named feature_sig as part of the gate
# sequence since it was written. It was never actually invoked. It reported TWO
# features whose implementation had changed since the day they were verified in
# play - and nothing consumed that, so the mod carried "21 verified" while two of
# those verifications were stale. Found 2026-08-29.
#
# WARNING, not a throw: drift is expected mid-development - you change a feature,
# then you re-verify it in the next live pass. Failing the build on it would make
# every legitimate edit unbuildable, and a gate that blocks normal work gets
# -Skip'd within a week and then guards nothing. The point is that it is SAID.
$fsig = Join-Path $Workspace 'tools\feature_sig.py'
if (Test-Path $fsig) {
    Write-Host "[5b/6] Feature drift..." -ForegroundColor Yellow
    & py $fsig
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ^ features have drifted since they were verified IN PLAY. Not a build failure - but do not call those features verified until a live pass re-blesses them." -ForegroundColor Yellow
    }
}

$rc = Join-Path $Workspace 'tools\release_check.py'
if (Test-Path $rc) {
    & py $rc --emit-info
    if ($LASTEXITCODE -ne 0) { Write-Host "  info.json generation failed" -ForegroundColor Red }
}

# --- 6. deploy OUTSIDE the MSIX container, then verify -----------------------
if ($SkipDeploy) {
    Write-Host "`n-SkipDeploy set. Pak is at $OutPak but was NOT installed.`n" -ForegroundColor Cyan
    return
}

Write-Host "`n[6/6] Deploying outside the app container..." -ForegroundColor Yellow

$shim   = Join-Path $DistDir '_deploy.cmd'
$report = Join-Path $DistDir '_deploy_report.txt'
Remove-Item $report -Force -ErrorAction SilentlyContinue

@"
@echo off
setlocal
if not exist "$ModsDir" mkdir "$ModsDir"
echo ===== COPY ===== > "$report"
copy /Y "$OutPak" "$ModsDir\$ModName.pak" >> "$report" 2>&1
echo. >> "$report"
echo ===== REAL MODS DIR ===== >> "$report"
dir /-C "$ModsDir" >> "$report" 2>&1
echo DONE >> "$report"
endlocal
"@ | Set-Content -LiteralPath $shim -Encoding ascii

Start-Process explorer.exe -ArgumentList "`"$shim`""

$waited = 0
while (-not (Test-Path $report) -and $waited -lt 15) { Start-Sleep -Milliseconds 500; $waited++ }
Start-Sleep -Milliseconds 800

if (-not (Test-Path $report)) {
    throw "Deploy shim produced no report. The pak is built at $OutPak but is NOT installed - copy it into $ModsDir by hand."
}

$rpt = Get-Content $report -Raw
Write-Host $rpt -ForegroundColor DarkGray

# the report is written by a NON-redirected process, so this is real evidence
if ($rpt -notmatch [regex]::Escape("$ModName.pak")) {
    throw "Deploy could not be verified - '$ModName.pak' is absent from the real Mods folder listing."
}
Write-Host "  verified on disk (report written from outside the container)" -ForegroundColor Green

Write-Host "`nDone. $ModName.pak is installed and confirmed present." -ForegroundColor Cyan
Write-Host "modsettings.lsx already lists the load order, so BG3 Mod Manager is NOT" -ForegroundColor Cyan
Write-Host "required - launch straight from Steam." -ForegroundColor Cyan
Write-Host "Load order relative to Compatibility Framework does NOT matter: CF's" -ForegroundColor Cyan
Write-Host "LoadConfigFiles() iterates Ext.Mod.GetLoadOrder() and scans every mod," -ForegroundColor Cyan
Write-Host "and its subclass auto-detection reads ALL ClassDescriptions. Verified in" -ForegroundColor Cyan
Write-Host "CF 2.9.0.0 source, 2026-08-09.`n" -ForegroundColor Cyan
