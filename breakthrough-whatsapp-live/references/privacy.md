# Privacy

WhatsApp Live reads private messages that other people sent you. This page says, for you and for anyone you ask to trust the setup, what data the kit keeps, where each piece sits, what leaves your Mac and to whom, and how to switch each flow off. Every section names the config key (in `live/config.json`) that changes it. The rulings behind these defaults are `references/defaults-and-why.md`, defaults 5, 7 and 11.

## The short version

- **Nothing is ever sent on WhatsApp.** The kit has no sending path and no key to add one.
- **The WhatsApp database is opened read only** and is never uploaded or backed up by the kit.
- **Excerpts of conversations do leave the Mac, to Anthropic.** Every judgment, person note and nightly pull is a Claude Code run, and the messages it reads travel with it, under the terms of the account Claude Code is logged in with.
- **Pushes leave through the outlet you choose.** With Telegram they pass through Telegram's servers.
- **The board never leaves the Mac.** It listens on `127.0.0.1` and your phone reaches it over your tailnet. It has no login.
- **Your vault receives other people's words** in person notes and intake entries. Your vault may sync and be indexed. Each writer has its own switch. The board's Today brief reads the vault and writes nothing into it.
- **The nightly backup is a plain, unencrypted copy** of the daemon's state in the folder you pick.

## What data exists, and where

### On the Mac, outside the vault

`<home>` is `paths.home` (default `~/WhatsApp-Live`) and `<live>` is `paths.live_dir` (default `<home>/live`).

