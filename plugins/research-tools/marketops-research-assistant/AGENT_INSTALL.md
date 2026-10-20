# Instructions for the agent installing MarketOps (Windows)

You are installing the MarketOps research plugin for the user on this Windows computer. Follow
these steps in order, and tell the user in their language what you are doing at each step. Run the
commands in PowerShell.

## Rules

- **Never ask the user to paste an API key into the chat**, and never write one into a file or a
  command line. Keys are typed by the user into the installer's own window (hidden input) and are
  stored in Windows Credential Manager.
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

2. **Help the user get the four keys** before the install asks for them. Show this table and wait
   until the user says they have the keys (or says to continue without some; those providers then
   stay off):

   | Service | Sign up and copy the key | Free start |
   |---|---|---|
   | Serper | https://serper.dev/ | 2,500 free queries |
   | SerpApi | https://serpapi.com/users/sign_up then https://serpapi.com/manage-api-key | 250 searches / month |
   | TinyFish | https://agent.tinyfish.ai/sign-up then https://agent.tinyfish.ai/api-keys | search and fetch free |
   | TypeSafe (Jev) | https://console.typesafe.ai/ (create an API key) | see Settings → Billing |

   Crawl4AI runs locally and needs no account.

3. **Run the installer in a window the user can type into.** It needs the user's hidden key input,
   so start it in its own console instead of capturing its output:
   ```powershell
   Start-Process -FilePath "$src\Install.cmd" -ArgumentList '-Client','all','-InstallPython' -WorkingDirectory $src
   ```
   Tell the user: "A black installer window opened. Press Enter to accept defaults, answer y to
   the yes/no questions, and paste each key when asked (it will not be shown). Tell me when it says
   the installation finished." The window pauses at the end.

4. **Connect the plugin to this agent.** The installer writes
   `%LOCALAPPDATA%\MarketOps\releases\0.5.7-rc7\START_HERE.md` (or the folder the user chose) with
   the exact paths for this computer. Read it and do the part for the agent you are:
   - **Codex**: run the two `codex plugin ...` commands it lists, then restart Codex.
   - **WorkBuddy**: Connector management → import custom connector → choose the `connectors\workbuddy`
     folder it lists; enable it and start a new conversation.
   - **Claude Code**: start with `claude --plugin-dir <the plugin folder it lists>`.
   - **DeepSeek Harness or any other MCP agent**: import `GENERIC_MCP.json` from the install folder
     into its MCP settings, and add the `skills` folder it lists to its skills/prompts (if it has no
     skills support, use `skills\marketops-research\SKILL.md` as the system prompt).

5. **Check.** In a new conversation, call `marketops_setup_status`, then `marketops_capabilities`.
   Report to the user which of Serper, SerpApi, TinyFish, Crawl4AI and Jev are ready. A provider
   that is not ready usually means a mistyped key or no credit; rerun the setup command from the
   install folder's START_HERE.md to change keys.

6. **Show the user how to start**, e.g.:
   > 用 MarketOps 调研：<地区> 的 <目标类型>，要找 <角色> 的邮箱。我们卖的是 <产品>，买来使用的是客户，卖同类产品的是竞品要排除。预算已授权，不要停下来问我，完成后给我 Excel、报告和反馈包。
