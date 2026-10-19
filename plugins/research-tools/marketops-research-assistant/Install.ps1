param(
    [string]$Destination,
    [ValidateSet('codex', 'workbuddy', 'both', 'claude', 'all')][string]$Client = 'both',
    [switch]$SkipFirstUse
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
function Find-MarketOpsPython {
    $candidates = @('py', 'python', 'python3', (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
        $probeArgs = @()
        if ($candidate -eq 'py') { $probeArgs += '-3.12' }
        $probeArgs += @('-c', 'import sys; assert sys.version_info[:2]==(3,12) and sys.maxsize>2**32; print(sys.executable)')
        try {
            $resolved = & $candidate @probeArgs 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved) { return [string]($resolved | Select-Object -Last 1) }
        } catch { }
    }
    return $null
}
Write-Host 'MarketOps: prepare Python 3.12, provider SDKs and Chromium. No provider credits are purchased.'
$pythonExe = Find-MarketOpsPython
if (-not $pythonExe) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw 'Install Python 3.12 x64 from https://www.python.org/downloads/windows/ then run Install.cmd again.'
    }
    & winget.exe install --exact --id Python.Python.3.12 --source winget --scope user --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed. Install Python 3.12 x64 and retry.' }
    $pythonExe = Find-MarketOpsPython
    if (-not $pythonExe) { throw 'Python 3.12 was not found. Reopen Install.cmd after Python finishes installing.' }
}
if (-not $Destination) {
    $version = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'plugin.json') -Encoding UTF8 -Raw | ConvertFrom-Json).version
    $defaultDestination = Join-Path $env:LOCALAPPDATA "MarketOps\releases\$version"
    $chosen = Read-Host "Install directory (Enter = $defaultDestination)"
    $Destination = if ($chosen) { $chosen } else { $defaultDestination }
}
$installArgs = @((Join-Path $PSScriptRoot 'scripts\setup_clients.py'), '--destination', $Destination,
                 '--client', $Client, '--apply', '--bootstrap-runtime', '--resume')
if ($Client -in @('codex', 'both', 'all') -and (Get-Command codex -ErrorAction SilentlyContinue)) {
    $installArgs += '--register-codex'
}
& $pythonExe @installArgs
if ($LASTEXITCODE -ne 0) { throw 'MarketOps install interrupted. Run the same command to resume; research data is preserved.' }
$receipt = Get-Content -LiteralPath (Join-Path $Destination 'INSTALL_RECEIPT.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if (-not $SkipFirstUse) {
    & $receipt.venv_python (Join-Path $receipt.plugin_copy 'scripts\customer_setup.py')
    if ($LASTEXITCODE -ne 0) { throw 'Local account setup incomplete. Reopen the installed START_HERE.md to continue.' }
}
Start-Process -FilePath (Join-Path $Destination 'START_HERE.md')
if ($receipt.workbuddy_bundle) { Start-Process explorer.exe -ArgumentList @($receipt.workbuddy_bundle.directory) }
Write-Host 'Files and runtime prepared. Follow START_HERE.md to verify the selected host and start a research task.'
