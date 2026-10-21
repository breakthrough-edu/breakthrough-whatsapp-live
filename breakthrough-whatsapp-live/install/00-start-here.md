# Start here: how the install wizard runs

You are a Claude Code session installing WhatsApp Live on the owner's always-on Mac, live, with the owner. Read this whole file before the first question. It says how the wizard runs; each station file says what to ask, what to do, how to prove it and what to write down.

## Before the first question

1. **Find the progress file**: `~/WhatsApp-Live/INSTALL-PROGRESS.md`, unless the owner says the install lives elsewhere (`ls -d ~/WhatsApp-Live*` when unsure). Missing means a new install: open `install/C0-fit-and-prerequisites.md`, and station C0 creates the file. Present means resume, below, once the owner confirms that file is the install they mean: a Mac can hold two installs, and an owner setting up a second one starts a new install at C0, whose step 2 gives it its own home, label and port.
2. **Read the rules in `SKILL.md`** if this session has not. Rules 1 to 5 decide most judgment calls during an install.

## Resuming

The `## Stations` list in the progress file is the only record of progress, and only a `- [ ]` line there is work left. The log under it records what happened; nothing in the log is a step.

1. The first `- [ ]` line names the station to open.
2. Read that station's log section: which questions were answered, which Do steps ran, which Verify rows passed.
3. Run that station's read-only Verify probes before anything else. The Mac may have changed since the last session (a permission switched off, a restart, a node upgrade, WhatsApp Desktop logged out). Continue from the first step whose probe does not pass.
4. Tell the owner in one or two sentences where the install stands and what comes next, then ask the next question.

## The order

Core stations run in this order, each only after the one before it has passed its Verify:

| Station | File | Needs |
|---|---|---|
| C0 fit and prerequisites | `install/C0-fit-and-prerequisites.md` | nothing |
| C1 reader | `install/C1-reader.md` | C0 |
| C2 daemon | `install/C2-daemon.md` | C1 |
| C3 board | `install/C3-board.md` | C2 |
| C4 person notes | `install/C4-person-notes.md` | C3 and a second-brain vault |
| C5 your tools | `install/C5-your-tools.md` | C3 |

C0 to C3 are never skipped. C4 is skipped when the owner has no vault or does not want person notes: log `skipped: <the reason>` and tick its line, so a resume moves on. C5 decides which optional stations run (O1 to O11, one file each in `install/`) and writes them into `## Stations` in the order they will run, O11 last.

⛔ No optional station opens before C3 has passed, even when the owner asks for one early ("connect my Telegram alerts now"). Say which station is open, and that the one they asked for comes after the board.

## Every station has four parts

- **Ask.** The questions, in order. One question per message, in the owner's language, conversational. When a question re-rules a default, read that default in `references/defaults-and-why.md` first and give the owner its reason in a sentence or two before asking. Wait for the answer before the next question.
- **Do.** The commands, each with the files it writes. Run the `--dry-run` form first wherever a command has one, and show the owner the plan when it writes anything outside `<home>`. Nothing installs software, changes System Settings, changes the tailnet or edits the vault without the owner's yes.
- **Verify.** Probes, and what passing looks like. Run each probe yourself, in this session, and compare its output with the passing line. The station passes only when every row does. When a row fails, change one thing, run the probe again; after three failed attempts on the same row, stop, log the machine state, and tell the owner where it is stuck (`references/troubleshooting.md` is the next place to look, never a guess).
- **Record.** Append to the progress file while the station runs, not at its end: an install can stop at any moment (station C0 even asks the owner to quit the app this session runs in).

⛔ This session does the work itself. Never hand a station, a probe or a verify to a subagent: a value this file records must come from the session that saw it printed.

## Tone

- A practitioner walking next to the owner, not a manual read aloud. No lectures between steps.
- One question at a time. When an answer is too vague to act on, give one concrete example of a usable answer and ask again.
- Offer every optional block with its cost said plainly: model usage and on which model, where the data goes, what it writes into the vault, what it adds to look after. The owner decides with the cost in front of them. A no is logged, never argued.
- When something cannot be done, say so in one sentence, without apology.
- House style (rule 10 in `SKILL.md`) in everything the owner reads.

## Shell conventions

