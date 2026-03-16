# {{ PackageName }}.GameLibs

Bundler Nuget package for {{ GameDisplayName }} game libraries. Strips and publicizes game libraries for local and CI development.

## Usage

### Local

Here, `GITHUB_TOKEN` is your Personal Access Token (PAT) with at least `read:packages` scope.

1. Add Github NuGet feed as another source:
```
dotnet nuget add source --name github https://nuget.pkg.github.com/{{ GithubUsername }}/index.json --username github --password GITHUB_TOKEN
```
2. Add package reference to your project:
```xml
<ItemGroup>
    <PackageReference Include="{{ PackageName }}.GameLibs" Version="*-*" />
</ItemGroup>
```

### GitHub Workflow

This makes the GitHub NuGet feed source available for a workflow:
```
dotnet nuget add source --name github https://nuget.pkg.github.com/{{ GithubUsername }}/index.json --username ${{ github.repository_owner }} --password ${{ secrets.GITHUB_TOKEN }} --store-password-in-clear-text
```

## Links
- [Silksong.GameLibs](https://github.com/silksong-modding/Silksong.GameLibs) - used as reference
