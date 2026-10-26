# Outreach rules (not run by this plugin)

This plugin researches; it never sends, reads send history or judges sendability. Bruce's
sending and reply-monitoring rules are kept here only because they decide what a research row
must carry so the sending side can queue it without another look-up.

## What the research row must carry for sending

- country, region and scenario on every row (the sending side splits queues by them, and copy
  names the country); a country is never "unknown";
- the grade A/B/C/D and the evidence (page and quote); D is a valid, low-priority route;
- the organisation reached, which may differ from the organisation researched (a club's ground
  may be run by the council or a contractor) - copy is matched to the organisation reached;
- a name only when the page shows it; a clear role + a real work address is enough.

## His sending-side rules, for reference

- Research and sending are separate; research output is called 研究有效邮箱.
- Before a wave: check the suppression list and every sending mailbox's history; an address
  confirmed sent is always skipped; a timeout is "ambiguous", never resent automatically.
- A failure in a bulk send is recorded and skipped; the wave finishes; failures are retried in
  a second and third pass.
- A reply that names a replacement contact is a routing event: add that address.
- Reply monitoring stays in the sending thread and only scans new messages.
- Open decisions go back as one numbered list in the owning thread, answered in one reply.
- Every fixed sending rule is pushed to all sending threads, including ones opened later.
- Per line: some lines send automatically, others never without approval - follow the line's
  own rule; there is no global one.
