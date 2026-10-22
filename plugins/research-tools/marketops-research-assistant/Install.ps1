param(
    [string]$Destination,
    [ValidateSet('codex', 'workbuddy', 'both', 'claude', 'all')][string]$Client = 'both',
    [switch]$SkipFirstUse,
    [switch]$RegisterCodex,
    [switch]$InstallPython,
    [switch]$NonInteractive
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$script:Interactive = (-not $NonInteractive) -and (-not [Console]::IsInputRedirected)

function Stop-MarketOpsInstall([string]$Code, [string]$Remedy) {
    [Console]::Error.WriteLine("INSTALL_BLOCKED: ${Code}: ${Remedy}")
    exit 2
}

if (-not [Environment]::Is64BitOperatingSystem) {
    Stop-MarketOpsInstall 'SUPPORTED_PLATFORM_WINDOWS_OR_LINUX_X64_REQUIRED' 'Use a 64-bit Windows machine.'
}

function Test-MarketOpsPython([string]$Exe, [string[]]$Prefix = @()) {
    if (-not $Exe) { return $null }
    try {
        $probe = @() + $Prefix + @('-c', 'import sys; assert sys.version_info[:2]==(3,12) and sys.maxsize>2**32; print(sys.executable)')
        $resolved = & $Exe @probe 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolved) {
            return ([string]($resolved | Select-Object -Last 1)).Trim()
        }
    } catch { }
    return $null
}

function Find-MarketOpsPython {
    $candidates = @('py', 'python', 'python3',
                    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        # The Microsoft Store ships a python.exe app-execution alias that opens
        # the Store instead of running Python. Never treat that shim as Python.
        if ($command.Source -like '*\WindowsApps\*') { continue }
        $prefix = @()
        if ($candidate -eq 'py') { $prefix = @('-3.12') }
        $resolved = Test-MarketOpsPython $candidate $prefix
        if ($resolved) { return $resolved }
    }
    # uv keeps interpreters outside PATH; ask uv rather than guessing its layout.
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        try {
            $candidate = (& uv python find 3.12 2>$null | Select-Object -Last 1)
            if ($candidate) {
                $resolved = Test-MarketOpsPython ([string]$candidate).Trim()
                if ($resolved) { return $resolved }
            }
        } catch { }
    }
    return $null
}

# Download one file: the machine's proxy first, then direct. A stale proxy left
# by a VPN or IDE (HTTP 502) must not block an install that can reach the site.
function Get-MarketOpsDownload([string]$Url, [string]$OutFile) {
    foreach ($direct in @($false, $true)) {
        try {
            $client = New-Object System.Net.WebClient
            if ($direct) { $client.Proxy = $null }
            $client.DownloadFile($Url, $OutFile)
            return $true
        } catch { }
    }
    return $false
}

# python.org's signed installer, per user, without touching PATH or the system.
function Install-MarketOpsPythonFromPythonOrg {
    $url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
    $installer = Join-Path $env:TEMP 'marketops-python-3.12.10-amd64.exe'
    if (-not (Get-MarketOpsDownload $url $installer)) { return $false }
    $signature = Get-AuthenticodeSignature -FilePath $installer
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
        return $false
    }
    $process = Start-Process -FilePath $installer -Wait -PassThru -ArgumentList @(
        '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=0', 'Include_test=0', 'Shortcuts=0')
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    return ($process.ExitCode -eq 0)
}

Write-Host 'MarketOps: prepare Python 3.12, provider SDKs and Chromium. No provider credits are purchased.'
$pythonExe = Find-MarketOpsPython
if (-not $pythonExe) {
    $consent = [bool]$InstallPython
    if (-not $consent -and $script:Interactive) {
        $answer = Read-Host 'Python 3.12 x64 was not found. Install it now for this Windows user (winget, or the signed installer from python.org)? [y/N]'
        if ($answer -match '^(?i:y|yes)$') { $consent = $true }
    }
    if (-not $consent) {
        Stop-MarketOpsInstall 'PYTHON_312_64BIT_REQUIRED' 'Install 64-bit Python 3.12 from https://www.python.org/downloads/windows/ (or rerun Install.cmd with -InstallPython), then run Install.cmd again.'
    }
    # A missing or broken winget only means: try python.org next.
    $installed = $false
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 1
    if ($consent) {
        & winget.exe install --exact --id Python.Python.3.12 --source winget --scope user --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        $installed = ($LASTEXITCODE -eq 0)
    }
    $ErrorActionPreference = 'Stop'
    if (-not $installed -or -not (Find-MarketOpsPython)) {
        Write-Host 'winget is unavailable or failed; downloading the signed Python 3.12 installer from python.org.'
        if (-not (Install-MarketOpsPythonFromPythonOrg)) {
            Stop-MarketOpsInstall 'PYTHON_312_64BIT_REQUIRED' 'Python could not be installed automatically. Install 64-bit Python 3.12 from https://www.python.org/downloads/windows/, then run Install.cmd again.'
        }
    }
    $pythonExe = Find-MarketOpsPython
    if (-not $pythonExe) {
        Stop-MarketOpsInstall 'PYTHON_312_64BIT_REQUIRED' 'Close and reopen Install.cmd after Python finishes installing.'
    }
}

