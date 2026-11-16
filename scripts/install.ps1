param(
    [string]$Repo = "$HOME\.skills-src",
    [string]$Source = "https://github.com/rockszq/skillsctl.git"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Repo)) {
    git clone $Source $Repo
}

Push-Location $Repo
try {
    python skillctl.py init
    python skillctl.py link
    python skillctl.py verify
    if ($LASTEXITCODE -ne 0) {
        throw "verify failed; run python skillctl.py doctor"
    }
    Write-Host "Restart each agent once so it rescans its skills directory."
} finally {
    Pop-Location
}
