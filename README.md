# Weekly Newsletter Podcast

Turn a week of newsletters into one podcast episode — hands-free.

Every Sunday it collects everything filed under a Gmail label, strips the
boilerplate, merges the issues into a single document, has NotebookLM record a
conversational overview, and emails you the MP3. When anything fails, you get
an email naming the failing step instead of a silent miss.

```
Gmail label "Newsletter"
   │  fetch (gmail.modify)
   ▼
clean + merge (trafilatura + bs4 rules — no LLM, no token cost)
   │  output/newsletter-YYYY-MM-DD.md
   ▼
NotebookLM audio generation (unofficial notebooklm-py)
   │  output/episode-YYYY-MM-DD.mp3
   ▼
source emails → Gmail Trash (30-day recoverable)
   ▼
Gmail send → your inbox (attached; Drive link if > 20 MB)
```

State (processed message IDs + episode lifecycle) lives in `state.db`
(SQLite), so re-runs are safe at any point.

## Features

- **Hands-free** — one scheduled entry; the pipeline is resumable and
  idempotent, so a retry never redoes finished work.
- **Free, deterministic cleaning** — regex/bs4 rules strip footers,
  unsubscribe links and tracking pixels without spending a single LLM token.
- **Real podcast audio** — NotebookLM narrates from one merged document
  instead of 30 raw sources.
- **Delivered to your inbox** — episodes attach directly under 20 MB; larger
  ones upload as an owner-only Drive link.
- **Self-cleaning label** — once a week's emails are safely ingested they move
  to Gmail Trash (recoverable for 30 days; `TRASH_INGESTED=0` opts out).
- **Loud failures** — every error path emails you the step name and the
  likely fix; logs land in `logs/run.log`.

## Requirements

