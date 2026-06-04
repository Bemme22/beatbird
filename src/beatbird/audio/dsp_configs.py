"""
beatbird.audio.dsp_configs — discover the CamillaDSP config variants a speaker
can hot-swap between.

A speaker's production config is ``config/camilladsp/<name>.yml`` (the profile's
``audio.camilladsp_config``). Variants live next to it sharing the prefix, e.g.
``<name>-meas.yml`` (flat REW-measurement) or ``<name>-variant-b.yml``. The web
UI lists these; the bridge hot-swaps to the selected one via the CamillaDSP
websocket. CamillaDSP reads the file directly (it runs as the repo-owning user),
so no copy/install step is needed for a variant to become switchable — just drop
the YAML in the repo and `git pull`.
"""

from __future__ import annotations

from pathlib import Path

import beatbird


def repo_root() -> Path:
    """Repo root for the editable install (src/beatbird/__init__.py → repo)."""
    return Path(beatbird.__file__).resolve().parents[2]


def config_dir() -> Path:
    return repo_root() / "config" / "camilladsp"


def list_configs(production: str) -> list[str]:
    """Config stem-names switchable for this speaker: the production config
    first, then its `<production>-<suffix>.yml` variants, sorted. Empty if the
    dir is gone. The variant glob requires a HYPHEN after the production name so
    e.g. "beat" doesn't swallow the unrelated "beatpimini.yml"."""
    d = config_dir()
    if not d.is_dir():
        return [production]
    variants = sorted(p.stem for p in d.glob(f"{production}-*.yml"))
    out = [production] + variants
    # Drop any that don't actually exist on disk (production might be missing
    # on a half-provisioned box) while keeping order + uniqueness.
    seen: set[str] = set()
    return [n for n in out if (config_dir() / f"{n}.yml").is_file()
            and not (n in seen or seen.add(n))]


def config_path(name: str) -> Path:
    return config_dir() / f"{name}.yml"


def is_valid(production: str, name: str) -> bool:
    """True only for names in this speaker's switchable set — guards against
    a POST trying to point CamillaDSP at an arbitrary path."""
    return name in list_configs(production)
