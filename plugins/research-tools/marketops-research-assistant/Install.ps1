# MarketOps installer (Windows): a private Python, the local browser, your keys, and the
# MCP server registered with Claude Code. Re-run any time; it keeps what is there.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$home_ = if ($env:MARKETOPS_HOME) { $env:MARKETOPS_HOME } else { Join-Path $env:USERPROFILE ".marketops" }
New-Item -ItemType Directory -Force -Path $home_ | Out-Null
$venv = Join-Path $home_ "venv"
$py = Join-Path $venv "Scripts\python.exe"

Write-Host "1/4 Python environment in $venv"
if (-not (Test-Path $py)) { py -3.12 -m venv $venv; if ($LASTEXITCODE) { python -m venv $venv } }
& $py -m pip install -q --upgrade pip
& $py -m pip install -q -r (Join-Path $here "requirements.txt")

Write-Host "2/4 Local browser for Crawl4AI"
& $py -m playwright install chromium | Out-Null

Write-Host "3/4 Provider keys (saved in $home_\keys.env; Enter skips one)"
$keys = Join-Path $home_ "keys.env"
if (-not (Test-Path $keys)) { New-Item -ItemType File -Path $keys | Out-Null }
$have = Get-Content $keys
foreach ($pair in @("SERPER_API_KEY=Serper", "SERPAPI_API_KEY=SerpApi", "DEEPSEEK_API_KEY=DeepSeek",
                    "TINYFISH_API_KEY=TinyFish", "TYPESAFE_API_KEY=Jev (TypeSafe)")) {
    $name, $label = $pair.Split("=", 2)
    if ($have -match "^$name=.") { Write-Host "  ${label}: kept"; continue }
    $secure = Read-Host "  $label key" -AsSecureString
    $value = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if ($value) { Add-Content -Path $keys -Value "$name=$value" }
}

Write-Host "4/4 Register the MCP server with Claude Code"
$app = Join-Path $home_ "app"
New-Item -ItemType Directory -Force -Path $app | Out-Null
Copy-Item -Recurse -Force (Join-Path $here "scripts\*") $app
Copy-Item -Recurse -Force (Join-Path $here ".claude-plugin") $home_
& $py (Join-Path $app "server.py") --check
claude mcp remove --scope user marketops 2>$null
claude mcp add --scope user marketops -- $py (Join-Path $app "server.py")
Write-Host "Done. Restart Claude Code. Re-run this installer after each plugin update."
