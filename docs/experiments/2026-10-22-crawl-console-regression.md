# Crawl4AI background-console regression

The desktop Paris run reported a working Chromium launch followed by
`LOCAL_CRAWL_RUNTIME_FAILED`. Its retained diagnostic was only
`OSError:SDK_EXCEPTION`, so it did not establish the originating OS error.

## Controlled reproduction

With the 0.5.10-rc4 SDK adapter and real Crawl4AI 0.9.5, a console stream that
rejects long progress writes with `OSError(22)` reproduces the same failure over
MCP stdio. The browser successfully fetches the page, then its FETCH log write
throws. Error logging throws again and masks the original read result. The
self-check marks the local reader down. Normal console pipes pass, including
several checks in one fresh process. This is a fault-injection reproduction,
not proof that every recorded desktop failure has this origin.

## Change and acceptance

Both BrowserConfig and CrawlerRunConfig explicitly disable terminal progress.
Setting just the browser config is insufficient: `arun` replaces the logger's
verbosity from its run config. Network refusals, egress policy, retry limits,
material persistence and normal error receipts are unchanged.

OS errors retain numeric errno/winerror values without exception messages or
paths. A console-write traceback has a distinct closed diagnosis. The page
self-check returns this safe diagnosis instead of dropping it, and refreshes
the installed browser selection when a repair finished after server startup.

Validation on the Windows development machine:

- 130 focused crawler, health and research-continuation tests passed.
- The real SDK, with both stdout and stderr rejecting writes, read two local
  HTML fixtures consecutively and produced a real PDF. This regression uses
  no provider credentials or public network.
- The modified source completed the normal managed MCP page check with the
  injected failing console; the old source failed the same check.
- The installed rc5 launcher advertised 31 tools. Its 163 packaged source
  files and installer source digest matched the source tree.
- On the existing Paris Run, a final handoff request remained a continuation
  checkpoint with 11 queued targets outstanding. This check made no provider
  requests and preserved the queue, account registry and configured budget.

The already-running desktop rc4 connection does not hot-reload Python modules.
The updated desktop connection must be tested after host reload; passing a
fresh launcher alone is not that verification.

The broad local Windows suite also encounters an existing client-view replay
failure (`os.rename` over an existing directory, WinError 183) outside the
changed files. The focused suite and the clean CI suite are separate results.
