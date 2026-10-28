# Retrospective: bench-paris11-0.7.3-20261028

Written 2026-10-28 14:38 at export. Masked: no addresses or phone numbers.

## Numbers

- 机构数: 328
- 场地数: 67
- 数据源饱和: 4 个数据源；最近新增占比 70%, 10%, 0%；未饱和，池子可能不全
- 研究有效账：确认的 A–D 研究有效邮箱（A / B / C / D，各级标注；不含待复核）: 104 条（13 / 7 / 7 / 77），77 家机构
- 严格账（简报 strict_ledger = AB：named_A 只算具名 A，A 只算 A，AB 算 A+B）: 20 条，17 家机构
- 广度：有确认 A+B 的机构占比（有确认 A/B 的机构 ÷ 机构数；不含待复核）: 5.2%（17 家）
- 每百机构 A+B 路线数（A+B 条数 ÷ 机构数 × 100，Bruce 口径；同一人两地址计一条）: 6.1
- 每百机构 A+B 路线数，每机构最多计 3 条: 6.1
- A+B 路线数 / 有 A+B 的机构数: 20 / 17
- 覆盖率（有任一 A–D 路线的机构 ÷ 机构数）: 26.5%
- 待复核的 A/B（低置信度、共用地址或来自他站；不计入上面的数）: 12
- 若待复核全部成立：每百机构 A+B 路线数: 9.8
- 对账：机构按最佳等级 确认A / 确认B / 只有待复核的A或B / C / D / 未找到（其中未抓取） = 机构数；确认A + 确认B = 上面的有 A+B 机构数: 13 + 4 + 11 + 4 + 55 + 241（236） = 328 / 328，对得上
- 国家未识别的机构（应补齐，不得交付为未识别）: 0
- 邮箱总数（A/B/C/D/未评级/X）: 13 / 19 / 7 / 77 / 274 / 280
- 证据强/中/弱（邮箱数；弱的也保留，由人判断）: 36 / 569 / 65
- 抽检准确率（抽检页已核对的确认 A/B 中正确的比例；交付门槛 ≥ 90%）: 未抽检（打开抽检页逐条核对，用 grade(set=[{value, org_id, checked}]) 记下结果）
- agent 已下结论的机构（agent 结论页）: 29

## Work waiting per stage at export

```
{
 "sources": {
  "pooled": 4,
  "saturated": false,
  "status": "keep looking: recent sources still add new organisations",
  "new_share_last": [
   0.704,
   0.104,
   0.0
  ],
  "waiting_to_pool": 0
 },
 "orgs": {
  "total": 328,
  "no_website": 214,
  "not_read": 299,
  "in_pipeline": 0,
  "pipeline_done": 29
 },
 "dig": {
  "done_without_confirmed_ab": 14,
  "waiting_for_an_agent": 0,
  "agent_verdicts": 29
 },
 "review": {
  "ab_rows_to_review": 12
 },
 "check": {
  "checked": 0,
  "ok": 0
 },
 "held_by_agents": 0
}
```

## Reads and searches

| provider | calls | failed | by kind |
|---|---|---|---|
| crawl4ai | 137 | 7 | ours 5, blocked 1, empty 1 |
| deepseek | 175 | 20 | empty 18, transient 2 |
| download | 1 | 0 |  |
| http | 2 | 2 | down 1, transient 1 |
| jev | 40 | 0 |  |
| serper | 31 | 1 | other 1 |
| tinyfish | 171 | 15 | gone 10, down 3, transient 2 |

## The tool calls (calls.jsonl)

| agent | first | last | calls | seconds | tools |
|---|---|---|---|---|---|
| lead | 05:52 | 06:38 | 13 | 9 | project 6, tasks 2, sources 2, grade 2, export 1 |
| finder | 05:53 | 05:57 | 21 | 118 | fetch 10, sources 5, search 4, project 2 |
| (unnamed) | 05:53 | 06:37 | 3 | 1 | project 2, tasks 1 |
| researcher | 05:53 | 06:37 | 62 | 526 | tasks 19, fetch 13, search 7, grade 7, orgs 6, project 5, run 5 |
| miner | 05:53 | 06:14 | 29 | 404 | tasks 12, pool 5, fetch 4, project 4, sources 3, orgs 1 |

