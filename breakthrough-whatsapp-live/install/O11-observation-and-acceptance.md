# Station O11 · Observation and acceptance

**Is everything installed really working, and is every ruling written where it belongs?** Always offered, always last. What it costs: a week in which more pushes go out (with `notify.mode` at `all` or `needs_me`, judgments with other people's names and draft text travel through the outlet); one short question in each morning's digest; a session of an hour or two with the owner to walk `references/feature-checklist.md`, a few rows of which spend a little model usage (marked **model call**); for rows marked **test install**, a second, throwaway install on this Mac that judges nothing by itself, writes only into its own folder and a throwaway vault, and pushes into a file, removed at the end; screenshots of real conversations kept outside the vault and deleted after the audit; and a second, independent session that audits the evidence.

Why the week and the switch back: `references/defaults-and-why.md`, "6 · Pushes only for errors, plus the morning digest". Why every ruling must be a decision note and never a line in a skill file: "14 · Skills point; rulings live in your Decisions". What the probes read: `references/interfaces.md`, "Before the probes", and the helper block at the top of the checklist.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="$LABEL.intake"
PORT="<board.port from the progress file header>"
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs.

## Ask

One question at a time. Read default 6 before question 1 and default 14 before question 3.

1. **Should every judgment reach your phone for the first week?** `all` shows the real volume and every draft; `needs_me` only the ones waiting on you; or keep `errors`. Name the day to switch back (seven days from today is usual).
2. **May the digest ask you one small check a day?** One line under the digest for seven mornings, each answered in a sentence or with a screenshot.
3. **Is every ruling from this install a decision note?** Read the `ruled:` lines of the progress file aloud and confirm each.
4. **When can we walk the checklist together, and may a test install run on this Mac for the rows that need one?**
5. **Who audits?** A new Claude Code session the owner starts later (not this one, and not a subagent of it), and where its audit file goes: the owner's project folder for WhatsApp Live.

## Do

### 1 · The observation week

With the config edit ritual (`install/00-start-here.md`), set `notify.mode` to the answer from question 1 and restart. The day to switch back goes into the default 6 decision note (a superseding note when it already landed at O4 or O6).

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
```

Each morning of the week, the owner reads the digest and the board; this session is not needed for it.

### 2 · The daily question

Write `live/acceptance.json` with the Write tool, one question per morning, keyed by the day the digest is sent (the digest for Monday is sent on Tuesday and shows Tuesday's question). Questions follow the stations that were installed, one small check each, for example:

```json
{"questions": {
  "<day 1>": "Open the board on your phone. Does the newest conversation show its three chips and a draft?",
  "<day 2>": "Reply to one draft from WhatsApp. Did that row move to replied within a minute?",
  "<day 3>": "Did every push this week come from WhatsApp Live alone, and did any look wrong?",
  "<day 4>": "Open one voice note on the board. Is its transcript there, and does it play?",
  "<day 5>": "Is anyone in 'Still waiting on you' someone you already answered elsewhere?",
  "<day 6>": "Open the person note the daemon wrote most recently. Does anything in it surprise you?",
  "<day 7>": "Which group woke the brain most this week, and should it stay on all?"
}}
```

The digest reads the file every morning (no restart) and adds two lines outside its twelve: `🧪 Today's check (<date>):` (`🧪 今天验收 (<date>):` in Chinese) and the question. The nightly backup copies the file while it exists. Delete it when the week ends. Writes: `live/acceptance.json`.

### 3 · Day seven: the review and the switch back

With the owner, read the week in numbers (counts only):

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
sqlite3 -readonly "$LIVE/state.sqlite" "select substr(started_at, 1, 10), kind, count(*), sum(ok) from runs where started_at >= date('now', 'localtime', '-7 days') group by 1, 2"
sqlite3 -readonly "$LIVE/state.sqlite" "select decision, count(*) from inbound_log where seen_at >= date('now', 'localtime', '-7 days') group by decision"
sqlite3 -readonly "$LIVE/state.sqlite" "select name, count(*) from inbound_log where decision like 'pass:%' and kind = 'group' and seen_at >= date('now', 'localtime', '-7 days') group by name order by 2 desc limit 5"
```

Ask about the busiest groups: a group whose runs the owner never wanted moves to `mention` or `skip` (the whitelist edit from C2 step 1, then a restart). Then switch `notify.mode` back to the ruled value with the config edit ritual and restart. Collect the seven answers into the progress file as one line each.

### 4 · The rulings

For every `ruled:` line in the progress file, open the decision note it names in `paths.decisions_dir` and check it is `status: active`, holds one default, and says what was ruled and why without copying config values. A ruling logged as "no Decisions room" means the vault has no Decisions room: stop here and propose one with the owner before going on (default 14). Then read `SKILL.md` in the payload once more: it holds pointers only, and anything this install wrote into it (a rule, a value, a group name) moves into a note and out of the skill.

### 5 · A test install, for the rows that need one

A second install on the same Mac, with its own home, label, port and state, reading the same WhatsApp database but judging nothing unless a row asks for one run. It is the one exception to "never two daemons", because it does no work on its own: both policies are `skip`, pushes go into a file, and every vault path points into a throwaway folder.

Pick its three values first and prove each is free: its home is the owner's home with `-test` added (`<home>-test`, beside it), its label is `<label>-test`, and its port is a free one you choose: never the owner's `$PORT`, never 8787, and never a port taken from an example (another install on this Mac may already use it). All three checks must find nothing:

```sh
HOME_DIR="<paths.home from the progress file header>"; LABEL="<launchd.label from the progress file header>"; PORT="<board.port from the progress file header>"
TPORT="<the port you picked for the test install>"
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
case "$TPORT" in ""|*"<"*|"$PORT") echo "pick the test install's own free port first, never the owner's PORT" >&2; exit 1;; esac
lsof -nP -iTCP:"$TPORT" -sTCP:LISTEN
ls ~/Library/LaunchAgents | grep -F "$LABEL-test"
ls -d "$HOME_DIR-test"
```

Pass: the first two print nothing, and the third answers `No such file or directory`. A listener or a plist found: pick another port or label and check again. Log the picked port under O11 (Verify 4 checks it again).

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; T="$HOME_DIR-test"
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
"$PY" "$KIT/daemon/wa_setup.py" init --home "$T" --config "$T/live/config.json" --dry-run
"$PY" "$KIT/daemon/wa_setup.py" init --home "$T" --config "$T/live/config.json"
```

