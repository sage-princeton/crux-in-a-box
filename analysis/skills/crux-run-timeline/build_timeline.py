#!/usr/bin/env python3
"""Build a self-contained HTML timeline + root-cause page from a CRUX run mirror.

Usage:
    python3 build_timeline.py <clean-room-dir>

<clean-room-dir> must contain:
    workspace/LOG.md      the run's research log (required)
    .telemetry.jsonl      raw telemetry (optional; used for operator messages)
    narrative.json        your written analysis (beats + 5-whys + meta)

Output:
    <clean-room-dir>/timeline.html   self-contained, no external assets

Design notes:
  - The verbatim LOG bodies are source data and are shown unedited (only
    credential-scrubbed). Everything you *write* (summary, beats, whys) lives
    in narrative.json so the same script serves any run.
  - Credentials are stripped from every rendered string and the script
    hard-asserts no known key shape survives into the output.
"""
import re, json, html, datetime, pathlib, sys

if len(sys.argv) != 2:
    sys.exit("usage: python3 build_timeline.py <clean-room-dir>")
ROOT = pathlib.Path(sys.argv[1]).resolve()
LOG = ROOT / "workspace" / "LOG.md"
TEL = ROOT / ".telemetry.jsonl"
NAR = ROOT / "narrative.json"
OUT = ROOT / "timeline.html"

if not LOG.exists():
    sys.exit(f"missing {LOG}")

# ---- credential scrub (belt-and-braces; extend if a run uses other key shapes) ----
SECRET = re.compile(
    r"sk-or-v1-[A-Za-z0-9]+|sk-ant-[A-Za-z0-9-]+|sk-[A-Za-z0-9]{20,}"
    r"|rp[-_][A-Za-z0-9]+|gh[pousr]_[A-Za-z0-9]+|AKIA[0-9A-Z]{16}"
)
def scrub(s): return SECRET.sub("«redacted-credential»", s)
def esc(s): return html.escape(scrub(s))

# ---- narrative (your analysis) ----
if NAR.exists():
    nar = json.loads(NAR.read_text(encoding="utf-8"))
else:
    nar = {}
meta   = nar.get("meta", {})
lead   = nar.get("lead", "")
beats  = nar.get("beats", [])          # [{time,title,desc,cls}]
whys   = nar.get("whys", [])           # [{problem,chain:[[q,a]...],root,fixes:[...]}]
title_h1 = meta.get("title", "Research agent run — what happened")
subtitle = meta.get("subtitle", "")

# ---- parse LOG entries ----
hdr = re.compile(r"^### (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})(?: host clock| UTC)? — (.*)$")
entries, cur = [], None
for ln in LOG.read_text(encoding="utf-8").split("\n"):
    m = hdr.match(ln)
    if m:
        if cur: entries.append(cur)
        cur = {"date": m.group(1), "time": m.group(2), "title": m.group(3).strip(), "body": []}
    elif cur is not None:
        cur["body"].append(ln)
if cur: entries.append(cur)
entries = [e for e in entries if re.match(r"\d{4}-\d{2}-\d{2}", e["date"])]

def classify(title):
    t = title.lower()
    if "tier-3" in t or "credential failure" in t or ("credential" in t and any(k in t for k in ["restore","absent","lost","missing"])):
        return "credential"
    if any(k in t for k in ["permanently killed","exhaust","terminal","block-kill"]) or t.endswith("kill") or "`kill`" in t or "kill " in t:
        return "kill"
    if any(k in t for k in ["block","reject","malformed","fail","refus","vacuity","underpowered","stuck","pivot"]):
        return "block"
    if title.startswith("MEMO") or " MEMO " in title: return "memo"
    if any(k in t for k in ["milestone","gate pass","gate `pass`","pass;","`go`","go;","author-pass","review-pass","adequate","freeze-pass","settled"]):
        return "progress"
    if any(k in t for k in ["launch","dispatch","scout","delegate","author"]): return "launch"
    if "heartbeat" in t: return "heartbeat"
    return "other"

for e in entries:
    e["cls"] = classify(e["title"])
    cm = re.search(r"[Cc]andidate[- ](\d+)", e["title"]); e["cand"] = f"C{cm.group(1)}" if cm else ""
    mm = re.search(r"\bM-(\d+)\b", e["title"]);          e["memo"] = f"M-{mm.group(1)}" if mm else ""

