---
name: breakthrough-whatsapp-live
description: "Live WhatsApp triage for the owner's personal number on an always-on Mac: a local daemon reads WhatsApp Desktop's own database read only, a rule gate picks which message bursts earn a headless Claude run, each run judges the conversation and drafts a reply, and a private board on the owner's tailnet shows it all. It never sends: the owner presses send. Use when the owner wants it set up or an install continued (\"set up whatsapp live\", \"install whatsapp live\"), updated (\"update whatsapp live\"), operated (\"is my whatsapp live running\", pause, restart, logs), diagnosed (\"why didn't this message show on my board\", a failed run, a board that will not open) or removed (\"uninstall whatsapp live\"). NOT the WhatsApp Business or Cloud API, NOT an unofficial WhatsApp library or browser automation, NOT the export-based breakthrough-whatsapp-triage kit, NOT reading WhatsApp to answer a question (breakthrough-whatsapp-read)."
---

# breakthrough-whatsapp-live

Read this file first, every time. It routes and it forbids. Procedures live in the files it points at; when one of them disagrees with this file, the specific file wins and this one gets fixed in the same session.

What the owner ends up with: every inbound message on their own WhatsApp number wakes a daemon on their always-on Mac. A rule gate (no model call) decides whether the burst is worth a run. One headless `claude -p` run reads the conversation, judges it (which world, which stage, how urgent, whose move it is) and writes a draft. A board served on `127.0.0.1` and put on the owner's tailnet with `tailscale serve` shows text, photos, videos, voice transcripts, the three chips and the draft, with a button that opens WhatsApp prefilled. Around that core: person notes in the owner's second-brain vault, and the optional blocks the owner picks at station C5, among them the board's Today brief (per project, what today's conversations mean, proposed as cards and never pushed; it replaces the retired evening intake push).

## The rules

These hold in every mode. Breaking one is a bug, not a judgment call.

1. **Nothing is ever sent.** The kit has no sending path and none gets added, whoever asks. The board opens WhatsApp with the draft prefilled (one-to-one) or copies it (groups); the owner presses send. Claude never sends a WhatsApp message and never drives the WhatsApp app.
2. **Only the official app's own database.** The daemon reads the database WhatsApp Desktop keeps on the Mac, read only. ⛔ Never an unofficial WhatsApp library, never browser automation, never the Business or Cloud API for a personal number, and never any of them offered as an advanced option later.
3. **Never touch another install's label or port.** This install owns one `launchd.label`, one `launchd.intake_label` and one `board.port`, recorded in the header of `<home>/INSTALL-PROGRESS.md`. Check they are free before choosing them (station C0, step 2), and run every command with this install's own values from that header, never the defaults. Never `bootout`, `disable`, `kickstart` or overwrite a LaunchAgent that is not this install's, and never serve on a port another program listens on.
4. **Never overwrite an owner copy or a vault note.** Prompts, the topic map and the playbooks belong to the owner once copied: an update merges them section by section on the owner's yes (`install/update.md`). Every vault change is a proposal the owner rules on (`references/vault-proposals.md`), applied with the Write or Edit tool in the owner's own session, or, only for a Today card the owner pressed OK on and only when the owner switched the write button on at station O7, by the daemon, by program and never by the model, with a backup and an undo (`references/vault-proposals.md`, proposal g). Person notes are placed only through `dossier_core.place_note`, which refuses to replace a note it did not start from.
5. **One station at a time; a station is done when its Verify passes.** Run every probe in this session and read its output yourself. "It worked", from the owner or from a subagent, is not evidence. Never open the next station on a failed or unrun probe.
6. **Record as you go** in `<home>/INSTALL-PROGRESS.md`: every answer, every probe with the value it printed, every skip with its reason. A new session resumes from that file, never from memory.
7. **Windows has no path.** WhatsApp Desktop for Windows keeps its database encrypted with a key bound to that machine, and the only tools that open it are forensic ones. Say so once, plainly, and stop. The kit runs on macOS only.
8. **Where macOS asks for a person, the owner acts.** Full Disk Access is switched on by the owner in System Settings. `tailscale serve` is run by the owner. Software (Homebrew formulas, a model download of about 1.5 GB) is installed on the owner's yes. Say what each change does before asking.
9. **Other people's words stay where they are.** Ask the owner to paste only what `wa_setup.py doctor` and `measure` print (counts, ages, paths, group names). Never ask for message text, a bot token or a key in the chat. What leaves the Mac, and to whom: `references/privacy.md`.
10. **House style** in everything this skill writes for the owner: no em dash, no en dash, no double hyphen or spaced hyphen used as a separator. Commas, colons, periods, parentheses. Commands and paths stay exactly as written.

## Route

