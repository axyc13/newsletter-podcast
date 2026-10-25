# Weekly Newsletter Podcast: System Design

Oct 25, 2026 · @andrew

## Summary

A weekly script reads your Gmail "newsletters" label, cleans and merges the emails, generates a podcast, and emails it to you. Start with NotebookLM through the unofficial `notebooklm-py` library, keep the audio step swappable, and fall back to Open Notebook or a self-built script if NotebookLM breaks. Run it every Sunday evening (NZ time) so a new episode is waiting on Monday. No browser extension or full app is needed: a script on a schedule is far less work to build and maintain.

## The pipeline

&#91;embedded content: weekly pipeline · 5 steps, 1 decision\]

Each weekly run fetches the label, cleans and merges the emails, generates the audio (shouldn't be over 15mins), then emails the MP3. Any step that fails emails you an error instead.

## Choosing the audio generator

NotebookLM gives the nicest-sounding podcast at no cost; Open Notebook is the more stable, self-owned option. Google has no official consumer API that I could confirm, so NotebookLM automation relies on the unofficial [notebooklm-py](https://pypi.org/project/notebooklm-py/0.8.0a1/) library. It covers audio overviews in several formats and lengths, in 50+ languages, and can be scheduled to produce a fresh briefing. Its repo notes that Google rebranded NotebookLM to "Gemini Notebook" in July 2026. [Open Notebook](https://www.open-notebook.ai) is an MIT-licensed open-source app whose podcast generator has customizable voices, speakers and episodes. I could not confirm its API or TTS provider details from its website, so check its [podcast docs](https://www.open-notebook.ai/features/podcast) before committing.

|                 | NotebookLM                                                      | Open Notebook                             | DIY script + TTS              |
| --------------- | --------------------------------------------------------------- | ----------------------------------------- | ----------------------------- |
| Audio quality   | Natural two-host sound; its signature                           | Depends on the LLM and TTS you plug in    | Depends on the TTS vendor     |
| Cost to you     | Free                                                            | Per-use LLM and TTS tokens                | Per-use LLM and TTS tokens    |
| Automation risk | Unofficial API; cookie logins expire; can break without warning | You host it, so nothing changes under you | Official APIs, so most stable |
| Setup           | Minimal                                                         | You run and maintain a server             | Most code to write            |
| Control         | Limited                                                         | Choice of speakers, voices, models        | Full                          |
| Privacy         | Content goes to Google                                          | Depends on providers you choose           | Depends on vendors you choose |

Recommendation: build with NotebookLM first, and keep audio generation as one function so you can swap it. NotebookLM audio takes roughly 10-20 minutes to generate and can fail on rate limits.

## Skipping the LLM pass

No LLM pre-processing step: the audio generator does the summarizing, so a pre-pass would pay for the same work twice. The cleaning step stays, because it is plain code and costs no tokens. It does four things:

- Converts each email's HTML to plain text or Markdown.
- Strips unsubscribe footers, tracking links, read more links, "view in browser" lines and ads.
- Merges the week's emails into one clean document.
- Records processed message IDs so nothing is repeated.

## Emailing the podcast

Email the episode to yourself

## Tech stack

Python, with a cron job on an always-on machine.

| Layer              | Choice                                                 | Why                                                                                                                                           |
| ------------------ | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Language           | Python                                                 | Best Gmail, HTML-parsing and NotebookLM tooling                                                                                               |
| Gmail access       | `google-api-python-client`, read-only OAuth            | Authorize once, then runs unattended with a stored refresh token                                                                              |
| Cleaning           | `beautifulsoup4` or `trafilatura`                      | Deterministic, no token cost                                                                                                                  |
| Audio              | `notebooklm-py` (fallback: Open Notebook or DIY + TTS) | Quickest route to a working podcast                                                                                                           |
| Runtime            | Cron on a small VPS or home machine                    | NotebookLM logs in with browser cookies that need occasional re-login; a cloud-only runner such as GitHub Actions suits the DIY option better |
| State              | SQLite file                                            | Remembers processed emails and episode status                                                                                                 |
| Large-file storage | Google Drive or Cloudflare R2                          | Hosts MP3s too big to attach                                                                                                                  |

## Day-to-day operation

The job runs by itself every Sunday evening (NZ time) and you only listen. A new episode is in your inbox for Monday's commute.

- **Your involvement:** none, apart from re-authenticating NotebookLM every few weeks.
- **Failure alerts:** if any step fails, the script emails you the error (for example, "this week's episode failed, retrying Monday"). Silent failure is the main reason projects like this get abandoned.
- **Housekeeping:** use a new notebook each week and delete old ones; keep the last 8-12 episodes in storage.

## Risks and things to watch

- **Unofficial API:** Google can change NotebookLM without warning, and generation can fail on rate limits. Keep the audio step swappable.
- **Source size limits:** NotebookLM caps how much you can add per notebook, so one merged weekly document is safer than 30 separate emails.
- **Link-only and paywalled newsletters:** some emails are teasers pointing to articles. Fetching the linked articles adds complexity, so leave it out of v1.
- **Privacy:** newsletter content goes to Google, and to any LLM or TTS vendor you add later. Keep any hosted file links private.

## Next steps and sources

Build the first half first: Gmail fetch, cleaning, and one merged weekly document. Check that output, then add the NotebookLM audio step and the email step.

Sources:

- [notebooklm-py on PyPI](https://pypi.org/project/notebooklm-py/0.8.0a1/)
- [notebooklm-py on GitHub](https://github.com/rog77/notebooklm-py)
- [NotebookLM automation skill notes](https://skills.sh/aravhawk/claude-code-utils/notebooklm)
- [Open Notebook](https://www.open-notebook.ai)
- [Open Notebook features](https://www.open-notebook.ai/features)