Slowest: fetch 240.53 s (miner); fetch 120.63 s (miner); fetch 64.61 s (finder); orgs 51.02 s (researcher); orgs 51.01 s (researcher)

## Playbook segments

- mairie 11e - créneaux sportifs associatifs

## Agent verdicts

- none: 1
- partial: 8
- routes: 20

## The lead's account

- 2026-10-28T14:38 (lead): 这轮不能直接进 0.7.3 基线表：简报多了一个 max_per_source=8（and fresh_since），而 references/benchmarks.md 的标准 smoke 简报里没有这个键。它把每个数据源的上限钉在 8 家，4 个源合计 29 家就自然到底，sweep 通道看起来「空」而 299 家未读——这是我自己造的上限，不是规划器的缺陷。另外文档要求空 MARKETOPS_HOME 才是真正的 from zero，这轮用的是共享库 + brief.fresh=true（无 brought-in 源，但 pages/searches 池是热的）。 - brief.json 的 max_per_source=8；flow.sources_take_turns worked 8/8/8/5 = 29 = pipeline_done；references/benchmarks.md 第 24-30 行的标准简报无此键

## Blockers

- 2026-10-28T13:53 (lead): project(name, brief={...}) replaces brief.json instead of merging: I passed {max_per_source: 8} to add one key and the goal/country/region/fresh were gone, needs_you then said 'the brief has no goal'. Recoverable only because the lead kept the full brief; the tool's docstring says 'brief (optional, saved as brief.json)' and nothing about replacement. - calls.jsonl <phone>T05:52:26 vs 05:52:50; project() needs_you after the partial call
- 2026-10-28T13:56 (researcher): ASC 11's own site aikido-asc11.fr no longer resolves (DNS NXDOMAIN, no reader could reach it). The city of Paris activity sheet 119419 carries the record instead and lists a generic c***@aikido-gcercce.fr shared with the GCERCCE group; no named officer with an address is published anywhere. - fetch http://aikido-asc11.fr -> all readers failed; paris.fr/activites/...119419
- 2026-10-28T13:56 (researcher): A club page can be another club's page: a GCERCCE news post mentioning ASC11's Japy class got its contact@ attributed to ASC11 by the reader. Attribution must be checked against the page's own identity before grading - I regraded it X then D once paris.fr confirmed the shared inbox. - fetch aikido-gcercce.fr/single-post/2020/10/07/nouveau-report, contacts_added 1 -> wrong org
- 2026-10-28T13:57 (finder): Federation club registers did not surface as usable lists: the long multi-federation query got HTTP 400 from serper, deepseek returned only regional PDF annuaires and competition guides, and the finders that did appear (monclub.ffhandball.fr, cartographie.ffhandball.fr) are JS maps with no crawlable row list. Went round it with the arrondissement's own registers (OMS, MVAC, mairie11) plus two open-data layers. - search 'annuaire des clubs fédération Paris 75011 ...' -> serper skipped HTTP 400, deepseek 12 results all PDFs/docs
- 2026-10-28T13:57 (finder): Two directory candidates are dead ends on this host: the Ville de Paris association portal associations-v2beta.paris.fr/searchasso fails on every reader (tinyfish target_http_error, crawl4ai crash, deepseek 504, http blocked and set aside), and assofinder.fr/associations-paris-11 is behind a Cloudflare JS challenge. Dropped both instead of logging in or paying a browse. - fetch attempts <phone>:0x
- 2026-10-28T13:59 (miner): MVAC Mailchimp page: rows print most emails as plain text and only a minority as mailto: links (8/110), so a mailto-only parse silently loses them; the same page glues the previous entry's contact onto the next club (Aikido Club du 11e shows Budo XI's address first, then its own), so the first email on a row is not always that row's. - miner/mvac-rows.json: 110 entries with email after adding plain-text regex; Aikido row had p***@gmail.com before b***@wanadoo.fr
- 2026-10-28T14:10 (researcher): The pool carries wrong websites: ASSOCIATION SPORTIVE CANNE D'ARME had sortiraparis.com as its website (a listings site), so 15 pages were read for nothing; its real site batoncanne.com was found only from the brochure. The websites lane should be corrected from the brochure's per-club URL column - one pass over pp.43-47 would fix a whole batch of these. - orgs(ids=[org-c4b58cbb6e]) website https://www.sortiraparis.com/ vs brochure 'Site web : https://www.batoncanne.com'
- 2026-10-28T14:25 (lead): Both lanes went empty at 14:12 and no worker said why: the miner finished its third source and stopped with three more registered at 13:55 waiting, and the researcher stopped after its first package. The lead found it by reading tasks.csv eight minutes later - nothing in project() pushes, and the workers do not quote why_empty before ending. Net cost of the stall: 13 minutes of a 35-minute box. - calls.jsonl: last worker call 06:12 UTC, all three workers idle by 06:12 while tasks.csv showed 3 source tasks open and flow showed organisations->contacts 310
- 2026-10-28T14:37 (researcher): Sweep lane ends empty after a waited claim (wait=50) while 299 organisations stay not_read: the run's own brief.max_per_source=8 caps every pooled source (Mairie 11e 8/8, OMS 11e 8/8, Paris Data 8/8, MVAC 5 dropped = 29 = pipeline_done), so the planner has nothing left that is eligible to package. Not a queue defect - the lane is bounded by the brief, and 29 worked already exceeds the brief's 'at most 20 organisations'. - tasks(action=claim, kind=sweep, wait=50) -> {"task": null, "note": "queue empty", "queue": {"source": {open:0,claimed:0,done:6}, "sweep": {open:0,claimed:0,done:5}}}; project(): orgs.not_read=299, pipeline_done=29, sources_take_turns worked 8/8/8/5, brief.max_per_source=8
- 2026-10-28T14:38 (lead): 同一个俱乐部被拆成 2-3 家机构，严格账因此重复计数：AS Aïkido 11 与 KI AIKIDO ASSOCIATION (K2A) 是同一家（同为 <person>、同一句 quote、同一邮箱），ASSOCIATION AIKIDO CLUB DU 11EME 与 AÏKIDO CLUB du 11ème 是同一家（同为 <person>），CLUB BASKET PARIS XI 在池子里以 CBP 11 / CLUB BASKET PARIS XI / CLUB BASKET PARIS XI CPB XI 三行并存。20 条严格路线里至少 3 条是同一个人的第二次计数；去重后约为 17 条 / 15 家。possible_chains 已经点出其中 3 组（kiaikidoassociation.org、eks-paris.com、yiquan78.org），但 lead 没有执行 orgs(set=part_of)，所以机制报出来了、没人接。 - contacts.csv: 两条 A 同值 d***@wanadoo.fr 分属 org-d6b1818a9c/org-4f9d73b66e；两条 A 同值 b***@wanadoo.fr 分属两家；grade 结果里 CPB11 出现 secreteriat@ 错拼变体

