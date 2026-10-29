# The tools' defaults, and how to overrule them

Read when a tool does something you did not expect, or before overruling one of its defaults. Each row: what the tools do by default, the run that taught it, and your call.


| The tools, by default | Why | Your call |
|---|---|---|
| Research lanes (`sweep`, `dig`, `site`) hand out organisations source by source in turns; each source gets a pilot of 30, then more only while it yields A/B (>= 5%) | Bruce: test a source on 30, scale at >= 10%, stop under 5%; 072 researched one source of 27 | `sources(set=[{source, status: "scale"}])` gives a source everything, `"dropped"` nothing; `flow.sources_take_turns` shows each pilot. You may claim or `tasks(action="add")` any package |
| A list row that prints an address gives it to that row's organisation as a contact, quoted from the row | school, club and timetable lists carry mailboxes; 072 threw away 1,658+ of them | map `email` / `person` / `role`; grade or correct as always |
| Organisations match on their registry number (UAI, SIRET, RNA) before name and town | one club became two when one list wrote `75011` and another `Paris` | map the number when the file has one |
| One organisation pooled twice (same name words, registry number or personal address) shows in `flow.possible_duplicates` and `needs_you`; the strict ledger counts one address once | paris11-073: six clubs pooled twice, two presidents counted twice | read the list, then `orgs(merge="suggested", keep_apart=[ids of groups that are two])` merges them all and again until none is left; one pair: `orgs(merge=[{keep, drop}])`. Not `part_of`: that is for a chain's stores |
| A list that carries a registry number (RNA, SIRET, UAI...) is mined early, beside (never ahead of) the lists that name people | it makes every later list's duplicates a mechanical match instead of a guess on names (077: 31 groups left to name likeness) | the lead orders the source lane: claim it first, or `tasks(action="add", kind="custom", title="pool <list> first")` |
| A researcher's empty `sweep` claim hands out `dig` and back - one job, two lanes | 077: 14 empty sweep claims while 50 read organisations waited in dig | - |
| A host that does not answer a connection in 10 s is not asked again for 30 minutes | 077: three tries on one unreachable PDF cost 367 s | its cached text, if any, is still read |
| Phones: an unbroken digit run not starting with 0 or + (a SIREN, a SIRET) is not a phone; the brief's country plan is checked; at most 20 per organisation | 077: 1,778 of 1,893 "phones" were record numbers on one directory | - |
| `grade(set=[{org_id, all: "X"}])` grades every address of one organisation you have not set yourself | 077: a dojo's 13 instructor mailboxes could not be cleared at once | any grade, not only X |
| A page that prints many other parties' addresses is a list: with `org_id`, only the entry that names the organisation (and its own domain's addresses) go to it; `fetch` returns `list_page` | 073: a district table gave a club 8 others' addresses; fr41: a commune's /associations page gave it 76 | the list is a source: `sources(add=...)` and pool its entries as organisations with their addresses (`orgs(add=[{name, email, person, role, quote}])`) |
| `run` without `orgs` reads sites ahead of researchers, never digs, waits once 30 are waiting | 071 | dig rounds for your own package: `run(orgs=[...], rounds=1-2)` |
| Stores of one chain (one domain, a shared name word) show in `flow.possible_chains` and `needs_you`; a store filed `part_of` its head office leaves the lanes and the headline | a store page gives a front desk, the head office decides (073 smoke: 0 A/B on 4 stores of one chain) | add the head office, `orgs(set=[{org_id, part_of}])` the stores - or leave them, for an independent store |
| Over 60 new addresses on one organisation (a chain's every store, a federation's every club) are not all sent to Jev: those naming the brief's region first, the rest are held - outside every total, on their own workbook sheet | 073: 1,167 on one chain; paris11: 274 held rows were 41% of the workbook | pool the list's entries, then `grade(held=[{org_id, held: "X"}])`; or `"grade"` to send them to Jev |
| An empty lane says why (`keep_waiting`, `other_lanes`, `lane_finished`, `why_empty` - a pilot, the brief's `max_per_source`, sources still mined, a project with nothing registered yet); a worker stops only on `lane_finished`. A source under 5% A/B after its pilot is paused by default - nobody waits for a verdict - and `needs_you` lists it | paris11-073: three workers stopped with 310 organisations unread; paris11-074: two workers idled seven minutes waiting for a lead's call | resume a paused source (`sources(set=[{source, status: "scale"}])`), raise `max_per_source` |
| `project(brief=...)` merges: send only the keys you change (`null` removes one) | paris11-073: one key wiped the goal, region and fresh | - |
| The brief's `minutes` is the run's time box, counted from the moment it is set (`project()["time_left_min"]`); when it is up, and after `tasks(action="stop", note)`, every claim answers `stop` and `run` ends | 078 ran three hours because nothing stopped the team; its A/B rate fell from 41% at 38 minutes to 37% | `tasks(action="resume")`, or raise `minutes` |
| `tasks(action="done")` answers `next`: claim the next package now; a worker silent for 3+ minutes while packages are open is named in `needs_you` | 079: three workers ended their turn after one package each; nothing moved for ten minutes | start it again with the same role |
| A researcher whose lanes are finished, while the pool is not saturated, is handed a `find` package ("find: more lists of ...") - at most one every 15 minutes, five in all | 079: workers waited or ended while the pool could still grow | add your own `find` lanes for the gaps you see |
| A source its pilot paused (under 5% A/B) counts as adding nothing to the pool's growth | 079: a company register gave 225 new names, 1.7% with A/B, and kept the pool "growing" | - |
| The address of the site a page is on, already given to another organisation from another page of that site, is held as the site's footer inbox | 079: a sports office's inbox on every club fiche of its yearbook | `grade(set=...)` it on the organisation it really is |
| A page frame read before its script ran (big page, almost no text) is not cached; another reader is tried | 079: cached frames hid five true A/B from the spot check | - |
| A website found by search is never a domain another organisation already has, and a one-word name needs its words in the domain | 079: a village directory became three clubs' website; an acronym dictionary another's | - |
| A website found by search must not show another département's postcodes; listings and company registers are never a website | fr41: a commune got its namesake's site in the Cher; paris11: a club got sortiraparis.com | `orgs(set=[{org_id, website}])` when you know better |
| `browse` (the paid TinyFish Agent) is off; a call records the page and the user is asked once for all of them | it costs per step | the user allows it in the brief (`paid_agent: true`) |
| A fresh project (`brief.fresh`) reuses nothing other projects read or learned, and flags files nobody fetched or registered here | a test must start from zero; 072b began from a 0.6.6 pool | without `fresh` the shared library makes reruns cheap |
| Every tool call goes to the project's `calls.jsonl` (agent, tool, arguments in brief, seconds, errors) | the whole chain is reviewable without transcripts | read it in the retrospective |


# Why the workflow is shaped this way

Five things decide a run; each has a reason and a place where the plugin holds it for you.
Under all five: **the plugin makes agents stronger; it does not script them.** The agents
choose the lists, mine them, judge whose an address is, dig, scale or drop a source; the tools
do the bulk work an agent asks for in one call (read ten sites, page through a list, grade a
batch) and hand back what they saw, with advice. A tool never researches alone: `run` without
`orgs` only reads sites ahead of the researchers, stops starting new ones once 30 wait for an
agent (`waiting_for_agents`), and ends after an hour with no agent taking work. Paris 071 let a
script dig six rounds alone for six hours: 15 organisations with A/B for 105
organisation-hours, most addresses wrongly shared. "What the tools give you" below lists each
default and how you overrule it.

- **Pipeline - stages hand over through pools, never through messages.** A stage takes what is
  on disk in the pool before it and writes to the pool after it, so no stage waits for another to
  finish and nothing is lost between them. Handovers in chat were Bruce's costliest failure (a
  middle layer built and never written back). `project()["flow"]` shows what waits between pools.
