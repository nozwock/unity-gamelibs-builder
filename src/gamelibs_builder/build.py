#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "python-dotenv ~= 1.2",
#   "python-rapidjson ~= 1.2",
#   "typer ~= 0.24",
# ]
# ///

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from sys import exit
from typing import Any, Iterable, Literal, cast

import dotenv
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
    """
    Build .nupkg by game paths.
    """
    for version in add_version(game_dirs):
        dotnet_build(version, configuration)


@cli.command()
def build_version(
    versions: list[str] = typer.Argument(None),
    configuration: CliConfigurationType = CliConfiguration,
) -> None:
    """
    Build .nupkg by VERSIONS in versions/

    Build for all VERSIONS if not specified.
    """
    if versions is None:
        for version in (it for it in VERSIONS_DIR.iterdir() if it.is_dir()):
            dotnet_build(version.name, configuration)

        return

    for version in versions:
        version_dir = VERSIONS_DIR / version
        if not version_dir.is_dir():
            print(f'No such directory: "{VERSIONS_DIR / version}"')
            exit(1)

        dotnet_build(version, configuration)


def publish_github_nuget_packages(nupkgs: Iterable[Path]) -> None:
    disable_github_cli_prompt()

    repo = subprocess.run(
        ["gh", "repo", "view", "--json", "owner,name"],
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
    )
    info = json_loads(repo.stdout)

    owner: str = info["owner"]["login"]
    registry = f"https://nuget.pkg.github.com/{owner}/index.json"
    gh_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    assert gh_token is not None

    for nupkg in nupkgs:
        subprocess.run(
            [
                "dotnet",
                "nuget",
                "push",
                nupkg,
                "--source",
                registry,
                "--api-key",
                gh_token,
                "--skip-duplicate",
            ],
            check=True,
        )


def publish_github_releases(nupkgs: Iterable[Path]) -> None:
    disable_github_cli_prompt()

    GITHUB_RELEASE_TAG = "nuget-packages"

    release_exists = (
        subprocess.run(
            ["gh", "release", "view", GITHUB_RELEASE_TAG],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )

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


@cli.command()
def publish_all(
    source: Literal["github-release", "github-nuget"] = typer.Argument("github-nuget"),
    clean: bool = typer.Option(
        True, "--clean/--no-clean", help="Clean *.nupkg before publish"
    ),
    force: bool = typer.Option(False, "-f", "--force", help="Disable sanity checks"),
) -> None:
    """
    GitHub NuGet requires a GITHUB_TOKEN/GH_TOKEN with write:packages scope.

    You can specify environment variables in an .env file.
    """
    if not force:
        if (
            branch := subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                check=True,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
            ).stdout.strip()
        ) != "main":
            print(
                f"Error: You must be on the main branch to publish. Current branch: {branch}"
            )
            exit(1)

        if (
            subprocess.run(
                ["git", "diff", "--quiet", "HEAD", "origin/main"],
            ).returncode
            != 0
        ):
            print(
                "Error: Local main branch is not up to date with origin/main. Please pull first."
            )
            exit(1)

        if subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
        ).stdout.strip():
            print(
                "Error: You have uncommitted changes. Please commit or stash them before publishing."
            )
            exit(1)

        print(
            "On main branch, up to date with origin/main, and no uncommitted changes."
        )

    configuration = "Release"

    build_dir = Path("bin") / configuration
    if clean and build_dir.is_dir():
        print(f'Cleaning *.nupkg from "{build_dir}"')
        for nupkg in build_dir.glob("*.nupkg", case_sensitive=False):
            nupkg.unlink()

    build_version(configuration=configuration)
    assert build_dir.is_dir()

    nupkgs = build_dir.glob("*.nupkg", case_sensitive=False)

    if source == "github-nuget":
        publish_github_nuget_packages(nupkgs)
    elif source == "github-release":
        publish_github_releases(nupkgs)


def main() -> None:
    dotenv.load_dotenv()
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    cli()


if __name__ == "__main__":
    main()
