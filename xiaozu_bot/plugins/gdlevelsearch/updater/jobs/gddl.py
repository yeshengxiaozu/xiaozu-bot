"""Updater job for the complete GDDL local snapshot."""

try:
    from ...api import gddl_store
    from ..paths import staged
except ImportError:  # standalone scripts/run_updater.py mode
    import sys
    from importlib import import_module
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[2] / "api"
    for import_dir in (api_dir.parent, api_dir):
        if str(import_dir) not in sys.path:
            sys.path.insert(0, str(import_dir))
    gddl_store = import_module("gddl_store")
    staged = import_module("updater.paths").staged


def fetch() -> None:
    if not gddl_store.refresh(staged("gddl_levels.json"), reload_after=False):
        raise RuntimeError("GDDL snapshot refresh failed")
