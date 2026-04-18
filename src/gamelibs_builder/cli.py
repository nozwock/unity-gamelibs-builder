import importlib
import importlib.resources
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from sys import exit
from typing import Annotated, Any, Iterable, Literal, cast

import dotenv
import typer
from more_itertools import first

from gamelibs_builder import data, game_version, utils

_VERSIONS_STR = "versions"

CliConfigurationType = Annotated[
    Literal["Debug", "Release"], typer.Option("-c", "--configuration")
]


def new_typer_cli() -> typer.Typer:
    return typer.Typer(
        context_settings=dict(help_option_names=["-h", "--help"]),
        no_args_is_help=True,
        rich_markup_mode=None,
        pretty_exceptions_enable=False,
    )


def get_versions_dir(cwd: Path | None = None) -> Path:
    dir = (Path.cwd() if cwd is None else cwd) / _VERSIONS_STR
    dir.mkdir(parents=True, exist_ok=True)
    return dir


def disable_github_cli_prompt() -> None:
    # https://cli.github.com/manual/gh_help_environment
    if os.getenv("GH_PROMPT_DISABLED") is None:
        os.environ["GH_PROMPT_DISABLED"] = "1"


def dotnet_build(
    configuration: CliConfigurationType,
    *,
    version: str,
    cwd: Path | None = None,
) -> None:
    subprocess.run(
        [
            "dotnet",
            "build",
            "--configuration",
            configuration,
            f"-p:VersionsDir={get_versions_dir(cwd)}",
            f"-p:GameVersion={version}",
        ],
        check=True,
        cwd=cwd,
    )


# NOTE: Now using `Annotated` because otherwise for `T | None` annotations `typer` gives default value of an internal
# type `.model.OptionInfo` instead of `None`.
# Who knows what other issues there could be, so best to stick to Annotation method

cli = new_typer_cli()
project = new_typer_cli()

cli.add_typer(
    project,
    name="project",
    help="Commands to be used from within a bundler NuGet package project.",
)


@cli.command(no_args_is_help=True)
def build_package(
    game_dir: Annotated[Path, typer.Argument(..., exists=True, file_okay=False)],
    package_name: Annotated[
        str, typer.Option(..., "--name", help="Package name (e.g. $name.GameLibs)")
    ],
    version: Annotated[
        str | None,
        typer.Option(help="Game version. Required if it cannot be inferred."),
    ] = None,
    output: Annotated[Path | None, typer.Option()] = None,
    display_name: Annotated[str | None, typer.Option()] = None,
    framework: Annotated[str, typer.Option("-f", "--framework")] = "netstandard2.1",
    package_tags: Annotated[
        list[str] | None, typer.Option("-t", "--package-tag")
    ] = None,
    version_prefix: Annotated[
        str | None,
        typer.Option(help="Prefix to game's version in the nupkg's version string."),
    ] = None,
    github_username: Annotated[
        str | None, typer.Option(help="Default is git's global user.name")
    ] = None,
    license_year: Annotated[int | None, typer.Option()] = None,
    no_repo: Annotated[
        bool, typer.Option("--no-repo", help="Empty RepositoryUrl/RepositoryUrl.")
    ] = False,
) -> None:
    """Build GameLibs NuGet package directly with template project placed in a temporary directory."""

    if output is None:
        Path.cwd()

    with tempfile.TemporaryDirectory() as tempdir:
        project_dir = Path(tempdir)
        project_init(
            dir=project_dir,
            package_name=package_name,
            display_name=display_name,
            framework=framework,
            package_tags=package_tags,
            version_prefix=version_prefix,
            github_username=github_username,
            license_year=license_year,
            git=False,
            no_repo=no_repo,
        )

        configuration = "Release"

        project_build_game(
            game_dir=game_dir,
            version=version,
            configuration=configuration,
            cwd=project_dir,
        )

        build_dir = project_dir / "bin" / configuration
        assert build_dir.is_dir()

        nupkg = first(build_dir.glob("*.nupkg", case_sensitive=False))
        to = Path.cwd() / nupkg.name if output is None else output
        nupkg.copy(to)
        print(f'Built NuGet package: "{to}"')


