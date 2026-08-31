#!/usr/bin/env python3
"""
extract_run_log.py -- build a run log DIRECTLY from the OpenClaw session store.

The session store is the record of a run; the telemetry plugin is a supplement.  Every
assistant record in <sid>.jsonl carries usage{input,output,cacheRead,cacheWrite,totalTokens,
cost{...,total}}, so spend is a deduplicated sum over the store, not an estimate.

Sources (all under --sessions-dir, optionally merged with --snapshots-dir):
  sessions.json                       registry: sessionKey -> {sessionId, spawnedBy, label,
                                      usageFamilySessionIds, ...}
  <sid>.jsonl                         live session transcript (authoritative content + per-call
                                      usage/cost)
  <sid>.jsonl.<suffix>                archived transcripts: the gateway renames a deleted transcript
                                      to .jsonl.deleted.<ts>; the thinking watchdog archives a reset
                                      main session to .jsonl.reset-watchdog.<ts>.  Parsed like .jsonl.
                                      A transcript (live or archived) that holds no message records --
                                      a header-only stub -- never shadows the trajectory: the session is
                                      then rebuilt from the trajectory and listed under
                                      inputs.emptyTranscriptFiles with a note.
  <sid>.trajectory.jsonl              "openclaw-trajectory" v1 run traces.  Used for:
                                        - sessionKey resolution for sessions missing from the registry
                                          (the re-keyed/orphaned main generation, cron runs)
                                        - run boundaries (run.start / run.end), run status, run-level usage
                                        - message content (model.completed.messagesSnapshot) ONLY when the
                                          session has no transcript anywhere (i.e. it was deleted)
                                        - spawn linkage (sessions_spawn results inside snapshots)
  --snapshots-dir                     copies of <sid>.jsonl taken during the run by
                                      crux-session-snapshot.sh (cron transcripts are deleted when the
                                      job next runs; the snapshot is then the only transcript).  Any
                                      file at any depth named <sid>.jsonl or <sid>.jsonl.<suffix> is a
                                      candidate; with several copies of one sid the LARGEST wins; a live
                                      transcript in --sessions-dir always wins over a snapshot.
                                      Snapshot copies of <sid>.trajectory.jsonl are unioned with the
                                      live trajectory by sourceSeq/seq (the gateway front-trims big
                                      trajectories, so an early copy can hold the lost head).

Outputs (in --out-dir):
  run_events.jsonl   one normalized, deduplicated event per user message / assistant turn /
                     tool result / error / session|run start|end, sorted by ts
  run_summary.json   per-session, per-kind and grand-total counts, tokens, costs, refusals,
                     auth-error accounting, time span, scrub report, data-quality notes

Deduplication: assistant turns are keyed by responseId (first occurrence wins).  OpenClaw
re-persists history after some prompt errors (observed: a block of turns re-appended from the
root with thinking stripped) -- a naive sum over the store double-counts those turns.
Delivery-mirror records (no responseId) are keyed by idempotencyKey.

Run-specific values are CLI options, never constants:
  --auth-revoked-at ISO   split auth-error turns at the moment the provider key was revoked.
                          Auth-error turns are always counted; the split is only added when given.
  --rates rates.json      USD/Mtok per model (see utils/rates.example.json).  Used ONLY to estimate
                          trajectory-only sessions from their run-level usage; recorded cost is
                          usage.cost.total.  A model with priced records in the store is calibrated
                          from those records instead.

Scrubbing (ON BY DEFAULT; --blacklist adds literals, --no-scrub opts out):
  (default)               class-shape redaction on the serialized JSON lines: vendor key prefixes,
                          Bearer, JWT, labelled token=/api key:/password: values (also at the start
                          of an escaped line, as in a cat'ed .env), opaque values near the word
                          "token", OAuth/PKCE URL parameters, long opaque URL values, PEM blocks.
                          Values the labelled patterns identify are then redacted everywhere they
                          recur (pass 1 discovers, pass 2 redacts).  The scrubber treats JSON escapes
                          (\\n, \\", \\\\) as atomic so a redaction never splits one; every scrubbed
                          line is re-parsed as a self-check.  Only counts are ever printed.
  --blacklist FILE        literal strings, one per line, replaced with [REDACTED] -- the same
                          format utils/clean-telemetry.sh consumes (build it on the box with
                          utils/make-blacklist.sh; it never leaves the box).  The class shapes miss
                          a box's own literal secrets that look like nothing; always pass it there.
  --scrub                 accepted for compatibility (older callers); it is the default.
  --no-scrub              write the outputs UNSCRUBBED (a warning goes to stderr).  Only for a
                          local diff against the scrubbed output on the box: the result holds
                          whatever the store holds, live credentials included, and must not leave
                          the box.  The store itself is unredacted by construction.

Python 3 stdlib only (3.9+).  No credential-shaped string is ever printed to stdout.

Example:
  python3 utils/extract_run_log.py --sessions-dir ~/.openclaw/agents/main/sessions \\
      --snapshots-dir ~/.openclaw/session-snapshots --out-dir ~/run-export \\
      --blacklist ~/run-export/blacklist.txt --rates utils/rates.json
  (utils/export-run.sh passes --scrub explicitly as well; it is a no-op.)
"""
import argparse
import collections
import datetime as _dt
import glob
import hashlib
import json
import os
import re
import statistics
import sys

# ----------------------------------------------------------------------------
# constants (properties of the OpenClaw store format, not of any one run)
# ----------------------------------------------------------------------------
# The trajectory writer head-truncates model.completed.messagesSnapshot at this many messages
# (a 64-item array cap plus the pinned first message, as observed on OpenClaw 2026.7).  A
# snapshot of exactly this length is flagged snapshotLikelyTruncated.
SNAPSHOT_CAP = 65
USAGE_FIELDS = ("input", "output", "cacheRead", "cacheWrite", "totalTokens")
COST_FIELDS = ("input", "output", "cacheRead", "cacheWrite", "total")
RATE_FIELDS = ("input", "output", "cacheRead", "cacheWrite")
SID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
TRANSCRIPT_RE = re.compile(r"^(" + SID_RE + r")\.jsonl(?:\.(.+))?$")
TRAJECTORY_RE = re.compile(r"^(" + SID_RE + r")\.trajectory\.jsonl(?:\.(.+))?$")
SOURCE_LABEL = {"live": "session", "deleted": "session.deleted", "archived": "session.archived",
                "snapshot": "session.snapshot"}

