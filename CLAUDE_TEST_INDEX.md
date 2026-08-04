# Claude Testing: Index

Three complementary approaches to test Claude. Choose based on your goal.

---

## 1. CLAUDE_SMOKE_TEST.md (⭐ START HERE)

**What:** Drop-in replacement test. Run Claude on the actual CRUX harness.

**Goal:** Answer "Can Claude run our 24h research workflow?"

**Setup:** 
- ~3 hours (provisioning + harness adaptations + single-turn test)
- 24 hours (smoke test with real task)

**Deliverable:** Real artifacts from Claude-driven research + comparison to OpenClaw.

**When to use:** You want definitive "does Claude work or not?" answer.

**Files:**
- `linux/src/start-claude.sh` (provisioning script)
- `CLAUDE_SMOKE_TEST.md` (detailed playbook)

---

## 2. spike-claude-code/ (Detailed Integration)

**What:** Full wrapper implementation. OpenClaw-like tool loop emulation.

**Goal:** Detailed engineering assessment. "How much work to fully integrate?"

**Setup:** 4–6 days of development + testing.

**Deliverable:** Working Claude wrapper with session persistence, cost tracking, subagent fork support.

**When to use:** You've decided Claude should replace OpenClaw long-term; need to understand integration cost.

**Files:**
- `spike-claude-code/src/claude_wrapper.py` (440 lines)
- `spike-claude-code/INTEGRATION_GUIDE.md` (file-by-file mapping)
- `spike-claude-code/TEST_REPORT.md` (detailed analysis)

---

## 3. claude-native-test/ (Research Quality Baseline)

**What:** Direct Claude API test. No tool emulation. No session management.

**Goal:** Understand Claude's native reasoning quality.

**Setup:** 1–2 hours to run.

**Deliverable:** Baseline for Claude's code review, literature analysis, planning, multi-turn reasoning.

**When to use:** You want to know "what is Claude good at without any wrapper?"

**Files:**
- `claude-native-test/claude_direct.py` (250 lines, 4 tests)
- `claude-native-test/README.md` (quick start)
- `claude-native-test/COMPARISON.md` (vs other approaches)

---

## Decision Tree

```
Question 1: Do you want to run Claude on the actual CRUX harness TODAY?
├─ YES  → CLAUDE_SMOKE_TEST.md (3h + 24h task)
│         └─ Gives you a real yes/no: does Claude work?
│
└─ NO   → Continue to Question 2

Question 2: Do you want to understand integration cost deeply?
├─ YES  → spike-claude-code/ (4–6 days engineering)
│         └─ Detailed wrapper + integration analysis
│
└─ NO   → Continue to Question 3

Question 3: Do you want a quick baseline of Claude's reasoning?
├─ YES  → claude-native-test/ (1–2 hours)
│         └─ 4 tests of Claude's native capabilities
│
└─ NO   → No Claude testing needed (stay with OpenClaw)
```

---

## Recommended Sequence

### If you have 1 day:
1. Run CLAUDE_SMOKE_TEST.md (3h setup + 1h smoke test)
2. Get results: "Claude works / doesn't work in our harness"
3. Decide: Invest more or stick with OpenClaw?

### If you have 1 week:
1. Run CLAUDE_SMOKE_TEST.md (1 day)
2. If promising, do spike-claude-code/ deep dive (4–6 days)
3. Full integration analysis + decision

### If you have 2 hours:
1. Run claude-native-test/ (1–2 hours)
2. Understand Claude's native strengths
3. Decide if it's worth pursuing further

---

## Comparison Table

| Aspect | Smoke Test | spike-claude-code | native-test |
|--------|---|---|---|
| **Time** | 3h + 24h | 4–6 days | 1–2h |
| **Cost** | ~$0.30–0.50 | ~$1–2 | ~$0.10–0.15 |
| **Output** | Real artifacts | Full wrapper | Reasoning baseline |
| **Decision** | Can Claude work? | How to integrate? | How good is Claude? |
| **Harness** | Full (actual) | Adapted | None (just API) |
| **Real task?** | YES ✓ | Possible | NO |
| **Tool emulation?** | Native API | YES | NO |
| **Ready now?** | YES ✓ | Partial | YES ✓ |

---

## File Organization

```
crux-in-a-box/
├── CLAUDE_SMOKE_TEST.md          ⭐ START HERE
├── CLAUDE_TEST_INDEX.md          (this file)
│
├── linux/src/start-claude.sh     (provisioning for smoke test)
│
├── spike-claude-code/            (detailed integration)
│   ├── src/claude_wrapper.py
│   ├── INTEGRATION_GUIDE.md
│   └── TEST_REPORT.md
│
├── claude-native-test/           (reasoning baseline)
│   ├── claude_direct.py
│   ├── README.md
│   └── COMPARISON.md
│
└── AGENT_SPIKE.md               (overview of all 4 agents)
```

---

## Status

| Test | Status | Next |
|------|--------|------|
| Smoke Test | ✓ Ready | Provision EC2 + run |
| spike-claude-code | ⚠ Partial (wrapper built; integration pending) | Implement heartbeat + subagents |
| native-test | ✓ Ready | Run tests |

---

## Next Steps

1. **Operator decision:** Which approach? (Smoke test recommended first)
2. **Get ANTHROPIC_API_KEY**
3. **Run chosen test**
4. **Evaluate results**
5. **Decide:** Commit to Claude? Build wrapper? Stay with OpenClaw?

---

**TL;DR:** Start with CLAUDE_SMOKE_TEST.md (3 hours). Real yes/no answer.
