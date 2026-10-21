# Station C5 · Your tools

The core now runs: WhatsApp in, the gate, judgments, the board on the phone, and person notes when C4 ran. Everything else is optional, and every optional block costs something: model usage, a place the owner's data travels to, words written into the vault, or one more thing to look after. This station asks about the tools the owner already uses, one at a time, and decides which optional stations run and in which order. It changes no config itself; each optional station does its own.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="$LABEL.intake"
PORT="<board.port from the progress file header>"
```

## Ask

One question at a time, each with its cost said plainly, and the owner's answer logged with the reason. Questions 1 to 5 are the five interfaces: ask them in the words of `references/interfaces.md`, "Choosing for the owner", whose table maps each common tool to an implementation, and whose two warnings (a phone number on every CSV row; a secret inside a command or a link travels into the backup) are said while choosing. Read `references/defaults-and-why.md`, defaults 10 and 13, before questions 1 and 2.

1. **When someone pays you, books, or gets served, where is that written down?** Station O1, facts source: `csv` (a local CSV, or a published CSV link: Google Sheets, and exports from Airtable, Notion, GoHighLevel or Excel), `lark`, or `none`. Cost: the nightly facts job calls no model, but a paying customer with a chat and no note gets queued for a note run (on `dossier.model`, up to `dossier.nightly_cap` at a time, 8 by default; station O1 says when the first ones start), the payment record starts deciding `won` and beyond (default 10), and with a link or Lark the source is read over the network every night.
2. **Besides WhatsApp, where do you talk with customers or your team?** Station O2, second message source: `lark`, an adapter of the owner's own for another app, or `none`. Cost: at most one model call a night, only when someone other than the owner wrote; one recent line per person into notes that carry that source's id key, and captures into the intake buffer; yesterday's whitelisted chats read through that app's API (default 13).
3. **Which calendar do you actually look at during the day?** Station O3, calendar destination: `ics` (the board makes a standard calendar file that Apple Calendar, Google Calendar and Outlook import; nothing leaves the Mac until the owner imports it), `lark`, or `none` (the button stays hidden). No model usage.
4. **When something breaks, where should the alert reach you?** Station O4, push outlet: `telegram` (a bot of the owner's own; alert text passes through Telegram's servers), `command` (any command that reads the text on stdin, such as a macOS notification or an ntfy call), or `none` (the log only). No model usage. What each alert carries, by `notify.mode`: `references/privacy.md`.
5. **Which folder should get a copy of the daemon's state every night?** Station O5, nightly backup: a folder in iCloud Drive, Google Drive or Dropbox, or an external disk. Cost: an unencrypted copy of the state database, drafts, summaries and three versions of every person note in that folder; a cloud folder puts all of it with that provider; seven nights are kept.
6. **When should the morning digest arrive, and should it be pushed?** Station O6. The digest is already on (08:00, written to `live/digest/`, pushed once an outlet exists); it is the one scheduled push, and on a quiet day it still arrives, which is how silence stays trustworthy. No model usage.
7. **Should things people ask of you be caught into an intake buffer in your vault**, and placed by saying "intake"? Station O7, which needs the vault and adds command-base patches 2 and 3. Cost: up to two captures a run, each with a verbatim quote of up to 200 characters, written into the vault; an optional evening push makes one model call on nights when entries are open.
8. **Should drafts learn from what you actually send?** Station O8, voice loop, already on. Cost: pairs of the brain's draft and the text the owner sent, kept in `live/voice-pairs.jsonl` (the owner's words only, no names), and a few of them added to every judgment's prompt.
9. **Should the conversations that were never judged get a starting summary now?** Station O9, summary backfill, run once when started. Cost: the heaviest single use of the model in the kit: a month of every chat with at least ten messages that the gate passes, ten chats per call (with a gate that passes no chat there is nothing to backfill, unless `backfill.respect_gate` is re-ruled). In return, the brain has a memory of each chat from its first run instead of building one run by run.
10. **Is there a second Mac that could take over if this one dies?** Station O10, standby machine: a written cold start and one rehearsal. ⛔ Never two daemons at once.
11. **An observation week, then the acceptance walk.** Station O11, always offered and always last: every feature ticked with evidence, and every default ruled during the install checked as a decision note (default 14).

For a tool the kit does not implement, say so in one sentence, show its contract in `references/interfaces.md`, and log `adapter wanted: <tool> (<interface>)`. Offer to write the adapter with the owner in a later session, as a file of their own in `live/adapters/` that updates never touch; never promise it in this one.

## Do

### 1 · Write the stations into the progress file

Append the chosen stations under `## Stations`, after C5, in this order, O11 last:

```markdown
- [ ] O1 facts source (csv)
- [ ] O3 calendar (ics)
- [ ] O4 push outlet (telegram)
- [ ] O5 nightly backup
- [ ] O6 morning digest
- [ ] O11 observation and acceptance
```

The example is illustrative; write the owner's own picks. The station files are `install/O1-facts-source.md`, `O2-second-message-source.md`, `O3-calendar.md`, `O4-push-outlet.md`, `O5-nightly-backup.md`, `O6-morning-digest.md`, `O7-intake.md`, `O8-voice-loop.md`, `O9-summary-backfill.md`, `O10-standby-machine.md` and `O11-observation-and-acceptance.md`. Keep the numeric order; O11 is always last. Writes: the progress file only.

A block the owner declines gets one log line, `skipped: O<n> <name> because <reason>`, and no station line. O6 and O8 are the exception: they are on by default, so declining one still gets its station line, marked `(switch off)`, because switching it off is that station's work.

### 2 · Rulings

Defaults 10 and 13 are ruled here, one decision note each, whatever the answers: where the payment record lives (or that there is none, so stages from `won` on are set by hand on the board), and where else conversations happen. Default 13's note is written also when the owner has no second source, and then says WhatsApp is the only place messages come from; with no second source, O2 is skipped and nothing else lands. The other defaults land at their own stations. How each note lands: `install/00-start-here.md`, "Rulings".

### 3 · Close the core

Tell the owner in two or three lines what runs now (the daemon judging new messages, the board on their phone, person notes when C4 ran) and name the first optional station on the list. Ask whether to continue now.

## Verify

1. **Every block was decided.** Pass: eleven decisions in the log, each either a station line under `## Stations` or a `skipped:` line with a reason.
2. **The order holds.** Pass: every optional line after C5, O11 last.
3. **C5 changed no config.** Pass: the config's last change is from before this station began.
   ```sh
   HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
   stat -f '%Sm' "$LIVE/config.json"
   ```
4. **Gaps are written down.** Pass: one `adapter wanted:` line per tool the kit does not implement, naming its interface, or none needed.

## Record

Under `### C5 your tools` in `<home>/INSTALL-PROGRESS.md`:

- each of the eleven answers, with the implementation picked and the owner's reason;
- the `adapter wanted:` lines;
- `ruled:` defaults 10 and 13, two notes, the second one also when WhatsApp is the only message source;
- the time the core was closed.

Tick `- [x] C5 your tools` when the four checks pass. Next: the first optional line under `## Stations`.
