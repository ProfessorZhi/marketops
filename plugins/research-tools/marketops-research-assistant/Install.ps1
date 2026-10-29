# MarketOps installer (Windows): a private Python, your keys, one server every host starts the
# same way (Claude Code, Codex, WorkBuddy, any MCP host), then the local browser reader.
# Re-run any time - after each plugin update too: it keeps what is there.
#   $env:MARKETOPS_NO_PROMPT="1"  an agent installs: no key is asked for, other hosts are left alone
#   $env:MARKETOPS_LITE="1"       skip the local browser reader (about 1 GB); the plugin works without it
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path (Join-Path $here "scripts\server.py"))) { throw "scripts\server.py is not beside the installer: run it inside the unzipped plugin" }
$home_ = if ($env:MARKETOPS_HOME) { $env:MARKETOPS_HOME } else { Join-Path $env:USERPROFILE ".marketops" }
New-Item -ItemType Directory -Force -Path $home_ | Out-Null
$venv = Join-Path $home_ "venv"
$py = Join-Path $venv "Scripts\python.exe"

# Python 3.10+ by whatever name this computer has it: the py launcher is missing on many
# machines, and "python" may be the Store's placeholder that prints nothing (0.8.2 stopped here
# on a computer without py)
function Find-Python {
    foreach ($cand in @("py -3.12", "py -3", "python", "python3")) {
        $exe, $rest = $cand.Split(" ", 2)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $argv = @(); if ($rest) { $argv += $rest }
        try { $ok = & $exe @argv -c "import sys; print(sys.version_info >= (3, 10))" 2>$null } catch { continue }
        if ("$ok".Trim() -eq "True") { return ,(@($exe) + $argv) }
    }
    return $null
}

$base = Find-Python
if (-not (Test-Path $py) -and -not $base) { throw "Python 3.10 or newer is needed (python.org), on PATH as py, python or python3" }
$probe = if (Test-Path $py) { @($py) } else { $base }

Write-Host "1/5 Network"
# a broken local proxy and a slow PyPI cost the 0.8.2 install half an hour: test both first
$helper = Join-Path $here "tools\install_helper.py"
$pexe = $probe[0]; $prest = @($probe | Select-Object -Skip 1)
$net = $null
try { $net = (& $pexe @prest $helper net 2>$null) | ConvertFrom-Json } catch { }
if ($net -and $net.drop_proxy) {
    Write-Host "  the proxy in HTTPS_PROXY does not answer; installing over a direct connection"
    foreach ($v in @("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")) { Remove-Item "Env:$v" -ErrorAction SilentlyContinue }
}
if ($net -and $net.pip_index) {
    Write-Host "  PyPI is slow from here; using the mirror $($net.pip_index)"
    $env:PIP_INDEX_URL = $net.pip_index
    if (-not $env:PLAYWRIGHT_DOWNLOAD_HOST) { $env:PLAYWRIGHT_DOWNLOAD_HOST = "https://npmmirror.com/mirrors/playwright" }
}

Write-Host "2/5 Python environment in $venv (the core: a minute or two)"
if (-not (Test-Path $py)) {
    $exe = $base[0]; $rest = @($base | Select-Object -Skip 1)
    & $exe @rest -m venv $venv
}
& $py -m pip install -q --upgrade pip
$core = Join-Path $home_ "requirements-core.txt"
Get-Content (Join-Path $here "requirements.txt") | Where-Object { $_ -notmatch "^crawl4ai" } | Set-Content $core
& $py -m pip install -q -r $core

Write-Host "3/5 Provider keys (saved in $home_\keys.env; Enter skips one)"
$keys = Join-Path $home_ "keys.env"
if (-not (Test-Path $keys)) { New-Item -ItemType File -Path $keys | Out-Null }
$have = Get-Content $keys
foreach ($pair in @("SERPER_API_KEY=Serper", "SERPAPI_API_KEY=SerpApi", "DEEPSEEK_API_KEY=DeepSeek",
                    "TINYFISH_API_KEY=TinyFish", "TYPESAFE_API_KEY=Jev (TypeSafe)")) {
    $name, $label = $pair.Split("=", 2)
    if ($have -match "^$name=.") { Write-Host "  ${label}: kept"; continue }
    # an agent installing for the user never types keys: they go into keys.env by hand
    if ($env:MARKETOPS_NO_PROMPT) { Write-Host "  ${label}: missing - add $name=... to $keys"; continue }
    $secure = Read-Host "  $label key" -AsSecureString
    $value = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if ($value) { Add-Content -Path $keys -Value "$name=$value" }
}

