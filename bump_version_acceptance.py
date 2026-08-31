# SPDX-License-Identifier: GPL-3.0-or-later
"""Controls for bump_version.py.

⚠ THE CONTROL THAT MATTERS MOST IS THE NEGATIVE ONE. This whole tool exists because a
    version check that only ever looked at COMMITS reported clean through the exact
    window a pak was built and handed to a tester (2026-08-27). So proving --check FIRES
    on an uncommitted source edit matters more than proving it stays quiet otherwise. A
    gate that cannot be shown to fail is not a gate.

⚠ AND PublishVersion IS TESTED SEPARATELY FROM ModuleInfo. They are two nodes serving two
    readers - the game and the mod manager - and they drift independently. Warpblade had
    them a full revision apart (1.7.9.0 against 1.8.0.0) while every tool reported fine,
    because every tool read "the version in meta.lsx" as though there were one.

Fixtures are synthetic git repos in a temp dir. Nothing here touches a real mod.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bump_version as B  # noqa: E402

ok = bad = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        bad += 1
        print(f"  FAIL  {label}" + (f" - {detail}" if detail else ""))


META = """<?xml version="1.0" encoding="UTF-8"?>
<save>
  <region id="Config">
    <node id="root">
      <children>
        <node id="Dependencies">
          <children>
            <node id="ModuleShortDesc">
              <attribute id="Version64" type="int64" value="36028797018963968"/>
            </node>
          </children>
        </node>
        <node id="ModuleInfo">
          <attribute id="Name" type="LSString" value="Fixture"/>
          <attribute id="Version64" type="int64" value="{MODULE}"/>
          <children>
            <node id="PublishVersion">
              <attribute id="Version64" type="int64" value="{PUBLISH}"/>
            </node>
          </children>
        </node>
      </children>
    </node>
  </region>
</save>
"""


def make_mod(module: int, publish: int | None = None) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "forge.json").write_text(json.dumps({"name": "Fixture"}), encoding="utf-8")
    meta = root / "Mods" / "Fixture"
    meta.mkdir(parents=True)
    (meta / "meta.lsx").write_text(
        META.format(MODULE=module, PUBLISH=publish if publish is not None else module),
        encoding="utf-8")
    stats = root / "Public" / "Fixture" / "Stats" / "Generated" / "Data"
    stats.mkdir(parents=True)
    (stats / "Passive.txt").write_text('new entry "Fixture_A"\n', encoding="utf-8")
    return root


def git(root: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=str(root), capture_output=True, text=True, errors="replace")


def git_init(root: Path) -> None:
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")


class Cfg:
    def __init__(self, root: Path):
        self.root = root
        self.meta = root / "Mods" / "Fixture" / "meta.lsx"


# ---- packing -------------------------------------------------------------------
V = B.encode(1, 8, 1, 0)
check("encode/decode round-trips", B.decode(V) == (1, 8, 1, 0))
check("fmt renders all four fields", B.fmt(V) == "1.8.1.0", B.fmt(V))
check("the fourth field is real, not ignored",
      B.encode(1, 8, 1, 1) != B.encode(1, 8, 1, 0))
check("a build bump is the SMALLEST possible move",
      B.encode(1, 8, 1, 1) - B.encode(1, 8, 1, 0) == 1)
check("a build bump does not disturb the other three fields",
      B.decode(B.encode(1, 8, 1, 1))[:3] == (1, 8, 1))

# ---- reading the two nodes apart -----------------------------------------------
root = make_mod(V, publish=B.encode(1, 7, 9, 0))
meta = root / "Mods" / "Fixture" / "meta.lsx"
check("ModuleInfo is read, not the dependency's Version64",
      B.module_version(meta) == V, str(B.module_version(meta)))
check("PublishVersion is read as its OWN value",
      B.publish_version(meta) == B.encode(1, 7, 9, 0))
check("drift between the two nodes is visible",
      B.module_version(meta) != B.publish_version(meta))

# ---- the bump repairs a half-applied state -------------------------------------
B.rewrite(meta, B.encode(1, 7, 9, 0), B.encode(1, 8, 2, 0), 1)
B.rewrite(meta, V, B.encode(1, 8, 2, 0), 1)
check("a drifted PublishVersion ends up AGREEING after a bump",
      B.module_version(meta) == B.publish_version(meta) == B.encode(1, 8, 2, 0))

# ---- rewrite refuses rather than guessing --------------------------------------
try:
    B.rewrite(meta, 999999, 111111, 1)
    check("rewrite refuses when the count is wrong", False, "it wrote anyway")
except SystemExit:
    check("rewrite refuses when the count is wrong", True)

# ---- --check: the negative control ---------------------------------------------
root = make_mod(V)
git_init(root)
cfg = Cfg(root)
check("a clean tree reports nothing", B.check(cfg) == [], str(B.check(cfg)))

src = root / "Public" / "Fixture" / "Stats" / "Generated" / "Data" / "Passive.txt"
src.write_text('new entry "Fixture_A"\nnew entry "Fixture_B"\n', encoding="utf-8")
problems = B.check(cfg)
check("** an UNCOMMITTED source edit with a still version FIRES",
      any("NOT BUMPED" in p for p in problems), str(problems))

meta = cfg.meta
B.rewrite(meta, V, B.encode(1, 8, 1, 1), 2)
check("** ...and goes quiet once the FOURTH field moves",
      B.check(cfg) == [], str(B.check(cfg)))

# a committed source change with no bump is still caught
root = make_mod(V)
git_init(root)
cfg = Cfg(root)
src = root / "Public" / "Fixture" / "Stats" / "Generated" / "Data" / "Passive.txt"
src.write_text('new entry "Fixture_C"\n', encoding="utf-8")
git(root, "add", "-A")
git(root, "commit", "-qm", "source change, no bump")
problems = B.check(cfg)
check("a COMMITTED source change with a still version is caught too",
      any("STALE" in p for p in problems), str(problems))

# a non-repo refuses instead of reporting clean
bare = make_mod(V)
check("a non-git tree says so rather than reporting clean",
      any("not a git repository" in p for p in B.check(Cfg(bare))))

print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
