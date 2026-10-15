---
name: marketops-setup
description: Help Member-A and colleagues install and preflight the MarketOps Research Plugin in Codex or WorkBuddy without secretly activating paid APIs or changing existing customer credentials.
---

# Install and configure MarketOps Research

Read references/MEMBER_A_INSTALL.md first. Inspect OS/Python/installed client, installed package collisions and installation destination before changing files. Never overwrite existing setup or security settings automatically.

The five upstream services are SerpApi, Serper, Crawl4AI, TinyFish and Jev/TypeSafe. Users may configure a subset. Missing provider access is UNAVAILABLE, not successful authentication. SerpApi and Serper require separate credentials. Crawl4AI can be local OSS or optional Cloud. LiteLLM or company model gateway does not replace the crawler; optional LLM extraction needs a distinct approved model API configuration.

Default networking is disabled for every provider. All provider call limits default to 0. Explain this before installing any package. If the user approves changing this computer, run:

python scripts/setup_clients.py --destination CUSTOMER-DIRECTORY --client codex|workbuddy|both --apply

Only add --install-deps after separately confirming that downloading Python packages is permitted. That flag does not create provider accounts. All keys remain with the customer OS or WorkBuddy credential form, never in source/Git/logs/chat.

For Codex: register the generated marketplace with the official Codex CLI and have the user enable plugin/Skills, then test MCP tool listing. For WorkBuddy: build the connector on the target machine after installing from source and import from its stable folder. An archive generated on a different machine is not a portable installed MCP.

Run a no-network capabilities check before requesting user approval for any live API calls. Full E2E can only be declared after a real search, acquisition, business candidate write/readback, Jev judgment if required, Excel output and event replay; all are not yet validated in the customer client.

Do not submit forms, send messages, subscribe to plans, top up credits or access secrets. Preserve user data outside Git.