Then, with the config edit ritual applied to `$T/live/config.json`:

- `launchd.label` `<label>-test`, `launchd.intake_label` `<label>-test.intake`, `board.port` the port picked above;
- `my_phone`, `my_names`, `owner_label`, `owner_marker` and `vocab` copied from the real config;
- `direct_policy` `skip`, `unlisted_group_policy` `skip`, `group_policies` `{}` (a row that needs a group run adds one test group at `all`, then removes it);
- `notify.outlet` `command`, `notify.command` `cat >> "<home>/push-test.txt"` (in this config the loader fills `<home>` with the test home, and each push lands on a line of its own), `notify.mode` `all`;
- `paths.vault`, `paths.people_dir`, `paths.filing_log`, `paths.decisions_dir` and `intake.buffer` inside the test home's own `vault/` folder, written out in full (create the folders; copy one or two person notes and the person-dossier playbook there when a C4 row needs them, with the Read and Write tools, since the frontmatter guard refuses a shell `cp` of a note, and point `paths.doctrine` and `paths.person_playbook` at those copies);
- `facts.enabled`, `second_source.enabled`, `backup.enabled` `false`; `dossier.enabled` only for the rows that need it.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; T="$HOME_DIR-test"
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$T/live/config.json"
"$PY" "$KIT/daemon/wa_setup.py" sync-reader-config --config "$T/live/config.json"
"$PY" "$KIT/daemon/wa_setup.py" render-launchd --config "$T/live/config.json"
"$PY" "$KIT/daemon/wa_setup.py" install-launchd --config "$T/live/config.json"
```

The test board is `http://127.0.0.1:<the picked port>/`, never served on the tailnet. A test install row fills the checklist's helper block with this install's values: `<home>-test` for `HOME_DIR`, `<label>-test` for `LABEL` (its `ILABEL` follows), the picked port for `PORT`. When the walk is over, remove it:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; T="$HOME_DIR-test"; LABEL="<launchd.label from the progress file header>"
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
"$PY" "$KIT/daemon/wa_setup.py" uninstall-launchd --config "$T/live/config.json"
launchctl print gui/$(id -u)/$LABEL-test
```

The second command must find no service (it exits nonzero, 113). Then the owner moves the test home (`<home>-test`) to the Trash in Finder (it holds a copy of their chats' judgments and transcripts).

### 6 · Walk the checklist

Open `references/feature-checklist.md` with the owner. Create the evidence folder, private and outside the vault:

```sh
HOME_DIR="<paths.home from the progress file header>"
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
mkdir -p "$HOME_DIR/acceptance" && chmod 700 "$HOME_DIR/acceptance"
```

- Walk the tables in order, skipping rows of stations that were not installed (write `n/a: <station> not installed`).
- Before starting, count the **model call** rows and tell the owner.
- For each row, paste the helper block from the checklist's top, filled with the values the progress file header records, run the probe, and compare with "Passing looks like".
- Write one line per row into `<home>/acceptance/evidence.md` (0600) with the Write or Edit tool (the frontmatter guard refuses a shell append to a `.md` path): `K<nnn> · pass · <the output line that passed>`, or `K<nnn> · fail · <what printed>`. Never message text: an output line that holds someone's words is summarized in a few words instead.
- A 📸 row gets a screenshot the owner takes on the device named, saved as `<home>/acceptance/K<nnn>.png`; the evidence line names the file.
- A failing row is fixed with `references/troubleshooting.md`, then run again. After three failed attempts on one row, stop and log the state, as `install/00-start-here.md` says.
- The rows under "Not verifiable on a single machine" get `pending: no standby Mac`, or the date of the O10 rehearsal that proved them.

### 7 · The independent audit

The owner starts a new Claude Code session on this Mac and asks it to audit the WhatsApp Live install: read `references/feature-checklist.md` and `<home>/acceptance/evidence.md`, look at every 📸 screenshot, run again every row whose evidence line is thin and ten random rows, and write the audit file into the owner's WhatsApp Live project folder in the vault: one line per row id, pass or fail, and for each fail the fix it proposes. No message text and no screenshot goes into that file. When the audit is accepted, delete the screenshots, the probe outputs some rows write there (such as K027's) and `evidence.md` with the owner, keeping only the audit file.

## Verify

Run each probe with the station's opening block in front of it. Every check must pass.

1. **The week ran, and pushes are back at the ruled mode** (after step 3). Pass: one row per day and kind for seven days; the newest `notify 出口` line shows the ruled mode (usually `mode=errors`); the switch-back day is in the default 6 note. (K308)
   ```sh
   grep "notify 出口" "$LIVE/wa_live.log" | tail -1
   ```
2. **The daily questions arrived** (during the week). Pass: seven digests in `live/digest/` carrying the `🧪` line, and seven answers in the progress file; `live/acceptance.json` gone after the week. (K309)
   ```sh
   grep -l "🧪" "$LIVE"/digest/*.md
   ls "$LIVE/acceptance.json"
   ```
3. **Every ruling is a note** (step 4). Pass: every `ruled:` line names a note with `status: active`, and the payload's `SKILL.md` holds no owner ruling. (K310)
4. **The test install is gone** (after step 5). Pass: `launchctl print` finds no `<label>-test`, nothing listens on the port step 5 picked for it (the one logged under O11), and the test home (`<home>-test`) is in the Trash.
   ```sh
   case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
   TPORT="<the port step 5 picked for the test install>"
   case "$TPORT" in ""|*"<"*|"$PORT") echo "fill in the test install's port from the O11 log first" >&2; exit 1;; esac
   lsof -nP -iTCP:"$TPORT" -sTCP:LISTEN
   launchctl print gui/$(id -u)/$LABEL-test
   ```
5. **Every row has evidence** (step 6). Pass: one line per row id, none left without `pass`, `n/a` or `pending`; every 📸 row names its screenshot. (K311)
   ```sh
   grep -c "^K[0-9]" "$HOME_DIR/acceptance/evidence.md"
   grep -c "· fail ·" "$HOME_DIR/acceptance/evidence.md"
   ```
6. **The audit is written** (step 7). Pass: the audit file exists in the project folder, its fails are fixed or accepted by the owner in a decision note, and the screenshots are deleted. (K312)

## Record

Under `### O11 observation and acceptance` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` the week's mode and the switch-back day, questions yes or no, the audit session's owner and the audit file's location;
- `did:` the seven answers, one line each;
- `did:` the review's counts (runs per day, pass and skip counts) and any whitelist change;
- `verify:` the rulings checked, by default number;
- `verify:` the checklist totals: pass, fail, n/a, pending;
- `did:` the test install's home, label and picked port, its removal, and the audit file's path;
- `ruled: default 14`, and default 6 again for the observation week.

Tick `- [x] O11 observation and acceptance` only when every check passes. Then close as `install/00-start-here.md` says under "When the stations run out".
