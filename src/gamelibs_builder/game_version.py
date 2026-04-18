from pathlib import Path
from typing import Callable, cast

from more_itertools import last

from .utils import json_load

_GAME_INFER_VERSION: dict[str, Callable[[Path], str | None]] | None = None


def get_version(game_dir: Path) -> str | None:
    """Try to infer game's version given its root path."""
    global _GAME_INFER_VERSION

    if _GAME_INFER_VERSION is None:
        _GAME_INFER_VERSION = {
            "NineSols.exe": _get_ninesols_version,
            "guigubahuang.exe": _get_tale_of_immortal_version,
        }

    exe = next((exe for exe in _GAME_INFER_VERSION if (game_dir / exe).exists()), None)
    if exe is None:
        return None
    return _GAME_INFER_VERSION[exe](game_dir)


def _get_ninesols_version(game_dir: Path) -> str:
    # https://github.com/nine-sols-modding/libs-stripped/blob/main/Program.cs
    cfg_file = (
        next(game_dir.glob("*_Data")) / "StreamingAssets" / "Config" / "config.json"
    )
    with open(cfg_file, encoding="utf-8") as f:
        cfg = json_load(f.read())
    return ".".join(cast(str, cfg["Version"]).split("-")[0].split(".")[1:])


# "Mod\modFQA\配置修改教程\配置（只读）Json格式" doesn't seem to contain StartGameTip.json by default unfortunately, so we require
# game data dump via:
# https://github.com/nozwock/tale-of-immortal-mods/tree/main/src/ExternalMods/DecrypterAndDumper
def _get_tale_of_immortal_version(game_dir: Path) -> str | None:
    cfg_file = game_dir / "DataDump" / "StartGameTip.json"
    if not cfg_file.is_file():
        print("Requires a dump of game data in DataDump/ to auto infer game version.")
        return None

    with open(cfg_file, encoding="utf-8") as f:
        cfg = json_load(f.read())

    return cast(str, last(cfg)["version"]).lstrip("v")