CLS_META = {
    "credential": ("#e11d48", "Key / credential"),
    "kill":       ("#b91c1c", "Stopped for good"),
    "block":      ("#d97706", "Blocked or failed"),
    "memo":       ("#7c3aed", "Decision"),
    "progress":   ("#059669", "Progress"),
    "launch":     ("#2563eb", "Started work"),
    "heartbeat":  ("#64748b", "Routine check"),
    "other":      ("#475569", "Other"),
    "start":      ("#0f766e", "Start"),
}

def body_html(body):
    txt = "\n".join(body).strip()
    if not txt: return ""
    txt = esc(txt)
    txt = re.sub(r"`([^`]+)`", r"<code>\1</code>", txt)
    txt = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", txt)
    return txt

# ---- event stream ----
rows, day_seen = [], set()
for e in entries:
    color, _ = CLS_META[e["cls"]]
    if e["date"] not in day_seen:
        day_seen.add(e["date"])
        dt = datetime.datetime.strptime(e["date"], "%Y-%m-%d")
        rows.append(f'<div class="daymark">{dt.strftime("%A %d %B %Y")}</div>')
    tags = ""
    if e["cand"]: tags += f'<span class="tag cand">{e["cand"]}</span>'
    if e["memo"]: tags += f'<span class="tag memo">{e["memo"]}</span>'
    b = body_html(e["body"])
    detail = f'<details><summary>details</summary><div class="body">{b}</div></details>' if b else ""
    rows.append(f'''<div class="row {e['cls']}" data-cls="{e['cls']}">
  <div class="ts">{e['date'][5:]}<br><span class="tm">{e['time']}</span></div>
  <div class="dot" style="background:{color}"></div>
  <div class="card"><div class="ttl">{esc(e['title'])} {tags}</div>{detail}</div>
</div>''')

# ---- beats ----
beat_html = []
for b in beats:
    color, _ = CLS_META.get(b.get("cls","other"), CLS_META["other"])
    t = b.get("time","")
    beat_html.append(f'''<div class="beat" style="--c:{color}">
  <div class="beat-ts">{esc(t)}</div>
  <div class="beat-ttl">{esc(b.get("title",""))}</div>
  <div class="beat-desc">{esc(b.get("desc",""))}</div>
</div>''')

# ---- 5 whys ----
whys_html = []
for w in whys:
    steps = ""
    for n, qa in enumerate(w.get("chain", []), 1):
        q, a = (qa + ["",""])[:2]
        steps += f'''<div class="why-step"><div class="why-n">{n}</div>
      <div class="why-qa"><div class="why-q">{esc(q)}</div><div class="why-a">{esc(a)}</div></div></div>'''
    fixes = "".join(f"<li>{esc(fx)}</li>" for fx in w.get("fixes", []))
    whys_html.append(f'''<div class="why-card">
    <div class="why-problem"><span class="why-label">Problem</span>{esc(w.get("problem",""))}</div>
    <div class="why-chain">{steps}</div>
    <div class="why-root"><span class="why-label root">Root cause</span>{esc(w.get("root",""))}</div>
    <div class="why-fixes"><span class="why-label fix">What would prevent it</span><ul>{fixes}</ul></div>
  </div>''')

from collections import Counter
counts = Counter(e["cls"] for e in entries)
legend = "".join(
    f'<button class="lg" data-f="{k}" style="--c:{CLS_META[k][0]}"><span class="sw"></span>{CLS_META[k][1]} <b>{counts.get(k,0)}</b></button>'
    for k in ["credential","kill","block","memo","progress","launch","heartbeat","other"]
)

def section(cond, htmlstr): return htmlstr if cond else ""

span = f"{entries[0]['date']} to {entries[-1]['date']}" if entries else ""
if not subtitle:
    subtitle = f"{span} · {len(entries)} log entries · Built from the run's own log. Credentials removed."

