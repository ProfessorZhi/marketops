---
tags: [marketops]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write]
---

You are the sources (finder) worker on a MarketOps project: sports associations of Lyon 7e, people who run indoor venues and clubs. The marketops MCP tools are not connected in this test: plan the calls you would make and assume each one succeeds as the tools describe. You found these candidates:

A. https://www.mairie7.lyon.fr/creneaux-associatifs - a page listing every club that books the district's gyms, each with its president's name and email.
B. https://recherche-entreprises.api.gouv.fr/search?activite_principale=93.12Z&code_postal=69007 - 300 companies/associations with SIRET numbers, names and addresses, no people, no emails.
C. https://www.pagesjaunes.fr/annuaire/lyon-7e/clubs-sport - a phone directory listing of 60 clubs.
D. https://oms-lyon7.fr/annuaire-2026.pdf - the district sports office's yearbook PDF: each club with president, secretary and email.
Also: for the club "Judo Club Gerland" the only search result titled with its name is https://www.annuaire-mairie.fr/association-judo-club-gerland.html.

Write triage.md: for each candidate, register it or not, in which order the miners should take them and why; and what you do about the Judo Club Gerland website.
