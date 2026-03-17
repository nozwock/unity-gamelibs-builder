import hashlib
import importlib
import importlib.resources
import importlib.resources.abc
import re
import subprocess
from pathlib import Path
from tabnanny import check

import rapidjson

json_load = rapidjson.Decoder(
    parse_mode=rapidjson.PM_COMMENTS | rapidjson.PM_TRAILING_COMMAS
)

_TEXT_PLACEHOLDER_PAT = re.compile(
    r"\{\{\s*(?P<placeholder>\w+)\s*\}\}", re.ASCII | re.MULTILINE
)
_FILENAME_PLACEHOLDER_PAT = re.compile(r"__(?P<placeholder>\w+)__", re.ASCII)


def file_digest(algorithm: str, path: Path, /) -> str:
    """Output hex string is always in lower case."""
    ONE_MIB = 1 << 20
    hasher = hashlib.new(algorithm)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(ONE_MIB), b""):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def _get_placeholder_value(
    placeholder_match: re.Match[str], values: dict[str, str]
) -> str:
    repl = values.get(placeholder_match.group("placeholder"), None)
    if repl is not None:
        return repl
    return placeholder_match.group(0)


def replace_text_placeholders(text: str, values: dict[str, str]) -> str:
    return _TEXT_PLACEHOLDER_PAT.sub(lambda m: _get_placeholder_value(m, values), text)


def replace_filename_placeholders(filepath: Path, values: dict[str, str]) -> str:
    return _FILENAME_PLACEHOLDER_PAT.sub(
        lambda m: _get_placeholder_value(m, values), filepath.name
    )


def convert_traversable_to_path(
    traversable: importlib.resources.abc.Traversable,
) -> Path:
    with importlib.resources.as_file(traversable) as path:
        return path


def is_git_repo_root(dir: Path) -> bool:
    if (dir / ".git").is_dir():
        return True
    return False


def is_git_repo(dir: Path) -> bool:
    if not dir.is_dir():
        return False

    return (
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def git_init_repo(dir: Path) -> None:
    subprocess.run(
        ["git", "init"],
        check=True,
        cwd=dir,
    )


def git_commit_all(repo: Path, message: str) -> None:
    subprocess.run(
        ["git", "add", "-A"],
        check=True,
        cwd=repo,
    )

    subprocess.run(
        (["git", "commit", "-m", message]),
        check=True,
        cwd=repo,
    )