@cli.command()
def publish_package(
    nupkg: Annotated[Path, typer.Argument(..., exists=True, dir_okay=False)],
    source: Annotated[
        Literal["github-release", "github-nuget"], typer.Argument()
    ] = "github-nuget",
) -> None:
    """
    GitHub NuGet requires a GITHUB_TOKEN/GH_TOKEN with write:packages scope.

    You can specify environment variables in an .env file.
    """

    dotenv.load_dotenv()

    if source == "github-nuget":
        publish_github_nuget_packages([nupkg], username=utils.git_username())
    elif source == "github-release":
        publish_github_releases([nupkg])


@project.command(name="init", no_args_is_help=True)
def project_init(
    dir: Annotated[Path, typer.Argument(..., file_okay=False)],
    package_name: Annotated[
        str, typer.Option(..., "--name", help="Package name (e.g. $name.GameLibs)")
    ],
    display_name: Annotated[str | None, typer.Option()] = None,
    framework: Annotated[str, typer.Option("-f", "--framework")] = "netstandard2.1",
    package_tags: Annotated[
        list[str] | None, typer.Option("-t", "--package-tag")
    ] = None,
    version_prefix: Annotated[
        str | None,
        typer.Option(help="Prefix to game's version in the nupkg's version string."),
    ] = None,
    github_username: Annotated[
        str | None, typer.Option(help="Default is git's global user.name")
    ] = None,
    license_year: Annotated[int | None, typer.Option()] = None,
    git: Annotated[bool, typer.Option()] = True,
    no_repo: Annotated[
        bool, typer.Option("--no-repo", help="Empty RepositoryUrl/RepositoryUrl.")
    ] = False,
) -> None:
    """
    Setup a git project for bundler nuget package.
    """
    if not display_name:
        display_name = package_name

    if not package_tags:
        package_tags = [package_name.lower()]
    package_tags_string = " ".join(package_tags)

    if not version_prefix:
        version_prefix = package_name.lower()
    version_prefix += "."

    if github_username is None:
        github_username = utils.git_username()

    if not license_year:
        license_year = datetime.now().year

    repo_url = ""
    if not no_repo:
        repo_url = f"https://github.com/{github_username}/{package_name}.GameLibs"

    placeholder_values: dict[str, str] = {
        "GameDisplayName": display_name,
        "GameVersionPrefix": version_prefix,
        "GithubUsername": github_username,
        "LicenseYear": str(license_year),
        "PackageName": package_name,
        "PackageTags": package_tags_string,
        "TargetFramework": framework,
        # TODO: Besides manually specifying repo url, the default should be retrieved from gh-cli
        "RepositoryUrl": repo_url,
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
    game_dir: Annotated[Path, typer.Argument(..., exists=True, file_okay=False)],
    version: Annotated[
        str | None,
        typer.Option(help="Game version. Required if it cannot be inferred."),
    ] = None,
    dll_dir: Annotated[Path | None, typer.Option(exists=True, file_okay=False)] = None,
    # Not passing --cwd in cli's root callback due to needing to add Context param to command functions, which would be
    # annoying since we also directly call these functions
    cwd: Annotated[
        Path | None, typer.Option("-C", "--cwd", exists=True, file_okay=True)
    ] = None,
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
                    first(game_dir.glob("*_Data/Managed"), None),
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

    target = get_versions_dir(cwd) / version
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.is_symlink():
        target.unlink()

    target.absolute().symlink_to(dll_dir.absolute(), target_is_directory=True)
    print(f'"{target}" -> "{dll_dir}"')

    return version


@project.command(name="build-game", no_args_is_help=True)
def project_build_game(
    game_dir: Annotated[Path, typer.Argument(..., exists=True, file_okay=False)],
    version: Annotated[
        str | None,
        typer.Option(help="Game version. Required if it cannot be inferred."),
    ] = None,
    dll_dir: Annotated[Path | None, typer.Option(exists=True, file_okay=False)] = None,
    configuration: CliConfigurationType = "Debug",
    cwd: Annotated[
        Path | None, typer.Option("-C", "--cwd", exists=True, file_okay=True)
    ] = None,
) -> None:
    """
    Build .nupkg by game path.
    """

    dotnet_build(
        configuration,
        version=project_add_version(game_dir, version, dll_dir, cwd=cwd),
        cwd=cwd,
    )


@project.command(name="build-version")
def project_build_version(
    versions: Annotated[list[str] | None, typer.Argument()] = None,
    configuration: CliConfigurationType = "Debug",
    cwd: Annotated[
        Path | None, typer.Option("-C", "--cwd", exists=True, file_okay=True)
    ] = None,
) -> None:
    """
    Build .nupkg by VERSIONS in versions/

    Build for all VERSIONS if not specified.
    """
    versions_dir = get_versions_dir(cwd)

    if versions is None:
        for version in (it for it in versions_dir.iterdir() if it.is_dir()):
            dotnet_build(configuration, version=version.name, cwd=cwd)
        return

    for version in versions:
        version_path = versions_dir / version
        if not version_path.is_dir():
            print(f'No such directory: "{version_path}"')
            exit(1)

        dotnet_build(configuration, version=version, cwd=cwd)


def publish_github_nuget_packages(
    nupkgs: Iterable[Path], *, username: str | None = None, cwd: Path | None = None
) -> None:
    disable_github_cli_prompt()

    if username is None:
        repo = subprocess.run(
            ["gh", "repo", "view", "--json", "owner,name"],
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )
        if repo.returncode == 0:
            info = utils.json_load(repo.stdout)
            username = info["owner"]["login"]
        else:
            username = utils.git_username()

    registry = f"https://nuget.pkg.github.com/{username}/index.json"
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
            cwd=cwd,
        )


