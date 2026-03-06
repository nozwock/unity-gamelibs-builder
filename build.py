#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "python-rapidjson ~= 1.2",
#   "typer ~= 0.24",
# ]
# ///

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal, cast

import rapidjson
import typer

PKG_NAME = "NineSols.GameLibs"
VERSIONS_DIR = Path("versions")


CliConfigurationType = Literal["Debug", "Release"]
CliConfiguration = typer.Option("Debug", "-c", "--configuration")


json_loads = rapidjson.Decoder(
    parse_mode=rapidjson.PM_COMMENTS | rapidjson.PM_TRAILING_COMMAS
)

cli = typer.Typer(
    context_settings=dict(help_option_names=["-h", "--help"]),
    no_args_is_help=True,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)


def disable_github_cli_prompt() -> None:
    # https://cli.github.com/manual/gh_help_environment
    if os.getenv("GH_PROMPT_DISABLED") is None:
        os.environ["GH_PROMPT_DISABLED"] = "1"


def file_digest(algorithm: str, path: Path, /) -> str:
    """Output hex string is always in lower case."""
    ONE_MIB = 1 << 20
    hasher = hashlib.new(algorithm)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(ONE_MIB), b""):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def get_ninesols_version(game_dir: Path) -> str:
    # https://github.com/nine-sols-modding/libs-stripped/blob/main/Program.cs
    cfg_file = (
        next(game_dir.glob("*_Data")) / "StreamingAssets" / "Config" / "config.json"
    )
    with open(cfg_file, encoding="utf-8") as f:
        cfg = json_loads(f.read())
    return ".".join(cast(str, cfg["Version"]).split("-")[0].split(".")[1:])


def dotnet_build(version: str, configuration: CliConfigurationType) -> None:
    subprocess.run(
        [
            "dotnet",
            "build",
            "--configuration",
            configuration,
            f"-p:VersionsDir={VERSIONS_DIR}",
            f"-p:GameVersion={version}",
        ],
        check=True,
    )


@cli.command(no_args_is_help=True)
def add_version(
    game_dirs: list[Path] = typer.Argument(..., exists=True, file_okay=False)
) -> list[str]:
    """
    Symlink game's Managed/ directory to a sub-directory (named with game's version) under versions/
    """
    versions = []

    for game_dir in game_dirs:
        dll_dir = next(game_dir.glob("*_Data/Managed"), None)
        assert dll_dir is not None and dll_dir.is_dir()

        version = get_ninesols_version(game_dir)

        target = VERSIONS_DIR / version
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.is_symlink():
            target.unlink()

        target.absolute().symlink_to(dll_dir.absolute(), target_is_directory=True)
        versions.append(version)

    return versions


@cli.command(no_args_is_help=True)
def build_game(
    game_dirs: list[Path] = typer.Argument(..., exists=True, file_okay=False),
    configuration: CliConfigurationType = CliConfiguration,
) -> None:
    for version in add_version(game_dirs):
        dotnet_build(version, configuration)


@cli.command(no_args_is_help=True)
def build_version(
    versions: list[str] = typer.Argument(...),
    configuration: CliConfigurationType = CliConfiguration,
) -> None:
    for version in versions:
        version_dir = VERSIONS_DIR / version
        if not version_dir.is_dir():
            print(f'No such directory: "{VERSIONS_DIR / version}"')
            exit(1)

        dotnet_build(version, configuration)


@cli.command()
def build_all(configuration: CliConfigurationType = CliConfiguration) -> None:
    for version in (it for it in VERSIONS_DIR.iterdir() if it.is_dir()):
        dotnet_build(version.name, configuration)


@cli.command()
def publish_all(
    source: Literal["Github Release"] = typer.Argument("Github Release"),
    configuration: CliConfigurationType = typer.Option(
        "Release", "-c", "--configuration"
    ),
) -> None:
    GITHUB_RELEASE_TAG = "nuget-packages"

    disable_github_cli_prompt()

    build_all(configuration)

    build_dir = Path("bin") / configuration
    assert build_dir.is_dir()

    release_exists = (
        subprocess.run(
            ["gh", "release", "view", GITHUB_RELEASE_TAG],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )

    nupkgs = build_dir.glob("*.nupkg", case_sensitive=False)
    if not release_exists:
        nupkgs = list(nupkgs)
        if not nupkgs:
            print("No *.nupkg available to upload.")
            exit(1)

        subprocess.run(
            [
                "gh",
                "release",
                "create",
                "--title",
                "NuGet Packages",
                "--notes",
                "",
                GITHUB_RELEASE_TAG,
            ]
            + nupkgs,
            check=True,
        )
    else:
        assets: list[dict[str, Any]] = json_loads(
            subprocess.run(
                ["gh", "release", "view", "--json", "assets", GITHUB_RELEASE_TAG],
                check=True,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
            ).stdout
        )["assets"]
        digests: dict[str, list[str]] = {
            asset["name"]: cast(str, asset["digest"]).split(":", 1) for asset in assets
        }

        # Filter out already uploaded nupkgs
        nupkgs = [
            nupkg
            for nupkg in nupkgs
            if not (
                (digest := digests.get(nupkg.name, None))
                and file_digest(digest[0], nupkg) == digest[1].lower()
            )
        ]

        if not nupkgs:
            print("Nothing new to upload.")
        else:
            subprocess.run(
                [
                    "gh",
                    "release",
                    "upload",
                    "--clobber",
                    GITHUB_RELEASE_TAG,
                ]
                + nupkgs,
                check=True,
            )


def main() -> None:
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    cli()


if __name__ == "__main__":
    main()
