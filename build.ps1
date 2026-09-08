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
    # Explicit override for the LSLib Divine.exe. Highest-priority source in the
    # discovery order below; use it when you have several LSLib builds around.
    [string]$DivinePath,
    # Allow a workspace with no tools/validate.py to build anyway. Without this a
    # missing validator is a hard failure - see the gate at step 0.
    [switch]$AllowMissingValidator,
    [switch]$SkipPack,
    [switch]$SkipDeploy,
    [switch]$SkipValidate,
    [switch]$SkipSelfTest
)

$ErrorActionPreference = 'Stop'

# --- validation provenance ---------------------------------------------------
# ⚠ A .pak SITTING ON DISK IS NOT EVIDENCE. Raised by the 2026-08-25 review: days
# later nobody can tell whether a pak was validated, merely packed, or actually
# playtested, and the difference is the whole question. Every gate below records
# its own verdict here and the build writes dist/validation-report.json at the end.
# A SKIPPED gate is recorded as skipped WITH ITS REASON - never omitted, because an
# absent line reads like a pass to anyone scanning the file.
$script:GateResults = [ordered]@{}
$script:GateSkips   = @()

function Write-Provenance {
    # Called at EVERY successful exit, not just the last one. The first version sat
    # after the deploy step, so -SkipDeploy returned before it and a perfectly good
    # build produced no report at all - caught by build_acceptance.py, which is the
    # entire reason that harness exists. A record that is missing on one legitimate
    # path is worse than no record, because its absence looks like a failed build.
    if (-not $script:ModName -or -not $script:OutPak) { return }
    $dir = Join-Path $Workspace 'dist'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    $report = [ordered]@{
        mod          = $script:ModName
        pak          = $script:OutPak
        source_root  = "$Workspace"
        divine       = "$script:Divine"
        validated_at = (Get-Date).ToString('o')
        checks       = $script:GateResults
        skips        = $script:GateSkips
        note         = ("Tracked gates only. A gate absent from 'checks' is NOT tracked " +
                        "yet and must not be read as a pass. Nothing here is evidence " +
                        "the mod was PLAYED - that is a human watching the game.")
    }
    # ⚠ NOT Set-Content -Encoding utf8. On Windows PowerShell 5.1 that writes a BOM,
    # and a BOM at the head of a .json makes strict parsers throw before they read a
    # byte of it - Python's json.loads among them. This file exists to be read by
    # other tools, so it is written as BOM-less UTF-8 and a control asserts that.
    [IO.File]::WriteAllText((Join-Path $dir 'validation-report.json'),
                            ($report | ConvertTo-Json -Depth 5),
                            (New-Object Text.UTF8Encoding($false)))
    Write-Host "  provenance -> dist/validation-report.json" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------- CONFIG ----
# Divine is DISCOVERED, not hardcoded. The old single literal path was the first
# thing that broke for an outside user of this repo (deep-analysis report,
# 2026-08-25): their LSLib lived at C:\Tools\LSLib and the build refused, telling
# them to edit a script they had just cloned. Order is most-specific first.
$DivineCandidates = @(
    $DivinePath,
    $env:BG3_DIVINE,
    (Get-Command divine.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source),
    "C:\Modding\tools\lslib\Packed\Tools\Divine.exe",     # LSLib v1.20.4, this machine
    "C:\Tools\LSLib\Packed\Tools\Divine.exe"
)
# NOTE: the winner is chosen at step 3 by PROBE, not here by existence - see
# Test-DivineLoca. Nothing before step 3 uses Divine, so there is deliberately no
# selection at config time; a second source of truth would only drift.
if ([string]::IsNullOrWhiteSpace($Workspace)) { $Workspace = $PSScriptRoot }
if (-not (Test-Path $Workspace)) { throw "no such workspace: $Workspace" }
# ⚠ RESOLVE TO ABSOLUTE. Divine refuses a relative -s with "[FATAL] Cannot proceed
# without absolute path [E2]", and every path below is derived from $Workspace. This
# only ever worked because the one caller - Warpblade - was always invoked with an
# absolute path; `-Workspace .` died at the pack step. Found 2026-09-01 by building a
# generated probe mod from inside its own directory, which is how anyone else would
# run it. Same class as the "-lt 20 / not a Warpblade build" guard: a shared script
# carrying an assumption that only held for the project it grew up in.
$Workspace = (Resolve-Path -LiteralPath $Workspace).Path

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

# ⛔ THE PYTHON GATES LOCATE THE MOD FROM THE CURRENT DIRECTORY, NOT FROM
# -Workspace. Nothing here set one, so every gate ran wherever the CALLER happened
# to be standing: the same build passed from inside the mod folder and failed from
# C:\Modding\Toolkit with "cannot locate this mod: no forge.json at or above ...".
# Found 2026-09-08 after two consecutive runs of an unchanged tree disagreed - and
# a gate whose verdict depends on the caller's shell is not a gate.
# Pop-Location is in a finally so a thrown gate does not strand the caller.
Push-Location -LiteralPath $Workspace
try {
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
        # ⚠ THIS USED TO BE A SKIP, AND THAT MADE THE GATE OPTIONAL IN PRACTICE.
        # Flagged by an outside review (2026-08-25): the framework "documents a
        # strong validation regime, but the generated mod does not actually receive
        # a validator", so a freshly scaffolded mod packed with no mechanical checks
        # at all while the build printed a reassuring line. forge.py now scaffolds a
        # starter tools/validate.py, so an absent one means something was deleted or
        # the workspace is not a forge mod - both worth stopping for.
        if ($AllowMissingValidator) {
            Write-Host "  validate.py not found - skip EXPLICITLY allowed" -ForegroundColor DarkYellow
            $script:GateResults['project_validate_py'] = 'skipped'
            $script:GateSkips += "validate.py absent, -AllowMissingValidator passed"
        } else {
            throw ("tools/validate.py not found in $Workspace. The build refuses without a " +
                   "project validator. Scaffolds get one from forge.py; pass " +
                   "-AllowMissingValidator only for a plumbing-only workspace, or " +
                   "-SkipValidate to override the gate deliberately.")
        }
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
    # A mod that ships its own tools/fx_audit.py is GATED on it. A mod without one
    # gets the forge copy as ADVICE: it still runs and still prints, but it does not
    # fail the build, because the fixture workspaces build_acceptance constructs have
    # no corpus for it to read and a hard gate there fails 9 controls over nothing.
    $fxAdvisory = $false
    if (-not (Test-Path $fx)) { $fx = Join-Path $PSScriptRoot 'fx_audit.py'; $fxAdvisory = $true }
    if (Test-Path $fx) {
        Write-Host "[0b/6] Auditing effects, sounds and animation text keys..." -ForegroundColor Yellow
        & py $fx
        if ($LASTEXITCODE -ne 0) {
            if ($fxAdvisory) {
                Write-Host "  fx_audit reported problems (advisory - this mod ships no tools/fx_audit.py)" -ForegroundColor Yellow
            } else {
                throw "fx_audit failed. Fix the ERRORs above, or re-run with -SkipValidate to pack anyway."
            }
        }

        Write-Host "  ok" -ForegroundColor Green
    }

# ⭐ ANIMATION SLOTS. A SpellAnimation GUID that resolves to no clips
    # plays nothing, silently, and every other gate passes it.
    Write-Host "[0e/6] Resolving animation slots..." -ForegroundColor Yellow
    # $PSScriptRoot IS the forge directory - this script lives in it. An earlier
    # version used $Forge, which does not exist, so Test-Path was false and the
    # gate silently never ran. That is the exact failure it was written to catch.
    $ak = Join-Path $PSScriptRoot "anim_textkeys.py"
    if (Test-Path $ak) {
        & py $ak --rebuild
        if ($LASTEXITCODE -ne 0) {
            # A NEW gate must not silently redefine "a good build" for every mod that
            # already exists. Advice unless a mod opts in with "gate_animation_slots":
            # true in its forge.json. Oath of Avernus does.
            $gateAnim = $false
            try { $gateAnim = (Get-Content $ForgeJson -Raw | ConvertFrom-Json).gate_animation_slots -eq $true } catch {}
            if (-not $gateAnim) {
                Write-Host "  animation slots unresolved (advisory - set gate_animation_slots in forge.json to gate on it)" -ForegroundColor Yellow
            } else {
                throw "anim_textkeys failed: an animation slot resolves to NO clips. Fix the GUID, or accept it in corpus/anim_textkeys_accepted.json."
            }
        }
    }

    # ⭐ SHAPESHIFT RULES. A POLYMORPHED status keeps exactly what its `Rules`
    # resource permits. A missing `Rules` field takes an undocumented engine
    # default; a MISSPELLED rule attribute is ignored rather than rejected, so the
    # only symptom is a screenshot. Oath of Avernus shipped polymorphs with no
    # `Rules` at all for a day and every other gate passed them.
    # Unconditional, unlike the animation gate: a mod with no polymorphs reports
    # "0 checked" and costs nothing, so there is no existing build to redefine.
    Write-Host "[0f/6] Checking shapeshift rules..." -ForegroundColor Yellow
    $ss = Join-Path $PSScriptRoot "shapeshift_audit.py"
    if (Test-Path $ss) {
        & py $ss
        # ⚠ 1 and 2 are DIFFERENT ANSWERS and conflating them broke this gate on
        # its first run. 1 = it looked and found a fault. 2 = it could not look,
        # usually because the game data is not unpacked on this machine. Only the
        # first is a reason to refuse to build.
        if ($LASTEXITCODE -eq 1) {
            throw "shapeshift_audit failed - see the errors above. A polymorph with a bad or missing Rules reference does not fail loudly in game; it just keeps the wrong things."
        } elseif ($LASTEXITCODE -ne 0) {
            Write-Host "  NOT CHECKED - shapeshift rules were not verified (exit $LASTEXITCODE). This is not a pass." -ForegroundColor Yellow
        } else {
            Write-Host "  ok" -ForegroundColor Green
        }
    } else {
        Write-Host "  shapeshift_audit.py not found beside build.ps1 - NOT CHECKED" -ForegroundColor Yellow
    }

    # A field the engine does not read on THIS entry type is invisible to every other
    # gate here: the name is real, the syntax parses, validate.py is happy, and the
    # feature simply never runs. AVERNUS_ZARIELS_FAVOR carried StatsFunctorContext,
    # Conditions and StatsFunctors on a StatusData - 678/647/785 shipped uses on
    # PassiveData, ZERO on StatusData - and shipped as a decorative icon.
    # The majority sweep cannot find these: it compares an entry to its peers OF THE
    # SAME TYPE, so a field that belongs to no peer is simply never counted.
    Write-Host "[0g/6] Checking for fields on the wrong entry type..." -ForegroundColor Yellow
    $wt = Join-Path $PSScriptRoot "wrongtype_audit.py"
    if (Test-Path $wt) {
        & py $wt $Workspace
        # ⚠ 1 and 2 are DIFFERENT ANSWERS. 1 = it looked and found a field the engine
        # will not read. 2 = the unpacked game data is missing, so it could not look.
        # Conflating them is what broke the 0f gate on its first run.
        if ($LASTEXITCODE -eq 1) {
            throw "wrongtype_audit failed - see above. A field on the wrong entry type is silently ignored in game; the feature just never fires."
        } elseif ($LASTEXITCODE -ne 0) {
            Write-Host "  NOT CHECKED - entry types were not verified (exit $LASTEXITCODE). This is not a pass." -ForegroundColor Yellow
        } else {
            Write-Host "  ok" -ForegroundColor Green
        }
    } else {
        Write-Host "  wrongtype_audit.py not found beside build.ps1 - NOT CHECKED" -ForegroundColor Yellow
    }

    # Three shapes, each one a bug a LIVE TEST found on 2026-09-08 after every static
    # gate here had passed the file:
    #   1 a value not attested for that field - the six spell lists used `Comment`,
    #     which appears 0 times in 358 shipped SpellList nodes, and an Icon name that
    #     does not exist draws a blank square on the character sheet.
    #   2 an identifier that resolves nowhere - a functor naming a status that is in
    #     neither the corpus nor our own files is a silent no-op.
    #   3 a feature on the wrong CARRIER - the aura applied itself from a passive on
    #     OnCreate; every shipped paladin aura is a free clickable Shout at duration -1.
    #     Class 3 is a NOTE, never a failure: it is a judgement call, not a fault.
    Write-Host "[0h/6] Checking value vocabularies and identifiers..." -ForegroundColor Yellow
    $cs = Join-Path $PSScriptRoot "class_sweep.py"
    if (Test-Path $cs) {
        & py $cs $Workspace
        # ⚠ 1 and 2 are DIFFERENT ANSWERS - see gate 0f, which broke by conflating them.
        # 1 = it looked and found an unattested value or a dangling name.
        # 2 = the unpacked game data is missing, so it could not look at all.
        if ($LASTEXITCODE -eq 1) {
            throw "class_sweep failed - see above. An unattested value or a name that resolves nowhere parses fine and is then ignored in game."
        } elseif ($LASTEXITCODE -ne 0) {
            Write-Host "  NOT CHECKED - values and identifiers were not verified (exit $LASTEXITCODE). This is not a pass." -ForegroundColor Yellow
        } else {
            Write-Host "  ok" -ForegroundColor Green
        }
    } else {
        Write-Host "  class_sweep.py not found beside build.ps1 - NOT CHECKED" -ForegroundColor Yellow
    }

    # sim.py runs the the mod's own functors against a scripted fight - a plain attack, a
    # status landing on the player, three hits into a detonation. Every Spatial Debt
    # regression so far cost a game launch to find, and three of them were visible in
    # the data the whole time (Pass 24, and Pass 29 twice).
    # WARN: REPORTS, DOES NOT BLOCK, and that is deliberate. Its scenarios encode
    #   DESIRED behaviour, so a known-and-accepted defect would wedge every build until
    #   it was fixed - which is how a gate gets bypassed permanently and then ignored.
    #   It prints its verdict on every build instead, where it cannot be missed.
    $sim = Join-Path $Workspace 'tools/sim.py'
    if (Test-Path $sim) {
        # The LINT half BLOCKS, unlike the scenarios below. A SavingThrow gated behind
        # another condition term is always a defect - it rolls on every event its field
        # fires on - and unlike a scenario it encodes no opinion about desired design,
        # so there is nothing to legitimately disagree with and nothing to wedge.
        # The TOOLKIT audits itself too. Every rule in tool_audit.py is a bug that
        # shipped in this repo; the ACCEPTED ledger keeps the run at zero unexplained
        # findings, so a NEW one is visible instead of being line eight of a report
        # nobody reads. Reports rather than blocks - it grades tools, not the mod.
        # ⚠ Forward slashes on purpose. This line was written once with '..\t...'
        #   and the \t became a literal TAB, so Test-Path got a path with the 't'
        #   eaten out of both 'tools' and 'tool_audit'. It failed loudly enough to
        #   spot but not to stop the build, so the audit silently never ran.
        $ta = Join-Path $PSScriptRoot '../tools/tool_audit.py'
        if (Test-Path $ta) {
            & py $ta | Select-Object -Last 3
        }
        # Does the mod tell the player the truth? Nothing else asks. A tooltip that
        # promises a status the functors never apply passes every other gate, because
        # both halves are well-formed on their own. Reports rather than blocks: its
        # EXPLAINED_BY ledger already keeps the run quiet, so a finding here is real.
        $tt = Join-Path $Workspace 'tools/tooltip_audit.py'
        if (Test-Path $tt) {
            Write-Host "[0c/6] Auditing tooltips against the functors..." -ForegroundColor Yellow
            & py $tt | Select-Object -Last 3
        }
        Write-Host "[0d/6] Linting for gated saving throws..." -ForegroundColor Yellow
        & py $sim --lint
        if ($LASTEXITCODE -ne 0) {
            throw "sim.py --lint failed: a SavingThrow is gated behind another term and will roll on every event. Move it into its own spell and fire it with UseSpell."
        }
        Write-Host "[0e/6] Simulating the fight against the mod's own functors..." -ForegroundColor Yellow
        & py $sim
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ^ scenarios above are FAILING. This does not block the build," -ForegroundColor DarkYellow
            Write-Host "    but do not call those mechanics working." -ForegroundColor DarkYellow
        } else {
            Write-Host "  ok - no modelled defect" -ForegroundColor Green
        }
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
$script:GateResults['xml_wellformed']       = 'pass'
$script:GateResults['localization_handles'] = 'pass'

if ($SkipPack) { Write-Host "`n-SkipPack set. Stopping after checks.`n" -ForegroundColor Cyan; return }

# --- 3. localisation ---------------------------------------------------------
# ⚠ THE FAILURE THIS SECTION EXISTS TO STOP, observed by an outside user on
# 2026-08-25 and reported with evidence: Vortex ships its own divine.exe that does
# NOT support convert-loca. It printed "[FATAL] Value convert-loca is not allowed
# for argument a(action)" and EXITED 0. The build trusted the exit code, packed on,
# and produced a .pak with no compiled .loca - a mod in which every string in the
# game renders as a raw handle, from a build that reported success.
#
# So an exit code is not evidence here. Three things are: a Divine that PROVES it
# can convert a synthetic file before the real mod is touched, a destination file
# that exists and is non-empty, and one that is NEWER than its source so a stale
# artifact from a previous run cannot be mistaken for a fresh one.
Write-Host "[3/6] Compiling localisation..." -ForegroundColor Yellow

function Test-DivineLoca {
    # Probe: can this exe actually convert a loca file? Returns $true/$false.
    param([string]$Exe)
    if (-not $Exe -or -not (Test-Path $Exe)) { return $false }
    $probe = Join-Path ([IO.Path]::GetTempPath()) ("forge_probe_" + [guid]::NewGuid().ToString('N'))
    $src = "$probe.xml"; $dst = "$probe.loca"
    try {
        Set-Content -LiteralPath $src -Encoding utf8 -Value @'
<?xml version="1.0" encoding="utf-8"?>
<contentList><content contentuid="hforgeprobe">probe</content></contentList>
'@
        $out = & $Exe -g bg3 -a convert-loca -s $src -d $dst
        if ($LASTEXITCODE -ne 0) { return $false }
        if ($out -and ($out -join "`n") -match '\[FATAL\]') { return $false }
        return ((Test-Path $dst) -and ((Get-Item $dst).Length -gt 0))
    } catch { return $false }
      finally { Remove-Item -LiteralPath $src, $dst -ErrorAction SilentlyContinue }
}

# Pick the first candidate that PASSES the probe, not merely the first that exists.
# Existence was never the question - Vortex's divine.exe exists and is wrong.
#
# ⚠ AN EXPLICIT -DivinePath IS STRICT: it is probed, and if it fails the build stops.
# It does NOT fall through to discovery. Caught by build_acceptance.py, which passed a
# deliberately broken Divine, watched the build quietly fall back to this machine's
# real one, and PASS - so the negative control proved nothing and would have gone on
# proving nothing. Silently using a different tool than the one you were handed is its
# own bug, separate from the test that found it.
if ($DivinePath) {
    if (-not (Test-Path $DivinePath)) { throw "-DivinePath '$DivinePath' does not exist." }
    if (-not (Test-DivineLoca $DivinePath)) {
        throw "-DivinePath '$DivinePath' cannot convert localisation (convert-loca failed, produced nothing, or reported [FATAL]). Refusing to fall back to another Divine when one was named explicitly."
    }
    $Divine = $DivinePath
}
if (-not $Divine) {
    foreach ($c in $DivineCandidates) {
        if (-not $c -or -not (Test-Path $c)) { continue }
        if (Test-DivineLoca $c) { $Divine = $c; break }
        Write-Host "  rejected (cannot convert-loca): $c" -ForegroundColor DarkYellow
    }
}
if (-not $Divine) {
    throw ("No usable LSLib Divine.exe found. Tried:`n  " +
           (($DivineCandidates | Where-Object { $_ }) -join "`n  ") +
           "`nSet one with -DivinePath <path> or `$env:BG3_DIVINE. " +
           "Note that Vortex's bundled divine.exe cannot convert localisation and is rejected on purpose.")
}
Write-Host "  divine: $Divine" -ForegroundColor DarkGray

foreach ($f in $locaFiles) {
    $dest = [IO.Path]::ChangeExtension($f.FullName, 'loca')
    # Delete first: without this a failed conversion leaves the PREVIOUS build's
    # .loca in place and every check below passes on a stale artifact.
    Remove-Item -LiteralPath $dest -ErrorAction SilentlyContinue
    $out = & $Divine -g bg3 -a convert-loca -s $f.FullName -d $dest
    if ($LASTEXITCODE -ne 0) { throw "convert-loca failed for $($f.Name) (exit $LASTEXITCODE)" }
    if ($out -and ($out -join "`n") -match '\[FATAL\]') {
        throw "convert-loca reported [FATAL] for $($f.Name) while exiting 0. Output:`n$($out -join "`n")"
    }
    if (-not (Test-Path $dest)) { throw "convert-loca produced no .loca for $($f.Name). The pak would ship with unresolved handles." }
    $di = Get-Item $dest
    if ($di.Length -le 0) { throw "convert-loca produced an EMPTY .loca for $($f.Name)." }
    if ($di.LastWriteTime -lt $f.LastWriteTime) { throw "$($di.Name) is older than its source $($f.Name) - stale artifact, not a fresh conversion." }
    Write-Host "  $($f.Name) -> $($di.Name) ($($di.Length) bytes)" -ForegroundColor Green
}

$script:GateResults['loca_compiled'] = 'pass'

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
#
# ⚠ THIS GUARD USED TO BE `-lt 20` WITH THE MESSAGE "that is not a Warpblade build",
# inside the ONE build script that is supposed to serve every mod made with this
# toolchain. A freshly scaffolded mod ships around ten files, so the shared script
# refused every small mod on a magic number calibrated to one project - and told its
# author their mod was not Warpblade. Found by build_acceptance.py on its first run,
# not by reading; the outside review missed it too.
#
# The honest invariant is a COMPARISON, not a constant: the stage must contain what
# the source says it should. That catches "staged nothing" and "staged half" alike,
# and it cannot go stale as a mod grows.
$staged   = (Get-ChildItem $stage -Recurse -File).Count
$expected = @(
    Get-ChildItem $Workspace -Recurse -File |
    Where-Object {
        $rel = $_.FullName.Substring($Workspace.Length).TrimStart('\', '/')
        ($rel -like 'Mods*' -or $rel -like 'Public*' -or $rel -like 'Localization*') -and
        $_.Extension -ne '.png' -and $_.Extension -ne '.xml'
    }
).Count
# The .loca files are ALREADY counted above: conversion happens at step 3, before
# staging, so they exist in the workspace by now. Adding them again double-counted
# and made the guard demand one more file than any mod could ever stage.
if ($staged -lt 1) {
    throw "Nothing staged. Refusing to pack an empty $ModName.pak."
}
if ($expected -gt 0 -and $staged -lt $expected) {
    throw "Staged $staged file(s) but the source has $expected shippable file(s). Something was dropped between the workspace and the stage; refusing to pack a partial $ModName.pak."
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

# ⛔ AND ACTUALLY CHECK IT. The listing above is PRINTED and nothing consumes it - the
# same shape as feature_sig reporting drift into the void for a week, and memory_guard
# printing to a channel nobody read. Every other gate in this repo reads the SOURCE;
# this is the only one that reads what the game will open.
# BLOCKS, unlike the reporting audits: a pak that is not what the source says is never
# an acceptable thing to ship, and there is no judgement call to disagree with.
# Workspace copy first, then the shared one in forge/. Before 2026-09-01 only the
# workspace was checked, so the newest gate in the repo protected exactly ONE mod
# - the same gap the outside review had already found for validate.py.
$pa = Join-Path $Workspace 'tools/pak_audit.py'
if (-not (Test-Path $pa)) { $pa = Join-Path $PSScriptRoot 'pak_audit.py' }
if (Test-Path $pa) {
    Write-Host "[5a/6] Verifying the built pak against its source..." -ForegroundColor Yellow
    # ⚠ POINT IT AT THIS WORKSPACE. pak_audit now lives in forge/ and resolves the mod
    # from the CWD, so without this it audits whatever forge.json is nearest the
    # process - the framework repo, not the mod being built. $FORGE_CONFIG is the
    # override modconfig documents for exactly this.
    $env:FORGE_CONFIG = $ForgeJson
    & py $pa --dist
    if ($LASTEXITCODE -eq 2) {
        Write-Host "  pak audit could not run (no Divine) - UNKNOWN, not clean" -ForegroundColor DarkYellow
    } elseif ($LASTEXITCODE -ne 0) {
        throw "The built pak does not match its source. See the findings above; do not ship this."
    }
}

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
    Write-Provenance
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

# ⛔ AND VERIFY WHAT WAS ACTUALLY DEPLOYED, not just that a file with the right NAME
# turned up. The listing check above proves a Warpblade.pak exists in the Mods folder;
# it says nothing about WHICH pak. A stale deploy - the game loading last week's build
# while every tool in the repo reports on today's source - is invisible to every other
# gate here, because all of them read the source and the source is fine.
if (Test-Path $pa) {
    Write-Host "[6b/6] Verifying the DEPLOYED pak..." -ForegroundColor Yellow
    $env:FORGE_CONFIG = $ForgeJson
    & py $pa --deployed
    if ($LASTEXITCODE -eq 2) {
        Write-Host "  could not read the deployed pak - UNKNOWN, not clean" -ForegroundColor DarkYellow
    } elseif ($LASTEXITCODE -ne 0) {
        throw "The DEPLOYED pak does not match the source. The game would load something else."
    }
    Write-Host "  the game will load exactly what was just built" -ForegroundColor Green
    $script:GateResults['deployed_pak_matches_source'] = 'pass'
}

Write-Provenance

Write-Host "`nDone. $ModName.pak is installed and confirmed present." -ForegroundColor Cyan
Write-Host "modsettings.lsx already lists the load order, so BG3 Mod Manager is NOT" -ForegroundColor Cyan
Write-Host "required - launch straight from Steam." -ForegroundColor Cyan
Write-Host "Load order relative to Compatibility Framework does NOT matter: CF's" -ForegroundColor Cyan
Write-Host "LoadConfigFiles() iterates Ext.Mod.GetLoadOrder() and scans every mod," -ForegroundColor Cyan
Write-Host "and its subclass auto-detection reads ALL ClassDescriptions. Verified in" -ForegroundColor Cyan
Write-Host "CF 2.9.0.0 source, 2026-08-09.`n" -ForegroundColor Cyan
} finally {
    Pop-Location
}
