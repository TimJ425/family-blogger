# Family Blogger: code review (<YYYY-MM-DD>)

Review of `<branch>` at `<short sha>`. <Read-only / fixes applied>.
Threat model: **<exposure>**. <One line on realistic adversaries.>

**How it was run**

| Check | Model | Result |
|---|---|---|
| Tests and flake8 | Haiku | <n/n suites, flake8 result> |
| `pip-audit` | Haiku | <result or "skipped"> |
| Docs vs code | Haiku | <one line> |
| Deploy, CI and backup | Sonnet | See Medium and Low |
| App security | Opus | See High and Medium |
| Dead code and logic | Opus | See all sections |

<One sentence on manual verification and any severity changes.>

---

## 1. What the project does

<Short paragraph plus bullets of the main features.>

## 2. Tools and stack

<Back end, content, serving, front end, ops, quality.>

## 3. What works well

<Bold lead-in per bullet; specific and verified.>

## 4. What doesn't work well

<Themes, not a repeat of the pipeline.>

## 5. What should change (themes)

<Numbered list of structural changes.>

## 6. Suggested features

<Each fits the guiding principles. Then "Items to ask about first".>

---

## 7. Fix pipeline

### 🔴 High: security issues, broken code and broken functionality

| # | Issue | Where | Scenario | Fix |
|---|---|---|---|---|
| H1 | **<title>** | `<file:line>` | <who does what → what happens> | <fix> |

### 🟠 Medium: minor bugs and hardening gaps

| # | Issue | Where | Fix |
|---|---|---|---|
| M1 | <issue> | `<file:line>` | <fix> |

### 🟢 Low: suggested improvements

Group under: **Security and ops hardening**, **Bugs and quirks**,
**Dead code**, **Duplication**, **Docs**, **Test gaps to add with the
fixes**.

---

### Suggested order of work

<Numbered steps, with dependencies, e.g. migrations before model
changes.>