Find the install first. `<home>` is `~/WhatsApp-Live` unless the owner moved it; when `~/WhatsApp-Live/INSTALL-PROGRESS.md` is missing and the owner says they installed, ask where (`ls -d ~/WhatsApp-Live*` helps). A Mac can hold more than one install, each with its own home, label and port: when more than one progress file turns up, ask which install the owner means before running anything, and take every value from that install's header. Then route by what the owner asked, and load one mode file per request:

| The owner says | Mode | Read |
|---|---|---|
| "set up whatsapp live", "install whatsapp live", "continue the install", or there is no progress file | Setup | `install/00-start-here.md` |
| "update whatsapp live", or they just ran `npx skills update` | Update | `install/update.md` |
| "is my whatsapp live running", pause or resume, restart, logs, a command file, change a setting | Operate | `references/operating.md` |
| "why didn't this message show on my board", a run failed, the board is empty or will not open, the board says the brain cannot be called, a voice note has no transcript | Diagnose | `references/troubleshooting.md`, then the pit it names in `references/pitfalls.md` |
| "uninstall whatsapp live", "remove whatsapp live" | Uninstall | `install/uninstall.md` |

Setup state changes two things:

- **A core station (C0 to C5) is still unticked** in the progress file: operate and diagnose still answer (a half install is still a machine to reason about), but offer once to resume setup, and never start an optional station.
- **Update and uninstall** work on any install, finished or not.

Read these only when a mode file sends you: `references/config-reference.md` (every config key, its default and station), `references/interfaces.md` (the five contracts and the shipped implementations), `references/defaults-and-why.md` (every default, why, and where the owner re-rules it), `references/feature-checklist.md` (every feature with its probe), `references/privacy.md`, `references/vault-proposals.md`, `references/pitfalls.md`.

## Where things live on the owner's Mac

With the defaults (`~/WhatsApp-Live`, label `local.whatsapp-live`); a second install has its own home and label.

```text
~/.claude/skills/breakthrough-whatsapp-live/     this payload; npx skills update replaces it whole, so it never holds owner data
~/.claude/skills/breakthrough-whatsapp-read      link into the payload (station C1)
~/.claude/skills/breakthrough-person-dossier     link into the payload (station C4)
~/WhatsApp-Live/                                 paths.home (0700)
    INSTALL-PROGRESS.md                          the wizard's record (0600)
    config.json                                  reader config, generated by wa_setup.py sync-reader-config; never hand edited
    runs-work/  prewarm/  sweeps/                reader working copies and reading packs
    transcripts-cache/                           voice transcripts, shared by the reader and the board
    doc-text-cache/                              text of documents people sent, converted for the reading packs
    secrets/  bin/claude-bot                     only with the optional setup token (station C2, step 12); never read by a run
    vault-write-backups/                         only with the write button (station O7): a copy of every task file before the board wrote it; never read by a run
    brain-cwd/                                   empty working folder of every headless run
    launchd/run.mjs  launchd/<label>.plist       rendered by wa_setup.py render-launchd
    live/                                        paths.live_dir
        config.json  telegram.json  claude-settings.json  seeds.json      (all 0600)
        prompts/brain-prompt.md  prompts/second-source-prompt.md  prompts/backfill-prompt.md   owner copies
        topic-map.json                                                                     owner copy
        adapters/                                     the owner's own interface implementations, kept through updates
        state.sqlite  summaries.md  facts.json  voice-pairs.jsonl  backup-last.json  second-source-last.json
        digest/  runs/  commands/  dossier-backups/  variants/  calendar/  wa_live.log  stderr.log
~/Library/LaunchAgents/<launchd.label>.plist     default local.whatsapp-live; <launchd.intake_label>.plist only when an earlier release's evening job was kept (retired)
~/.cache/whisper-models/<model>/                 the whisper model copy, outside Documents on purpose
<vault>/                                         People room, playbook folders, intake buffer, Decisions, registry entries,
                                                 every one of them through references/vault-proposals.md
```

The daemon imports the reader and dossier scripts from this payload (`paths.reader_dir`, `paths.dossier_dir`), whether or not the two links exist. The links are for the owner's own sessions: `breakthrough-whatsapp-read` reads WhatsApp to answer a question, and `breakthrough-person-dossier` writes or updates one person note by hand.

## What this skill is not

- Not an auto-reply bot, not bulk messaging, not a way to read another person's number or watch a team.
- Not the WhatsApp Business API or Cloud API, and not an unofficial library: the only door is WhatsApp Desktop's database on the owner's own Mac.
- Not `breakthrough-whatsapp-triage`, which builds a one-page board on demand from an exported history, on any computer. This one runs all day on one always-on Mac.
- Not a hosted service. Nothing is deployed anywhere, and the board has no login, so the owner's tailnet is its only lock.