HTML = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title_h1)}</title>
<style>
/* CRUX-inspired light theme: white canvas, olive-green primary, ink accents */
:root{{
  --bg:#ffffff; --ink:#111827; --mut:#6b7280; --line:#e5e7eb;
  --primary-300:#c5d7d8; --primary-100:#e4eeef; --primary-50:#f2f7f7;
  --primary-400:#a6c0c2; --primary-fg:#152526; --accent:#2f5e61;
  --warn-bg:#fde68a; --warn-br:#f59e0b; --warn-fg:#713f12;
}}
*{{box-sizing:border-box}}
html{{scroll-padding-top:96px}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  position:relative;}}
/* diagonal repeating watermark behind everything */
body::before{{content:"";position:fixed;inset:-20%;z-index:0;pointer-events:none;
  background-image:repeating-linear-gradient(-45deg,
    transparent 0 180px,
    rgba(120,113,108,.05) 180px 181px);
  }}
body::after{{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.06;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='420' height='240'><text x='0' y='150' transform='rotate(-45 210 120)' font-family='Arial,sans-serif' font-size='46' font-weight='700' fill='%23713f12'>PRELIMINARY</text></svg>");
  background-repeat:repeat;}}
/* yellow preliminary banner */
.prelim{{position:sticky;top:0;z-index:30;background:var(--warn-bg);
  border-bottom:2px solid var(--warn-br);color:var(--warn-fg);
  font-weight:700;letter-spacing:.04em;text-align:center;
  padding:9px 16px;font-size:13.5px}}
.prelim span{{font-weight:800;text-transform:uppercase}}
/* content sits above watermark */
header,.wrap{{position:relative;z-index:1}}
header{{background:var(--primary-300);border-bottom:1px solid var(--primary-400);
  padding:28px 24px 22px}}