def publish_github_releases(
    nupkgs: Iterable[Path], *, repo_dir: Path | None = None
) -> None:
    disable_github_cli_prompt()

    GITHUB_RELEASE_TAG = "nuget-packages"

    release_exists = (
        subprocess.run(
            ["gh", "release", "view", GITHUB_RELEASE_TAG],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=repo_dir,
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
            cwd=repo_dir,
        )
    else:
        assets: list[dict[str, Any]] = utils.json_load(
            subprocess.run(
                ["gh", "release", "view", "--json", "assets", GITHUB_RELEASE_TAG],
                check=True,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                cwd=repo_dir,
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
                cwd=repo_dir,
            )


@project.command(name="publish-all")
def project_publish_all(
    source: Annotated[
        Literal["github-release", "github-nuget"], typer.Argument()
    ] = "github-nuget",
    clean: Annotated[
        bool, typer.Option("--clean/--no-clean", help="Clean *.nupkg before build.")
    ] = True,
    force: Annotated[
        bool, typer.Option("-f", "--force", help="Disable sanity checks")
    ] = False,
    cwd: Annotated[
        Path | None, typer.Option("-C", "--cwd", exists=True, file_okay=True)
    ] = None,
) -> None:
    """
    GitHub NuGet requires a GITHUB_TOKEN/GH_TOKEN with write:packages scope.

    You can specify environment variables in an .env file.
    """
    cwd = Path.cwd() if cwd is None else cwd

    if not force and utils.is_git_repo(cwd):
        if (
            branch := subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                check=True,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                cwd=cwd,
            ).stdout.strip()
        ) != "main":
            print(
                f"Error: You must be on the main branch to publish. Current branch: {branch}"
            )
            exit(1)

        if (
            subprocess.run(
                ["git", "diff", "--quiet", "HEAD", "origin/main"],
                cwd=cwd,
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
            cwd=cwd,
        ).stdout.strip():
            print(
                "Error: You have uncommitted changes. Please commit or stash them before publishing."
            )
            exit(1)

        print(
            "On main branch, up to date with origin/main, and no uncommitted changes."
        )

    envfile = cwd / ".env"
    if envfile.is_file():
        dotenv.load_dotenv(envfile)

    configuration = "Release"

    build_dir = cwd / "bin" / configuration
    if clean and build_dir.is_dir():
        print(f'Cleaning *.nupkg from "{build_dir}"')
        for nupkg in build_dir.glob("*.nupkg", case_sensitive=False):
            nupkg.unlink()

    project_build_version(configuration=configuration, cwd=cwd)
    assert build_dir.is_dir()

    nupkgs = build_dir.glob("*.nupkg", case_sensitive=False)

    if source == "github-nuget":
        publish_github_nuget_packages(nupkgs, cwd=cwd)
    elif source == "github-release":
        publish_github_releases(nupkgs, repo_dir=cwd)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
