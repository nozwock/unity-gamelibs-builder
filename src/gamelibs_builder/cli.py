import importlib
import importlib.resources
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from sys import exit
from typing import Any, Iterable, Literal, cast

import dotenv
import typer
from more_itertools import first

from gamelibs_builder import data, game_version, utils

VERSIONS_DIR = Path("versions")


CliConfigurationType = Literal["Debug", "Release"]
CliConfiguration = typer.Option("Debug", "-c", "--configuration")


def new_typer_cli() -> typer.Typer:
    return typer.Typer(
        context_settings=dict(help_option_names=["-h", "--help"]),
        no_args_is_help=True,
        rich_markup_mode=None,
        pretty_exceptions_enable=False,
    )


def disable_github_cli_prompt() -> None:
    # https://cli.github.com/manual/gh_help_environment
    if os.getenv("GH_PROMPT_DISABLED") is None:
        os.environ["GH_PROMPT_DISABLED"] = "1"


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


cli = new_typer_cli()
project = new_typer_cli()

cli.add_typer(
    project,
    name="project",
    help="Commands to be used from within a bundler NuGet package project.",
)


@project.command(name="init", no_args_is_help=True)
def project_init(
    dir: Path = typer.Argument(..., file_okay=False),
    package_name: str = typer.Option(
        ..., "--name", help="Package name (e.g. $name.GameLibs)"
    ),
    display_name: str = typer.Option(None),
    framework: str = typer.Option("netstandard2.1", "-f", "--framework"),
    package_tags: list[str] = typer.Option(None, "-t", "--package-tag"),
    game_version_prefix: str = typer.Option(
        None, help="Prefix to game's version in the nupkg's version string."
    ),
    github_username: str = typer.Option(None, help="Default is git's global user.name"),
    license_year: int = typer.Option(None),
    git: bool = typer.Option(True),
) -> None:
    """
    Setup a git project for bundler nuget package.
    """
    if not display_name:
        display_name = package_name

    if not package_tags:
        package_tags = [package_name.lower()]
    package_tags_string = " ".join(package_tags)

    if not game_version_prefix:
        game_version_prefix = package_name.lower()
    game_version_prefix += "."

    if github_username is None:
        github_username = subprocess.run(
            ["git", "config", "--global", "user.name"],
            check=True,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
        ).stdout.strip()

    if not license_year:
        license_year = datetime.now().year

    placeholder_values: dict[str, str] = {
        "GameDisplayName": display_name,
        "GameVersionPrefix": game_version_prefix,
        "GithubUsername": github_username,
        "LicenseYear": str(license_year),
        "PackageName": package_name,
        "PackageTags": package_tags_string,
        "TargetFramework": framework,
    }

    dir.mkdir(parents=True, exist_ok=True)

    # Copying template files
    for filepath in (
        utils.convert_traversable_to_path(it)
        for it in importlib.resources.files(data).iterdir()
        if it.is_file() and it.name not in ("__init__.py",)
    ):
        out_filepath = dir / utils.replace_filename_placeholders(
            filepath, placeholder_values
        )
        filepath.copy(out_filepath)
        out_filepath.write_text(
            utils.replace_text_placeholders(
                out_filepath.read_text(encoding="utf-8"), placeholder_values
            ),
            encoding="utf-8",
        )

    if git and not utils.is_git_repo_root(dir):
        utils.git_init_repo(dir)
        utils.git_commit_all(dir, "Initial commit")

    print(f'Initialized {package_name}.GameLibs: "{dir.absolute()}"')


@project.command(name="add-version", no_args_is_help=True)
def project_add_version(
    game_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    version: str | None = typer.Option(
        None, help="Required if version cannot be inferred."
    ),
    dll_dir: Path | None = typer.Option(None, exists=True, file_okay=False),
) -> str:
    """
    Symlink game's Managed/ directory to a sub-directory (named with game's version) under versions/

    For Il2Cpp games, directory to managed dlls provided by MelonLoader or BepInEx is used instead.
    """

    def joindir(path: Path, *other: str | Path) -> Path | None:
        path = path.joinpath(*other)
        return path if path.is_dir() else None

    if dll_dir is None:
        dll_dir = first(
            filter(
                lambda it: it is not None,
                (
                    # Il2Cpp manged introp dlls
                    joindir(game_dir, "BepInEx", "interop"),
                    # https://melonwiki.xyz/#/modders/quickstart?id=assembly-references
                    joindir(game_dir, "MelonLoader", "Il2CppAssemblies"),
                    joindir(game_dir, "MelonLoader", "Managed"),
                    # Game's original managed dlls
                    first(game_dir.glob("*_Data/Managed")),
                ),
            )
        )
        assert dll_dir is not None and dll_dir.is_dir()
        print(f'Manged DLLs directory to be symlinked: "{dll_dir}"')

    if version is None:
        version = game_version.get_version(game_dir)
        if version is None:
            print(f"Error: Cannot infer version for {game_dir=!r}")
            exit(1)

    target = VERSIONS_DIR / version
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.is_symlink():
        target.unlink()

    target.absolute().symlink_to(dll_dir.absolute(), target_is_directory=True)
    print(f'"{target}" -> "{dll_dir}"')

    return version


@project.command(name="build-game", no_args_is_help=True)
def project_build_game(
    game_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    version: str | None = typer.Option(
        None, help="Required if version cannot be inferred."
    ),
    dll_dir: Path | None = typer.Option(None, exists=True, file_okay=False),
    configuration: CliConfigurationType = CliConfiguration,
) -> None:
    """
    Build .nupkg by game path.
    """

    dotnet_build(project_add_version(game_dir, version, dll_dir), configuration)


@project.command(name="build-version")
def project_build_version(
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
    info = utils.json_load(repo.stdout)

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
        assets: list[dict[str, Any]] = utils.json_load(
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
                and utils.file_digest(digest[0], nupkg) == digest[1].lower()
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


@project.command(name="publish-all")
def project_publish_all(
    source: Literal["github-release", "github-nuget"] = typer.Argument("github-nuget"),
    clean: bool = typer.Option(
        True, "--clean/--no-clean", help="Clean *.nupkg before build."
    ),
    force: bool = typer.Option(False, "-f", "--force", help="Disable sanity checks"),
) -> None:
    """
    GitHub NuGet requires a GITHUB_TOKEN/GH_TOKEN with write:packages scope.

    You can specify environment variables in an .env file.
    """
    if not force and utils.is_git_repo(Path.cwd()):
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

    project_build_version(configuration=configuration)
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
