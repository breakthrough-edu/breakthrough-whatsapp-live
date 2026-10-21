# Station O7 · Intake

**Should the things people ask of you, and the things you promised, be caught into one intake buffer note in your vault, and placed only when you say "intake"?** What it costs: no extra model call during the day, because captures ride on each judgment run. Each run may append at most two entries, of five kinds only (a request with a date, something you promised and have not delivered, an appointment, money, a decision the other side stated), each with a quote of up to 200 characters (marked 🗣 when the program found it word for word in the message, ≈ when it is the brain's gist), into one note in your vault (plus the second source's captures when O2 runs). Your command-base skill gains two patches: the "intake" mode and a line in the morning start. The optional evening push adds a second resident job (its own LaunchAgent) and, on evenings when entries are open, one model call on `brain.model` (read only tools, 4 turns, 300 seconds) that suggests a place for each entry, then pushes the open list, with names and quotes, through your outlet. Nothing is ever placed without your yes, and the daemon only appends.

The buffer's shape and the three command-base patches: `references/vault-proposals.md`, proposals c and d. The registry entries: proposal f. Why a buffer and a mode instead of writing straight into tasks: `references/defaults-and-why.md`, "12 · The intake buffer is a command-base mode". Turning the evening push on also re-rules "6 · Pushes only for errors, plus the morning digest". What the quotes mean for privacy: `references/privacy.md`, "Quotes in your vault, and switching each writer off".

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="$LABEL.intake"
PORT="<board.port from the progress file header>"
BUF="<vault>/99_Meta/intake-buffer.md"            # intake.buffer, as question 2 decides
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs. This station needs a vault and the owner's command-base skill. ⛔ The `BUF` line holds a path ending in `.md`, and the frontmatter guard reads a command before the shell expands anything (`install/00-start-here.md`, "Shell conventions"): never put that line in front of a command that copies or redirects.

## Ask

One question at a time. Read default 12 before question 1.

1. **Do you want captures?** Read default 12: the machine makes sure nothing is lost, you put each item where it belongs, and a pile of open entries is information, not a mess.
2. **Where should the buffer note live?** The default `<vault>/99_Meta/intake-buffer.md`, next to the capture buffer, with no front matter. The folder must already exist. Its markers follow `board.lang`.
3. **May the command base learn two things?** Patch 2, "intake", and patch 3, one morning line counting open entries (proposal d). If C4 did not add patch 1 ("update <name>'s note"), offer it here too.
4. **Should the open list reach your phone in the evening, with a suggested place for each entry?** Say the cost (one model call on evenings with open entries, a second LaunchAgent, names and quotes through the outlet) and that a yes re-rules default 6. Then: at what time (`intake.evening_at`, 20:00)?

## Do

### 1 · The buffer note

On the owner's yes, write the note with the Write tool at the path from question 2: a few lines of rules in the owner's language (optional; nothing reads them), with the legend line for the quote marks among them, then the placed heading alone on its line (`## Placed` for `board.lang: en`, `## 已归` for `zh`). The legend line, for `board.lang: en`:

```
Marks: 🗣 the person's own words, found in the message · 🎙 found in a voice transcript · 📄 found in a document's text · ≈ gist, not verbatim (or not checked) · ✏️ the person edited the message; this entry replaces the older one.
```

For `zh`: `标记: 🗣 讯息里找得到的原话 · 🎙 语音转字里找到的 · 📄 文件内文里找到的 · ≈ 大意, 非逐字 (或未核对) · ✏️ 对方改了讯息, 以这条为准。` (`references/vault-proposals.md`, proposal c, has the five marks in both languages.) No front matter. Log one line in the owner's filing log, as their doctrine asks for any new file.

### 2 · Config and restart

With the config edit ritual (`install/00-start-here.md`), set `intake.enabled` to `true` and `intake.buffer` to the note's absolute path (leave `paths.intake_buffer` empty). Then:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_intake.py" --config "$LIVE/config.json" --count-open
```

`check-config` refuses a buffer whose folder does not exist. `--count-open` prints `0` for the new note. From now on a run whose judgment carries a capture logs `run #<id> intake +<n> (<name>)` and appends above the placed heading.

### 3 · Patches 2 and 3 in the command base