Write-Host "4/5 The plugin in $home_\plugin and its launcher $home_\bin\marketops-mcp.cmd"
# the whole plugin, not only the scripts: the server hands its skills and role files to every host
# through guide(), and reads them from beside itself (0.8.0 copied scripts only: no role guides)
$plugin = Join-Path $home_ "plugin"
$staging = Join-Path $home_ "plugin.new"
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Force -Path $staging, (Join-Path $home_ "bin") | Out-Null
foreach ($item in @(".claude-plugin", "scripts", "skills", "references", "agents", "commands", "tools",
                    "requirements.txt", "HOSTS.md", "README.md")) {
    $src = Join-Path $here $item
    if (Test-Path $src) { Copy-Item -Recurse -Force $src $staging }
}
if (Test-Path $plugin) { Remove-Item -Recurse -Force $plugin }
Move-Item $staging $plugin
$launcher = Join-Path $home_ "bin\marketops-mcp.cmd"
$server = Join-Path $plugin "scripts\server.py"
Set-Content -Path $launcher -Encoding ASCII -Value "@`"$py`" `"$server`" %*"
& $py $server --check

# Hosts start the server as python.exe + server.py, never through cmd.exe: some hosts' sandboxes
# block cmd.exe (WorkBuddy's did), and a .cmd launcher would not start there. The .cmd file is for
# people at a prompt.
# Claude Code and Codex are registered when you run the installer yourself; an agent installing
# for one host (MARKETOPS_NO_PROMPT) leaves the other hosts alone.
if (-not $env:MARKETOPS_NO_PROMPT) {
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        try { claude mcp remove --scope user marketops 2>$null } catch { }
        claude mcp add --scope user marketops -- $py $server
        Write-Host "Claude Code: registered"
    }
    if (Get-Command codex -ErrorAction SilentlyContinue) {
        try { codex mcp remove marketops 2>$null } catch { }
        codex mcp add marketops -- $py $server
        Write-Host "Codex: registered"
    }
}
$jpy = ($py -replace "\\", "\\"); $jserver = ($server -replace "\\", "\\")
Write-Host ""
Write-Host "The same server for every host - command: $py, argument: $server (stdio)"
Write-Host "  WorkBuddy, Claude Desktop, Cursor and any MCP host (JSON):"
Write-Host "      {`"mcpServers`": {`"marketops`": {`"command`": `"$jpy`", `"args`": [`"$jserver`"]}}}"
Write-Host "  Codex (~/.codex/config.toml, if not registered above):"
Write-Host "      [mcp_servers.marketops]"
Write-Host "      command = '$py'"
Write-Host "      args = ['$server']"
Write-Host "Then ask the agent to call guide() - every host reads the same workflow from it."
Write-Host "Restart the host. Re-run this installer after each plugin update."
Write-Host "MarketOps works from here on; step 5 adds the local browser reader."

# Crawl4AI and its browser are most of the download (about 1 GB). The plugin works without them
# (TinyFish, DeepSeek and plain HTTP read pages), so they come last and their failure stops nothing.
if ($env:MARKETOPS_LITE) {
    Write-Host "5/5 Local browser reader: skipped (MARKETOPS_LITE); run the installer again without it to add it"
    exit 0
}
Write-Host "5/5 Local browser reader (Crawl4AI, about 1 GB; the plugin already works without it)"
$ErrorActionPreference = "Continue"
& $py -m pip install -q -r (Join-Path $here "requirements.txt")
if ($LASTEXITCODE -eq 0) {
    # Chrome or Edge already on this computer does the job of a 150-300 MB Chromium download
    $env:MARKETOPS_HOME = $home_
    $found = "$(& $py $helper browser 2>$null)".Trim()
    if (-not $found) {
        & $py -m playwright install chromium | Out-Null
        $found = "$(& $py $helper browser 2>$null)".Trim()
    }
    if ($found) { Write-Host "  browser: $found" } else { Write-Host "  browser: none - pages are read by TinyFish, DeepSeek and plain HTTP" }
} else {
    Write-Host "  Crawl4AI did not install; MarketOps works without it - run the installer again later to add it"
}
& $py $server --check | Select-Object -Index 2
