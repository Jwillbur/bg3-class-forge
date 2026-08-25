# SPDX-License-Identifier: GPL-3.0-or-later
"""Where this mod's files are, read from forge.json instead of hardcoded per tool.

    from modconfig import load
    cfg = load()                 # walks up from the caller to find forge.json
    cfg.stats                    # .../Public/<Mod>/Stats/Generated/Data
    cfg.loca                     # .../Localization/English/<Mod>.xml

⭐ WHY THIS EXISTS
    Every tool in this toolchain opened with four or five module-level constants naming
    the mod: STATS, PUBLIC, LOCA, and the mod's own name spelled out inside path strings.
    That is fine for exactly one mod and useless for the second, which is the whole
    problem with a framework whose tools only work on the project they were born in.

    The fix is not to copy the tools into each new mod. **A copied tool is frozen** - it
    stops receiving every fix the original gets, and the two silently diverge until the
    day one of them is wrong in a way the other already solved. One codebase; the paths
    come from config.

⚠ THE MOD ROOT IS FOUND, NOT ASSUMED.
    `load()` walks UP from the calling file looking for forge.json, so a tool works the
    same whether it is run from the mod root, from tools/, or by an editor with some
    other working directory. Tools that assumed `Path(__file__).parent.parent` were
    quietly asserting a directory layout that nothing enforced.

⚠ MISSING CONFIG IS A REFUSAL, NOT A DEFAULT.
    Guessing a mod name from a folder is how a tool ends up validating the wrong tree and
    reporting it clean. Same rule as forge.py's parent-GUID refusal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class ConfigError(RuntimeError):
    """No usable forge.json. Say what is missing; never fall back to a guess."""


class ModConfig:
    """Resolved paths for one mod. Every attribute is derived, none is stored twice."""

    def __init__(self, data: dict, path: Path):
        self.path = path
        self.root = path.parent
        self.data = data
        name = data.get("name")
        if not name:
            raise ConfigError(f"{path} has no `name`. That name IS the folder under "
                              f"Public/ and Mods/, so nothing can be located without it.")
        self.name: str = name

    # ---- the mod's own tree ---------------------------------------------------
    @property
    def public(self) -> Path:
        return self.root / "Public" / self.name

    @property
    def mods(self) -> Path:
        return self.root / "Mods" / self.name

    @property
    def stats(self) -> Path:
        return self.public / "Stats" / "Generated" / "Data"

    @property
    def lists(self) -> Path:
        return self.public / "Lists"

    @property
    def levelmaps(self) -> Path:
        return self.public / "Levelmaps"

    @property
    def loca(self) -> Path:
        lang = self.data.get("language", "English")
        return self.root / "Localization" / lang / f"{self.name}.xml"

    @property
    def meta(self) -> Path:
        return self.mods / "meta.lsx"

    @property
    def gui(self) -> Path:
        return self.mods / "GUI"

    @property
    def dist(self) -> Path:
        return self.root / "dist"

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def corpus(self) -> Path:
        return self.root / "corpus"

    @property
    def pak(self) -> Path:
        return self.dist / f"{self.name}.pak"

    # ---- game data ------------------------------------------------------------
    # Env wins over config: the config travels with the mod between machines, the
    # env var describes the machine it landed on.
    @property
    def unpacked(self) -> Path:
        return Path(os.environ.get("BG3_UNPACKED")
                    or self.data.get("unpacked")
                    or r"C:\Modding\bg3_unpacked")

    @property
    def unpacked_lsx(self) -> Path:
        return Path(os.environ.get("BG3_UNPACKED_LSX")
                    or self.data.get("unpacked_lsx")
                    or str(self.unpacked) + "_lsx")

    def __repr__(self) -> str:
        return f"<ModConfig {self.name} at {self.root}>"


def find(start: Path | None = None, filename: str = "forge.json") -> Path:
    """The nearest forge.json at or above `start`. Raises rather than returning None.

    ⭐ `FORGE_CONFIG` overrides the search entirely, and that is what lets a tool be
    pointed at a DIFFERENT mod than the one it lives beside. Without it every tool here
    could only ever validate its own project - which is most of the value of a generic
    toolchain missing. It is an env var rather than a flag because the config loads at
    import time, before any argument parsing could have run.
    """
    override = os.environ.get("FORGE_CONFIG")
    if override:
        p = Path(override)
        if p.is_dir():
            p = p / filename
        if not p.is_file():
            raise ConfigError(f"FORGE_CONFIG points at {p}, which does not exist.")
        return p

    here = (start or Path.cwd()).resolve()
    if here.is_file():
        here = here.parent
    for d in [here, *here.parents]:
        candidate = d / filename
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"no {filename} at or above {here}.\n"
        f"Every tool here reads the mod's name and layout from that file. Run "
        f"`py forge.py init`\nin the mod root to create one - this will not guess a mod "
        f"name from a directory.")


def load(start: Path | None = None) -> ModConfig:
    path = find(start)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from e
    return ModConfig(data, path)