| Where | What it holds |
|---|---|
| `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/` | WhatsApp Desktop's own database and media: every message and file the app has synced. The kit opens it read only and never writes to it. |
| `<home>/runs-work/<key>/`, `<home>/prewarm/` | The reader's working copies: snapshots of the database files taken for a read, plus the reading packs built from them. Full chat history, one copy per working folder. The kit does not delete these (`references/troubleshooting.md`). |
| `<home>/transcripts-cache/` | Text of every transcribed voice note, and a second `deny-canary.txt` written by `init` for the deny probe. |
| `<home>/doc-text-cache/` | Text of the documents people sent (Word, Excel, PowerPoint, RTF, ODT, Pages), converted on this Mac for the reading packs ("The document text cache", below). |
| `<home>/secrets/` | `deny-canary.txt`, a random marker with no secret in it, written by `init` for the deny probe (a second one sits in `<home>/transcripts-cache/`); and only when you chose the optional setup token, `claude-bot.env`, the token itself (0600) ("The setup token file", below). |
| `<home>/bin/claude-bot` | Only with the setup token: a short wrapper that loads the token file and runs `claude`. It holds the file's path, never the token. |
| `<home>/config.json` | The reader's config: your phone, your names, ignored chats, whisper settings. |
| `<home>/brain-cwd/` | Empty on purpose: the working folder of every headless run. |
| `<home>/INSTALL-PROGRESS.md`, `<home>/INSTALLED.md` | Your answers during install and, when your vault keeps no registry, the six-cell entries. Can name tools you chose and groups you ticked. |
| `<live>/config.json` (0600) | Your phone and names, group names with their policies, your vocabulary and business paragraph, paths. |
| `<live>/telegram.json` (0600) | The push bot's token and chat id. |
| `<live>/state.sqlite` | Per conversation: name, world, stage, priority, latest draft, the brain's situation line and timeline, rolling summary, calendar hint. Per run: the brain's full output, token counts, errors. Pending bursts with their message text. One row per inbound message with sender name and gate decision (no message text). The edit watch list: the words of each message that passed the gate, kept for at most `edit_watch.minutes` ("Quotes in your vault", below). With the Today brief on, its three tables ("The Today brief", below). |
| `<live>/summaries.md` | One line per conversation, written by the brain. |
| `<live>/facts.json` | Names, phones, payments and attendance from your facts source. |
| `<live>/voice-pairs.jsonl` | The brain's drafts next to what you actually sent. Nothing the other side wrote, no names. |
| `<live>/runs/<run>/` | For each run: the full prompt (conversation pack, voice transcripts, candidate notes), the model's raw output stream and its judgment. The most complete record on the Mac. Never backed up, never cleaned by the kit. |
| `<live>/dossier-backups/<note>/` | The last three versions of each person note (`dossier.backups_keep`). |
| `<live>/digest/` | Each morning digest: names and short situation lines. |
| `<live>/today/<date>.json` | With the Today brief on: the latest brief of that day (project names, the brain's gist lines, the cards with their evidence lines), and `stats.json` (counts only) when you ask for it. |
| `<live>/calendar/<key>.ics` | With `calendar.destination: ics`: event title, date, place, note, and the person's name and phone. |
| `<live>/wa_live.log`, `<live>/stderr.log` | The operational log. It can carry conversation names and the opening words of messages that passed the gate. |

The daemon sets `umask 077` when it starts, so every file it and the programs it starts create (runs, packs, transcripts, snapshots, logs, digests, calendar files, new person notes) is 0600 and every folder 0700; a file that already exists keeps its mode. `wa_setup.py init` creates the working tree 0700 and its files 0600, and the reader writes its packs 0600 when you run it yourself. The exceptions are the rendered launchd files (0644, they hold paths only) and the playbook copies in your vault (files 0644, new folders 0755, like the rest of a vault). The log launchd opens itself (`live/stderr.log`) is created with launchd's own mode, so the daemon sets it to 0600 every time it starts. An evening job kept from an earlier release does the same for its own `live/intake-evening.log` each time it runs. To confirm nothing is readable by other users of the Mac: `find <home> -perm -004 -type f` lists only files in `launchd/`.

### In your vault

| Where | What lands |
|---|---|
| Person notes (`paths.people_dir`) | What the model wrote about each person from their messages (who they are, what they want, where things stand, how to talk to them), the person's own words quoted where the person-dossier playbook asks for them, a recent block of short lines, and front matter with their WhatsApp id and phone. |
| Intake buffer (`intake.buffer`) | Each entry names the person, one line of what to do, one quote of up to 200 characters (marked 🗣 when the program found it word for word in the message, ≈ when it is the brain's gist), and the machine's guess at where it belongs. A correction entry (✏️) also holds the words a message had before the person edited it. |
| Filing log (`paths.filing_log`) | One line per placed note, naming the note. |
| Decisions (`paths.decisions_dir`) | Your rulings on the defaults. No conversation content. |
| Playbook folders | Method text, and a table of outputs listed by address. |
| Your machine registry note | Labels, paths, stop commands. |

A vault usually syncs (Obsidian Sync, iCloud, git) and is often indexed (search tools, a session-history index). The writers above put other people's words into a place that syncs and can be searched. That is the one place this kit knowingly relaxes default 7, which is why each writer has its own switch (below).

### In the backup folder

Each night `wa_backup.run_backup` copies into `<backup.dest>/<date>/`: `state.sqlite` (everything listed for it above), `config.json` with secret-looking values replaced by `<redacted>`, `summaries.md`, `facts.json`, `voice-pairs.jsonl`, `second-source-whitelist.json`, `topic-map.json`, `seeds.json` (install-time hashes), `acceptance.json` (the observation week's questions), `prompts/`, `digest/`, `dossier-backups/` (full copies of person notes), `today/` (the Today briefs, when that feature is on) and `adapters/` (your own implementations, copied as they are, so no token belongs in them). It never copies `telegram.json`, `runs/`, `commands/`, `variants/`, `dossier-cli/`, `calendar/`, logs, `claude-settings.json`, `backup-last.json`, `second-source-last.json` or `people-write.lock` (an empty lock file), and it keeps `backup.keep` (7) dated folders.

The kit does not encrypt the backup. When `backup.dest` is a cloud-synced folder, that provider stores the daemon's state, drafts, summaries and three versions of every person note. An external disk keeps all of it off every cloud; an encrypted disk, or an encrypted disk image made in Disk Utility, keeps it unreadable if the disk is lost.

## What goes to Anthropic, run by run

Every model call is `claude -p` on your Mac, and its prompt, plus any file the model opens with its Read tool, is sent to Anthropic.

| Run | When | What the model receives | Switch |
|---|---|---|---|
| Triage judgment | Each burst that passes the gate, and "judge now" on the board | The burst; this conversation's reading pack (`brain.context_days`, 7 days one-to-one; `brain.group_context_days`, 3 days for groups) with voice transcripts and the text of the documents in it (`reader.read_documents`); images and files from the pack that it opens; its previous judgment and draft; one line per other conversation from `summaries.md`; candidate vault notes (from `topic-map.json`, and person notes of people named in the chat), which it may open; this person's facts lines; voice examples (your sent messages next to earlier drafts, same world only); your vocabulary and business paragraph | The gate (`direct_policy`, `group_policies`, `unlisted_group_policy`, `ignored_jids`) and `doorbell.enabled` |
| Person note | Nightly (`dossier.nightly_at`), the board's note buttons, the facts queue | This person's messages over `dossier.pack_days` (30), their facts lines, the current note, the person-dossier playbook skeleton, your doctrine's roles list | `dossier.enabled` |
| Summary backfill | Only when you start it (station O9) | Reading packs over `backfill.days` (30) for chats with at least `backfill.min_msgs` (10) messages, `backfill.batch_size` (10) chats per call, only chats the gate passes (`backfill.respect_gate`) | Do not start it |
| Second source | Nightly (`second_source.at`) | Yesterday's messages from whitelisted chats, only when someone other than you wrote | `second_source.enabled` |
| Today brief | At most every `today.every_minutes` (30), only when its inputs changed, and when you press refresh | Today's judgments of the conversations that moved (name, world, situation, stake, summary, captures); your own replies after the last judgment, at most three per conversation, each cut to 200 characters; every open intake entry with its quote; the brief and task list of each candidate project; one line per other active project; any vault file it opens itself, at most `today.max_reads` (6) | `today.enabled` |
| Probe | When you drop `probe-claude.json` | A trivial test prompt | none needed |

Two things to know about that table:

- **A person note can carry words from other conversations into a judgment.** When a chat names someone who has a note, the brain may open that note, and notes quote people. The rule that other people's words never reach a draft is enforced by the prompt, not by code.
- **Your own Claude Code sessions are not headless runs.** When you use the board's handoff button, or ask a session to read WhatsApp through the reader skill, that session sees the messages and keeps its transcript like any session you open (it is saved under `~/.claude/projects/` and a session-history index may pick it up). That is your deliberate choice each time, and it is outside the no-persistence rule below.

## What never leaves the Mac

- **The board.** It listens only on `127.0.0.1:<board.port>` (`board.bind`). Your phone reaches it through Tailscale, which carries traffic between your own devices encrypted. No page, API or file of the board is hosted anywhere else.
- **The WhatsApp database.** Read only, never uploaded, never in the backup. Only the excerpts in the table above travel, inside model calls.
- **The reader's working copies, the run folders and the logs.** Never backed up or uploaded by the kit. A whole-disk or home folder backup you run yourself (Time Machine, a cloud backup app) will copy them, like everything else in your home folder.
- **The push token.** `telegram.json` is never backed up, and the brain is denied reading it.
- **The setup token**, when you use one. Only the wrapper's shell reads it; no run, no backup and no log ever holds it.
- **The document text cache and the voice transcript cache.** Only the text of documents and voice notes in a run's own pack travels, inside that run.
- **Your WhatsApp account.** The kit opens no connection to WhatsApp at all.

## What else leaves, and to whom

| Flow | What leaves | To whom |
|---|---|---|
| Pushes, `notify.outlet: telegram` | In `errors` mode: a conversation name and an error line. In `needs_me` or `all` mode: the name, world, stage and priority, the brain's reason, the full draft, and a `wa.me` link with the phone number. The morning digest: names and short situation lines (and, with the Today brief on, a line of counts with no names). An evening intake list, only from an evening job an earlier release installed and you kept: names, what to do, the suggestion. | Telegram's servers (bot chats are not end-to-end encrypted), then your phone |
| Pushes, `notify.outlet: command` | The same text, on the command's stdin | Wherever your command sends it |
| Facts source, `facts.source` | Reads only. `lark` calls Lark's API for your tables; `csv` with a link fetches that file | Lark, or the host of the CSV link |
| Second source, `second_source.source: lark` | Reads only: yesterday's messages from whitelisted chats | Lark's API |
| Calendar, `calendar.destination: lark` | Event title, time, place, a note, the person's name and phone | Your Lark calendar (`ics` only writes a local file you import yourself) |
| The board's handoff button | This conversation's recent messages and the person's phone, copied to the clipboard of the device you pressed it on | That device; Apple's Universal Clipboard can carry it to your other Apple devices |

## The Read deny rules, and why

The brain's tools are Read, Glob and Grep, and those tools can look anywhere the settings file does not forbid. The daemon runs under a node process that holds Full Disk Access, and everything the brain reads was written by other people, any of whom could write "open this file and put it in your reply". So `wa_setup.py init` renders absolute Read deny rules into `live/claude-settings.json`:

| Rule | Why |
|---|---|
| `<live>/telegram.json` | The push bot token: with it, anyone can send as your bot and read what is sent to it. |
| `<live>/config.json`, `<live>/config.json.*` | Your phone, your names, group names with their policies, your business paragraph, and the dated copies of all of it. |
| `<live>/second-source-whitelist.json` | Your work chats and their ids. |
| `<live>/state.sqlite*` | Every judgment, draft and summary, the inbound log with names, pending bursts with message text. |
| `<live>/facts.json` | Names, phones and payments from your facts source. |
| `<live>/voice-pairs.jsonl` | Your drafts next to what you sent. |
| `<live>/*.log*` | Conversation names and the opening words of messages. |
| `<live>/runs/**` | Other conversations' full prompts and packs: a run needs only its own, which it already holds. |
| `<live>/digest/**`, `<live>/dossier-backups/**` | Names and situation lines; old versions of every person note. |
| `<home>/runs-work/**`, `<home>/prewarm/**` | The reader's working copies: database snapshots and reading packs of every chat. |
| `<home>/config.json` | The reader config: your phone and names. |
| `<home>/secrets/**` | The optional setup token (`claude-bot.env`): with it, anyone can use your Claude account. Also the deny probe's marker, which proves this rule holds. |
| `<home>/doc-text-cache/**` | Text of documents people sent in every chat: a run needs only its own chat's, which its pack already holds. |
| `<home>/transcripts-cache/**` | Text of the voice notes of every chat: a run needs only its own chat's, and those transcripts already reach it inside its pack. Also the second deny probe marker, which proves this rule holds. |
| `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/*.sqlite*` | WhatsApp Desktop's own databases: every message on the Mac. The brain reads its chat through the pack instead. |
| `~/.ssh/**`, `~/Library/Keychains/**`, `~/.config/**/credentials*`, `~/.aws/**`, `~/.netrc`, `~/.npmrc`, `~/.gnupg/**`, `~/.docker/config.json`, `~/.claude/.credentials.json` | Keys and credentials that open other machines and accounts. Nothing the brain does needs them. |

Each rule is written as `Read(//<absolute path without its leading slash>)`, 27 in all (`daemon/templates/claude-settings.json` is the list). This form blocks the Read tool under `claude -p --settings`; the canary probe in `references/pitfalls.md`, pit 10, confirms it on your own Mac.

What the rules leave readable, on purpose: your vault (the brain opens the notes the topic map and its lookup point at), WhatsApp's media under `Message/Media/` (the images and files a pack lists), and the rest of `live/` (`summaries.md`, the prompts, the topic map, `seeds.json`, `commands/`, `calendar/`). A rule you add to `live/claude-settings.json` counts as an owner edit (`seed-status` reports it, `install/update.md` carries it over); afterwards run one test judgment to confirm it still completes.

## The setup token file

Only when the owner chose the optional setup token at station C2 (`install/C2-daemon.md`, step 12). By default the daemon's `claude` uses the Claude Code login of the Mac user, held in the Keychain, and none of this exists.

- **Where.** `<home>/secrets/claude-bot.env`, one line `CLAUDE_CODE_OAUTH_TOKEN=<token>`, in a folder that is 0700. The owner creates it and writes the token in a text editor, never on a command line and never into a chat: a command line lands in the shell history, a chat lands in a transcript.
- **Mode.** 0600, a regular file owned by the Mac user. `render-claude-wrapper` refuses any other mode, a link or a folder in its place, and `doctor` reports a blocker when it changes later.
- **Who reads it.** Only the shell of `<home>/bin/claude-bot`, which loads it and then runs the real `claude` with the token in its environment. `wa_setup.py` checks the file's mode and that the token line exists, and never reads out or prints what follows the equals sign. Every headless run is denied reading `<home>/secrets/**`; the deny probe (`probe-claude.json` with `{"read_canary": true}`) proves it with a harmless marker beside the token, never the token itself.
- **What it can do.** It is a long lived login to the owner's Claude account and shares its usage: anyone holding it can run Claude as the owner until it expires (about a year) or is revoked.
- **Backups.** The nightly backup copies only from `<live>/`, so it never copies `<home>/secrets/`. A whole-disk or home folder backup the owner runs (Time Machine, a cloud backup app) does copy it, like every file in the home folder.
- **Removing it.** Set `bin.claude` back to the previous `bin.claude` value (the `previous bin.claude:` line `render-claude-wrapper` printed, recorded at C2 step 12; usually `<auto>`) (config edit ritual), then `wa_setup.py render-launchd --config <live>/config.json` and follow its `next:` line (`install-launchd` when the plist changes, otherwise `wa_setup.py restart`), then delete `<home>/secrets/claude-bot.env` and `<home>/bin/claude-bot`, and revoke the token from the Claude account (or let it run out). The daemon then uses the Keychain login again.

## The document text cache

- **What it holds.** `<home>/doc-text-cache/<key>.json`: the text the reader converted from a document someone sent (at most 3000 characters each), one file per document. The key follows the same rule as the voice transcript cache (a hash of the file's real path and size), so a document is converted once and every later read uses the cache.
- **Where it goes.** Into the reading pack of a run on that chat, and so to Anthropic inside that run, like the rest of the pack. Runs are denied reading the cache folder itself, so a run sees only its own chat's documents. It never leaves the Mac otherwise, and the nightly backup does not copy it (it copies only from `<live>/`).
- **Clearing it.** Move the folder's files to the Trash; the next read converts again what it needs.
- **Turning it off.** `reader.read_documents` false, then `wa_setup.py sync-reader-config --config <live>/config.json`: packs list documents by file name only and nothing new is cached. One manual read can skip it with `wa_sweep.py --no-documents`.

## Quotes in your vault, and switching each writer off

| Writer | What lands | Where | Switch it off |
|---|---|---|---|
| Intake capture from a triage run | One quote (up to 200 characters, marked 🗣 when found word for word, ≈ when a gist), who said it, what to do, the machine's guess; a correction entry also holds the words before an edit | Intake buffer | `intake.enabled: false` |
| Intake capture from the second source | The same, tagged with that source | Intake buffer | `intake.enabled: false` (it stops every intake writer), or `second_source.enabled: false` |
| Recent line from a one-to-one run | One line the brain wrote about where things stand, not a quote | The recent block of an existing person note | See below |
| Recent line from a group speaker | The speaker's own words, up to 90 characters, with the group's name | The recent block of that speaker's existing note | See below |
| Recent line from the second source | One line about what the person has been saying | The recent block of a note carrying that source's id key (`second_source.id_key`) | `second_source.enabled: false` |
| Person note body | The model's account of the person, with their own words quoted where the playbook asks; quotes are checked against the pack | The note | `dossier.enabled: false` |

**Recent lines.** Every recent line goes through one writer, `Dossier.append_recent`, which only adds to notes that already exist and already carry the recent block (between `<!-- wa-live:begin` and `<!-- wa-live:end -->`); it never creates a note, and it writes nothing while `dossier.enabled` is false or the daemon runs with `--dry-run`. A switch is only as good as every writer honoring it, so prove it on your own install before relying on it: take `md5 -q <that person's note>`, let one of that person's chats run, and compare. Stopping the daemon always stops them.

**What 🗣 promises, and what ≈ means.** Before an intake entry is written, the program looks its quote up in the run's own message pack (`intake.verify_quotes`, on by default). 🗣 (🎙 for a voice transcript, 📄 for a document's text) means the words on the line are the stored characters of that message, copied by the program, never retyped by the brain: what the line says the person wrote, they wrote. ≈ `gist, not verbatim` means the program did not find those words: the line is the brain's summary and may be wrong in a detail, so a date, amount or place in it is checked against the chat before it is used. ≈ `not checked` means nobody looked (the switch is off, or the pack could not be read). A machine never promotes a gist to a quote, because a person's paraphrased words in a vault that syncs read as something they said.

**The edit watch.** WhatsApp lets a person edit a message after sending it, so a message judged a minute ago may now say something else. While `edit_watch.enabled` is true, the daemon keeps the words of each message that passed the gate in the `edit_watch` table of `<live>/state.sqlite`, for at most `edit_watch.minutes` (30) after it passed, and compares them with what WhatsApp Desktop holds now; the row is purged when the window ends. The log names the conversation and never the words. `state.sqlite` is in the nightly backup, so words still on the watch list at backup time are in that night's copy, for as long as the backup folder keeps it (`backup.keep`). An edit that changed an intake entry's words is written as a new correction entry (✏️) holding the old and the new words; the older entry stays as it was. Turning it off: `edit_watch.enabled: false` and a restart; edits are then no longer seen, and the table empties within the window.

**Removing a quote that already landed.** Edit the note or the buffer entry by hand. Earlier versions still sit in `<live>/dossier-backups/<note>/` (three) and in every nightly backup folder (seven days), and in your vault's own sync history; clean those too if the words must be gone everywhere.

## The Today brief

Only while `today.enabled` is true (station O7).

- **What it reads.** Today's judgments of the conversations that moved, the open entries of the intake buffer, the brief note and task list of each project a source may belong to (found from your vault's own folders and doctrine, `paths.vault` and `paths.doctrine`), one line per other active project, and at most `today.max_reads` (6) vault files the run opens itself. It also reads your own replies after a conversation's last judgment, at most three per conversation and 200 characters each, from the WhatsApp database through the daemon, so a card can say you already answered. To check quoted words, it reads the run packs the triage runs already wrote, on the Mac.
- **What it stores.** Three tables in `<live>/state.sqlite` (`today_brief`, `today_proposals`, `today_assign`): each build's brief, each card with its status, your note on it and the evidence lines the program built (entry titles, checked quote lines, capture lines), and which project a source was assigned to or taken away from. `<live>/today/<date>.json` (0600) holds the latest brief of each day. Each build has a run folder under `<live>/runs/` with its `prompt.md` and the model's output, like every run. The nightly backup copies `today/` and `state.sqlite`, never `runs/`.
- **What it never stores.** The text of your replies goes into the brief run folder's `prompt.md` and nowhere else the brief writes (a later triage run of that conversation reads your reply like any message, in its own run folder); the run folder also keeps the model's raw answer (`result.txt`, `claude.stdout.jsonl`, and `claude.stderr.txt` when the call wrote one), which may repeat them, like every run folder (the kit never deletes run folders; `references/operating.md` says how to clear old ones). A card records only the time you replied. Before anything else is stored (tables, `today/<date>.json`, the board's answers, the log, and the error text of a failed call), the program replaces any stretch of the model's answer (and of a failed call's error text, which is otherwise stored as written), in any field (labels, task names and task fields included, and again after a list or an object is joined into text), that also appears in one of your replies (at least `today.quote_min_chars` CJK characters, or 4 Latin words (6 letters or digits or more), ignoring case, spacing, punctuation (inside a word too) and full-width forms, and reading what is written between the words the way a program would: backslash escapes (`\n`, `\t`, `\uXXXX`, `\u{...}`, `\UXXXXXXXX`, `\xXX`, octal of one to three digits, `\N{...}`, at any depth, a backslash before a letter or digit read both as an escape and as a plain separator), HTML entities (named, legacy names such as `&nbsp` also without the semicolon, and numeric), percent codes (`%20`, `%2520`, `%u0020`), tags (also malformed ones, read the way a browser reads them) and HTML comments, also when one is written inside another, at any depth (`&lt;br&gt;`, `&amp;nbsp;`); a CSS escape (`\0020 `) is not read as one) with "(your reply, not copied)" (zh "(你的回覆, 不抄)"). Known limitation: the match covers the common forms above, nested ones included; a deliberately constructed shape outside them (a CSS escape, decomposed Unicode, a gap that reads two different ways, one reply split across two fields) can keep words, and no model has a reason to write one. CJK here means Chinese characters, Japanese kana and Korean Hangul, each counted on its own. A paraphrase shorter than that, or reworded, is not caught: it is the model's own text and is stored like the rest of the brief.
- **What it writes into the vault.** Nothing. The board proposes; your OK or No and your note change only the daemon's own state. Placing happens in your own session, through the intake mode. A card whose entries were placed retires itself, which again changes only the daemon's state.
- **What goes to Anthropic.** The prompt above, once per build ("What goes to Anthropic, run by run").
- **Turning it off.** `today.enabled: false` and a restart. The tables and `live/today/` stay; delete the folder by hand if you want the briefs gone (the run folders follow the rule for every run folder).

## The old session transcript hazard

`claude -p` normally saves every session as a transcript under `~/.claude/projects/<a folder named after the working directory>/`. Every headless call this kit makes (triage, person notes, backfill, second source, the Today brief, probe) passes `--no-session-persistence`, so no transcript is written, and `brain.cwd` sits outside the vault so no vault instructions or indexes are involved. Each one also runs with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, so your own Claude Code memory and any `CLAUDE.md` above the working folder do not travel to Anthropic inside every run; the `probe-claude.json` probe proves it (`references/pitfalls.md`, pit 14).

If anything ever ran `claude -p` from that folder, or from an older working folder, without the flag (an older version, a test run, an adapter you wrote), its transcripts are still on disk, and each one holds a full prompt: a conversation pack with other people's messages.

Look for them:

```sh
ls ~/.claude/projects | grep -i -e brain-cwd -e whatsapp
ls -la ~/.claude/projects/<each folder found>/
```

Removing them is your call, and your hand: a session shows you the command and waits. Three more copies may exist:

- **A session-history index.** If you use the my-second-brain session-history tool, it may already have indexed those transcripts, and deleting the files does not remove indexed rows. See what it holds: `sqlite3 ~/.my-second-brain/session-history.db "select id, started_at from sessions where project = '<folder name>'"`. Removing them: `sqlite3 ~/.my-second-brain/session-history.db "delete from messages where session_id in (select id from sessions where project = '<folder name>'); delete from sessions where project = '<folder name>'"`.
- **A machine backup** (Time Machine or similar) taken while the files existed.
- **A synced home folder**, if your home folder syncs to a cloud.

To confirm nothing new appears: after a few runs, `ls ~/.claude/projects | grep -ci brain-cwd` still prints 0.

## Tailscale, and a board with no login

The board has no login, no password and no device check. Anyone who can reach it can read every conversation on it (text, images, videos, voice), change drafts, worlds and stages, dismiss conversations, start judgments (spending your model usage), queue person note rewrites (writing your vault), and create calendar events.

Who can reach it:

- every device logged into your tailnet;
- anyone you share the Mac with through Tailscale's device sharing, and anyone your tailnet's access rules let in;
- nobody on the internet, as long as `tailscale funnel` is never turned on for that port;
- nobody on your local network, as long as `board.bind` stays `127.0.0.1`.

What to do about it:

- Keep the tailnet to your own devices, or restrict access to the Mac with Tailscale's access rules.
- Remove a lost or sold phone or laptop from the tailnet in the Tailscale admin console.
- Check what is exposed: `tailscale serve status` shows the proxy to `http://127.0.0.1:<board.port>`, marked tailnet only, and nothing you did not serve on purpose; `lsof -nP -iTCP:<board.port> -sTCP:LISTEN` shows only `127.0.0.1`.
- `tailscale serve --http=80` serves plain HTTP inside the tailnet, and Tailscale encrypts the traffic between devices. If your tailnet has HTTPS certificates enabled, `--https` works too. `wa_setup.py tailscale-hint` reads `tailscale serve status` first and never prints a command that would replace another service's rule: when port 80 is taken, it warns and uses the first free port from 8443 up.

## Switching each flow off

Restart after any change: `wa_setup.py restart --config <live>/config.json`.

| Flow | Key | What it does |
|---|---|---|
| Automatic judgments | `doorbell.enabled: false` | New messages are no longer considered; the board and scheduled jobs keep running |
| One-to-one chats, groups, single chats | `direct_policy`, `group_policies`, `unlisted_group_policy`, `ignored_jids` | Narrows what the gate passes |
| Pushes | `notify.mode: off`, or `notify.outlet: none` | No pushes at all |
| The digest push | `digest.push: false` (still written to `<live>/digest/`), or `digest.enabled: false` | No morning push |
| The Today brief | `today.enabled: false` | No brief run; the board hides the page; the tables and `live/today/` stay until you delete them |
| Intake writers | `intake.enabled: false` | Nothing appended to the buffer |
| Person notes | `dossier.enabled: false` | No note runs (prove the recent lines separately, above) |
| Second source | `second_source.enabled: false` | No nightly pull |
| Facts | `facts.enabled: false` | No nightly facts read |
| Backup | `backup.enabled: false` | No nightly copy |
| Voice examples | `voice.enabled: false` | No new pairs, none shown to the brain |
| Document text in packs | `reader.read_documents: false`, then `sync-reader-config` | Packs list documents by name only |
| The setup token | `bin.claude` back to its previous value, then remove the token file ("The setup token file", above) | The daemon uses the Keychain login |
| The board | `board.enabled: false`, and remove its `tailscale serve` rule | No board |
| Everything | `wa_setup.py uninstall-launchd --config <live>/config.json` (the same with `--intake` when an evening job from an earlier release is still installed) | The daemon's python is stopped first, then the job is booted out and its plist removed from `~/Library/LaunchAgents/`, so a reboot does not bring it back; `install-launchd` brings it back |
