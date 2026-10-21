---
description: Set up MarketOps once - keys, data directory, local crawler and budget
---

Help the user finish MarketOps setup. Keys given to you here go into the OS credential store and
nowhere else: pass them on stdin to `scripts/research_credentials.py configure --stdin`, never echo
one back, never put one in a file, a command argument, a URL or a commit.

1. Call `marketops_setup_status`. If it is `CONFIGURED` and the providers they need show keys,
   say so and stop.
2. If the user has not given you keys yet, ask for the four (Serper, SerpApi, TinyFish,
   TypeSafe/Jev) and point them at `references/FOUR_ACCOUNT_START.md` for the sign-up pages. A
   provider they leave out stays unavailable and everything else still works.
3. Store them as one JSON object of provider id to key on stdin:

   ```
   @{ serper = '<key>'; serpapi = '<key>'; tinyfish = '<key>'; typesafe = '<key>' } |
       ConvertTo-Json -Compress | & "<install dir>\.venv\Scripts\python.exe" "<install dir>\plugins\marketops-research-assistant\scripts\research_credentials.py" configure --stdin
   ```

   On macOS/Linux the same call is
   `printf '%s' '{"serper": "<key>"}' | "<install dir>/.venv/bin/python" "<install dir>/plugins/marketops-research-assistant/scripts/research_credentials.py" configure --stdin`.

   `SAVED_NOT_AUTHENTICATED` for a provider means it is stored; `UNCHANGED` means you left it alone.
   `INVALID_API_KEY_INPUT` means one value is not a single-line key - ask for that one again.
4. Finish first-use setup in a terminal on this machine (data directory, local Crawl4AI, which
   providers to enable, budget). `--skip-keys` is right: the keys are already stored.

   ```
   "<install dir>\.venv\Scripts\python.exe" "<install dir>\plugins\marketops-research-assistant\scripts\customer_setup.py" --skip-keys
   ```

   Recommend a data directory on a local disk (not OneDrive, iCloud, Dropbox or a network
   drive): every window will share it.
5. Call `marketops_setup_status` again and confirm which providers are ready. Then suggest
   `/research <task>` to start.