Follow `references/vault-proposals.md`, proposal d, "Before patching": read the whole command-base skill and its `references/` folder first, put trigger words in both the router row and the description, and leave every block with a revision marker untouched. On the owner's yes:

- **Patch 2**: the "intake" router row, the words in the description, `<command base>/references/intake.md` written from the outline with this install's absolute paths, and the one sentence appended to the compile row.
- **Patch 3**: the "4b. Intake doorbell" line under the morning start's compile backfill doorbell.
- **Patch 1**, only when C4 did not add it: as C4 step 7 describes.

These are the owner's files in the owner's words; never copy over them. Writes: `<command base>/SKILL.md` and `<command base>/references/intake.md` (and `<command base>/references/update-person.md` for patch 1).

### 4 · The evening push (only on a yes to question 4)

The registry entry comes first: write the evening job's six cells (`references/vault-proposals.md`, proposal f, "The evening intake job") with the real label and paths. Then, with the config edit ritual, set `intake.evening_push` to `true`, `intake.evening_at`, and `intake.rules_file` to `<command base>/references/intake.md` from step 3, so the evening suggestions match the places the mode offers. Then:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_intake.py" --config "$LIVE/config.json" --evening-status
"$PY" "$KIT/daemon/wa_setup.py" render-launchd --intake --config "$LIVE/config.json" --dry-run
"$PY" "$KIT/daemon/wa_setup.py" render-launchd --intake --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" install-launchd --intake --config "$LIVE/config.json" --dry-run
"$PY" "$KIT/daemon/wa_setup.py" install-launchd --intake --config "$LIVE/config.json"
```

`--evening-status` prints the JSON the job reads: `"run": true` and the open count, or `"run": false` with the reason (`intake.evening_push is false, evening push skipped`, `notify.outlet is none or notify.mode is off, evening push skipped`, and the like). `render-launchd --intake` writes `<home>/launchd/intake-evening.mjs` and `<home>/launchd/<intake label>.plist`; `install-launchd --intake` refuses unless `intake.enabled` and `intake.evening_push` are both true, and ends with the evening job's own proof line: `proof: with one open entry in the buffer, launchctl kickstart -k gui/<uid>/<intake label>; <live>/intake-evening.log gets a line and one push arrives (station O7)`. The job never fires at load; it runs once a day at `intake.evening_at`, logs to `live/intake-evening.log`, and sets that log to 0600 each time it runs.

To prove it, one open entry must exist: a real capture, or a test entry written by hand in the entry shape and placed as `drop` through the intake mode afterwards. Then fire it once (one model call):

```sh
LABEL="<launchd.label from the progress file header>"; ILABEL="$LABEL.intake"
launchctl kickstart -k gui/$(id -u)/$ILABEL
```

### 5 · The registry addition

Under the daemon's entry, the O7 block from proposal f: reads the brain's captures, writes the intake buffer, switch `intake.enabled`, proof `wa_intake.py --count-open` rises after a run with a capture.

## Verify

Run each probe with the station's opening block in front of it. Every check must pass.

1. **The intake code runs on this Mac.** Pass: `wa_intake selftest PASS (<n> checks)`. (K274)
   ```sh
   "$PY" "$KIT/daemon/wa_intake.py" --selftest | tail -1
   ```
2. **Config is clean and the buffer is found** (after step 2). Pass: `check-config: 0 error(s)` with no `intake.buffer` line, then a number.
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
   "$PY" "$KIT/daemon/wa_intake.py" --config "$LIVE/config.json" --count-open
   ```
