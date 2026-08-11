# Handover

**For a non-engineer taking ownership of this project.**

You've been handed a working piece of software and you're not a programmer. That's a normal
situation and a workable one. This document tells you what you now own, what state it's in,
what you can do unaided, and — the main event — exactly how to use Claude Code as your
engineer so that the project keeps moving.

Read this once end to end before you touch anything. It takes about twenty minutes and will
save you days.

---

## Contents

1. [What you've inherited](#1-what-youve-inherited)
2. [Where things stand today](#2-where-things-stand-today)
3. [Your first day](#3-your-first-day)
4. [What you can do without any help](#4-what-you-can-do-without-any-help)
5. [Using Claude Code: the essentials](#5-using-claude-code-the-essentials)
6. [Using Claude Code: step by step](#6-using-claude-code-step-by-step)
7. [How to verify work you can't read](#7-how-to-verify-work-you-cant-read)
8. [Your safety net: git](#8-your-safety-net-git)
9. [Worked examples you can copy](#9-worked-examples-you-can-copy)
10. [Red flags — when to stop](#10-red-flags--when-to-stop)
11. [The inherited backlog](#11-the-inherited-backlog)
12. [Routine maintenance](#12-routine-maintenance)
13. [When you need a human engineer](#13-when-you-need-a-human-engineer)
14. [Reference card](#14-reference-card)

---

## 1. What you've inherited

A tool that does two jobs.

**Job one: read Bill of Materials PDFs.** Engineering BoMs arrive as PDFs — parts lists,
printed for humans. The tool reads them and produces structured data: every part, its
description, quantity, where it sits in the assembly tree, and which suppliers make it. This
part is reliable, offline, and free to run.

**Job two: research those parts on the web.** For every supplier part number found, it runs a
Google search through a paid service and records the top result links in an Excel
spreadsheet, keeping the assembly structure visible. This costs money per search.

The value is in job one. It replaces someone retyping hundreds of part numbers off a PDF, and
it does it without being told what the PDF looks like — it works out the table structure by
itself, which is why it survives new BoM formats.

Two documents sit alongside this one:

- **[USER_GUIDE.md](USER_GUIDE.md)** — how to run it. Written for you. Start here after this.
- **[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)** — how it works inside. You won't read it, but
  **Claude Code will**, and that's precisely why it exists. When you ask for a change, point
  Claude at it.

---

## 2. Where things stand today

An honest status, current as of this handover.

### Working

- **PDF parsing.** Verified against two real BoMs. One produces 146 parts across 12 pages.
- **All five commands** — `parse`, `batch`, `inspect`, `summary`, `enrich`.
- **The automated test suite** — 142 tests passing, *once sample PDFs are in place* (see
  below). Without them you'll see around 66 passing and the rest erroring, which is expected
  rather than broken.
- **Documentation** — README plus the two guides, all current.

### Needs attention

| Item | What it means for you |
| --- | --- |
| **The Bright Data account is suspended.** | Web-search enrichment (job two) will not run until someone reactivates billing at `brightdata.com/cp/setting/billing`. Job one is unaffected. This is an account problem, not a software problem. |
| **Two tests fail on "golden snapshots".** | These compare current output against a saved copy from months ago. The current output is *better* — it fixed some split words. The saved copy just needs refreshing. [Section 11](#11-the-inherited-backlog) has the exact request to hand Claude. |
| **Unfinished work is uncommitted.** | Two source files have improvements (better progress messages during enrichment) that were never saved into version history. They work. They need committing. |
| **Current work sits on a side branch.** | Everything recent is on a branch named `serp-api-implementation`, not on the main line (`master`). Merging it is a decision someone needs to make deliberately. |

### Known limitations, by design

- **Scanned PDFs don't work.** If a PDF is a photograph of a page rather than a real document,
  the tool can't read it. It will warn you rather than fail silently.
- **One BoM template is partly hard-coded.** Page footers are recognised by specific text from
  one company's report generator. A very different BoM format may pick up footer junk.
- **Searches aren't cached.** Running enrichment twice on the same file pays twice.

---

## 3. Your first day

Work through this in order. Roughly an hour. Don't skip the verification steps — they're how
you learn what "working" looks like, which you'll need later.

**1. Get the code.** It lives at `https://github.com/tommazin-opt/bom-parser`. If it's not
already on your machine, ask Claude Code: *"Clone the repository at [URL] into a folder on my
Desktop and set it up so I can run it."*

**2. Install it.** Follow [USER_GUIDE.md § 2](USER_GUIDE.md#2-installing-it-once). Three
commands. If anything errors, paste the error into Claude Code verbatim and ask it to fix it.

**3. Prove it works.** Parse a sample BoM:

```powershell
bom-parser parse "path\to\a-bom.pdf" -o out\test.json
```

You should see `Wrote N parts to out\test.json`. Sample PDFs live in the sibling
`automatic-quotes/Resources/PDFBoMs/` folder.

**4. Look at the result.**

```powershell
bom-parser summary out\test.json
```

Open the original PDF next to this output and spot-check ten parts. This is the single most
important skill you'll need — knowing what correct output looks like. **Everything in
[section 7](#7-how-to-verify-work-you-cant-read) depends on you having done this once,
carefully.**

**5. Run the tests.**

```powershell
pytest
```

Note the numbers you get — that's your baseline. If a change ever makes that number worse, the
change is wrong.

Some tests parse real PDFs and look for them in a `Resources/BoMs/` folder that isn't included
in the repository (PDFs aren't kept in version control). Until you copy sample BoMs there,
those tests error rather than run. That's expected. To get the full suite working:

```
Some integration tests need sample PDFs in Resources/BoMs/ that aren't in the
repo. Copy the two sample BoMs from ../automatic-quotes/Resources/PDFBoMs/ into
that folder, then run the full test suite and tell me the result.
```

**6. Set up Claude Code.** See [section 5](#5-using-claude-code-the-essentials).

**7. Give Claude Code its project memory.** Once installed, open it in the project folder and
run:

```
/init
```

This reads the codebase and writes a `CLAUDE.md` file — a briefing note that every future
Claude Code session reads automatically. It means you don't have to re-explain the project
every time. Do this once; it pays for itself immediately.

---

## 4. What you can do without any help

More than you'd expect. These are all text-file edits, fully documented in the user guide, and
none of them require understanding code.

| Task | Where |
| --- | --- |
| Run any of the five commands | [USER_GUIDE § 12](USER_GUIDE.md#12-command-reference) |
| Teach it a new BoM column heading | Edit `config/header_synonyms.yaml` |
| Fix a supplier appearing under several names | Edit `config/supplier_aliases.yaml` |
| Make it stricter or looser about part numbers | Edit `config/heuristic_weights.yaml` |
| Diagnose a PDF that parses badly | Run `bom-parser inspect` |
| Check the cost of an enrichment run before paying | Run with `--dry-run` |

The config files are plain text. Open them in Notepad or VS Code — **not Word**, which adds
invisible formatting that breaks them. Copy the shape of the entries already there.

If you're changing config and something breaks, that's a safe failure: undo your edit and the
tool goes back to how it was.

---

## 5. Using Claude Code: the essentials

### What it is

Claude Code is an AI assistant that works inside your project folder. Unlike a chatbot you
paste snippets into, it can read every file, write changes, run the tool, run the tests, and
report what happened. For you, it's the difference between "I can't change this software" and
"I can direct changes to this software."

### What it isn't

It is not infallible, and this matters more for you than for a programmer. A programmer
catches a bad suggestion by reading it. You can't. So your defence is different: **you verify
by behaviour, not by reading** — did the tests pass, does the output still match the PDF.
[Section 7](#7-how-to-verify-work-you-cant-read) is the whole discipline, and it's not
optional.

It also won't make product decisions for you. It'll happily implement whatever you ask,
including things you'll regret. Deciding *what* the tool should do stays your job.

### Getting it

Claude Code runs as a VS Code extension, a desktop app, in the terminal, and on the web. If
you're already using VS Code to open this project — which the project is set up for — the
extension is the smoothest route: it sits in a side panel next to your files.

Whichever you choose, the rule that matters: **it must be opened in the project folder**, the
one containing `config`, `src`, and `docs`. Claude Code can only see the folder it's started
in. Open the wrong folder and it will be confidently unhelpful.

### The three things to remember

1. **Ask for a plan before changes.** Say *"don't change anything yet — explain what you'd
   do."* Read the plan. If it doesn't match what you wanted, say so before any file is
   touched. This one habit prevents most bad outcomes.
2. **Make it prove the work.** Every change request should end with *"then run the tests and
   show me the result."* An unverified change is not a finished change.
3. **Start fresh for unrelated tasks.** Type `/clear` between jobs. Long sessions that drift
   across several topics get muddled. One task, one conversation.

---

## 6. Using Claude Code: step by step

This is the core workflow. Every change you ever make to this project follows these seven
steps.

### Step 1 — Open the project folder

Open the `bom-parser` folder in VS Code, then open the Claude Code panel. Confirm it's in the
right place by asking:

```
What project am I in and what does it do?
```

If it describes a BoM PDF parser, you're set. If it doesn't, you've opened the wrong folder.

### Step 2 — Describe what you want, with context

The quality of what you get back is mostly determined here. A good request has four
ingredients:

| Ingredient | Example |
| --- | --- |
| **What's happening now** | "When I parse `UA000512.pdf`, it stops with an error about the header on page 1." |
| **What you want instead** | "It should parse like the other BoMs do." |
| **Where to look** | "`docs/DEVELOPER_GUIDE.md` explains how column detection works." |
| **How you'll check** | "Then run the tests and parse the file to show me it works." |

Compare:

> ❌ "The parser is broken, fix it."

> ✅ "When I run `bom-parser parse "Resources/BoMs/UA000512.pdf" -o out/512.json` it fails with
> `HeaderDetectionError` on page 1. Other BoMs parse fine. `docs/DEVELOPER_GUIDE.md` section 5
> covers how header detection works. Please investigate and explain the cause before changing
> anything."

The second gets you a real answer. **Paste error messages in full** — never summarise them.
The exact text is the most useful thing you have.

### Step 3 — Ask for a plan first

For anything beyond a trivial fix:

```
Before changing any files, explain what you'd change and why, in plain language.
```

Claude Code also has a dedicated planning mode that researches without editing anything —
in the terminal you cycle to it with Shift+Tab. But asking in words, as above, works
everywhere and is easier to remember.

Read the plan. You're checking three things, none of which need coding knowledge:

- Does it address what you actually asked?
- Does it touch more of the project than seems necessary? (More files changed = more risk.)
- Does it mention deleting or rewriting something you didn't ask about?

If anything looks off, say so: *"That's more than I wanted — can you do just the first part?"*

### Step 4 — Approve the work

Say go. As Claude Code works, it will ask permission before actions like editing a file or
running a command. Read these prompts — they're your last checkpoint.

| Prompt type | Normally fine? |
| --- | --- |
| Read a file | Yes, always |
| Edit a file in this project | Yes, if it matches the plan |
| Run `pytest`, `bom-parser`, `git status` | Yes |
| Run `git commit` | Yes, once you've verified the change works |
| Delete files, `git push`, `git reset --hard` | **Stop and think.** Ask what it does and why. |
| Anything mentioning the Bright Data token | **Stop.** That's a paid credential. |

There's an option to stop asking and let it run freely. **Don't use it** while you're learning.
The prompts are slow but they're the thing standing between you and an unnoticed mistake.

### Step 5 — Verify (never skip)

See [section 7](#7-how-to-verify-work-you-cant-read). At minimum: run the tests, and run the
tool on a real PDF.

### Step 6 — Ask for an explanation you can keep

```
Explain what you changed in plain English, as if writing a note for someone
who doesn't code. What could go wrong as a result?
```

Two reasons. First, you need to be able to tell colleagues what happened. Second, if it can't
explain the change simply, that's a signal the change may be more complicated than it should
be.

### Step 7 — Save it

Once verified:

```
Commit this change with a clear message describing what it does.
```

See [section 8](#8-your-safety-net-git) for why this matters so much.

---

## 7. How to verify work you can't read

You cannot check the code. You *can* check the behaviour, and for this project that's enough.
Run these four checks after any change. They take five minutes.

### Check 1 — The tests still pass

```powershell
pytest
```

You noted your baseline on day one. The passing count should be the same or higher, **never
lower**. If it dropped, the change broke something:

```
Before this change the tests were passing [your baseline number].
Now [N] are failing. Please investigate and fix, or undo the change.
```

### Check 2 — A real PDF still parses

```powershell
bom-parser parse "path\to\known-good.pdf" -o out\check.json
```

Same part count as before? If a change unexpectedly moves 146 parts to 89, something is
badly wrong regardless of what the tests say.

### Check 3 — The output still matches the PDF

```powershell
bom-parser summary out\check.json
```

Spot-check the same ten parts you checked on day one. Descriptions complete, suppliers on the
right parts, nothing garbled.

### Check 4 — Ask it to argue against itself

```
What could this change have broken that the tests wouldn't catch?
```

A genuinely useful question. It surfaces edge cases — and if the answer is a confident
"nothing", be a little more skeptical, not less.

> **The golden rule: if you didn't verify it, it isn't done.** Not "probably fine". Not "the
> tests are slow today". A change you haven't checked is a change that will surprise you in
> three weeks, when you've forgotten it happened.

---

## 8. Your safety net: git

Git is the version history. It's the reason mistakes here are recoverable, and it's the single
most valuable thing for someone in your position — you can always get back to a working state.

You don't need to learn git commands. You need to understand three ideas and know how to ask
for them.

**A commit is a save point.** It records the state of every file with a note about what
changed. You can return to any commit, ever.

**Commit often — after every verified change.** Not at the end of the week. Small save points
mean small, easily-undone mistakes. Ask:

```
Commit this with a clear message.
```

**You can always go back.** If something breaks and you don't know why:

```
Something's broken and I don't know what caused it. Show me what's changed
since the last commit, and help me undo it if it looks wrong.
```

Two more things worth knowing about this project's history:

- **Work is on a branch called `serp-api-implementation`**, separate from the main line
  (`master`). A branch is a parallel copy where work happens without disturbing the known-good
  version. Merging it into `master` is a decision to make deliberately, once you're confident:
  *"Explain what merging serp-api-implementation into master would change, and what the risks
  are."*
- **There's uncommitted work right now** in two files. It works but was never saved into
  history. Deal with this early — see [section 11](#11-the-inherited-backlog).

Ask before anything involving `push`, `reset`, or `force`. Those reach beyond your machine or
throw work away.

---

## 9. Worked examples you can copy

Real requests for real situations. Adapt the specifics.

### A BoM won't parse

```
Running: bom-parser parse "Resources/BoMs/NEW-BOM.pdf" -o out/new.json

It fails with this error:

[paste the entire error message here]

Other BoMs parse fine. docs/DEVELOPER_GUIDE.md section 5 explains how column
detection works, and docs/USER_GUIDE.md section 10 says this is often a missing
header synonym.

Please diagnose the cause and tell me whether I can fix it myself by editing
config/header_synonyms.yaml. Don't change any code yet.
```

Note the last line. Config edits you can make and undo safely; code changes you can't review.
Always ask which one you're facing.

### The output is wrong in a specific way

```
In out/456.json, the part LB000300 shows supplier "North Coast Com" with part
number 596-00379. Looking at page 4 of the PDF, the part number should be
596-00381.

Please investigate why it's reading the wrong value. Explain what you find
before changing anything.
```

Specifics are everything: which part, which page, what it says, what it should say. A vague
"the numbers are wrong" produces a vague investigation.

### You want a new feature

```
I'd like to export results as CSV as well as Excel, because our purchasing
system imports CSV.

Before writing anything: explain how you'd add this, which files change,
and roughly how big a change it is. I'm not a programmer, so keep it
plain-language.
```

Then decide based on the answer. "Two files, small change" and "restructures the export layer"
are very different propositions.

### You inherited something and don't understand it

```
Explain what src/bom_parser/services/row_assembler.py does, in plain English,
as if to someone who doesn't code. Why is it the most complicated file in
the project?
```

Use this freely. Understanding your own system is a legitimate use of time, and this is the
fastest way to get it.

### Something urgent broke

```
This was working yesterday and now it doesn't. Here's what I ran and what
happened:

[command]
[full output]

Show me what changed since it last worked, and help me get back to a working
state first. We can work out the cause afterwards.
```

Restore service first, diagnose second.

---

## 10. Red flags — when to stop

Stop and get a second opinion if you see any of these.

**"I've simplified this by removing…"** — Removing code you didn't ask about. Ask precisely
what was removed and why. This project's odd-looking details are frequently deliberate fixes
for real bugs; several are documented as such in the code.

**A change that touches many files when you asked for something small.** Ask: *"Why does this
need to change so many files? Is there a smaller version?"*

**Tests were changed to make them pass.** Tests exist to catch mistakes. Changing a test so it
stops complaining is sometimes right and often exactly wrong. Ask: *"Did you change the code to
fix the problem, or change the test to stop it reporting the problem?"*

**Anything about the Bright Data token.** Real money and a real credential. Never let a token
be written into a file — it belongs in environment variables only, which is how the project is
built.

**"This should work"** without having run anything. Ask it to actually run the thing.

**You don't understand the explanation.** Not your failure — ask again: *"Explain that more
simply."* Approving something you don't understand is how projects drift out of your control.

**Repeated failed attempts at the same problem.** After two or three rounds of "that didn't
work either", stop. Going round again usually makes things worse. Time for
[section 13](#13-when-you-need-a-human-engineer).

---

## 11. The inherited backlog

Known work, roughly in priority order. Each has a request you can paste straight in.

### 1. Commit the unfinished work (do this first)

Two files have working improvements that were never committed. Until they are, they could be
lost.

```
Two files have uncommitted changes: src/bom_parser/cli.py and
src/bom_parser/services/serp_client.py. Show me what changed in plain English,
confirm the tests still pass, and if it all looks sound, commit it with a
clear message.
```

### 2. Refresh the stale golden snapshots

Two tests fail because a saved copy of expected output is out of date. The current output is
better than the saved copy.

```
Two golden snapshot tests are failing in tests/integration/test_golden_snapshots.py.
docs/DEVELOPER_GUIDE.md section 10 says the fixtures are stale and current output
is an improvement.

Please show me the actual differences in plain English first, so I can confirm
they're improvements. If they are, regenerate the fixtures with
scripts/regenerate_goldens.py, re-run the tests, and commit.
```

Insist on seeing the differences before regenerating. Refreshing a snapshot without looking is
how a real bug gets baked in permanently.

### 3. Fix `bom-parser --version`

A small, safe first change — good practice.

```
`bom-parser --version` fails with "Missing command" instead of printing the
version. docs/DEVELOPER_GUIDE.md section 13 says the fix is adding
invoke_without_command=True to the Typer app in src/bom_parser/cli.py, and
that adding no_args_is_help=True would also make a bare command print help.

Please make both changes, run the tests, and show me it working.
```

### 4. Reactivate Bright Data, or decide not to

Not a software task. Enrichment is dead until the account at
`brightdata.com/cp/setting/billing` is reactivated. Someone must decide whether that's worth
paying for. Until then, job one still works fine.

### 5. Cache search results

Currently, re-running enrichment on the same file pays for every search again.

```
Enrichment doesn't cache results, so re-running on the same file re-sends and
re-pays for every search. docs/DEVELOPER_GUIDE.md section 13 suggests a
persistent cache keyed on the query string.

Explain how you'd add this and how much it would cut costs on a repeat run.
Don't write anything yet.
```

### 6. Generalise footer detection

The one place the tool is tied to a specific company's BoM format.

```
docs/DEVELOPER_GUIDE.md section 13 says footer detection is hard-coded to one
company's report format via FOOTER_LINE_MARKERS in utils/consts.py, and
suggests detecting repeated text at consistent positions across pages instead.

Explain what would be involved and what could break. Don't change anything yet.
```

Larger and riskier than the others. Only worth doing if you're actually hitting BoM formats
that break on it.

### 7. Write the missing design note

`docs/FUTURE_BOM_FORMATS.md` is referenced twice in the code but doesn't exist.

```
Two source files reference docs/FUTURE_BOM_FORMATS.md, which doesn't exist.
Please find those references and either write the document — covering the
planned OCR approach for scanned PDFs — or remove the references if the plan
is gone.
```

---

## 12. Routine maintenance

**After any change** — run `pytest`, parse a known-good PDF, commit.

**Monthly** — run the test suite even if nothing changed; dependencies shift underneath you.
Parse a recent BoM and spot-check it. Skim `metadata.new_supplier_candidates` in a recent
output and add anything genuinely new to `config/supplier_aliases.yaml`.

**Every few months** — ask Claude Code:

```
Check whether this project's dependencies have known security issues or are
badly out of date. Explain any risks in plain language. Don't change anything yet.
```

**Whenever a new BoM format appears** — run `bom-parser inspect` on it first. Nine times out
of ten it's a missing header synonym you can fix yourself in a minute.

**When someone new joins** — point them at this document, then the user guide. If they're
technical, the developer guide.

---

## 13. When you need a human engineer

Claude Code covers most day-to-day work. Bring in a person when:

- **You've gone three rounds on the same problem** without progress. Repetition compounds
  damage.
- **The change involves money or credentials** beyond a straightforward config edit.
- **You're deciding architecture** — should this become a web service, integrate with the ERP,
  process thousands of BoMs a day? Those are judgement calls with consequences a year out.
- **Something is wrong in production and you can't restore it** with the git steps in
  [section 8](#8-your-safety-net-git).
- **You need to be certain.** For anything where being wrong is expensive, have a person read
  the code. Verification by behaviour is good; it isn't proof.

When you bring someone in, hand them `docs/DEVELOPER_GUIDE.md` first. It's written to get a
developer productive quickly, and it includes the known-issues list so they don't rediscover
problems you already know about.

---

## 14. Reference card

### Commands

```powershell
.\.venv\Scripts\Activate.ps1                          # start of every session

bom-parser parse "file.pdf" -o out\file.json          # one PDF to data
bom-parser batch "folder\" -o out\                    # a folder of PDFs
bom-parser inspect "file.pdf"                         # diagnose a problem PDF
bom-parser summary out\file.json                      # human-readable check
bom-parser enrich out\file.json -o out\ --dry-run     # cost preview, free
bom-parser enrich out\file.json -o out\               # real run, costs money

pytest                                                # run the tests
```

### Locations

| What | Where |
| --- | --- |
| Code repository | `https://github.com/tommazin-opt/bom-parser` |
| Current branch | `serp-api-implementation` (main line is `master`) |
| Settings you can edit | `config/*.yaml` |
| Sample BoM PDFs | `../automatic-quotes/Resources/PDFBoMs/` |
| Test PDFs must be copied to | `Resources/BoMs/` |
| Documentation | `README.md`, `docs/` |

### Credentials

| Variable | What |
| --- | --- |
| `BRIGHTDATA_API_TOKEN` | Bright Data **account** API token (Settings → API tokens — not a zone password) |
| `BRIGHTDATA_SERP_ZONE` | Zone name, currently `bom_serp_api` |

Set with `setx NAME "value"`, then open a new terminal. Never put these in a file.

### Prompts worth memorising

```
Before changing anything, explain what you'd do and why.

Run the tests and show me the result.

Explain that in plain English, as if I don't code.

What could this have broken that the tests wouldn't catch?

Commit this with a clear message.

Show me what's changed since the last commit, and help me undo it.
```

---

**Last thing.** The most common way a handover like this fails is the new owner being too
cautious to touch anything, until the project quietly dies. The second most common is the
opposite — changing things freely without verifying, until nobody trusts the output.

The middle path is the whole of this document: small changes, always verified, always
committed. You have a working tool, a full test suite, complete documentation, and an
assistant that can read all of it. That's a good position to start from.