# Pattern-based redaction (applied to the serialized JSON line, so it catches keys and values).
# Where a secret label may begin: after a non-alphanumeric character, or directly after a JSON newline/tab
# escape.  A plain \b misses "\napi_key = ..." (the 'n' of the escape is a word character) -- and a label
# on its own line is exactly how a cat'ed .env file or a config dump looks.  '_' is not a boundary breaker
# either, so ANTHROPIC_API_KEY=... is a labelled value too.
LABEL_START = r"(?:(?<![A-Za-z0-9])|(?<=\\n)|(?<=\\r)|(?<=\\t))"
SCRUB_PATTERNS = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_style_key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("runpod_key", re.compile(r"(?<![A-Za-z0-9])rpa_[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")),
    ("telegram_bot_token", re.compile(r"(?<![0-9])[0-9]{8,10}:AA[A-Za-z0-9_\-]{30,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("slack_token", re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("google_api_key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_\-]{35}")),
    ("huggingface_token", re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{30,}")),
    ("groq_key", re.compile(r"(?<![A-Za-z0-9])gsk_[A-Za-z0-9]{30,}")),
    ("openrouter_key", re.compile(r"(?<![A-Za-z0-9])sk-or-v1-[A-Za-z0-9]{40,}")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{32,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    # class patterns (shape-based, not vendor-prefix based).  The scrubber runs on the *serialized JSON line*, so a
    # newline inside a string is the two characters backslash+n; connectors treat "\n" / "\r" / "\t" escapes as
    # atomic units and never consume a bare backslash (that would corrupt the JSON escaping).
    #  - a long opaque value (no dots, so file names never qualify) directly after a secret-ish label:
    #    "api key = XXXX", "password: XXXX", "== relay.secret\nXXXX"
    #    Connectors consume JSON escapes as units ("\\." = backslash + escaped char) so nested JSON (\"token\": \"X\")
    #    and escaped newlines are crossed without ever splitting an escape.
    ("labeled_secret", re.compile(r"(?i)" + LABEL_START + r"(token|api[_ \-]?key|apikey|secret|password|passwd|credential)\b(?:\\.|[^A-Za-z0-9\\\n]){0,8}(?:is|=|:)?(?:\\.|[^A-Za-z0-9\\\n]){0,4}(?!\*\*\*|\[REDACTED)(?![0-9a-f]{8}-[0-9a-f]{4}-)([A-Za-z0-9_\-]{32,})(?![A-Za-z0-9_\-\.])")),
    #  - a 40-64 char base64url blob within ~120 chars after the word "token" in the same (possibly nested) string
    #    ("Review token: `XXXX`", "(token (email delivery flaky): **XXXX**", "token to view your review later:\nXXXX")
    ("token_nearby_opaque", re.compile(r"(?i)" + LABEL_START + r"token\b(?:\\.|[^\"\\\n]){0,120}?(?:(?<![A-Za-z0-9_\-\\])|(?<=\\n)|(?<=\\r)|(?<=\\t))(?![0-9a-f]{8}-[0-9a-f]{4}-)(?!\[REDACTED)([A-Za-z0-9_\-]{40,64})(?![A-Za-z0-9_\-])")),
    #  - OAuth / PKCE / session values carried as URL query parameters in browser tool output, plain or %-encoded
    #    (the value class admits '/' and '+': Google authorization codes start with "4/", and base64 state blobs
    #    carry '+' when the browser shows them unencoded)
    ("oauth_url_param", re.compile(r"(?i)(?:\b|%26|%2526)(code_challenge|code_verifier|access_token|refresh_token|id_token|client_secret|state|code|session_state|sessionid|auth_token|part|rart)(?:=|%3D|%253D)((?!\[REDACTED)[A-Za-z0-9_\-\.%/+]{20,})")),
    #  - any URL query value that is a long opaque blob (>=100 chars, no spaces): consent/continue blobs, signed state
    ("long_url_param_value", re.compile(r"(?i)[?&](?:%26|%2526)?[A-Za-z0-9_\-\.]{1,40}(?:=|%3D|%253D)((?!\[REDACTED)[A-Za-z0-9_\-\.%]{100,})")),
]
# for the class patterns only the value group is replaced; the label / parameter name is kept
VALUE_GROUP_PATTERNS = {"labeled_secret": 2, "token_nearby_opaque": 1, "oauth_url_param": 2, "long_url_param_value": 1}


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def iso_from_ms(ms):
    if ms is None:
        return None
    try:
        return _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except Exception:
        return None


def ms_from_iso(s):
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        return int(_dt.datetime.fromisoformat(s2).timestamp() * 1000)
    except Exception:
        return None


def normalize_iso(s):
    """Canonical UTC form 'YYYY-MM-DDTHH:MM:SSZ' (or with .mmm) so string comparison against record
    timestamps is chronological.  Accepts 'Z' or an explicit offset; a date-only value means midnight UTC."""
    if not s:
        return None
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        s += "T00:00:00Z"
    d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    d = d.astimezone(_dt.timezone.utc)
    if d.microsecond:
        return d.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return d.isoformat(timespec="seconds").replace("+00:00", "Z")


def kind_of(session_key):
    if not session_key:
        return "unknown"
    if session_key == "agent:main:main":
        return "main"
    if session_key.startswith("agent:main:subagent:"):
        return "subagent"
    if session_key.startswith("agent:main:cron:"):
        return "cron"
    if session_key.startswith("agent:main:telegram:"):
        return "telegram"
    return "other"


def cron_job_key(session_key):
    m = re.match(r"(agent:main:cron:[0-9a-f\-]{36}):run:\d+$", session_key or "")
    return m.group(1) if m else None


def text_of(content):
    """Flatten a message content field (str or list of blocks) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
            parts.append(b["text"])
    return "\n".join(parts)


def summarize_blocks(content):
    """Return tool-result content with image payloads replaced by their size."""
    if not isinstance(content, list):
        return content
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "image":
            d = b.get("data")
            out.append({"type": "image", "mimeType": b.get("mimeType"), "bytes": len(d) if isinstance(d, str) else None})
        else:
            out.append(b)
    return out


def classify_error(msg, diagnostics):
    """Map an errorMessage / diagnostics pair to (errorType, refusalCategory).

    Matches the class of each failure (an authentication_error body, a provider refusal
    diagnostic, ...) rather than one observed message string."""
    cat = None
    for d in diagnostics or []:
        if isinstance(d, dict) and d.get("type") == "provider_refusal":
            cat = (d.get("details") or {}).get("category")
    m = msg or ""
    if "authentication_error" in m or "API key is invalid" in m or "invalid x-api-key" in m or "permission_error" in m:
        return "auth", cat
    if m.startswith("Anthropic refusal") or cat:
        if not cat:
            mm = re.search(r"category:\s*([a-z_\-]+)", m)
            cat = mm.group(1) if mm else "unknown"
        return "refusal", cat
    if "aborted" in m.lower():
        return "aborted", None
    if "timed out" in m.lower() or "timeout" in m.lower():
        return "timeout", None
    if "rate_limit" in m or "overloaded" in m:
        return "rate_limit", None
    if m:
        return "other", None
    return None, None


def stable_hash(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def read_jsonl(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def zero_usage():
    return {k: 0 for k in USAGE_FIELDS}


def zero_cost():
    return {k: 0.0 for k in COST_FIELDS}


def add_usage(acc, u):
    if not u:
        return
    for k in USAGE_FIELDS:
        v = u.get(k)
        if k == "totalTokens" and v is None:
            v = u.get("total")          # trajectory run-level usage uses 'total'
        acc[k] += int(v or 0)


def add_cost(acc, c):
    if not c:
        return
    for k in COST_FIELDS:
        acc[k] += float(c.get(k) or 0.0)


def sig_model(sig):
    """The thinking signature is a server-signed blob that embeds the serving model's id."""
    if not sig or not isinstance(sig, str):
        return None
    try:
        import base64
        raw = base64.b64decode(sig + "=" * (-len(sig) % 4))
    except Exception:
        return None
    # The id is immediately followed by a protobuf tag byte that is often an ASCII digit ('8'), e.g. the bytes read
    # 'claude-fable-58' / 'claude-opus-4-88'.  Model ids in this family use single-digit version components, so match
    # exactly one digit per component.
    m = re.search(rb"claude-[a-z]+-\d(?:-\d)?", raw)
    return m.group(0).decode() if m else None


def load_rates_file(path):
    """utils/rates.example.json format: {"unit": "usd_per_mtok", "rates": {model: {input, output, cacheRead,
    cacheWrite}}}.  A bare {model: {...}} mapping is accepted too.  Returns {model: {field: usd_per_token}}."""
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    unit = "usd_per_mtok"
    table = doc
    if isinstance(doc, dict) and isinstance(doc.get("rates"), dict):
        table = doc["rates"]
        unit = doc.get("unit") or unit
    scale = {"usd_per_mtok": 1e-6, "usd_per_token": 1.0}.get(unit)
    if scale is None:
        raise ValueError("rates file: unknown unit %r (expected usd_per_mtok or usd_per_token)" % unit)
    out = {}
    for model, row in table.items():
        if model.startswith("_") or not isinstance(row, dict):
            continue
        out[model] = {f: float(row.get(f) or 0.0) * scale for f in RATE_FIELDS}
    return out


def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


# ----------------------------------------------------------------------------
# the extractor
# ----------------------------------------------------------------------------
class Extractor:
    def __init__(self, sessions_dir, snapshots_dir=None, rates=None, auth_revoked_at=None):
        self.dir = sessions_dir
        self.snapshots_dir = snapshots_dir
        self.rates_file = dict(rates or {})   # model -> {field: usd/token}; fills models absent from the store
        self.auth_revoked_at = normalize_iso(auth_revoked_at) if auth_revoked_at else None
        self.registry = {}                 # sessionKey -> meta
        self.key_by_sid = {}               # sessionId -> sessionKey
        self.key_source = {}               # sessionId -> where the key came from
        self.session_files = {}            # sessionId -> path (.jsonl, .jsonl.<suffix>, or a snapshot copy)
        self.session_file_kind = {}        # sessionId -> 'live' | 'deleted' | 'archived' | 'snapshot'
        self.empty_transcripts = {}        # sessionId -> path of a transcript that held no message records
        self.traj_files = {}               # sessionId -> path (live, or the largest snapshot copy)
        self.traj_extra = collections.defaultdict(list)   # sessionId -> other trajectory copies to union
        self.snapshot_files_seen = 0
        self.runs = collections.defaultdict(list)   # sessionId -> [run dict]
        self.events = []
        self.dups = collections.Counter()
        self.seen_ids = set()
        self.seen_rids = set()
        self.seen_mirror = set()
        self.child_parent = {}             # child sessionKey -> {parentSessionKey, ts, toolCallId, taskName}
        self.job_mentions = collections.defaultdict(list)   # cronJobId -> [(ts_ms, sessionKey)]
        self.notes = []                    # data-quality notes (strings; never content)
        self.rates = {}                    # model -> {field: usd/token}
        self.rate_source = {}              # model -> 'store' | 'rates-file'
        self.rate_evidence = {}
        self.unpriced = collections.Counter()   # model -> runs whose usage could not be priced
        self.session_info = {}             # sessionId -> dict (model, thinkingLevel, cwd, first/last ts, label...)

    # ------------------------------------------------------------------ discovery
    def _classify_transcript_name(self, base):
        """Return (sid, kind) for a transcript file name, or (None, None)."""
        m = TRANSCRIPT_RE.match(base)
        if not m:
            return None, None
        sid, suffix = m.group(1), m.group(2)
        if suffix is None:
            return sid, "live"
        if suffix == "lock" or suffix.endswith(".lock"):
            return None, None
        if suffix.startswith("deleted."):
            return sid, "deleted"
        return sid, "archived"

    def discover(self):
        reg_path = os.path.join(self.dir, "sessions.json")
        if os.path.exists(reg_path):
            with open(reg_path) as fh:
                self.registry = json.load(fh)
        for key, meta in self.registry.items():
            sid = meta.get("sessionId")
            if sid:
                self.key_by_sid[sid] = key
                self.key_source[sid] = "registry"
        # a session reset (thinking watchdog) keeps the previous generation's id under the same key
        for key, meta in self.registry.items():
            for old in meta.get("usageFamilySessionIds") or []:
                if old and old not in self.key_by_sid:
                    self.key_by_sid[old] = key
                    self.key_source[old] = "registry.usageFamily"
        # live transcripts win; among archived copies of one sid the largest wins
        archived = {}                       # sid -> (size, path, kind)
        for p in glob.glob(os.path.join(self.dir, "*.jsonl*")):
            base = os.path.basename(p)
            tm = TRAJECTORY_RE.match(base)
            if tm:
                if tm.group(2) is None:
                    self.traj_files[tm.group(1)] = p
                continue
            sid, kind = self._classify_transcript_name(base)
            if not sid:
                continue
            if kind == "live":
                self.session_files[sid] = p
                self.session_file_kind[sid] = "live"
            else:
                cand = (file_size(p), p, kind)
                if sid not in archived or cand[0] > archived[sid][0]:
                    archived[sid] = cand
        # snapshot copies (any depth): <sid>.jsonl or <sid>.jsonl.<suffix>; largest per sid
        snaps = {}                          # sid -> (size, path)
        traj_snaps = collections.defaultdict(list)
        if self.snapshots_dir and os.path.isdir(self.snapshots_dir):
            for root, _dirs, files in os.walk(self.snapshots_dir):
                for base in files:
                    p = os.path.join(root, base)
                    tm = TRAJECTORY_RE.match(base)
                    if tm:
                        traj_snaps[tm.group(1)].append((file_size(p), p))
                        continue
                    sid, kind = self._classify_transcript_name(base)
                    if not sid:
                        continue
                    self.snapshot_files_seen += 1
                    cand = (file_size(p), p)
                    if sid not in snaps or cand[0] > snaps[sid][0]:
                        snaps[sid] = cand
        for sid, (size, p, kind) in archived.items():
            if sid in self.session_files:
                continue
            self.session_files[sid] = p
            self.session_file_kind[sid] = kind
        for sid, (size, p) in snaps.items():
            if self.session_file_kind.get(sid) == "live":
                live_size = file_size(self.session_files[sid])
                if size > live_size:
                    self.notes.append("session %s: live transcript (%d B) is smaller than a snapshot copy (%d B); "
                                      "the live file was used" % (sid, live_size, size))
                continue
            if sid in self.session_files and file_size(self.session_files[sid]) >= size:
                continue
            self.session_files[sid] = p
            self.session_file_kind[sid] = "snapshot"
        for sid, cands in traj_snaps.items():
            cands.sort(reverse=True)
            if sid not in self.traj_files:
                self.traj_files[sid] = cands[0][1]
                self.traj_extra[sid] = [p for _s, p in cands[1:]]
            else:
                self.traj_extra[sid] = [p for _s, p in cands]

    # ------------------------------------------------------------------ trajectories
    def _trajectory_records(self, sid):
        """Records of the live trajectory, unioned with any snapshot copies by sourceSeq/seq."""
        primary = self.traj_files[sid]
        extra = self.traj_extra.get(sid) or []
        if not extra:
            for e in read_jsonl(primary):
                yield e
            return
        merged = {}
        order = []
        unkeyed = []
        for path in [primary] + extra:
            for e in read_jsonl(path):
                k = e.get("sourceSeq")
                if k is None:
                    k = e.get("seq")
                if k is None:
                    unkeyed.append(e)
                    continue
                if k not in merged:
                    merged[k] = e
                    order.append(k)
        if len(order) > sum(1 for _ in read_jsonl(primary)):
            self.notes.append("trajectory %s: %d records recovered from snapshot copies beyond the live file"
                              % (sid, len(order) - sum(1 for _ in read_jsonl(primary))))
        for k in sorted(order):
            yield merged[k]
        for e in unkeyed:
            yield e

    def load_trajectories(self):
        for sid in sorted(self.traj_files):
            run = None
            seq_min = None
            for e in self._trajectory_records(sid):
                if e.get("traceSchema") != "openclaw-trajectory":
                    continue
                skey = e.get("sessionKey")
                if skey and sid not in self.key_by_sid:
                    self.key_by_sid[sid] = skey
                    self.key_source[sid] = "trajectory"
                seq = e.get("seq")
                if seq_min is None or (seq is not None and seq < seq_min):
                    seq_min = seq
                t = e.get("type")
                d = e.get("data") or {}
                rid = e.get("runId")
                if t == "session.started" or run is None or (rid and run.get("runId") != rid):
                    run = {"runId": rid, "sessionKey": skey, "sessionId": sid, "startTs": e.get("ts"),
                           "endTs": None, "status": None, "trigger": d.get("trigger") if t == "session.started" else None,
                           "messageProvider": d.get("messageProvider") if t == "session.started" else None,
                           "provider": e.get("provider"), "modelId": e.get("modelId"),
                           "usage": None, "promptCache": None, "snapshot": None, "snapshotDropped": False,
                           "assistantTexts": None, "toolMetas": None, "terminalError": None, "promptError": None,
                           "successfulCronAdds": None, "harness": None, "headTruncated": False,
                           "finalPromptText": None, "prompt": None}
                    self.runs[sid].append(run)
                if t == "session.started":
                    run["trigger"] = d.get("trigger")
                    run["messageProvider"] = d.get("messageProvider")
                elif t == "trace.metadata":
                    h = d.get("harness") or {}
                    run["harness"] = {"version": h.get("version"), "gitSha": h.get("gitSha")}
                elif t == "prompt.submitted":
                    run["prompt"] = d.get("prompt")
                elif t == "model.completed":
                    run["usage"] = d.get("usage")
                    run["promptCache"] = d.get("promptCache")
                    run["terminalError"] = d.get("terminalError")
                    run["promptError"] = d.get("promptError")
                    run["assistantTexts"] = d.get("assistantTexts")
                    run["finalPromptText"] = d.get("finalPromptText")
                    ms = d.get("messagesSnapshot")
                    if d.get("truncated") and "messagesSnapshot" in (d.get("droppedFields") or []):
                        run["snapshotDropped"] = True
                    elif isinstance(ms, list):
                        run["snapshot"] = ms
                elif t == "trace.artifacts":
                    run["toolMetas"] = d.get("toolMetas")
                    run["successfulCronAdds"] = d.get("successfulCronAdds")
                    run["itemLifecycle"] = d.get("itemLifecycle")
                    if run["terminalError"] is None:
                        run["terminalError"] = d.get("terminalError")
                    if run["promptError"] is None:
                        run["promptError"] = d.get("promptError")
                    if run["usage"] is None:
                        run["usage"] = d.get("usage")
                elif t == "session.ended":
                    run["endTs"] = e.get("ts")
                    run["status"] = d.get("status")
            if self.runs.get(sid) and seq_min not in (None, 1):
                self.runs[sid][0]["headTruncated"] = True

    # ------------------------------------------------------------------ pricing
    def calibrate_rates(self):
        """Derive USD/token per model per field from records that carry both usage and cost (OpenClaw's own
        pricing table, as charged).  Models with no priced records fall back to the --rates file."""
        samples = collections.defaultdict(lambda: collections.defaultdict(list))
        for sid, path in self.session_files.items():
            for r in read_jsonl(path):
                if r.get("type") != "message":
                    continue
                m = r.get("message") or {}
                if m.get("role") != "assistant":
                    continue
                u = m.get("usage") or {}
                c = u.get("cost") or {}
                # OpenClaw prices at the *requested* model's rate unless its own provider_fallback
                # diagnostic fired (then at the fallback model's rate).  Key the calibration the same way.
                fb = any(isinstance(d, dict) and d.get("type") == "provider_fallback" for d in (m.get("diagnostics") or []))
                model = (m.get("responseModel") if fb else None) or m.get("model")
                if not model or not c:
                    continue
                for f in RATE_FIELDS:
                    tok = u.get(f) or 0
                    if tok >= 100:
                        samples[model][f].append(float(c.get(f) or 0.0) / tok)
        for model, per in samples.items():
            self.rates[model] = {}
            self.rate_source[model] = "store"
            self.rate_evidence[model] = {}
            for f, vals in per.items():
                self.rates[model][f] = statistics.median(vals)
                self.rate_evidence[model][f] = {"n": len(vals), "usd_per_mtok": round(statistics.median(vals) * 1e6, 4)}
        for model, r in self.rates_file.items():
            if model not in self.rates:
                self.rates[model] = dict(r)
                self.rate_source[model] = "rates-file"

    def estimate_cost(self, usage, model, cache_write_multiplier=None):
        """Price a usage dict at `model`'s rates, or None when the model is unpriced.  cache_write_multiplier
        overrides the cacheWrite rate as a multiple of the input rate (1.25 = 5-minute TTL, 2.0 = 1-hour TTL)."""
        r = self.rates.get(model)
        if not r or not usage:
            return None
        c = zero_cost()
        for f in RATE_FIELDS:
            rate = r.get(f, 0.0)
            if f == "cacheWrite" and cache_write_multiplier is not None:
                rate = r.get("input", 0.0) * cache_write_multiplier
            c[f] = float(usage.get(f) or 0) * rate
        c["total"] = c["input"] + c["output"] + c["cacheRead"] + c["cacheWrite"]
        return c

    # ------------------------------------------------------------------ linkage
    def note_spawn_result(self, text, parent_key, ts_ms, tool_call_id, details=None):
        key = None
        task = None
        if isinstance(details, dict) and details.get("childSessionKey"):
            key = details.get("childSessionKey")
            task = details.get("taskName")
        if not key and text:
            m = re.search(r'"childSessionKey"\s*:\s*"([^"]+)"', text)
            if m:
                key = m.group(1)
            mt = re.search(r'"taskName"\s*:\s*"([^"]*)"', text)
            task = mt.group(1) if mt else task
        if key and key not in self.child_parent:
            self.child_parent[key] = {"parentSessionKey": parent_key, "ts": iso_from_ms(ts_ms), "toolCallId": tool_call_id, "taskName": task}

    def note_job_mentions(self, text, session_key, ts_ms):
        if not text:
            return
        for jid in re.findall(SID_RE, text):
            self.job_mentions[jid].append((ts_ms or 0, session_key))

    def parent_of(self, session_key, sid):
        """Return (parentSessionKey, spawnerSessionKey, linkSource, label)."""
        kind = kind_of(session_key)
        meta = self.registry.get(session_key) or {}
        if kind == "subagent":
            if meta.get("spawnedBy"):
                return meta["spawnedBy"], meta["spawnedBy"], "registry.spawnedBy", meta.get("label")
            cp = self.child_parent.get(session_key)
            if cp:
                return cp["parentSessionKey"], cp["parentSessionKey"], "sessions_spawn.result", cp.get("taskName")
            return None, None, "unresolved", meta.get("label")
        if kind == "cron":
            job = cron_job_key(session_key)
            jid = job.split(":")[-1] if job else None
            run_start = None
            for r in self.runs.get(sid, []):
                run_start = ms_from_iso(r.get("startTs"))
                break
            creator = None
            if jid and self.job_mentions.get(jid):
                cands = sorted(self.job_mentions[jid])
                for ts, k in cands:
                    if k and k != session_key and (run_start is None or ts <= run_start):
                        creator = k
                        break
            return job, creator, "cron.jobKey(+earliest job-id mention)" if creator else "cron.jobKey", meta.get("label")
        return None, None, "none", meta.get("label")

    # ------------------------------------------------------------------ event emit
    def emit(self, ev):
        self.events.append(ev)

    def base_event(self, event, ts, sid, skey, source, **extra):
        ev = {"ts": ts, "event": event, "sessionKey": skey, "sessionId": sid, "kind": kind_of(skey),
              "parentSessionKey": None, "spawnerSessionKey": None, "turnIndex": None, "model": None,
              "provider": None, "stopReason": None, "usage": None, "cost": None, "responseId": None,
              "source": source}
        ev.update(extra)
        return ev

    def after_revocation(self, ts):
        if not self.auth_revoked_at:
            return None
        return bool(ts and ts >= self.auth_revoked_at)

    # ------------------------------------------------------------------ session files
    def process_session_file(self, sid, path):
        skey = self.key_by_sid.get(sid)
        source = SOURCE_LABEL.get(self.session_file_kind.get(sid), "session")
        info = self.session_info.setdefault(sid, {"sessionKey": skey, "source": source, "model": None, "thinkingLevel": None,
                                                  "cwd": None, "firstTs": None, "lastTs": None, "records": 0})
        turn = 0
        last_tool_ts_ms = None
        call_map = {}          # toolCallId -> (name, turnIndex)
        started_emitted = False
        pending_start = {}
        for r in read_jsonl(path):
            info["records"] += 1
            t = r.get("type")
            ts = r.get("timestamp")
            ts_ms = ms_from_iso(ts)
            if ts:
                info["firstTs"] = info["firstTs"] or ts
                info["lastTs"] = ts
            rid = r.get("id")
            if rid is not None:
                if rid in self.seen_ids:
                    self.dups["record_id"] += 1
                    continue
                self.seen_ids.add(rid)
            if t == "session":
                pending_start = {"ts": ts, "cwd": r.get("cwd"), "version": r.get("version")}
                info["cwd"] = r.get("cwd")
                continue
            if t == "model_change":
                if not started_emitted:
                    pending_start["model"] = r.get("modelId")
                    pending_start["provider"] = r.get("provider")
                    info["model"] = r.get("modelId")
                else:
                    self.emit(self.base_event("system", ts, sid, skey, source, systemType="model_change",
                                              model=r.get("modelId"), provider=r.get("provider"), recordId=rid, turnIndex=turn))
                continue
            if t == "thinking_level_change":
                if not started_emitted:
                    pending_start["thinkingLevel"] = r.get("thinkingLevel")
                    info["thinkingLevel"] = r.get("thinkingLevel")
                else:
                    self.emit(self.base_event("system", ts, sid, skey, source, systemType="thinking_level_change",
                                              thinkingLevel=r.get("thinkingLevel"), recordId=rid, turnIndex=turn))
                continue
            if t == "custom" and r.get("customType") == "model-snapshot":
                if not started_emitted:
                    d = r.get("data") or {}
                    pending_start.setdefault("model", d.get("modelId"))
                    pending_start.setdefault("provider", d.get("provider"))
                    pending_start["api"] = d.get("modelApi")
                continue
            if not started_emitted:
                self.emit(self.base_event("session.start", pending_start.get("ts") or ts, sid, skey, source,
                                          model=pending_start.get("model"), provider=pending_start.get("provider"),
                                          api=pending_start.get("api"), thinkingLevel=pending_start.get("thinkingLevel"),
                                          cwd=pending_start.get("cwd"), transcriptVersion=pending_start.get("version"),
                                          transcriptFile=os.path.basename(path), turnIndex=0))
                started_emitted = True
            if t == "leaf":
                self.dups["leaf_records_skipped"] += 1
                continue
            if t == "custom" and r.get("customType") == "openclaw:prompt-error":
                d = r.get("data") or {}
                self.emit(self.base_event("error", ts, sid, skey, source, errorType="prompt-error",
                                          errorMessage=d.get("error"), runId=d.get("runId"), model=d.get("model"),
                                          provider=d.get("provider"), recordId=rid, turnIndex=turn))
                continue
            if t == "custom_message":
                self.emit(self.base_event("system", ts, sid, skey, source, systemType=r.get("customType"),
                                          text=r.get("content"), details=r.get("details"), recordId=rid, turnIndex=turn))
                continue
            if t != "message":
                self.dups["unknown_record_type:" + str(t)] += 1
                continue
            m = r.get("message") or {}
            role = m.get("role")
            mts = m.get("timestamp")
            if role == "user":
                turn_for_user = turn + 1
                txt = text_of(m.get("content"))
                prov = m.get("provenance")
                self.note_job_mentions(txt, skey, ts_ms)
                self.emit(self.base_event("user", ts, sid, skey, source, turnIndex=turn_for_user, text=txt,
                                          provenance=prov, senderIsOwner=(m.get("__openclaw") or {}).get("senderIsOwner"),
                                          recordId=rid, parentRecordId=r.get("parentId"), messageTs=iso_from_ms(mts)))
                if prov and prov.get("sourceSessionKey"):
                    # an inter-session completion event: child -> this session
                    ck = prov.get("sourceSessionKey")
                    if ck and ck not in self.child_parent and kind_of(ck) == "subagent":
                        self.child_parent[ck] = {"parentSessionKey": skey, "ts": ts, "toolCallId": None, "taskName": None}
                continue
            if role == "assistant":
                resp = m.get("responseId")
                mirror_key = m.get("idempotencyKey")
                if resp:
                    if resp in self.seen_rids:
                        self.dups["duplicate_responseId"] += 1
                        continue
                    self.seen_rids.add(resp)
                elif mirror_key:
                    if mirror_key in self.seen_mirror:
                        self.dups["duplicate_idempotencyKey"] += 1
                        continue
                    self.seen_mirror.add(mirror_key)
                turn += 1
                content = m.get("content") if isinstance(m.get("content"), list) else []
                thinking = [{"text": b.get("thinking"), "signature": b.get("thinkingSignature")} for b in content if b.get("type") == "thinking"]
                tool_calls = [{"id": b.get("id"), "name": b.get("name"), "params": b.get("arguments")} for b in content if b.get("type") == "toolCall"]
                for tc in tool_calls:
                    call_map[tc["id"]] = (tc["name"], turn)
                u = m.get("usage") or {}
                cost = u.get("cost")
                usage = {k: u.get(k) for k in USAGE_FIELDS}
                err_type, ref_cat = classify_error(m.get("errorMessage"), m.get("diagnostics"))
                fallback = None
                for d in m.get("diagnostics") or []:
                    if isinstance(d, dict) and d.get("type") == "provider_fallback":
                        fallback = d.get("details")
                sigm = next((sig_model(t.get("signature")) for t in thinking if t.get("signature")), None)
                ev = self.base_event("assistant", ts, sid, skey, source, turnIndex=turn, model=m.get("responseModel") or m.get("model"),
                                     requestedModel=m.get("model"), servedModelFromSignature=sigm,
                                     pricedAsModel=(m.get("responseModel") if fallback else None) or m.get("model"),
                                     provider=m.get("provider"), api=m.get("api"),
                                     stopReason=m.get("stopReason"), usage=usage, cost=cost, responseId=resp,
                                     text=text_of(content), thinking=thinking, toolCalls=tool_calls,
                                     contextUsage=u.get("contextUsage"), startedAt=iso_from_ms(mts),
                                     latencyMs=(ts_ms - mts) if (ts_ms and mts) else None,
                                     errorMessage=m.get("errorMessage"), errorCode=m.get("errorCode"), errorType=err_type,
                                     refusalCategory=ref_cat, providerFallback=fallback, idempotencyKey=mirror_key,
                                     recordId=rid, parentRecordId=r.get("parentId"))
                self.emit(ev)
                last_tool_ts_ms = ts_ms
                if err_type or m.get("stopReason") in ("error", "aborted"):
                    self.emit(self.base_event("error", ts, sid, skey, source, turnIndex=turn, errorType=err_type or m.get("stopReason"),
                                              refusalCategory=ref_cat, errorMessage=m.get("errorMessage"), errorCode=m.get("errorCode"),
                                              stopReason=m.get("stopReason"), responseId=resp, model=ev["model"], provider=ev["provider"],
                                              diagnostics=m.get("diagnostics"), recordId=rid, afterKeyRevocation=self.after_revocation(ts)))
                continue
            if role == "toolResult":
                cid = m.get("toolCallId")
                name = m.get("toolName")
                tinfo = call_map.get(cid)
                turn_idx = tinfo[1] if tinfo else turn
                details = m.get("details")
                txt = text_of(m.get("content"))
                dur = None
                dur_src = None
                if isinstance(details, dict) and isinstance(details.get("durationMs"), (int, float)):
                    dur = details["durationMs"]
                    dur_src = "details.durationMs"
                elif mts and last_tool_ts_ms:
                    dur = mts - last_tool_ts_ms
                    dur_src = "approx(resultTs - previousEventTs)"
                if name == "sessions_spawn":
                    self.note_spawn_result(txt, skey, mts or ts_ms, cid, details)
                self.note_job_mentions(txt, skey, ts_ms)
                if isinstance(details, dict):
                    self.note_job_mentions(json.dumps(details), skey, ts_ms)
                self.emit(self.base_event("tool_result", ts, sid, skey, source, turnIndex=turn_idx, toolCallId=cid, toolName=name,
                                          isError=m.get("isError"), text=txt, content=summarize_blocks(m.get("content")),
                                          details=details, durationMs=dur, durationSource=dur_src, resultTs=iso_from_ms(mts),
                                          recordId=rid, parentRecordId=r.get("parentId")))
                if mts:
                    last_tool_ts_ms = mts
                continue
            self.dups["unknown_role:" + str(role)] += 1
        if started_emitted:
            self.emit(self.base_event("session.end", info["lastTs"], sid, skey, source, turnIndex=turn, endReason="transcript-end",
                                      transcriptFile=os.path.basename(path)))
        info["turns"] = turn
        return started_emitted

    # ------------------------------------------------------------------ trajectory-only sessions
    def process_trajectory_only(self, sid):
        runs = self.runs.get(sid) or []
        skey = self.key_by_sid.get(sid)
        source = "trajectory"
        info = self.session_info.setdefault(sid, {"sessionKey": skey, "source": source, "model": None, "thinkingLevel": None,
                                                  "cwd": None, "firstTs": None, "lastTs": None, "records": 0})
        if not runs:
            return
        info["model"] = runs[0].get("modelId")
        info["firstTs"] = runs[0].get("startTs")
        info["lastTs"] = runs[-1].get("endTs") or runs[-1].get("startTs")
        self.emit(self.base_event("session.start", runs[0]["startTs"], sid, skey, source, model=runs[0].get("modelId"),
                                  provider=runs[0].get("provider"), trigger=runs[0].get("trigger"), turnIndex=0,
                                  note="reconstructed from trajectory (transcript file deleted)"))
        turn = 0
        for run in runs:
            snap = run.get("snapshot")
            call_map = {}
            last_ts = ms_from_iso(run.get("startTs"))
            if isinstance(snap, list):
                for m in snap:
                    role = m.get("role")
                    mts = m.get("timestamp")
                    ts = iso_from_ms(mts) or run.get("startTs")
                    if role == "user":
                        txt = text_of(m.get("content"))
                        self.note_job_mentions(txt, skey, mts)
                        self.emit(self.base_event("user", ts, sid, skey, source, turnIndex=turn + 1, text=txt, runId=run["runId"],
                                                  provenance=m.get("provenance"), messageTs=ts, tsSource="message.timestamp"))
                    elif role == "assistant":
                        resp = m.get("responseId")
                        key = resp or ("snap:" + stable_hash([sid, mts, m.get("content")]))
                        if key in self.seen_rids:
                            self.dups["duplicate_responseId"] += 1
                            continue
                        self.seen_rids.add(key)
                        turn += 1
                        content = m.get("content") if isinstance(m.get("content"), list) else []
                        thinking = [{"text": b.get("thinking"), "signature": b.get("thinkingSignature")} for b in content if b.get("type") == "thinking"]
                        tool_calls = [{"id": b.get("id"), "name": b.get("name"), "params": b.get("arguments")} for b in content if b.get("type") == "toolCall"]
                        for tc in tool_calls:
                            call_map[tc["id"]] = (tc["name"], turn)
                        u = m.get("usage") or {}
                        err_type, ref_cat = classify_error(m.get("errorMessage"), m.get("diagnostics"))
                        fb_snap = any(isinstance(d, dict) and d.get("type") == "provider_fallback" for d in (m.get("diagnostics") or []))
                        sigm = next((sig_model(t.get("signature")) for t in thinking if t.get("signature")), None)
                        ev = self.base_event("assistant", ts, sid, skey, source, turnIndex=turn, model=m.get("responseModel") or m.get("model"),
                                             requestedModel=m.get("model"), servedModelFromSignature=sigm,
                                             pricedAsModel=(m.get("responseModel") if fb_snap else None) or m.get("model"),
                                             provider=m.get("provider"), api=m.get("api"),
                                             stopReason=m.get("stopReason"), usage={k: u.get(k) for k in USAGE_FIELDS}, cost=u.get("cost"),
                                             responseId=resp, text=text_of(content), thinking=thinking, toolCalls=tool_calls,
                                             contextUsage=u.get("contextUsage"), startedAt=ts, latencyMs=None, errorMessage=m.get("errorMessage"),
                                             errorCode=m.get("errorCode"), errorType=err_type, refusalCategory=ref_cat, runId=run["runId"],
                                             tsSource="message.timestamp")
                        self.emit(ev)
                        last_ts = mts or last_ts
                        if err_type or m.get("stopReason") in ("error", "aborted"):
                            self.emit(self.base_event("error", ts, sid, skey, source, turnIndex=turn, errorType=err_type or m.get("stopReason"),
                                                      refusalCategory=ref_cat, errorMessage=m.get("errorMessage"), stopReason=m.get("stopReason"),
                                                      responseId=resp, model=ev["model"], provider=ev["provider"], runId=run["runId"],
                                                      afterKeyRevocation=self.after_revocation(ts)))
                    elif role == "toolResult":
                        cid = m.get("toolCallId")
                        name = m.get("toolName")
                        tinfo = call_map.get(cid)
                        details = m.get("details")
                        txt = text_of(m.get("content"))
                        dur = None
                        dur_src = None
                        if isinstance(details, dict) and isinstance(details.get("durationMs"), (int, float)):
                            dur, dur_src = details["durationMs"], "details.durationMs"
                        elif mts and last_ts:
                            dur, dur_src = mts - last_ts, "approx(resultTs - previousEventTs)"
                        if name == "sessions_spawn":
                            self.note_spawn_result(txt, skey, mts, cid, details)
                        self.note_job_mentions(txt, skey, mts)
                        if isinstance(details, dict):
                            self.note_job_mentions(json.dumps(details), skey, mts)
                        self.emit(self.base_event("tool_result", ts, sid, skey, source, turnIndex=(tinfo[1] if tinfo else turn), toolCallId=cid,
                                                  toolName=name, isError=m.get("isError"), text=txt, content=summarize_blocks(m.get("content")),
                                                  details=details, durationMs=dur, durationSource=dur_src, resultTs=ts, runId=run["runId"],
                                                  tsSource="message.timestamp"))
                        last_ts = mts or last_ts
                    else:
                        self.dups["snapshot_unknown_role:" + str(role)] += 1
            # tool metas (names only) when snapshot is missing or truncated
            metas = run.get("toolMetas") or []
            snap_tool_results = sum(1 for m in (snap or []) if m.get("role") == "toolResult") if isinstance(snap, list) else 0
            if metas and (not isinstance(snap, list) or len(metas) > snap_tool_results):
                for i, tm in enumerate(metas):
                    if isinstance(snap, list) and i < snap_tool_results:
                        continue
                    self.emit(self.base_event("tool_meta", run.get("endTs") or run.get("startTs"), sid, skey, source, turnIndex=turn,
                                              toolName=(tm or {}).get("toolName"), meta=(tm or {}).get("meta"), runId=run["runId"],
                                              note="tool call known only from trace.artifacts.toolMetas (content lost)"))
        self.emit(self.base_event("session.end", info["lastTs"], sid, skey, source, turnIndex=turn, endReason="trajectory-end"))
        info["turns"] = turn

    # ------------------------------------------------------------------ run boundaries (all sessions)
    def emit_runs(self):
        for sid, runs in self.runs.items():
            skey = self.key_by_sid.get(sid)
            source = "trajectory"
            has_transcript = sid in self.session_files
            for run in runs:
                snap = run.get("snapshot")
                snap_len = len(snap) if isinstance(snap, list) else None
                est = self.estimate_cost(run["usage"], run.get("modelId")) if run.get("usage") else None
                if run.get("usage") and est is None and not has_transcript:
                    self.unpriced[str(run.get("modelId"))] += 1
                self.emit(self.base_event("run.start", run["startTs"], sid, skey, source, runId=run["runId"], trigger=run.get("trigger"),
                                          messageProvider=run.get("messageProvider"), model=run.get("modelId"), provider=run.get("provider"),
                                          prompt=run.get("prompt"), harness=run.get("harness"), headTruncated=run.get("headTruncated")))
                self.emit(self.base_event("run.end", run.get("endTs") or run.get("startTs"), sid, skey, source, runId=run["runId"],
                                          status=run.get("status"), model=run.get("modelId"), provider=run.get("provider"),
                                          runUsage=run.get("usage"), runCostEstimated=est,
                                          costSource=("estimated_from_run_usage" if est else None),
                                          promptCache=run.get("promptCache"), terminalError=run.get("terminalError"), promptError=run.get("promptError"),
                                          assistantTexts=run.get("assistantTexts"), toolMetaCount=len(run.get("toolMetas") or []),
                                          itemLifecycle=run.get("itemLifecycle"), successfulCronAdds=run.get("successfulCronAdds"),
                                          snapshotMessages=snap_len, snapshotDropped=run.get("snapshotDropped"),
                                          snapshotLikelyTruncated=(snap_len == SNAPSHOT_CAP), transcriptAvailable=has_transcript))

    # ------------------------------------------------------------------ finalize
    def resolve_links(self):
        cache = {}
        for ev in self.events:
            sid = ev["sessionId"]
            if sid not in cache:
                cache[sid] = self.parent_of(ev["sessionKey"], sid)
            parent, spawner, src, label = cache[sid]
            ev["parentSessionKey"] = parent
            ev["spawnerSessionKey"] = spawner
            ev["linkSource"] = src
            if label:
                ev["label"] = label
        return cache

    def sort_events(self):
        for i, ev in enumerate(self.events):
            ev["_i"] = i
        self.events.sort(key=lambda e: (e.get("ts") or "", e["_i"]))
        for i, ev in enumerate(self.events):
            ev["seq"] = i + 1
            del ev["_i"]

    def run(self):
        self.discover()
        self.load_trajectories()
        self.calibrate_rates()
        for sid in sorted(self.session_files):
            if self.process_session_file(sid, self.session_files[sid]):
                continue
            # A transcript with no message records (a header-only stub left by a deletion or a crash) must not
            # shadow the trajectory: whatever content survives is in the trajectory's messagesSnapshot.
            self.empty_transcripts[sid] = self.session_files.pop(sid)
            self.session_file_kind.pop(sid, None)
            self.session_info.pop(sid, None)
            if sid in self.traj_files:
                self.notes.append("session %s: transcript %s holds no message records; content taken from its trajectory"
                                  % (sid, os.path.basename(self.empty_transcripts[sid])))
        traj_only = sorted(sid for sid in self.traj_files if sid not in self.session_files)
        for sid in traj_only:
            self.process_trajectory_only(sid)
        self.emit_runs()
        links = self.resolve_links()
        self.sort_events()
        return traj_only, links


# ----------------------------------------------------------------------------
# summary
# ----------------------------------------------------------------------------
def build_summary(x, traj_only, links, args):
    revoked_at = x.auth_revoked_at
    per_session = {}
    per_kind = collections.defaultdict(lambda: {"sessions": 0, "turns": 0, "userMessages": 0, "toolCalls": 0, "toolResults": 0,
                                                 "thinkingBlocks": 0, "errors": 0, "refusals": 0, "authErrors": 0,
                                                 "authErrorsAfterRevocation": (0 if revoked_at else None),
                                                 "runs": 0, "usage": zero_usage(), "cost": zero_cost(),
                                                 "runUsage": zero_usage(), "runCostEstimated": zero_cost(),
                                                 "runsWithContentLost": 0, "runsWithSnapshotTruncated": 0})
    refusals = []
    auth_all = []
    auth_after = []
    fallback_turns = 0
    mirror_turns = 0
    ev_counts = collections.Counter()
    tool_names = collections.Counter()
    models = collections.Counter()
    first_ts = None
    last_ts = None
    first_ts_pre = None
    last_ts_pre = None
    last_billable_ts = None
    for ev in x.events:
        ev_counts[ev["event"]] += 1
        sid = ev["sessionId"]
        skey = ev["sessionKey"]
        kind = ev["kind"]
        ps = per_session.setdefault(sid, {"sessionKey": skey, "kind": kind, "parentSessionKey": ev.get("parentSessionKey"),
                                          "spawnerSessionKey": ev.get("spawnerSessionKey"), "linkSource": ev.get("linkSource"),
                                          "label": ev.get("label"), "source": ev.get("source"), "firstTs": None, "lastTs": None,
                                          "turns": 0, "userMessages": 0, "toolCalls": 0, "toolResults": 0, "thinkingBlocks": 0,
                                          "errors": 0, "refusals": 0, "authErrors": 0,
                                          "authErrorsAfterRevocation": (0 if revoked_at else None), "runs": 0,
                                          "usage": zero_usage(), "cost": zero_cost(), "runUsage": zero_usage(),
                                          "runCostEstimated": zero_cost(), "runsWithContentLost": 0, "runsWithSnapshotTruncated": 0,
                                          "models": collections.Counter()})
        ts = ev.get("ts")
        if ts:
            ps["firstTs"] = ps["firstTs"] or ts
            ps["lastTs"] = ts
            first_ts = first_ts or ts
            last_ts = ts
            if revoked_at and ts < revoked_at:
                first_ts_pre = first_ts_pre or ts
                last_ts_pre = ts
        pk = per_kind[kind]
        e = ev["event"]
        if e == "assistant":
            ps["turns"] += 1
            pk["turns"] += 1
            ps["toolCalls"] += len(ev.get("toolCalls") or [])
            pk["toolCalls"] += len(ev.get("toolCalls") or [])
            for tc in ev.get("toolCalls") or []:
                tool_names[tc.get("name")] += 1
            ps["thinkingBlocks"] += len(ev.get("thinking") or [])
            pk["thinkingBlocks"] += len(ev.get("thinking") or [])
            add_usage(ps["usage"], ev.get("usage"))
            add_usage(pk["usage"], ev.get("usage"))
            add_cost(ps["cost"], ev.get("cost"))
            add_cost(pk["cost"], ev.get("cost"))
            if float((ev.get("cost") or {}).get("total") or 0) > 0 and ts:
                last_billable_ts = ts if (last_billable_ts is None or ts > last_billable_ts) else last_billable_ts
            ps["models"][ev.get("model")] += 1
            models[ev.get("model")] += 1
            if ev.get("providerFallback"):
                fallback_turns += 1
            if ev.get("provider") == "openclaw":
                mirror_turns += 1
        elif e == "user":
            ps["userMessages"] += 1
            pk["userMessages"] += 1
        elif e == "tool_result":
            ps["toolResults"] += 1
            pk["toolResults"] += 1
        elif e == "error":
            ps["errors"] += 1
            pk["errors"] += 1
            if ev.get("errorType") == "refusal":
                ps["refusals"] += 1
                pk["refusals"] += 1
                refusals.append({"responseId": ev.get("responseId"), "ts": ev.get("ts"), "sessionKey": skey, "category": ev.get("refusalCategory")})
            if ev.get("errorType") == "auth":
                ps["authErrors"] += 1
                pk["authErrors"] += 1
                auth_all.append({"ts": ev.get("ts"), "sessionKey": skey, "sessionId": sid})
                if ev.get("afterKeyRevocation"):
                    ps["authErrorsAfterRevocation"] += 1
                    pk["authErrorsAfterRevocation"] += 1
                    auth_after.append({"ts": ev.get("ts"), "sessionKey": skey, "sessionId": sid})
        elif e == "run.end":
            ps["runs"] += 1
            pk["runs"] += 1
            add_usage(ps["runUsage"], ev.get("runUsage"))
            add_usage(pk["runUsage"], ev.get("runUsage"))
            if ev.get("runCostEstimated"):
                add_cost(ps["runCostEstimated"], ev["runCostEstimated"])
                add_cost(pk["runCostEstimated"], ev["runCostEstimated"])
            if not ev.get("transcriptAvailable") and (ev.get("snapshotDropped") or ev.get("snapshotLikelyTruncated")):
                ps["runsWithContentLost" if ev.get("snapshotDropped") else "runsWithSnapshotTruncated"] += 1
                pk["runsWithContentLost" if ev.get("snapshotDropped") else "runsWithSnapshotTruncated"] += 1
    for sid, ps in per_session.items():
        per_kind[ps["kind"]]["sessions"] += 1
        ps["models"] = dict(ps["models"])
    # cost roll-ups
    recorded_session = zero_cost()
    recorded_snapshot = zero_cost()
    est_traj_only = zero_cost()
    est_traj_only_usage = zero_usage()
    unpriced_usage = zero_usage()
    est_lost_runs = zero_cost()
    for ev in x.events:
        if ev["event"] == "assistant" and ev.get("cost"):
            add_cost(recorded_session if ev["source"] != "trajectory" else recorded_snapshot, ev["cost"])
        if ev["event"] == "run.end" and not ev.get("transcriptAvailable"):
            if ev.get("runCostEstimated"):
                add_cost(est_traj_only, ev["runCostEstimated"])
                add_usage(est_traj_only_usage, ev.get("runUsage"))
                if ev.get("snapshotDropped") or ev.get("snapshotLikelyTruncated"):
                    add_cost(est_lost_runs, ev["runCostEstimated"])
            elif ev.get("runUsage"):
                add_usage(unpriced_usage, ev.get("runUsage"))
    grand_usage = zero_usage()
    grand_cost = zero_cost()
    for k, pk in per_kind.items():
        add_usage(grand_usage, pk["usage"])
        add_cost(grand_cost, pk["cost"])
    best_total = recorded_session["total"] + est_traj_only["total"]
    # ---- reconciliation variants -------------------------------------------------------------
    # (1) as recorded by OpenClaw: priced at the requested model unless its own fallback diagnostic fired.
    # (2) priced at the SERVED model (message.responseModel; confirmed by the model id embedded in the thinking
    #     signature) -- the provider bills sticky-routed fallback turns at the fallback model's rates.
    # (3) as (2) but cacheWrite billed at the 1-hour-TTL rate (2x input) instead of 1.25x
    #     (gateway config agents.defaults.params.cacheRetention = "long").
    served_5m = zero_cost()
    served_1h = zero_cost()
    served_turns = collections.Counter()
    served_recorded_cost = collections.Counter()
    for ev in x.events:
        if ev["event"] == "assistant" and ev.get("usage") and ev["source"] != "trajectory" and ev.get("provider") == "anthropic":
            served = ev.get("model") or ev.get("requestedModel")
            served_turns[(ev.get("requestedModel"), served, bool(ev.get("providerFallback")))] += 1
            served_recorded_cost[(ev.get("requestedModel"), served, bool(ev.get("providerFallback")))] += float((ev.get("cost") or {}).get("total") or 0)
            add_cost(served_5m, x.estimate_cost(ev["usage"], served, 1.25))
            add_cost(served_1h, x.estimate_cost(ev["usage"], served, 2.0))
        if ev["event"] == "run.end" and not ev.get("transcriptAvailable") and ev.get("runUsage"):
            add_cost(served_5m, x.estimate_cost(ev["runUsage"], ev.get("model"), 1.25))
            add_cost(served_1h, x.estimate_cost(ev["runUsage"], ev.get("model"), 2.0))
    alt_cw = 0.0
    for ev in x.events:
        if ev["event"] == "assistant" and ev.get("usage") and ev["source"] != "trajectory":
            r = x.rates.get(ev.get("pricedAsModel")) or {}
            alt_cw += float(ev["usage"].get("cacheWrite") or 0) * (r.get("input", 0.0) * 2.0 - r.get("cacheWrite", 0.0))
        if ev["event"] == "run.end" and not ev.get("transcriptAvailable") and ev.get("runUsage"):
            r = x.rates.get(ev.get("model")) or {}
            alt_cw += float(ev["runUsage"].get("cacheWrite") or 0) * (r.get("input", 0.0) * 2.0 - r.get("cacheWrite", 0.0))
    # ---- per-kind "best" numbers: transcript records where available, run-level usage (+estimate) otherwise
    best_kind = collections.defaultdict(lambda: {"usage": zero_usage(), "cost": zero_cost(), "sessions": 0, "sessionsFromTranscripts": 0,
                                                  "sessionsFromSnapshots": 0, "sessionsFromTrajectoryOnly": 0})
    for sid, ps in per_session.items():
        b = best_kind[ps["kind"]]
        b["sessions"] += 1
        if ps["source"] == "trajectory":
            b["sessionsFromTrajectoryOnly"] += 1
            add_usage(b["usage"], ps["runUsage"])
            add_cost(b["cost"], ps["runCostEstimated"])
        else:
            b["sessionsFromTranscripts"] += 1
            if ps["source"] == "session.snapshot":
                b["sessionsFromSnapshots"] += 1
            add_usage(b["usage"], ps["usage"])
            add_cost(b["cost"], ps["cost"])
    for b in best_kind.values():
        b["cost"] = {k: round(v, 4) for k, v in b["cost"].items()}
    kinds_count = collections.Counter(x.session_file_kind.values())
    summary = {
        "generatedAt": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "sessionsDir": x.dir,
        "snapshotsDir": x.snapshots_dir,
        "inputs": {
            "registryKeys": len(x.registry),
            "transcriptFiles": kinds_count.get("live", 0),
            "deletedTranscriptFiles": kinds_count.get("deleted", 0),
            "archivedTranscriptFiles": kinds_count.get("archived", 0),
            "snapshotFilesSeen": x.snapshot_files_seen,
            "sessionsFromSnapshots": kinds_count.get("snapshot", 0),
            "emptyTranscriptFiles": len(x.empty_transcripts),
            "trajectoryFiles": len(x.traj_files),
            "trajectoryOnlySessions": len(traj_only),
            "trajectoryOnlySessionIds": traj_only,
            "sessionsWithoutTrajectory": sorted(s for s in x.session_files if s not in x.traj_files),
            "keySource": collections.Counter(x.key_source.values()),
        },
        "timeSpan": {"firstEventTs": first_ts, "lastEventTs": last_ts, "lastBillableTurnTs": last_billable_ts,
                     "authRevokedAt": revoked_at,
                     "firstEventBeforeRevocation": first_ts_pre, "lastEventBeforeRevocation": last_ts_pre},
        "eventCounts": dict(ev_counts),
        "totalEvents": len(x.events),
        "dedupe": dict(x.dups),
        "models": dict(models),
        "providerFallbackTurns": fallback_turns,
        "deliveryMirrorTurns": mirror_turns,
        "toolCallsByName": dict(tool_names.most_common()),
        "perKind": {k: v for k, v in sorted(per_kind.items())},
        "grandTotal": {"sessions": len(per_session), "turns": sum(p["turns"] for p in per_kind.values()),
                       "toolCalls": sum(p["toolCalls"] for p in per_kind.values()),
                       "toolResults": sum(p["toolResults"] for p in per_kind.values()),
                       "thinkingBlocks": sum(p["thinkingBlocks"] for p in per_kind.values()),
                       "errors": sum(p["errors"] for p in per_kind.values()),
                       "refusals": len(refusals), "authErrorTurns": len(auth_all),
                       "authErrorsAfterRevocation": (len(auth_after) if revoked_at else None),
                       "usageFromRecords": grand_usage, "costFromRecords": grand_cost},
        "cost": {
            "recorded_from_transcripts_usd": round(recorded_session["total"], 4),
            "dedup_total": round(recorded_session["total"], 2),
            "recorded_from_trajectory_snapshots_usd_partial": round(recorded_snapshot["total"], 4),
            "estimated_for_trajectory_only_sessions_from_run_usage_usd": round(est_traj_only["total"], 4),
            "estimated_for_trajectory_only_sessions_usage": est_traj_only_usage,
            "of_which_runs_with_lost_or_truncated_content_usd": round(est_lost_runs["total"], 4),
            "best_estimate_total_usd": round(best_total, 4),
            "best_estimate_breakdown": {k: round(recorded_session[k] + est_traj_only[k], 4) for k in COST_FIELDS},
            "alt_if_cacheWrite_billed_at_1h_rate_2x_input_usd": round(best_total + alt_cw, 4),
            "alt_cacheWrite_delta_usd": round(alt_cw, 4),
            "reconciliation": {
                "1_as_recorded_by_openclaw_usd": round(best_total, 4),
                "2_repriced_at_served_model_5m_cacheWrite_usd": round(served_5m["total"], 4),
                "3_repriced_at_served_model_1h_cacheWrite_usd": round(served_1h["total"], 4),
                "served_model_turns": {"%s->%s%s" % (k[0], k[1], " (fallbackDiag)" if k[2] else ""): v for k, v in served_turns.items()},
                "served_model_recorded_cost_usd": {"%s->%s%s" % (k[0], k[1], " (fallbackDiag)" if k[2] else ""): round(v, 4) for k, v in served_recorded_cost.items()},
                "breakdown_2": {k: round(v, 4) for k, v in served_5m.items()},
                "breakdown_3": {k: round(v, 4) for k, v in served_1h.items()},
            },
            "perKindBest": {k: v for k, v in sorted(best_kind.items())},
            "ratesUsedUsdPerMtok": {m: {f: round(v * 1e6, 4) for f, v in r.items()} for m, r in x.rates.items()},
            "rateSource": dict(x.rate_source),
            "rateEvidence": x.rate_evidence,
            "unpricedModels": dict(x.unpriced),
            "unpriced_trajectory_only_usage": unpriced_usage,
        },
        "refusals": refusals,
        "authErrorTurns": {"count": len(auth_all), "bySession": dict(collections.Counter(a["sessionKey"] for a in auth_all)),
                           "first": auth_all[0]["ts"] if auth_all else None, "last": auth_all[-1]["ts"] if auth_all else None},
        "authErrorTurnsAfterRevocation": ({"count": len(auth_after), "bySession": dict(collections.Counter(a["sessionKey"] for a in auth_after)),
                                           "first": auth_after[0]["ts"] if auth_after else None, "last": auth_after[-1]["ts"] if auth_after else None}
                                          if revoked_at else None),
        "spawnLinks": {k: v for k, v in sorted(x.child_parent.items())},
        "notes": list(x.notes),
        "perSession": {sid: per_session[sid] for sid in sorted(per_session, key=lambda s: per_session[s]["firstTs"] or "")},
    }
    return summary


# ----------------------------------------------------------------------------
# scrubbing
# ----------------------------------------------------------------------------
class Scrubber:
    """Two-pass redaction over serialized JSON lines.

    Pass 1 (discover_line) collects the opaque values that the labelled / class patterns identify as
    secrets.  Pass 2 (scrub_line) replaces blacklist literals, then every recurrence of a discovered
    value, then the patterns themselves.  JSON escapes are never split: the class patterns consume
    "\\x" pairs atomically and a discovered value is never replaced where it directly follows a
    backslash (that occurrence would be the tail of an escape such as \\n + value)."""

    def __init__(self, enabled, blacklist_path=None):
        self.enabled = enabled
        self.counts = collections.Counter()
        self.blacklist = []
        self.discovered = set()
        self.discovered_list = []
        if blacklist_path:
            with open(blacklist_path, "r", encoding="utf-8") as fh:
                self.blacklist = [ln.rstrip("\n") for ln in fh if ln.strip()]
            self.blacklist.sort(key=len, reverse=True)

    def discover_line(self, line):
        """Pass 1: collect opaque values that the labeled/class patterns identify as secrets, so that pass 2 can
        redact every other occurrence of the same value (e.g. the same token later listed in a JSON array or a
        JS snippet where no label is nearby)."""
        if not self.enabled:
            return
        for name, rx in SCRUB_PATTERNS:
            g = VALUE_GROUP_PATTERNS.get(name)
            if not g:
                continue
            for m in rx.finditer(line):
                v = m.group(g)
                if v and len(v) >= 32 and "%" not in v and not re.match(r"[0-9a-f]{8}-[0-9a-f]{4}-", v):
                    self.discovered.add(v)

    def finalize_discovery(self):
        self.discovered_list = sorted(self.discovered, key=len, reverse=True)

    def scrub_line(self, line):
        if not self.enabled and not self.blacklist:
            return line
        for secret in self.blacklist:
            if secret in line:
                n = line.count(secret)
                self.counts["blacklist"] += n
                line = line.replace(secret, "[REDACTED]")
        # order: fixed-shape (vendor) patterns first so a key that is ALSO a labelled value is reported under
        # its vendor class; then every recurrence of a discovered value; then the labelled/class patterns.
        if self.enabled:
            line = self._apply_patterns(line, value_group=False)
        for secret in getattr(self, "discovered_list", ()):
            if secret in line:
                # never replace an occurrence that directly follows a backslash (it would split a JSON escape)
                line, n = re.subn(r"(?<!\\)" + re.escape(secret), "[REDACTED:discovered_value]", line)
                self.counts["discovered_value"] += n
        if self.enabled:
            line = self._apply_patterns(line, value_group=True)
        return line

    def _apply_patterns(self, line, value_group):
        for name, rx in SCRUB_PATTERNS:
            g = VALUE_GROUP_PATTERNS.get(name)
            if bool(g) != value_group:
                continue
            if g:
                def _rep(m, _g=g, _name=name):
                    s, e = m.span(_g)
                    return m.group(0)[: s - m.start()] + "[REDACTED:%s]" % _name + m.group(0)[e - m.start():]
                line, n = rx.subn(_rep, line)
            else:
                line, n = rx.subn("[REDACTED:%s]" % name, line)
            if n:
                self.counts[name] += n
        return line


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions-dir", required=True, help="~/.openclaw/agents/main/sessions (or a copy of it)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--snapshots-dir", help="crux-session-snapshot.sh output (~/.openclaw/session-snapshots); "
                                           "merged: largest copy per sid, live transcript wins")
    scrub_group = ap.add_mutually_exclusive_group()
    scrub_group.add_argument("--scrub", action="store_true",
                             help="accepted for compatibility: class-shape secret redaction of the outputs is the default")
    scrub_group.add_argument("--no-scrub", action="store_true",
                             help="write the outputs UNSCRUBBED (warning on stderr); only for a local diff on the box -- "
                                  "the result holds live credentials and must not leave it")
    ap.add_argument("--blacklist", help="file of literal strings to redact in addition to the class shapes (one per line; "
                                        "utils/clean-telemetry.sh format; build it on the box with utils/make-blacklist.sh)")
    ap.add_argument("--rates", help="USD/Mtok per model (utils/rates.example.json); only for trajectory-only estimates")
    ap.add_argument("--auth-revoked-at", help="ISO-8601 moment the provider key was revoked; adds the after-revocation "
                                             "split to the auth-error accounting")
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)

    rates = load_rates_file(args.rates) if args.rates else None
    try:
        x = Extractor(args.sessions_dir, snapshots_dir=args.snapshots_dir, rates=rates, auth_revoked_at=args.auth_revoked_at)
    except ValueError as exc:
        print("error: --auth-revoked-at: %s" % exc, file=sys.stderr)
        return 2
    traj_only, links = x.run()
    summary = build_summary(x, traj_only, links, args)

    scrub_enabled = not args.no_scrub          # scrubbing is the default; --scrub is a compatibility no-op
    if args.no_scrub:
        print("WARNING: --no-scrub: run_events.jsonl / run_summary.json are NOT redacted and hold whatever the store "
              "holds, live credentials included -- keep them on the box", file=sys.stderr)
    scrub = Scrubber(scrub_enabled, args.blacklist)
    ev_path = os.path.join(args.out_dir, "run_events.jsonl")
    lines = [json.dumps(ev, ensure_ascii=False, default=str) for ev in x.events]
    for ln in lines:                      # pass 1: discover labeled secret values
        scrub.discover_line(ln)
    scrub.finalize_discovery()
    invalid_json_lines = 0
    with open(ev_path, "w", encoding="utf-8") as fh:   # pass 2: redact
        for ln in lines:
            out = scrub.scrub_line(ln)
            if out is not ln:
                try:
                    json.loads(out)
                except json.JSONDecodeError:
                    invalid_json_lines += 1     # self-check: redaction must never corrupt the JSON line
            fh.write(out + "\n")
    summary["scrub"] = {"enabled": scrub_enabled, "blacklistEntries": len(scrub.blacklist),
                        "discoveredSecretValues": len(scrub.discovered_list),
                        "replacements": dict(scrub.counts), "patterns": [n for n, _ in SCRUB_PATTERNS],
                        "invalidJsonLinesAfterScrub": invalid_json_lines}
    if invalid_json_lines:
        print("WARNING: %d event lines are not valid JSON after scrubbing" % invalid_json_lines, file=sys.stderr)
    sm_path = os.path.join(args.out_dir, "run_summary.json")
    text = json.dumps(summary, indent=1, ensure_ascii=False, default=str)
    text = "\n".join(scrub.scrub_line(ln) for ln in text.split("\n"))
    # re-embed final counts (the summary scrub may add replacements)
    summary["scrub"]["replacements"] = dict(scrub.counts)
    doc = json.loads(text)
    doc["scrub"] = summary["scrub"]
    text = json.dumps(doc, indent=1, ensure_ascii=False, default=str)
    with open(sm_path, "w", encoding="utf-8") as fh:
        fh.write(text)

    # stdout report (never prints content, only counts)
    print("events: %d -> %s (%.1f MB)" % (len(x.events), ev_path, os.path.getsize(ev_path) / 1e6))
    print("summary: %s (%.1f KB)" % (sm_path, os.path.getsize(sm_path) / 1e3))
    print("event counts:", json.dumps(summary["eventCounts"]))
    print("dedupe:", json.dumps(summary["dedupe"]))
    print("cost:", json.dumps({k: v for k, v in summary["cost"].items() if k not in ("rateEvidence", "perKindBest", "reconciliation")}))
    if summary["notes"]:
        print("notes: %d (see run_summary.json)" % len(summary["notes"]))
    print("scrub:", json.dumps(summary["scrub"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
