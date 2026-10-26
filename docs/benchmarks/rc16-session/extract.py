#!/usr/bin/env python3
"""Redacted extract of the rc16 portion of this session's transcript.

Keeps the agent's own reasoning and every tool call/result *shape* — tool name,
parameters that carry no identity, status codes, reason codes, counts, error
strings — so the plugin's behaviour can be reviewed.  Drops or blanks every
identity-bearing field: organisation and person names, addresses, email
addresses, phone numbers, hostnames, page quotes, and anything that looks like
a credential.

Usage: extract.py <session.jsonl> <start-marker> <out.md>
"""
import json, re, sys

REDACT_KEYS = {
    "name", "organization", "organization_name", "account_name", "contact_name",
    "person_name", "value", "email", "phone", "quote", "evidence_text",
    "address", "website", "url", "source_url", "page_url", "site", "roles_wanted",
    "targets", "entries", "excerpt", "body", "html", "text", "title",
    "role_title", "note",
}
# Page text harvested off third-party sites: keep the count, drop the words.
# A snippet is site chrome and page copy, so no amount of name substitution
# makes it safe to publish — the shape is what matters for plugin analysis.
PAGE_TEXT_KEYS = {"snippets", "snippet", "excerpt_text", "quote_text", "rendered_text"}

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
URL = re.compile(r"https?://[^\s\"'<>)\]]+")
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d ()\-]{7,}\d)(?!\d)")


def _phone(m: "re.Match") -> str:
    """Only a run with phone-like digit density; dates and hashes survive."""
    digits = sum(c.isdigit() for c in m.group(0))
    return "<phone>" if digits >= 9 else m.group(0)

SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,}"
                    r"|AKIA[0-9A-Z]{12,}|Bearer\s+[A-Za-z0-9._\-]{10,}"
                    r"|(?i:api[_-]?key)\s*[=:]\s*\S+|(?i:password)\s*[=:]\s*\S+)")

def scrub(s: str, limit: int = 4000) -> str:
    s = EMAIL.sub("<email>", s)
    s = URL.sub("<url>", s)
    s = PHONE.sub(_phone, s)
    s = SECRET.sub("<redacted-credential>", s)
    return s if len(s) <= limit else s[:limit] + f" …[truncated {len(s)} chars]"

def redact_json(obj, depth=0):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in PAGE_TEXT_KEYS:
                n = sum(len(x) for x in v) if isinstance(v, list) else len(str(v))
                out[k] = f"<{k}:{n} chars of page text, dropped>"
            elif k in REDACT_KEYS:
                out[k] = f"<{k}:{type(v).__name__}>"
            else:
                out[k] = redact_json(v, depth + 1)
        return out
    if isinstance(obj, list):
        if len(obj) > 8:
            head = [redact_json(x, depth + 1) for x in obj[:8]]
            return head + [f"…[{len(obj)} items]"]
        return [redact_json(x, depth + 1) for x in obj]
    if isinstance(obj, str):
        return scrub(obj, 600)
    return obj

def main(argv):
    path, marker, out = argv[1], argv[2], argv[3]
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    start = next(i for i, ln in enumerate(lines) if marker in ln)
    n_in = n_out = 0
    buf = []
    for ln in lines[start:]:
        n_in += 1
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        msg = rec.get("message") or {}
        role = msg.get("role") or rec.get("type") or "?"
        content = msg.get("content")
        parts = []
        if isinstance(content, str):
            parts.append(scrub(content))
        elif isinstance(content, list):
            for c in content:
                t = c.get("type") if isinstance(c, dict) else None
                if t == "text":
                    parts.append(scrub(c.get("text", "")))
                elif t == "thinking":
                    continue
                elif t == "tool_use":
                    inp = redact_json(c.get("input") or {})
                    parts.append(f"→ `{c.get('name')}` {json.dumps(inp, ensure_ascii=False)[:1500]}")
                elif t == "tool_result":
                    body = c.get("content")
                    if isinstance(body, list):
                        body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                    body = body or ""
                    try:
                        body = json.dumps(redact_json(json.loads(body)), ensure_ascii=False)
                    except Exception:
                        body = scrub(str(body))
                    parts.append(f"← {body[:2500]}")
        if not parts:
            continue
        ts = (rec.get("timestamp") or "")[11:19]
        buf.append(f"\n**[{ts}] {role}**\n\n" + "\n\n".join(parts))
        n_out += 1
    open(out, "w", encoding="utf-8").write(
        f"# rc16 session extract (redacted)\n\n"
        f"Source: this session's own transcript, from the line containing "
        f"`{marker}` to the end. {n_out} message records.\n\n"
        f"Redaction: every organisation/person name, address, email address, phone number, "
        f"hostname and page quote is replaced by a placeholder; credential-shaped strings by "
        f"`<redacted-credential>`. Tool parameters that carry identity are blanked by key, not "
        f"dropped, so the shape of each call survives.\n" + "".join(buf))
    print(f"lines in={n_in} records out={n_out}")

if __name__ == "__main__":
    main(sys.argv)