if (-not $Destination) {
    if (-not $script:Interactive) {
        Stop-MarketOpsInstall 'INSTALL_DESTINATION_REQUIRED' 'Run Install.cmd with an install directory, for example: Install.cmd -Destination C:\Users\you\MarketOps'
    }
    $version = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'plugin.json') -Encoding UTF8 -Raw | ConvertFrom-Json).version
    $defaultDestination = Join-Path $env:LOCALAPPDATA "MarketOps\releases\$version"
    $chosen = Read-Host "Install directory (Enter = $defaultDestination)"
    $Destination = if ($chosen) { $chosen } else { $defaultDestination }
}
$Destination = $Destination.Trim().Trim('"')
if ($Destination -eq '~') {
    $Destination = $HOME
} elseif ($Destination.StartsWith('~/') -or $Destination.StartsWith('~\')) {
    $Destination = Join-Path $HOME $Destination.Substring(2)
}
if (-not $Destination) {
    Stop-MarketOpsInstall 'INSTALL_DESTINATION_REQUIRED' 'Run Install.cmd with an install directory, for example: Install.cmd -Destination C:\Users\you\MarketOps'
}
try {
    $Destination = [System.IO.Path]::GetFullPath($Destination)
} catch {
    Stop-MarketOpsInstall 'INSTALL_CONFIGURATION_INVALID' 'Use an absolute local directory, for example: Install.cmd -Destination C:\Users\you\MarketOps'
}

$installArgs = @((Join-Path $PSScriptRoot 'scripts\setup_clients.py'), '--destination', $Destination,
                 '--client', $Client, '--apply', '--bootstrap-runtime', '--resume')
$register = [bool]$RegisterCodex
if (-not $register -and ($Client -in @('codex', 'both', 'all')) -and (Get-Command codex -ErrorAction SilentlyContinue)) {
    if ($script:Interactive) {
        $answer = Read-Host 'Add this install to your global Codex CLI now? [y/N]'
        if ($answer -match '^(?i:y|yes)$') { $register = $true }
    }
}
if ($register) {
    $installArgs += '--register-codex'
} elseif ($Client -in @('codex', 'both', 'all')) {
    Write-Host "NOTICE: global Codex registration skipped. To add it later, run: codex plugin marketplace add `"$Destination`""
}

# Windows PowerShell turns a native program's stderr into errors; with 'Stop'
# the first warning from pip would end the install. Collect it instead.
Write-Host 'Installing packages and the browser; this takes several minutes.'
$ErrorActionPreference = 'Continue'
$installOutput = & $pythonExe @installArgs 2>&1 | ForEach-Object { [string]$_ }
$installExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
$logFile = Join-Path $env:TEMP ('MarketOps-install-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$installOutput | Set-Content -LiteralPath $logFile -Encoding UTF8
if ($installExit -ne 0) {
    $blocked = @($installOutput | Where-Object { $_ -match '^INSTALL_BLOCKED: [A-Z][A-Z0-9_]{2,80}: ' })
    if ($blocked.Count -gt 0) {
        [Console]::Error.WriteLine($blocked[-1])
    } else {
        [Console]::Error.WriteLine('INSTALL_BLOCKED: INSTALL_STEP_FAILED: Re-run Install.cmd: it resumes the same destination. If it stops again, open INSTALL_STATE.json beside the destination.')
    }
    [Console]::Error.WriteLine("Full installer output: $logFile")
    exit 2
}
$installOutput | ForEach-Object { Write-Output $_ }

$receipt = Get-Content -LiteralPath (Join-Path $Destination 'INSTALL_RECEIPT.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if ($receipt.destination) { $Destination = [string]$receipt.destination }

if (-not $SkipFirstUse -and $script:Interactive) {
    & $receipt.venv_python (Join-Path $receipt.plugin_copy 'scripts\customer_setup.py')
    if ($LASTEXITCODE -ne 0) {
        Stop-MarketOpsInstall 'CUSTOMER_SETUP_INCOMPLETE' 'Reopen START_HERE.md in the install directory and rerun the printed local account setup command.'
    }
} elseif (-not $SkipFirstUse) {
    Write-Host ('NOTICE: no terminal; local account setup was not started. Run: "{0}" "{1}"' -f $receipt.venv_python, (Join-Path $receipt.plugin_copy 'scripts\customer_setup.py'))
}

if ($script:Interactive) {
    Start-Process -FilePath (Join-Path $Destination 'START_HERE.md')
    if ($receipt.workbuddy_bundle) { Start-Process explorer.exe -ArgumentList @($receipt.workbuddy_bundle.directory) }
}
Write-Host 'Files and runtime prepared. Follow START_HERE.md to verify the selected host and start a research task.'
exit 0
