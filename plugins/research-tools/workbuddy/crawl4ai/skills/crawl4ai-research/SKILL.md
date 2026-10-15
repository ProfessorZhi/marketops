---
name: crawl4ai-public-extraction
description: Fetch small sets of approved public webpages using Crawl4AI Cloud, retaining canonical URLs and respecting provider budgets.
---

Use Crawl4AI Cloud to read a small explicitly approved set of public webpages or extract structured fields. Prefer plain scrape over large batch/recursive crawling. For every extracted claim provide the actual URL, source timestamp and the text supporting it.

Do not use topup/recharge/spend_cap/report_issue or billing/purchase actions; if exposed despite client filtering, stop and report the incorrect setup. Do not crawl restricted pages, CAPTCHA or login walls. Never treat a blocked challenge page as a usable document.

Missing data must remain unknown, not fabricated. Report whether a page was fetched successfully and whether its content actually contains the target fact. Check key/remaining credits with the human owner, never print a secret.

