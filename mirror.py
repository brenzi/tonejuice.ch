#!/usr/bin/env python3
"""Mirror tonejuice.ch to a fully static site.

WordPress + Polylang defeat Simply Static's free version (multilingual
export is a Pro feature), so we crawl the rendered site instead.

Output is written next to this script, preserving URL paths:
  https://tonejuice.ch/de/band/ -> de/band/index.html

Same-domain absolute URLs are rewritten to root-absolute paths so the
result works when served from the domain root (npx serve, IPFS DNSLink).
"""

import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

BASE = "https://tonejuice.ch"
OUT = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) static-mirror"}
SKIP_PREFIXES = ("wp-json", "wp-login.php", "xmlrpc.php")


def fetch(url):
    for _ in range(2):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=20
            ) as r:
                return r.geturl(), r.read(), r.headers.get("Content-Type", "")
        except Exception:
            time.sleep(0.5)
    return None, None, None


def refs(body):
    # strip inline script blocks (keep <script src=...> tags): their
    # src/href templates are not real references
    stripped = re.sub(
        rb"<script(?![^>]*\ssrc)[^>]*>.*?</script>", b"", body, flags=re.S | re.I
    )
    for m in re.finditer(rb"""(href|src|srcset)=["']([^"']+)["']""", stripped):
        attr, val = m.group(1).decode(), m.group(2).decode("utf-8", "replace")
        if "data:" in val or "base64" in val:
            continue
        if attr == "srcset":
            for cand in val.split(","):
                yield cand.strip().split(" ")[0]
        else:
            yield val
    for m in re.finditer(rb"url\(['\"]?([^)'\"]+)['\"]?\)", stripped):
        v = m.group(1).decode("utf-8", "replace")
        if "data:" not in v and "base64" not in v:
            yield v


def is_bad(url):
    return any(c in url for c in ("{{", " ", "<", "base64"))


def is_internal(url):
    return urlsplit(url).netloc.endswith("tonejuice.ch")


def is_skipped(url):
    p = urlsplit(url).path
    return any(p.startswith("/" + s) for s in SKIP_PREFIXES)


def local_path(url, kind):
    p = urlsplit(url).path
    if kind == "xml":
        return (p.rstrip("/") + "/index.xml").lstrip("/")
    if p.endswith("/") or p == "":
        return (p + "index.html").lstrip("/")
    return p.lstrip("/")


pages = {}  # absolute url -> (kind, body); kind is html|xml|asset
queue = [BASE + "/"]
seen = set()

while queue:
    url = queue.pop(0)
    if url in seen:
        continue
    seen.add(url)
    final, body, ctype = fetch(url)
    if final is None:
        print(f"FAIL {url}", flush=True)
        continue
    if not is_internal(final):
        continue
    kind = "html" if "html" in ctype else ("xml" if "xml" in ctype else "asset")
    if kind != "asset" and is_skipped(final):
        continue
    pages[final] = (kind, body)
    if kind == "html":
        for ref in refs(body):
            if is_bad(ref):
                continue
            full = urljoin(final, ref).split("#", 1)[0]
            if not full or full in seen:
                continue
            if is_internal(full) and not is_skipped(full):
                queue.append(full)
    elif "css" in ctype:
        for m in re.finditer(rb"url\(['\"]?([^)'\"]+)['\"]?\)", body):
            ref = m.group(1).decode("utf-8", "replace")
            if is_bad(ref) or ref.startswith(("data:", "//")):
                continue
            full = urljoin(final, ref).split("#", 1)[0]
            if full not in seen and is_internal(full) and not is_skipped(full):
                queue.append(full)
    if len(seen) % 50 == 0:
        print(f"{len(seen)} fetched, queue={len(queue)}", flush=True)


def rewrite_ref(ref):
    if ref.startswith(("#", "mailto:", "tel:", "data:", "//")):
        return None
    full = urljoin(BASE + "/", ref)
    if not is_internal(full) or is_skipped(full):
        return None
    kind = pages.get(full, (None,))[0]
    return "/" + local_path(full, kind or "asset")


def rewrite_value(attr, val):
    if attr == "srcset":
        out = []
        for cand in val.split(","):
            cand = cand.strip()
            if not cand:
                continue
            url = cand.split(" ")[0]
            out.append((rewrite_ref(url) or url) + cand[len(url):])
        return ", ".join(out)
    return rewrite_ref(val) or val


for url, (kind, body) in pages.items():
    if kind != "asset":
        body = re.sub(
            rb"""(href|src|srcset)=["\']([^"\']+)["\']""",
            lambda m: (
                m.group(1).decode()
                + '="'
                + rewrite_value(
                    m.group(1).decode(), m.group(2).decode("utf-8", "replace")
                )
                + '"'
            ).encode(),
            body,
        )
        body = body.replace(b"https://tonejuice.ch", b"")
    dest = OUT / local_path(url, kind)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)

root = OUT / "index.html"
root.write_text(
    '<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0; url=/de/band/">'
    "<title>Alain's Tonejuice</title></head><body></body></html>\n"
)
print(f"saved {len(pages) + 1} resources")