## What went smoothly

- 2026-10-28T13:54 (miner): Mairie 11e créneaux page: regular accordion panels (h2 discipline, span header time, p.accordion-panel with venue<br>address<br><strong>Association <a href=site>Name</a></strong>). Parsing the saved crawl4ai HTML gave 630 rows / 141 names without any harvest calls; the only trap was several <strong> blocks where the first is an empty <a href></a> - concatenate/last non-empty strong, else 98 rows lose the name. - miner/mairie11-slots.json 630 rows, 141 names; after fix 3 rows without association
- 2026-10-28T13:56 (researcher): The district sports office guide is the highest-yield page for Paris 11e clubs: one PDF (oms-paris11.org/documents/BROCHURE_OMS_2026.pdf, clubs list pp.43-47) gives, per club, the président/présidente name, mobile and often a personal email - 40+ A-grade routes from one fetch. It should be read before any club site. - fetch of BROCHURE_OMS_2026.pdf: 117 addresses extracted, one per named officer
- 2026-10-28T13:57 (finder): The district's own two registers beat every federation: the OMS 11e yearbook PDF (annuaire des associations affiliées, pp.43-47) prints for each club the président(e) name + personal email + mobile, and the MVAC's Mailchimp annuaire prints an email and/or mobile per association with a 'Site internet' link. Both found by searching for the arrondissement's association directory, not for a federation club finder. - http://www.oms-paris11.org/documents/OMS-<phone>.pdf (101 addresses in 41.8k chars); https://mailchi.mp/c679f28ad6fd/9d0e4locin (175 addresses, ~90 associations)
- 2026-10-28T13:58 (miner): OMS PDF: direct download reader timed out (ConnectTimeout) but the finder had already read the same URL with TinyFish, so its extracted text was in the page library - the miner can reuse it instead of re-fetching. The roster's value is that each row prints a president/contact with a personal email; splitting the PDF text into clubs on 'Siege social' and assigning each email to the nearest preceding President/Contact label gave 61 of 62 rows a named contact. - miner/oms-rows.json: 44 clubs, 62 rows, 61 with email; pool contacts_from_rows=57
- 2026-10-28T14:10 (researcher): Second package confirmed the pattern on 7 of 10: the OMS 11e brochure (p.43-45) alone gave a named président·e with a personal address for AS Bouvines, ASCP XI, Aïkido Club du 11e, Amicale Bouliste du Père Lachaise and AS PVP 11e, plus a role mailbox for APSAP VP and CODERS 75 - while the organisations' own sites yielded nothing above C/D. Reading the district guide first is ~10x cheaper than per-club crawling. - BROCHURE_OMS_2026.pdf pp.43-45; 7 of 10 orgs resolved A/B from it
- 2026-10-28T14:25 (lead): Source accounting holds: 6 lists registered with their URL, 3 pooled and 3 dropped with a reason each (RES Paris host unreachable, OMS html mirror a duplicate of the PDF), every one of the 328 organisations attributable, reconcile adds_up true. - tasks.csv 6 source tasks done 0 open; contacts 297 rows from 6 sources; orgs_without_logged_source 0