3. **A capture landed once, in the right shape** (after the first run with a capture; ask a friend for a message with a date in it). Pass: a `run #<id> intake +<n> (<name>)` line; the entry heading `### <date> · [wa] · <who> · <what to do>` above the placed heading, with its open status line; the entry's first mark, on the line under its heading, is 🗣 when the program found the quote in the message (🎙 in a voice transcript, 📄 in a document's text) and ≈ otherwise (`gist, not verbatim`, or `not checked` while `intake.verify_quotes` is false); `--count-open` one or two higher (plus one for each ✏️ correction the program appends, which does not count against the brain's two per run); the `intake_seen` table has the `key_kind` and `entry_key` columns and holds the quote's hashes. (K275, K276, K344)
   ```sh
   grep "intake +" "$LIVE/wa_live.log" | tail -1
   grep -n -e "^### " -e "^## " -e "^- [🗣🎙📄≈]" -e "status: " -e "状态: " "$BUF"
   sqlite3 -readonly "$LIVE/state.sqlite" ".schema intake_seen"
   ```
4. **Placing works, on a copy first.** Pass: `1. <heading>` from the list with a `    key: <12 hex>` line under the entry, then `placed → drop` and the heading, and one fewer open entry in the copy; the real buffer untouched. The intake mode places by key: `--mark-placed-key KEY --to "<where>"` (KEY a full key or a unique prefix of at least 8 hex, as `--list-open` prints it; `--list-open --json` gives full keys, `--list-placed [--json]` the placed zone) prints the same confirmation, and is the safe way to place several entries in a row, because numbers shift and keys do not. A prefix matching two entries answers `key <p> matches <n> entries (run --list-open again)`; an entry placed already, `entry <k> is already placed → <where>`. The number form below is the same placement (K277, K343). The copy is a `.txt` file (`--buffer` does not care about the extension), and the block takes the buffer's path from config instead of the `BUF` line: a command holding `cp` and any `.md` path is refused by the frontmatter guard. Run it with its own first lines, not the opening block. (K277)
   ```sh
   KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
   HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
   B=$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1] + "/daemon"); import wa_common as W; print(W.load_config(sys.argv[2])["intake"]["buffer"])' "$KIT" "$LIVE/config.json")
   cp "$B" "$HOME_DIR/intake-copy.txt"
   "$PY" "$KIT/daemon/wa_intake.py" --buffer "$HOME_DIR/intake-copy.txt" --list-open
   "$PY" "$KIT/daemon/wa_intake.py" --buffer "$HOME_DIR/intake-copy.txt" --mark-placed 1 --to drop
   "$PY" "$KIT/daemon/wa_intake.py" --buffer "$HOME_DIR/intake-copy.txt" --count-open
   rm "$HOME_DIR/intake-copy.txt"
   ```
5. **The patches are in the command base** (after step 3). Pass: lines for `update-person` and `intake` in the router, the description and the morning start. (K280)
   ```sh
   grep -n -e "update-person" -e "intake" "<the command-base skill>/SKILL.md"
   ```
6. **The mode, the morning line and compile behave** (a session with the owner, using the test entry). Pass: "morning" prints one line saying entries wait and changes nothing (same `md5` before and after); "intake" lists the entries with one proposal each and writes only on a yes, then "placed N, still open M"; "compile" writes one journal line for the placed entries, clears the placed zone and leaves open entries. (K282, K283, K284)
   ```sh
   md5 -q "$BUF"
   ```
7. **The evening job is installed as ruled** (step 4 only). Pass: `false`; the hour of `intake.evening_at`; the log's last lines end with `push: notify: delivered (exit 0)`, or with `open entries: 0, nothing sent` when nothing was open; the list on the phone headed `🗂 intake pre-sort · <n> open` (`🗂 intake 预分类 · <n> 条等归位` in Chinese); the buffer's `md5` unchanged by the run; the log `-rw-------` (the job sets it when it runs). (K278, K279)
   ```sh
   plutil -extract RunAtLoad raw ~/Library/LaunchAgents/$ILABEL.plist
   plutil -extract StartCalendarInterval.Hour raw ~/Library/LaunchAgents/$ILABEL.plist
   tail -3 "$LIVE/intake-evening.log"
   stat -f %Sp "$LIVE/intake-evening.log"
   ```

## Record

Under `### O7 intake` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` captures yes, the buffer path, patches 2 and 3 (and 1) yes or no, the evening push and its time;
- `did:` the note created and its filing-log line; the patches, by file;
- `verify:` the first `intake +` line, the copy test's `placed → drop` line, the morning line seen;
- `did:` for the evening job, `install-launchd --intake` state line and the first `intake-evening.log` result;
- the registry blocks added (the O7 addition, and the evening job's own entry when installed);
- `ruled: default 12`, one note; with the evening push on, the same note says default 6 is re-ruled.

Tick `- [x] O7 intake` only when every check that applies passes. Next: the next `- [ ]` line under `## Stations`.
