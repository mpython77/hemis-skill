param([ValidateSet("antigravity","claude")][string]$Target = "antigravity")
$ErrorActionPreference = "Stop"
$dest = if ($Target -eq "claude") { "$HOME\.claude\skills\hemis" } else { "$HOME\.gemini\config\skills\hemis" }
New-Item -ItemType Directory -Force $dest | Out-Null
foreach ($item in "VERSION","SKILL.md","AGENTS.md","references","scripts","docs") {
    Copy-Item -Recurse -Force (Join-Path $PSScriptRoot $item) $dest
}
python "$dest\scripts\hemis_cli.py" doctor --offline | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host "Installed to $dest (doctor OK)" } else { throw "doctor failed" }
