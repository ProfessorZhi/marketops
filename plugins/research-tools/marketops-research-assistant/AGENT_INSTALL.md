# Instructions for the agent installing MarketOps (Windows)

You are installing the MarketOps research plugin for the user on this Windows computer. Follow
these steps in order, and tell the user in their language what you are doing at each step. Run the
commands in PowerShell.

## Rules

- **The user may send you the four API keys here. They belong in Windows Credential Manager and
  nowhere else.** Pass them to `research_credentials.py configure --stdin` (step 4) and never to a
  file, a command argument, a URL, a log line, a commit or a message back to the user. Do not repeat
  a key in your reply; say which providers were stored instead. When step 4 has succeeded, suggest
  the user clear those messages from the chat history.
- If the user would rather not paste the keys, skip step 4 and instead let them type the keys into
  the installer's own hidden prompt: run step 3 without `-SkipFirstUse -NonInteractive` and drop
  step 5, so the installer asks for everything itself.
- Do not change files inside the extracted package.
- If a step fails, show the user the last lines of the output and rerun the same step: the
  installer resumes where it stopped and keeps saved data.

## Steps

1. **Unpack.** If the user gave you the ZIP path, extract it into a fresh folder:
   ```powershell
   $pkg = "$env:USERPROFILE\MarketOps\package"
   Expand-Archive -LiteralPath "<path to the zip>" -DestinationPath $pkg -Force
   $src = (Get-ChildItem $pkg -Recurse -Filter Install.cmd | Select-Object -First 1).DirectoryName
   ```

2. **Collect the four keys.** Show this table so the user can sign up for anything they are missing,
   then ask them to send the keys here (or name the ones they want to leave off; those providers
   stay unavailable and everything else still works):

   | Service | Sign up and copy the key | Free start |
   |---|---|---|
   | Serper | https://serper.dev/ | 2,500 free queries |
   | SerpApi | https://serpapi.com/users/sign_up then https://serpapi.com/manage-api-key | 250 searches / month |
   | TinyFish | https://agent.tinyfish.ai/sign-up then https://agent.tinyfish.ai/api-keys | search and fetch free |
   | TypeSafe (Jev) | https://console.typesafe.ai/ (create an API key) | see Settings → Billing |

   Crawl4AI runs locally and needs no account. Do not register, pay for or upgrade anything on the
   user's behalf.

3. **Install the files and the runtime, with no key prompts yet.** This stops before the first-use
   questions, so the keys you were given are already stored when those questions run:
   ```powershell
   $ver = (Get-Content -LiteralPath "$src\plugin.json" -Raw | ConvertFrom-Json).version
   $dest = "$env:LOCALAPPDATA\MarketOps\releases\$ver"
   & "$src\Install.cmd" -Destination $dest -Client all -InstallPython -SkipFirstUse -NonInteractive
   $receipt = Get-Content -LiteralPath "$dest\INSTALL_RECEIPT.json" -Raw | ConvertFrom-Json
   ```
   If it stops with `INSTALL_BLOCKED: PYTHON_312_64BIT_REQUIRED`, ask the user to install 64-bit
   Python 3.12 from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH") and
   rerun the same command.

4. **Store the keys in Windows Credential Manager.** Send one JSON object of provider id to key on
   stdin — the keys never appear on a command line:
   ```powershell
   $cred = Join-Path $receipt.plugin_copy 'scripts\research_credentials.py'
   @{ serper = '<key>'; serpapi = '<key>'; tinyfish = '<key>'; typesafe = '<key>' } |
       ConvertTo-Json -Compress | & $receipt.venv_python $cred configure --stdin
   ```
   Include only the providers the user actually gave you. Readiness per provider:
   `SAVED_NOT_AUTHENTICATED` means stored (normal — it has not been used yet), `UNCHANGED` means you
   left it alone. If it prints `CREDENTIAL_SETUP_FAILED` with `INVALID_API_KEY_INPUT`, one value is
   not a single-line key: ask for that one again and leave it out of the object meanwhile. If it
   prints `CREDENTIAL_SETUP_FAILED` with `OS_KEYRING_SECURE_BACKEND_UNAVAILABLE`, tell the user the
   machine's credential store is not available and stop — never fall back to a file or a variable.

5. **First-use setup in a window the user can type into.** It asks for the data directory, offers the
   free local Crawl4AI, and asks which providers to enable. Start it in its own console instead of
   capturing its output:
   ```powershell
   Start-Process -FilePath $receipt.venv_python -Wait `
       -ArgumentList @((Join-Path $receipt.plugin_copy 'scripts\customer_setup.py'), '--skip-keys')
   ```
   `--skip-keys` is correct here: the keys are already in the credential store, and the setup reads
   them from there. Tell the user: "A black window opened. Press Enter to accept the default data
   directory, answer y to the Crawl4AI and network questions, and keep the suggested request limit.
   Tell me when it finishes."

6. **Connect the plugin to this agent.** The installer writes
   `%LOCALAPPDATA%\MarketOps\current\START_HERE.md` (or the folder the user chose) with
   the exact paths for this computer. Read it and do the part for the agent you are:
   - **Codex**: run the two `codex plugin ...` commands it lists, then restart Codex.
   - **WorkBuddy**: Connector management → import custom connector → choose the `connectors\workbuddy`
     folder it lists; enable it and start a new conversation.
   - **Claude Code**: start with `claude --plugin-dir <the plugin folder it lists>`.
   - **DeepSeek Harness or any other MCP agent**: import `GENERIC_MCP.json` from the install folder
     into its MCP settings, and add the `skills` folder it lists to its skills/prompts (if it has no
     skills support, use `skills\marketops-research\SKILL.md` as the system prompt).

7. **Check.** In a new conversation, call `marketops_setup_status` and read its
   `provider_readiness`: report to the user which of Serper, SerpApi, TinyFish, Crawl4AI and Jev
   are `READY`, and for any other state tell them the fix it lists (`KEY_MISSING` here means the key
   for that provider was never sent — rerun step 4 with just that one).

8. **Show the user how to start**, e.g.:
   > 用 MarketOps 调研：<地区> 的 <目标类型>，要找 <角色> 的邮箱。我们卖的是 <产品>，买来使用的是客户，卖同类产品的是竞品要排除。预算已授权，不要停下来问我，完成后给我 Excel、报告和反馈包。

## Updating later

Installs under `%LOCALAPPDATA%\MarketOps\releases\<version>` get a stable
`%LOCALAPPDATA%\MarketOps\current` that every host config points at, plus `Update.cmd`.
When `marketops_update_check` reports `UPDATE_AVAILABLE`, or the user asks to update:

```powershell
$env:MARKETOPS_NO_PAUSE = "1"
& "$env:LOCALAPPDATA\MarketOps\Update.cmd"
```

It installs the newest GitHub release beside the current one, checks it, and only then moves
`current`; on any failure nothing changes. Then tell the user to restart this agent and call
`marketops_setup_status`. `Update.cmd --rollback` returns to the previous version. Never run the
update in the middle of a research task.
