# MarketOps installer (Windows): a private Python, the local browser, your keys, and one launcher
# every host uses the same way (Claude Code, Codex, WorkBuddy, any MCP host).
# Re-run any time - after each plugin update too: it keeps what is there.
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

Write-Host "1/4 Python environment in $venv"
if (-not (Test-Path $py)) {
    $base = Find-Python
    if (-not $base) { throw "Python 3.10 or newer is needed (python.org), on PATH as py, python or python3" }
    $exe = $base[0]; $rest = @($base | Select-Object -Skip 1)
    & $exe @rest -m venv $venv
}
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
    # an agent installing for the user never types keys: they go into keys.env by hand
    if ($env:MARKETOPS_NO_PROMPT) { Write-Host "  ${label}: missing - add $name=... to $keys"; continue }
    $secure = Read-Host "  $label key" -AsSecureString
    $value = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if ($value) { Add-Content -Path $keys -Value "$name=$value" }
}

Write-Host "4/4 The plugin in $home_\plugin and its launcher $home_\bin\marketops-mcp.cmd"
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