## Decisions

- 2026-10-28T13:56 (researcher): CAP Aikido Parisien (Cercle d'Aikido Parisien) came from an 11e source but its dojo is 93 rue Pelleport 75020 and its second venue 75019. I recorded the A route and flagged scope instead of dropping it; whether a club that practises outside 75011 belongs in the Paris 11e delivery is the user's call. - aikido-paris-cap.org/contact
- 2026-10-28T14:25 (lead): Pilot decision as the lead: scale Mairie 11e (50% A/B over 8 worked) and OMS 11e (100% over 5), drop MVAC (20% over 5, plain-text addresses and shared mailboxes). The table was right and easy to act on - this is the 0.7.3 change working as designed. - flow.sources_take_turns at 14:26: Mairie 11e 8/4 A-B 50%, OMS 11e 5/5 100%, MVAC 5/1 20%, Paris Data 0/78; not_read 310

## Ideas for the plugin

- 2026-10-28T14:10 (researcher): For this market the yield order is: district/brochure PDF (président + personal email) > club site mentions légales > club site contact page (generic inbox + phone) > city activity sheet (contact@ shared with the administering club). A researcher's first action on a Paris club should be to look for the organisation's entry in the OMS brochure before touching its website. - two packages, 15 organisations, 10 A/B rows; brochure rows win on both grade and cost
- 2026-10-28T14:14 (miner): Opendatasoft portals: /explore/dataset/<id>/export/?format=csv serves the HTML page, not the CSV. The export that works is /api/explore/v2.1/catalog/datasets/<id>/exports/csv?delimiter=%3B. Bonus: the v2 export of Paris Data's occupation des créneaux keeps telephone_du_contact and adresse_mail_du_contact, which the finder's note said were absent - the 75011 slice alone gave 104 contacts. Also worth knowing: pooling one slot file twice (into=venues group_by=association, then into=orgs name=association) yields the facility view and the contacts; the venue pass does not attach row addresses. - miner/pd-75011.json: 748 rows, 124 associations, 114 with email; pool orgs pass contacts_from_rows=104

## Noticed by the plugin

- 2026-10-28T14:26 (plugin): organisation org-b7ecefb4cd: 274 more addresses than the 60 graded - its pages list a whole directory; an agent decides which belong (they wait ungraded) - contacts
