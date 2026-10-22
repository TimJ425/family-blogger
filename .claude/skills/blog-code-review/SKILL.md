---
name: blog-code-review
description: Whole-project, security-conscious code review of Family Blogger that produces a written report (what it does, tools, what works, what doesn't, changes, feature ideas, and a High/Medium/Low fix pipeline) using cost-tiered sub-agents. Use this whenever the user asks to review, audit, health-check or security-review the blog/project/codebase as a whole, asks "what should we fix next", or invokes /blog-code-review — even if they don't say "skill". For reviewing only the current diff or a single PR, prefer the built-in code-review skill instead.
---

# Blog code review

A full review of the Family Blogger codebase, written up as a report the
user can work through. The point is a trustworthy, prioritised to-do
list, so verification and honest severity matter more than finding
lots of things.

## 1. Before starting agents: plan and ask

Do a quick, cheap orientation yourself (follow the targeted-reading
skill): tracked files with line counts, `requirements*.txt`, the README
intro, and `git log -5`. Never read `instance/` — it holds real family
data.

Then walk the user through the plan **before** launching anything:

- one paragraph on what the project is and its size
- the agent table (section 2) with model per agent
- the report headings (section 4)

Ask with AskUserQuestion (skip any already answered earlier in the
conversation or saved in memory):

1. **Exposure** — home LAN only / LAN + remote access / public. This
   sets severity: a guest on the Wi-Fi is a real adversary on a LAN; a
   stranger on the internet is only relevant if exposed.
2. **Scope** — report only (default) / report then fix High.
3. **Dependency audit** — may `pip-audit` be installed into `.venv`
   only (it queries the public vulnerability DB with package names)?
   Respect CLAUDE.md principle 1: ask, don't assume.
4. **Output** — chat only / Markdown in `docs/code-review-<date>.md`
   (uncommitted) / private artifact.

## 2. Agents (cost-tiered, run in parallel)

Match model to task complexity: cheap models for mechanical checks,
the largest for judgement-heavy work. Launch all in one turn, in the
background. Every prompt must say: read-only, never read `instance/`,
follow the quiet-output and targeted-reading skills, state the threat
model, and report each finding as *file:line, severity, concrete
failure scenario, one-line fix*, marking unverified ones PLAUSIBLE.

| Agent | Model | Job |
|---|---|---|
| Test runner | haiku | `tests/run_all.py`, flake8, pip-audit (if allowed), key package versions |
| Docs checker | haiku | `TEMPLATE_CONTRACT.md` vs routes/context, README claims vs code, CONTRIBUTING/CLAUDE.md rules (docstrings, no `typing`, `--fb-*` colours, CSRF, alt text, labels), `.env.example` vs config |
| Deploy/CI | sonnet | Dockerfile, compose files, `backup.sh` safety, CI permissions/pinning, secret key, cookies, headers, migrations, reproducibility, multi-arch |
| Security | opus | Authorization/IDOR, private-post leaks (feed, search, profile, mood data, uploads, JSON), auth/session lifecycle, signup, CSRF, XSS (markdown/nh3, CSP, `\|safe`, values inside inline JS/CSS), uploads (EXIF/GPS stripping, bombs, traversal), redirects, rate limiting |
| Dead code & logic | opus | Unused code/templates/CSS/JS (grep-verified), main user flows end to end, data integrity, no-JS behaviour, N+1, test gaps |

Detailed prompt checklists: `references/agent-prompts.md`. Read it when
writing the prompts.

Known environment quirk: if `.venv/bin/flake8` fails with "bad
interpreter", use `.venv/bin/python -m flake8`.

## 3. Verify before reporting

Agents miss things and overstate things. Before writing:

- Re-read the code for every High and any surprising Medium yourself.
- Cross-check agents against each other — one agent's "low" note may
  be another's missed High (e.g. a display name placed in an inline
  `onsubmit` is stored XSS, not a cosmetic bug).
- Downgrade claims the code already prevents, and say so in the report.
- Merge duplicates found by several agents.

Relay progress to the user in a line or two as each agent reports.

## 4. Report

Use the template in `references/report-template.md`. Headings, in order:

1. What the project does
2. Tools and stack
3. What works well
4. What doesn't work well
5. What should change (themes)
6. Suggested features — must fit CLAUDE.md's guiding principles (calm,
   family-only, parents in charge, memories matter); list anything
   borderline under "ask first"
7. Fix pipeline — High / Medium / Low, plus a suggested order of work

Severity definitions (keep them consistent across reviews):

- **High** — security holes, privacy leaks, broken code or broken
  functionality, data-loss risk, or a breach of a guiding principle
  (e.g. deletion without confirmation).
- **Medium** — minor bugs, hardening gaps with realistic impact, docs
  drift that misleads.
- **Low** — suggestive improvements: dead code, duplication, nits,
  defence in depth, test gaps.

## 5. Wrap up

Give a short chat summary: counts per severity, the High headlines in a
sentence each, open questions for the user, and housekeeping (anything
installed, broken env). Offer to fix the High items on a branch; don't
start fixing without the user's go-ahead.