- **Concurrency - every stage at once, several agents per stage, one writer per claim.** Finding
  lists, mining them, finding sites and researching people overlap from the first minute; inside
  a lane each agent owns its claim (one source, ten organisations) end to end. Splitting one
  package across many hands made Bruce's runs slower (coordination, not crawling, became the
  cost); more claims in parallel made them faster.
- **Many sources - coverage comes from the union of lists, not from one good list.** Each source
  knows a part of the market and one kind of entity; saturation (the last sources mostly repeat
  what is pooled) is the only honest sign the pool is complete. And every pooled source must be
  *researched*, not only pooled: the research lanes give each one a turn (Paris 072 pooled 27
  sources, then four researchers spent forty minutes on the first one; 26 were never read).
- **Speed - more organisations in flight, cheap tools before expensive ones.** Throughput is
  agents x claims, not pages per site; the tools race providers, batch requests, cache every page
  and search for every project, and the lightest tool goes first (search -> fetch -> Agent). Never
  poll a status; work, and look at `flow` every 10-15 minutes.
- **Quality - evidence on every row, judged at the source, checked before delivery.** Every
  address keeps its page and quote; grades follow one table; weak sources are dropped after 30
  organisations, not after the run; a 100-row spot check gates delivery. A missing route is better
  than a wrong one - and a status line is never evidence.

