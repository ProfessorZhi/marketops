---
description: Set up MarketOps once - keys, data directory, local crawler and budget
---

Help the user finish MarketOps setup. API keys must never be typed into this chat.

1. Call `marketops_setup_status`. If it is `CONFIGURED` and the providers they need show keys,
   say so and stop.
2. Otherwise tell the user to run this in a terminal on this machine (it asks for the data
   directory, the four API keys with hidden input, installs Crawl4AI and its browser on request,
   and sets the budget):

   ```
   <install dir>/.venv/bin/python <install dir>/plugins/marketops-research-assistant/scripts/customer_setup.py
   ```

   On Windows: `<install dir>\.venv\Scripts\python.exe <install dir>\plugins\marketops-research-assistant\scripts\customer_setup.py`.
3. Recommend a data directory on a local disk (not OneDrive, iCloud, Dropbox or a network
   drive): every window will share it.
4. After they finish, call `marketops_setup_status` again and confirm which providers are ready.
   Then suggest `/research <task>` to start.