Every station opens with this block. Fill each `<...>` with the value the progress file header records, exactly as written there:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="<launchd.intake_label from the progress file header>"
PORT="<board.port from the progress file header>"
```

- **Every block starts with these variables, filled from the header, never with the defaults.** The defaults (home `~/WhatsApp-Live`, label `local.whatsapp-live`, port `8787`) are in the header only when station C0 found no other install. On a Mac with two installs, a block that names a default drops command files into the other install, restarts its daemon or reads its board.
- **An unfilled placeholder fails loudly instead of touching another install**: `wa_setup.py` stops with `config not found`, a command file cannot be written into a folder that does not exist, `launchctl` finds no service by that name, and `curl` rejects the port. Two kinds of block would not fail by themselves: one that creates a folder from `HOME_DIR` (it makes a stray folder named after the placeholder), and a probe whose passing output is an absence, such as no listener, no plist or no service (it passes on nothing). Blocks of those kinds start with this fill check; add it to any such block that lacks it:
  ```sh
  case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
  ```
- `paths.home` is recorded as an absolute path, because a `~` inside quotes is not expanded. `ILABEL` is `launchd.intake_label` as the header (and the config) records it; never build it from the label, since an install may carry another value. A header written by an earlier version of this wizard says `home:`, `label:`, `intake_label:` and `port:`: same values; fill `HOME_DIR` with its home written out as a full path.
- `KIT` is the folder this wizard was read from. A project level install sits under that project's `.claude/skills/` instead.
- Claude Code keeps no shell variables between commands: start every command with the lines it needs from this block.
- An owner with a my-second-brain vault carries its frontmatter guard, which refuses any shell command that pairs `cp`, or `cat`, `tee`, `printf` or `echo` with a `>` redirect, with a path ending in `.md`. The blocks here keep those apart: create or copy a `.md` file with the Write tool, and run a command that writes and a check that names a `.md` file as two separate commands.
- `wa_setup.py doctor` and `measure` print counts, ages, paths and group names only; that output is safe to show and to log. Everything that holds message text (a reading pack, a draft, a transcript) is read with the owner inside this session and never copied into the progress file.

## Editing `live/config.json`

The config holds the owner's number, names and group list, so it stays 0600, and the daemon silently ignores a misspelled key. Every change goes the same way:

1. Back it up: `cp -p "$LIVE/config.json" "$LIVE/config.json.bak-$(date +%Y%m%d-%H%M%S)"`
2. Edit it with the Edit tool: valid JSON, keys exactly as `references/config-reference.md` names them.
3. `chmod 600 "$LIVE/config.json"`, then `"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"`. No new errors.
4. After a change to `my_phone`, `my_names`, `owner_label`, `ignored_jids`, a `brain.whisper_*` key or a `paths.*` key: `"$PY" "$KIT/daemon/wa_setup.py" sync-reader-config --config "$LIVE/config.json"`.
5. Once the daemon is installed: `"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"` (never `launchctl kickstart -k`, which can leave a frozen python holding the board port: `references/pitfalls.md`, pit 15). The daemon reads its config at start; runs in flight are killed and their bursts run again. A prewarm the restart cuts short runs again from the start at the next start. A backfill in flight is cut short and never run again: when one was started, wait for its `backfill 完成` (done) line in `$LIVE/wa_live.log` before restarting.

## The progress file

Station C0 writes it with the Write tool in step 2, once the home, label and port are proven free, and sets it to 0600:

```markdown
# WhatsApp Live · install progress

kit_version: <VERSION from daemon/wa_live.py>
mac: <the Mac's name, from scutil --get ComputerName>
paths.home: <absolute path of the working folder>
launchd.label: <the label>
launchd.intake_label: <the intake label, as written into the config>
board.port: <the port>
vault: <absolute path, or none>
started: <YYYY-MM-DD>

## Stations

- [ ] C0 fit and prerequisites
- [ ] C1 reader
- [ ] C2 daemon
- [ ] C3 board
- [ ] C4 person notes
- [ ] C5 your tools

## Log

## Updates
```

Each station appends its own section under `## Log`, line by line as the work happens:

```markdown
### C2 daemon · <YYYY-MM-DD>
- asked: <the question in a few words> → <the answer>
- did: <the command> → <the one output line that mattered>
- verify: <the Verify row> → <the value the probe printed> → pass
- skipped: <what> because <the owner's reason>
- ruled: default <n> → <path of the decision note, or "no Decisions room, logged here">
```

⛔ A `verify` line carries the value the probe printed, never just the word ok. A station's line is ticked `- [x]` only when every Verify row of that station has a `pass` line.

## Rulings

Several questions re-rule a default. Whatever the owner rules, kept or changed, lands as one decision note in their Decisions room, one default per note (`references/defaults-and-why.md`, "How your ruling lands"). The value itself lives in config, never in the note. How the note lands depends on what the vault has:

- **A command-base skill**: its "log a decision" route writes the note, after its check against the active decisions.
- **A Decisions room but no command-base skill**: write the note by hand with the Write tool, with the keys doctrine section 8 gives the `decision` family, and append one filing-log line. The check against the active decisions is a read of the Decisions room before the note is written.
- **No Decisions room**: the ruling is logged in the progress file, and station O11 comes back to it.

## When the stations run out

When the last chosen station has passed: tell the owner in two or three lines what now runs on their Mac, what their phone shows, and what they can say from here on ("is my whatsapp live running", "why didn't this message show on my board", "update whatsapp live"). Nothing else is offered at that close.