.head-in{{max-width:1000px;margin:0 auto}}
h1{{margin:0 0 6px;font-size:30px;line-height:1.15;letter-spacing:-.01em;color:var(--primary-fg)}}
.sub{{color:#294446;font-size:14px;max-width:70ch}}
.wrap{{max-width:1000px;margin:0 auto;padding:8px 24px 90px}}
h2{{font-size:20px;font-weight:800;color:var(--ink);margin:38px 0 14px;padding-bottom:8px;border-bottom:2px solid var(--line)}}
.beats{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}}
.beat{{background:var(--primary-50);border:1px solid var(--primary-400);border-left:4px solid var(--c);border-radius:8px;padding:13px 15px}}
.beat-ts{{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--c);font-weight:700}}
.beat-ttl{{font-weight:700;margin:3px 0 5px;color:var(--ink)}}
.beat-desc{{color:var(--mut);font-size:13px}}
.legend{{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 18px}}
.lg{{background:#fff;border:1px solid var(--line);color:var(--ink);border-radius:20px;padding:5px 11px;font-size:12px;cursor:pointer;display:flex;align-items:center;gap:6px}}
.lg .sw{{width:10px;height:10px;border-radius:50%;background:var(--c)}}
.lg b{{color:var(--mut);font-weight:600}} .lg.off{{opacity:.35}}
.tools{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px}}
.tools input{{background:#fff;border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:8px 11px;min-width:240px}}
.tools button.util{{background:#fff;border:1px solid var(--line);color:var(--mut);border-radius:6px;padding:8px 11px;cursor:pointer}}
.tools button.util:hover{{border-color:var(--primary-400);color:var(--ink)}}
.daymark{{position:sticky;top:38px;background:linear-gradient(#fff,#fff 72%,transparent);padding:14px 0 8px 62px;font-weight:800;color:var(--accent);z-index:5}}
.row{{display:grid;grid-template-columns:56px 16px 1fr;gap:6px;position:relative}}
.row::before{{content:"";position:absolute;left:63px;top:0;bottom:0;width:2px;background:var(--line)}}
.ts{{text-align:right;color:var(--mut);font-family:ui-monospace,Menlo,monospace;font-size:11px;padding-top:13px}}
.ts .tm{{color:var(--ink)}}
.dot{{width:12px;height:12px;border-radius:50%;margin-top:15px;z-index:2;box-shadow:0 0 0 3px var(--bg)}}
.card{{background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px 13px;margin:5px 0;box-shadow:0 1px 2px rgba(16,24,40,.04)}}
.ttl{{font-weight:600}}
.tag{{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:10px;margin-left:6px;vertical-align:middle}}
.tag.cand{{background:#dbeafe;color:#1e40af}} .tag.memo{{background:#ede9fe;color:#5b21b6}}
details{{margin-top:6px}} summary{{cursor:pointer;color:var(--accent);font-size:12px}}
.body{{margin-top:7px;padding-top:7px;border-top:1px dashed var(--line);color:#374151;font-size:12.5px;white-space:pre-wrap}}
code{{background:var(--primary-50);padding:1px 5px;border-radius:4px;font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:#2b5457}}
.hidden{{display:none!important}}
.note{{color:var(--mut);font-size:12.5px;margin:10px 0 0}}
.lead{{font-size:17px;line-height:1.6;color:var(--ink);background:var(--primary-50);border:1px solid var(--primary-400);border-left:4px solid var(--accent);border-radius:8px;padding:16px 18px;margin:0}}
.why-card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:0 0 18px;box-shadow:0 1px 2px rgba(16,24,40,.04)}}
.why-label{{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin-bottom:4px}}
.why-label.root{{color:#b91c1c}} .why-label.fix{{color:var(--accent)}}
.why-problem{{font-size:16px;font-weight:800;color:var(--ink);margin-bottom:14px}}
.why-chain{{border-left:2px solid var(--line);margin:0 0 4px 12px}}
.why-step{{display:grid;grid-template-columns:34px 1fr;gap:10px;position:relative;padding:0 0 14px}}
.why-n{{width:26px;height:26px;border-radius:50%;background:var(--primary-100);border:1px solid var(--primary-400);color:var(--accent);font-weight:800;font-size:13px;display:flex;align-items:center;justify-content:center;margin-left:-14px}}
.why-q{{font-weight:700;color:var(--ink)}} .why-a{{color:var(--mut);font-size:13px;margin-top:2px}}
.why-root{{background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px 14px;margin:12px 0 14px;color:#7f1d1d;font-size:14px}}
.why-fixes ul{{margin:6px 0 0;padding-left:20px}} .why-fixes li{{color:#334155;font-size:13.5px;margin:4px 0}}
</style></head>
<body>
<div class="prelim">⚠ <span>This is preliminary analysis</span> — not reviewed or final; details may change.</div>
<header>
  <div class="head-in">
    <h1>{esc(title_h1)}</h1>
    <div class="sub">{esc(subtitle)}</div>
  </div>
</header>
<div class="wrap">
  {section(lead, f'<h2>The short version</h2><p class="lead">{esc(lead)}</p>')}
  {section(beat_html, f'<h2>The story in {len(beat_html)} steps</h2><div class="beats">{"".join(beat_html)}</div>')}
  {section(whys_html, '<h2>Why it happened (5 Whys)</h2><p class="note" style="margin:0 0 16px">For each failure, keep asking &ldquo;why&rdquo; until the answer is a fixable cause, not another symptom.</p>' + "".join(whys_html))}

  <h2>Every logged event</h2>
  <div class="tools">
    <input id="q" placeholder="Search the events&hellip;">
    <button class="util" id="expand">Open all details</button>
    <button class="util" id="collapse">Close all</button>
  </div>
  <div class="legend">{legend}</div>
  <div id="stream">{"".join(rows)}</div>
  <p class="note">Click a coloured label to show or hide that kind of event. Click &ldquo;details&rdquo; on any row to read the original log entry.</p>
</div>
<script>
const off=new Set();
document.querySelectorAll('.lg').forEach(b=>b.onclick=()=>{{const f=b.dataset.f;if(off.has(f)){{off.delete(f);b.classList.remove('off')}}else{{off.add(f);b.classList.add('off')}}apply();}});
const q=document.getElementById('q'); q.oninput=apply;
function apply(){{const term=q.value.trim().toLowerCase();document.querySelectorAll('.row').forEach(r=>{{const okCls=!off.has(r.dataset.cls);const okTxt=!term||r.textContent.toLowerCase().includes(term);r.classList.toggle('hidden',!(okCls&&okTxt));}});}}
document.getElementById('expand').onclick=()=>document.querySelectorAll('details').forEach(d=>d.open=true);
document.getElementById('collapse').onclick=()=>document.querySelectorAll('details').forEach(d=>d.open=false);
</script>
</body></html>'''

# hard safety gate: no known credential shape may survive
leaks = SECRET.findall(HTML)
assert not leaks, f"SECRET LEAK in output: {leaks[:1]}"
OUT.write_text(HTML, encoding="utf-8")
print(f"wrote {OUT} ({len(HTML)} bytes) — {len(entries)} entries, {len(beat_html)} beats, {len(whys_html)} why-chains")
