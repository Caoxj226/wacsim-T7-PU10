param(
    [string]$OutputRoot = (Join-Path (Get-Location) "results\smoke_pipeline\wacsim_ctown_smoke"),
    [int]$Seed = 460,
    [string]$Image = "crave-wacsim-ctown:smoke-pipeline-20261110-v5"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ResolvedOutput = [System.IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $ResolvedOutput) {
    if ((Get-ChildItem -LiteralPath $ResolvedOutput -Force | Measure-Object).Count -ne 0) {
        throw "Smoke output directory must be empty: $ResolvedOutput"
    }
} else {
    New-Item -ItemType Directory -Path $ResolvedOutput | Out-Null
}

$CraveCommit = (git -C $RepoRoot rev-parse HEAD).Trim()
$Dirty = if ((git -C $RepoRoot status --porcelain | Measure-Object).Count -gt 0) { "true" } else { "false" }
$ImageId = (docker image inspect --format '{{.Id}}' $Image).Trim()
$Invocation = "powershell -ExecutionPolicy Bypass -File .\reproduction\scripts\run_wacsim_smoke_pipeline.ps1 -OutputRoot `"$ResolvedOutput`" -Seed $Seed -Image $Image"
$RunnerLog = "$ResolvedOutput.runner.log"

$SavedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& docker run --rm `
    --mount "type=bind,source=$ResolvedOutput,target=/out" `
    --workdir /opt/crave `
    $Image `
    reproduction/scripts/run_wacsim_smoke_pipeline.py `
    --output-root /out `
    --seed $Seed `
    --crave-commit $CraveCommit `
    --crave-worktree-dirty $Dirty `
    --docker-image $Image `
    --docker-image-id $ImageId `
    --invocation $Invocation `
    --runner-log $RunnerLog 1> $RunnerLog 2>&1
$DockerExit = $LASTEXITCODE
$ErrorActionPreference = $SavedErrorActionPreference
if ($DockerExit -ne 0) {
    throw "Smoke pipeline failed with exit code $DockerExit; see $RunnerLog"
}
