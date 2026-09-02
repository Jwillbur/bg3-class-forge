"""The ONE place that finds LSLib's Divine.exe, and the only one that PROBES it.

⚠ WHY THIS FILE EXISTS. On 2026-09-01 there were THREE independent copies of Divine
discovery - `forge/build.ps1`, `forge/load_order_audit.py` and the mod's
`tools/pak_audit.py` - and only build.ps1 probed. The other two accepted the first path
that EXISTED, and both fell back to `shutil.which("divine.exe")`, which on any machine
with Vortex installed returns **Vortex's** divine: it exists, it is on PATH, and it
CANNOT perform the actions these tools need. It prints `[FATAL]` and exits 0.

The three lists had already drifted - only build.ps1 knew about `C:\\Tools\\LSLib`.

⭐ Existence was never the question. A Divine that is present and wrong is the whole
failure, so this resolver picks the first candidate that PASSES A CAPABILITY PROBE.

    py forge/divine.py            # print the resolved path, or exit 1
    py forge/divine.py --verbose  # ...and say what was rejected and why
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Most specific first. $BG3_DIVINE is the documented override; PATH is LAST because it
# is the entry most likely to be Vortex's.
CANDIDATES = (
    os.environ.get("BG3_DIVINE"),
    r"C:\Modding\tools\lslib\Packed\Tools\Divine.exe",
    r"C:\Tools\LSLib\Packed\Tools\Divine.exe",
)

PROBE_XML = ('<?xml version="1.0" encoding="utf-8"?>\n'
             '<contentList><content contentuid="hforgeprobe">probe</content></contentList>\n')


def can_convert_loca(exe: str) -> bool:
    """The discriminating capability: Vortex's divine cannot do convert-loca.

    Chosen over create-package deliberately - this is the exact action that failed for
    an outside user, printing `[FATAL] Value convert-loca is not allowed for argument
    a(action)` while exiting 0. An exit code alone would have passed it.
    """
    d = Path(tempfile.mkdtemp(prefix="divine_probe_"))
    src, dst = d / "p.xml", d / "p.loca"
    try:
        src.write_text(PROBE_XML, encoding="utf-8")
        r = subprocess.run([exe, "-g", "bg3", "-a", "convert-loca",
                            "-s", str(src), "-d", str(dst)],
                           capture_output=True, text=True, errors="replace", timeout=60)
        if r.returncode != 0 or "[FATAL]" in ((r.stdout or "") + (r.stderr or "")):
            return False
        return dst.is_file() and dst.stat().st_size > 0
    except Exception:
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def find_divine(probe: bool = True, verbose: bool = False) -> Path | None:
    """First candidate that passes the probe. None if nothing usable is installed.

    `probe=False` skips the capability check - only for callers that merely need to
    report whether SOMETHING is installed, never for one about to trust the output.
    """
    seen = []
    for c in list(CANDIDATES) + [shutil.which("Divine") or shutil.which("divine.exe")]:
        if not c or c in seen:
            continue
        seen.append(c)
        if not Path(c).is_file():
            continue
        if not probe or can_convert_loca(c):
            return Path(c)
        if verbose:
            print("  rejected (cannot convert-loca): %s" % c)
    return None


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    d = find_divine(verbose="--verbose" in sys.argv)
    if not d:
        print("no usable Divine.exe found. Tried:\n  " +
              "\n  ".join(c for c in CANDIDATES if c) +
              "\n  (and PATH)\nSet $BG3_DIVINE. Vortex's divine.exe is rejected on "
              "purpose - it cannot convert localisation.", file=sys.stderr)
        return 1
    print(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