- Python 3.10+
- A Gmail account and a free Google Cloud project (Gmail + Drive APIs)
- A [NotebookLM](https://notebooklm.google.com/) account — audio uses the
  unofficial [`notebooklm-py`](https://pypi.org/project/notebooklm-py/) client,
  which needs a re-login every few weeks

## Project layout

```
src/newsletter_podcast/
  config.py       env-driven settings, OAuth scopes
  gmail_fetch.py  OAuth flow, label lookup, fetching, trash-after-ingest
  clean.py        HTML → noise-stripped text; weekly merge
  state.py        SQLite: processed IDs (week-scoped) + episode lifecycle
  storage.py      Drive upload/prune for oversized episodes
  notify.py       episode email + failure alerts
  audio/          AudioGenerator protocol (notebooklm backend + dummy for tests)
  main.py         CLI: auth, notebooklm-login, fetch, generate, send, run, status
tests/            32 unit tests (cleaning rules, state machine, trash, week keys, auth)
scripts/          run-weekly.sh — launchd entry point (surfaces pre-Python failures)
                  reauth.sh     — weekly Google re-auth + pipeline resume
com.andrew.newsletterpodcast.plist   launchd job (Sun 18:00 + Mon catch-ups)
```

Full design rationale — including why NotebookLM over the alternatives — is in
[SystemDesign.md](SystemDesign.md).

## Quickstart

### 1. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

### 2. Google Cloud (Gmail + Drive APIs)

1. [console.cloud.google.com](https://console.cloud.google.com) → create project `newsletter-podcast`.
2. **APIs & Services → Library** → enable **Gmail API** and **Google Drive API**.
3. **APIs & Services → OAuth consent screen** → External → fill in a name, your
   support email, and add your Google account under **Test users**.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID →
   Desktop app** → download the JSON and save it as `credentials.json` in this
   directory (it is git-ignored).

The flow requests three scopes: `gmail.modify` (fetch newsletters and move
processed ones to Trash), `gmail.send` (episodes + failure alerts), and
`drive.file` (only files this app creates — used solely for episodes over the
20 MB attachment threshold).

### 3. Authenticate

```bash
.venv/bin/newsletter-podcast auth             # browser opens; approve once
.venv/bin/newsletter-podcast notebooklm-login # browser cookie login to NotebookLM
```

### 4. Gmail label

Create a Gmail label named `Newsletter` (or set `GMAIL_LABEL` to your own) and
file your newsletters under it. The pipeline reads only that label.

### 5. Dry-run before trusting it

```bash
.venv/bin/newsletter-podcast fetch --dry-run
```

Prints the cleaned, merged document without saving or marking anything
processed. Inspect it: footers/unsubscribes should be gone, stories intact.

## Usage

```bash
.venv/bin/newsletter-podcast run      # fetch → generate → send (resumable)
.venv/bin/newsletter-podcast status   # episode states + processed count
.venv/bin/newsletter-podcast fetch    # step-by-step instead of `run`
.venv/bin/newsletter-podcast generate
.venv/bin/newsletter-podcast send
```

`run` resumes wherever the week stopped: if generation failed yesterday, today
it skips fetch (doc already saved) and retries generation only.

Once an episode's audio has been generated, that week's source emails are
**moved to Gmail Trash** (recoverable for 30 days; Gmail purges them after
that). Set `TRASH_INGESTED=0` to keep them instead. Trash failures only log a
warning — your content is already in the episode by then, and any missed
messages are retried at the send step.

## Weekly automation (macOS launchd)

The bundled job runs at **18:00 Sunday**, then makes catch-up attempts at
**09:00 and 20:00 Monday** plus **once per login** (`RunAtLoad`). It is
spawn-and-die — no resident process. Install it with:

```bash
mkdir -p logs
cp com.andrew.newsletterpodcast.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.andrew.newsletterpodcast.plist
# unload: launchctl bootout gui/$(id -u)/com.andrew.newsletterpodcast
```

The job runs `scripts/run-weekly.sh`, not the console script directly. The
pipeline's own failure email can only fire once Python has started, so the
wrapper reports the failures that happen *before* that: a missing venv, a broken
shebang, a permission error. Without it those die silently — no `run.log` entry
and no email.

> **Keep the checkout out of `~/Desktop`, `~/Documents` and `~/Downloads`.**
> macOS privacy protection (TCC) covers those folders, and a launchd-spawned
> process gets `EPERM` on everything inside them without any prompt, because
> there is nobody to prompt. The interpreter then dies while reading
> `.venv/pyvenv.cfg`:
>
> ```
> Fatal Python error: init_import_site: Failed to import the site module
> PermissionError: [Errno 1] Operation not permitted: .venv/pyvenv.cfg
> ```
>
> Your interactive shell keeps working (Terminal/VS Code hold the grant), which
> makes this look like "the schedule stopped". If you must keep it in a protected
> folder, grant **Full Disk Access** to the Python interpreter the venv points at
> (System Settings → Privacy & Security → Full Disk Access); re-grant it after
> every Homebrew Python upgrade.

A `StartCalendarInterval` missed because the Mac was **off** is not replayed on
the next boot — launchd just reschedules the next occurrence — so the extra
triggers and `RunAtLoad` exist to catch up. All of them are safe to run
repeatedly: a run for a week that is already `sent` exits immediately.

The one thing the schedule cannot fix by itself is the Google credential: while
the OAuth consent screen stays in *Testing*, the refresh token dies after 7 days,
so re-arm it weekly with `scripts/reauth.sh` (see Troubleshooting).

Adjust the `Label`, paths and times inside the plist if you fork it, and keep
your system timezone set to what you want "Sunday 18:00" to mean. On Linux,
an equivalent crontab entry works the same way:

```cron
0 18 * * 0  cd /path/to/podcast && scripts/run-weekly.sh >> logs/cron.log 2>&1
0 9  * * 1  cd /path/to/podcast && scripts/run-weekly.sh >> logs/cron.log 2>&1
0 20 * * 1  cd /path/to/podcast && scripts/run-weekly.sh >> logs/cron.log 2>&1
```

## Troubleshooting (expect a weekly re-auth while in Testing mode)

| Symptom                                                      | Cause                             | Fix                                                                  |
| ------------------------------------------------------------ | --------------------------------- | -------------------------------------------------------------------- |
| Failure email at `generate` mentioning login/storage/cookies | NotebookLM cookies expired        | `.venv/bin/notebooklm login` then `.venv/bin/newsletter-podcast run` |
| `Not authenticated yet` or `token is no longer usable`       | Google refresh token revoked/aged | `rm token.json && .venv/bin/newsletter-podcast auth`                 |
| `invalid_grant: Token has been expired or revoked` at `fetch` | OAuth app still in **Testing** — Google kills those refresh tokens after 7 days (see below) | `scripts/reauth.sh` |
| `Gmail label 'Newsletter' not found`                          | Label renamed                     | create/rename label or set `GMAIL_LABEL`                             |

**Weekly re-auth (consent screen in Testing).** While the OAuth consent screen's
publishing status is *Testing*, Google expires the refresh token after **7 days**,
so the pipeline needs re-consenting roughly weekly. One command does both halves:

```bash
scripts/reauth.sh      # re-auth (browser consent), then resume the pipeline
```

It is safe at any time: `run` is idempotent, so a week that is already sent is a
no-op, and re-authing mid-week never produces a second episode for the same week.
In practice, re-auth on a **Saturday or Monday–Wednesday**: the resume step is
then a no-op and the next Sunday 18:00 is still inside the 7-day window. (A
Sunday-*morning* re-auth would build that week's episode from whatever has
arrived so far, so avoid that unless you want it early.)
The failure cannot be *emailed* when the Gmail credential is what broke, so
`scripts/run-weekly.sh` treats a `token.json` older than 6 days as the likely
cause and raises a desktop notification: "Google token likely expired - run
scripts/reauth.sh".

If the weekly ritual gets old, publish the app instead: Google Cloud console →
APIs & Services → OAuth consent screen → **Publish app**. Leaving it unverified is
fine — you click through "Google hasn't verified this app → Advanced → Go to …"
once per re-auth — and refresh tokens then stop expiring after 7 days. The
app-domain fields it asks for (home page, privacy policy, authorised domains) can
be left blank for a single-user app; they only matter for verification.

You also get a failure email automatically on any error (it includes the step
name and this fix). Failures that happen *before* Python starts (missing venv,
permission errors) can't email — `scripts/run-weekly.sh` logs those to
`logs/launchd.err.log` and raises a notification instead. Logs: `logs/run.log`
(every run), `logs/launchd.*.log` (stdout/stderr of scheduled runs).

## Configuration (environment variables / `.env`)

| Variable             | Default               | Purpose                                      |
| -------------------- | --------------------- | -------------------------------------------- |
| `GMAIL_LABEL`        | `Newsletter`          | label to read                                |
| `FETCH_DAYS`         | `7`                   | look-back window for messages                |
| `NOTIFY_EMAIL`       | authenticated account | episode/failure recipient                    |
| `AUDIO_BACKEND`      | `notebooklm`          | `dummy` = write placeholder file, no network |
| `AUDIO_MAX_MINUTES`  | `15`                  | length hint in NotebookLM instructions       |
| `RETENTION_EPISODES` | `10`                  | Drive files kept after pruning               |
| `TRASH_INGESTED`     | `1`                   | `0` keeps ingested source emails              |

## Design notes

- **One merged document per week**, not 30 separate sources — respects
  NotebookLM's per-notebook size limits and keeps one coherent episode.
- **Cleaning is deterministic** (regex/bs4 rules, zero token cost). `trafilatura`
  does HTML→text; headings are demoted first because it otherwise discards the
  `<h1>` as page metadata.
- **The audio backend is a swappable `AudioGenerator`.** If notebooklm-py breaks
  (it is unofficial), implement `generate(doc_path, week_of) -> Path` and set
  `AUDIO_BACKEND`; the rest of the pipeline is untouched. Fallback options from
  the design doc: Open Notebook, or DIY LLM + TTS.
- **Episodes ≤ 20 MB attach directly**; bigger ones upload to Drive (owner-only
  link) and older ones are pruned beyond `RETENTION_EPISODES`.
- **Idempotency**: processed message IDs are recorded the moment the document is
  saved; a week's episode is keyed by its Sunday, so Sunday's run and Monday's
  retry share one episode row.

## Development

```bash
.venv/bin/python -m pytest -q   # 32 tests
.venv/bin/ruff check src tests  # lint
```

Add cleaning rules in `clean.py` (`_FOOTER_TEXT_PATTERNS`, `_NOISE_TOKENS`,
anchor rules) with a fixture + test in `tests/`.
