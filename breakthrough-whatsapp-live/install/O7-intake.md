# Station O7 · Intake

**Should the things people ask of you, and the things you promised, be caught into one intake buffer note in your vault, and placed only when you say "intake"?** What it costs: no extra model call during the day, because captures ride on each judgment run. Each run may append at most two entries, of five kinds only (a request with a date, something you promised and have not delivered, an appointment, money, a decision the other side stated), each with a quote of up to 200 characters (marked 🗣 when the program found it word for word in the message, ≈ when it is the brain's gist), into one note in your vault (plus the second source's captures when O2 runs). Your command-base skill gains two patches: the "intake" mode and a line in the morning start. The optional Today brief (question 5) adds one extra model call, at most every `today.every_minutes` (30) minutes, only when its inputs changed (today's judgments, the open intake entries, a source you took out of a project, or your replies in conversations its cards cover); it reads a few vault files (the briefs and task lists of the projects in play, and at most `today.max_reads` it opens itself), shows its proposals on the board, and is never pushed. The optional write button (question 6) costs no model call: on a card you pressed OK on, one more press writes that task file into your vault by program, with a backup and an undo; it needs PyYAML for the system Python and your my-second-brain schema reader. Nothing is ever placed without your yes, and without the write button the daemon only appends.

The buffer's shape and the three command-base patches: `references/vault-proposals.md`, proposals c and d. The registry entries: proposal f. Why a buffer and a mode instead of writing straight into tasks, and why the brief is never pushed: `references/defaults-and-why.md`, "12 · The intake buffer is a command-base mode". What the brief reads and stores: `references/privacy.md`, "The Today brief". What the write button writes, and what refuses a write: `references/vault-proposals.md`, proposal g; why it is off by default: default 16. There is no evening push: it is retired, and a fresh install never has an evening job. What the quotes mean for privacy: `references/privacy.md`, "Quotes in your vault, and switching each writer off".

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="<launchd.intake_label from the progress file header>"
PORT="<board.port from the progress file header>"
BUF="<vault>/99_Meta/intake-buffer.md"            # intake.buffer, as question 2 decides
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs. This station needs a vault and the owner's command-base skill. ⛔ The `BUF` line holds a path ending in `.md`, and the frontmatter guard reads a command before the shell expands anything (`install/00-start-here.md`, "Shell conventions"): never put that line in front of a command that copies or redirects.

## Ask

One question at a time. Read default 12 before question 1.

1. **Do you want captures?** Read default 12: the machine makes sure nothing is lost, you put each item where it belongs, and a pile of open entries is information, not a mess.
2. **Where should the buffer note live?** The default `<vault>/99_Meta/intake-buffer.md`, next to the capture buffer, with no front matter. The folder must already exist. Its markers follow `board.lang`.
3. **May the command base learn two things?** Patch 2, "intake", and patch 3, one morning line counting open entries (proposal d). If C4 did not add patch 1 ("update <name>'s note"), offer it here too.
4. *(Retired in v1.3.0: the evening push. The number stays so older progress files still read the same; go on to question 5.)*
5. **Do you want the board to propose, per project, what today's conversations mean?** Say what it does: a Today page on the board, one block per project of yours, each with a few cards (update a task, create one, seed a new project, drop an entry) that you answer OK or No; the intake mode reads those cards under each entry as a starting point. Say what it costs: one extra model call, at most every 30 minutes, only when its inputs changed (today's judgments, the open intake entries, a source you took out of a project, or your replies in conversations its cards cover); it reads a few vault files. Say what it reads: today's judgments, the open intake entries, the briefs and task lists of the projects a conversation may belong to (found from your vault's own folders and doctrine), at most 6 more vault files it opens itself, and your own replies after a judgment (at most three per conversation, each cut to 200 characters; their text, and the model's raw answer, stay only in that run's folder (`prompt.md`, `result.txt`, `claude.stdout.jsonl`, `claude.stderr.txt`), like every run folder; before anything else is stored, any stretch of the model's answer, in any field, that repeats one of them, at least `today.quote_min_chars` CJK characters (Chinese, kana or Hangul) or 4 Latin words (6 letters or digits or more), ignoring case, spacing, punctuation (inside a word too), full-width forms, and the escapes, entities, percent codes and tags listed in `references/privacy.md`, is replaced with "(your reply, not copied)"; a shorter or reworded paraphrase is not caught). Say what it never does: write into the vault, or push. It needs `paths.vault`; it works without intake too, from judgments alone. Optionally ask for a paragraph of the owner's own judgment rules for it (`today.extra_rules`, at most 2000 characters), for example "a question about a cake class date belongs to the Weekend Classes project, never to Wholesale Orders".

6. **Do you want a write-to-vault button on the Today page?** Ask only after a yes to question 5, and read default 16 first. Say what it does: on a card you pressed OK on, a Write to vault button (or Queue write) puts that card into your vault: an update card changes the frontmatter values it names in an existing task file (only `status`, `due`, `waiting_on` and `priority`, a status only from your doctrine's list) and appends one dated section; a create card writes one new task file in the project's `Tasks/` folder with the keys your doctrine requires; a drop card places its intake entries `→ drop` and touches no note. The card's intake entries are placed by key to that task, one line goes into your filing log, and Undo puts the file back. Say what it never writes: a seed card (a new project is opened in your session), the evidence lines under a card, a quote, or any stretch of a cited message or entry quote (replaced with `(…)` first); the model never writes, the program renders every byte from the card you pressed. Say what refuses it whole: a card that changed after your OK, an entry already placed elsewhere, a file of the same name for a create, a file your doctrine's own schema check refuses, and a file that does not read back as written (restored from the backup). Say what it costs: no model call; a backup folder under `<home>/vault-write-backups/` that the kit never cleans. Say what it needs: your my-second-brain schema reader (`scripts/doctrine_schema.py`, read in place), PyYAML for the system Python (installed in this session on your yes; the kit never installs it), `paths.vault`, `paths.doctrine` and the folder of your filing log. Then the mode: `direct` (a press writes at once) or `queue` (a press only queues, and your own session applies the queue with one command, for an owner who wants every write to happen where they watch). No, or no answer: `today.write_mode` stays `off`, and the brief only proposes.

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

### 4 · The Today brief (only on a yes to question 5)

With the config edit ritual, set `today.enabled` to `true`, and `today.extra_rules` when the owner gave a paragraph; leave the other `today.*` keys at their defaults unless the owner asks (`references/config-reference.md`, "`today`"). `check-config` refuses `today.enabled` with an empty `paths.vault`, and warns while `intake.enabled` is false. Then:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json" | grep today
"$PY" "$KIT/daemon/wa_today.py" --config "$LIVE/config.json" --build --dry-run | head -40
```

`doctor` prints `today: on (model <model>, every 30 min)`. The dry run prints the prompt the brief would send, with the projects it found in the vault and the candidates for today's conversations, and calls no model; show the owner the project list and fix `paths.vault` or `paths.doctrine` if a project is missing. After a restart the brief waits for the first change (a judgment, an entry, a reply); the first build for Verify is asked for by hand (one model call):

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/today-now.json"
```

Then point the intake mode at the brief: in `<command base>/references/intake.md` from step 3, the list step uses `--list-open --with-brief http://127.0.0.1:<board.port>/api/today` and the outline's step 5c (the cards with no entry) is in (`references/vault-proposals.md`, Patch 2). On the owner's yes, like every patch.

### 4b · The write button (only on a yes to question 6)

With the config edit ritual, set `today.write_mode` to `direct` or `queue`, as the owner chose. Then find the schema reader, in the order the daemon looks:

```sh
ls -l "<vault>/.claude/skills/my-second-brain/scripts/doctrine_schema.py" ~/.claude/skills/my-second-brain/scripts/doctrine_schema.py
```

One of the two found: leave `paths.schema_reader` empty. Neither: ask the owner where their my-second-brain copy lives and set `paths.schema_reader` to its `scripts/doctrine_schema.py` (config edit ritual); no copy anywhere means the button cannot be offered yet (say so, set `today.write_mode` back to `off`, record it). Then:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" init --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" vault-write-check --config "$LIVE/config.json"; echo "exit $?"
```

`check-config` refuses a mode other than `off`, `direct` or `queue`, `direct` or `queue` with `today.enabled` false or `paths.vault` empty, and a `paths.schema_reader` that is set but missing; a reader not found or PyYAML missing is only a warning. `init` creates `<home>/vault-write-backups/` (0700) and keeps everything that exists. `vault-write-check` prints `available (direct)` or `available (queue)` and exits 0, or `unavailable: <code> <reason>` and exits 2 (`references/troubleshooting.md`, "The write button is missing", lists every code, including `no_module` and `module_error`, which the setup side reports when the write module itself cannot be imported or its check fails). It never runs pip. On `no_yaml` it also prints the install line the schema reader gives for this Python: show it to the owner, say it installs PyYAML into their user's Python packages, and run it only on their yes (rule 8 in `SKILL.md`); then run `vault-write-check` again. No answer: nothing is installed, the button stays hidden with its reason in the Today page's header, and that is recorded. Once it answers available:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json" | grep -i "vault write"
```

`doctor` prints `vault write: available (direct)` (or `queue`) on the line under `today:`. In queue mode, show the owner the command their session runs to apply the queue (`references/operating.md`, "The write button, from your session").

### The evening push is retired

`render-launchd --intake` and `install-launchd --intake` refuse with one line pointing here, and exit 3. The evening push of earlier releases (an evening list of the open entries, with a suggested place each, pushed through the outlet) is retired: the board's Today brief replaces it, because two proactive surfaces that may disagree are worse than one, and a push is a second inbox (`references/defaults-and-why.md`, default 12). Its templates left the payload, so a fresh install never has the job. An install that has one from an earlier release removes it through `install/update.md`, step 8b, on the owner's yes, or by hand with `wa_setup.py uninstall-launchd --intake --config <live>/config.json`, which prints `removed: <paths>`.

### 5 · The registry addition

Under the daemon's entry, the O7 block from proposal f: reads the brain's captures, writes the intake buffer, switch `intake.enabled`, proof `wa_intake.py --count-open` rises after a run with a capture. With the Today brief on, the "O7 Today brief" row of the same table as well, and with the write button on, the "O7 write button" row.

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
7. **No evening job, on every install.** Pass: no plist under the intake label; both `--intake` commands refuse in one line pointing to this station, exit 3, and write nothing. (K278, K279)
   ```sh
   case "$ILABEL" in ""|*"<"*) echo "fill in ILABEL first (launchd.intake_label from the progress file header)" >&2; exit 1;; esac
   ls ~/Library/LaunchAgents/$ILABEL.plist
   "$PY" "$KIT/daemon/wa_setup.py" render-launchd --intake --config "$LIVE/config.json" --dry-run
   "$PY" "$KIT/daemon/wa_setup.py" install-launchd --intake --config "$LIVE/config.json" --dry-run
   ```
8. **The brief was built** (step 4 only, after the `today-now.json` there and a minute or two). A refresh or `today-now.json` within `today.manual_min_gap_seconds` (60) of any build, automatic ones included, answers `already` and builds nothing: when the log shows that answer, wait out the gap and drop the file again. Pass: `True`, today's date twice, `False` (not empty); a run folder ending `-today`. (K356)
   ```sh
   curl -s "http://127.0.0.1:$PORT/api/today" | "$PY" -c 'import json, sys; d = json.load(sys.stdin); print(d["ok"], d["date"], (d.get("built_at") or "")[:10], d["empty"])'
   ls -td "$LIVE"/runs/*-today/ | head -1
   ```
9. **A second refresh within the gap answers `already`** (step 4 only, within `today.manual_min_gap_seconds` of the last build, automatic or manual, so right after Verify 8). Pass: `{"ok": true, "already": true, ...}`, and no new run folder. (K357)
   ```sh
   curl -s -X POST "http://127.0.0.1:$PORT/api/today/refresh"
   ```
10. **The intake list shows the card under its entry** (step 4 only, with the test entry from Verify 6 open and a brief built after it). Pass: under the test entry, `    board: <project> · <kind> <task> · <status>` and its `      proposal:` line, or `    board: no card, judge it as before` when the brief made no card for it; the cards with no entry follow the list, each with `id <8 hex> rev <n>`. (K362, K363)
   ```sh
   "$PY" "$KIT/daemon/wa_intake.py" --config "$LIVE/config.json" --list-open --with-brief "http://127.0.0.1:$PORT/api/today"
   ```
11. **Placing retires the card within a minute** (step 4 only, when Verify 10 showed a card under the test entry). Place the test entry as `drop` through the intake mode, wait 90 seconds, then list again. Pass: the entry is gone and so is its card (the database row reads `superseded · placed_elsewhere`). (K364)
   ```sh
   sqlite3 -readonly "$LIVE/state.sqlite" "select status, json_extract(retired_json, '$.why') from today_proposals where id like '<the card's 8 hex>%'"
   ```
12. **A settle with a wrong revision is refused** (step 4 only, on any live card; nothing changes). Pass: `refused: rev: …` (it says to run `--list-open --with-brief` again) and `1`. (K365)
   ```sh
   "$PY" "$KIT/daemon/wa_intake.py" --config "$LIVE/config.json" --settle-card <8 hex> --rev 999 --to "test" --with-brief "http://127.0.0.1:$PORT/api/today"; echo $?
   ```
13. **The page reads at phone width** (step 4 only). 📸 board on the phone (or a window 375 px wide): the Today button, the brief with its blocks and cards, OK and No, the way back. Pass: everything readable, nothing cut, no sideways scroll. (K376)
14. **The write module runs, and config knows its two keys** (step 4b only). Pass: `wa_vault_write selftest PASS (<n> checks)`, then `check-config: 0 error(s)` with no `today.write_mode` or `paths.schema_reader` line. (K395)
   ```sh
   "$PY" "$KIT/daemon/wa_vault_write.py" --selftest | tail -1
   "$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
   ```
15. **The button shows when it can work, and the page says why when it cannot** (step 4b only). Pass: `vault-write-check` reads `available (<mode>)` and `exit 0`, and the board's `write` block reads `available` true with the same mode; on the board a card answered OK shows Write to vault (or Queue write), a pending card does not. (K383)
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" vault-write-check --config "$LIVE/config.json"; echo "exit $?"
   curl -s "http://127.0.0.1:$PORT/api/today" | "$PY" -c 'import json, sys; print(json.load(sys.stdin).get("write"))'
   ```
16. **One write lands in its shape** (step 4b, direct mode; the owner presses, on a card they want written, or on a card about a test task in a test project). Before the press, `md5 -q` of the task file and the body above where the section will go; after it: the frontmatter changed only in the keys the card named (and `updated`), one section `## <today> · WhatsApp Live` appended with the proposal line and a source line naming conversations and entry keys, no quote; the body above it unchanged; the card's entries listed by `--list-placed` as placed to that task's path; a backup folder for the card; the newest filing-log line naming the file and the card's id, no task text. (K384, K392)
   ```sh
   tail -12 "<the task file>"
   "$PY" "$KIT/daemon/wa_intake.py" --config "$LIVE/config.json" --list-placed | tail -5
   ls -l "$HOME_DIR/vault-write-backups/"
   tail -1 "<paths.filing_log>"
   ```
17. **Undo restores, and refuses after an edit** (step 4b, after Verify 16, with the owner). Press Undo on the written card. Pass: `md5 -q` of the task file equals the md5 taken before the press, an undo line sits under the entries it had placed, and a second filing-log line names the undo. Then press Write again (the owner keeps it, or it was a test), add one word to the file by hand, and press Undo: refused, changed since, and the word is still there. Take the word out by hand afterwards. (K390)
   ```sh
   md5 -q "<the task file>"
   ```
18. **Queue mode waits for the session** (step 4b, queue mode only). Press Queue write on one card. Pass: the card says queued, the task file is unchanged, `pending-writes.json` holds one entry; then the session's `--apply-pending` writes it, prints its summary, and leaves the file empty, and the card reads written. (K391)
   ```sh
   cat "$LIVE/today/pending-writes.json"
   "$PY" "$KIT/daemon/wa_vault_write.py" --apply-pending --config "$LIVE/config.json"
   cat "$LIVE/today/pending-writes.json"
   ```
19. **Runs cannot read the backups** (step 4b only). Pass: `1`, the rendered rule for `<home>/vault-write-backups/**`; and, after the next deny probe (station C2 check 9 or `install/update.md` Verify 9), the line `probe-claude read_canary [vault-write-backups]: 读取被挡下 (...)` in `$LIVE/wa_live.log`. (K393)
   ```sh
   grep -c 'vault-write-backups/\*\*' "$LIVE/claude-settings.json"
   ```
20. **A seed card is never written** (step 4b only, when a brief shows a seed card). Pass: the seed card has no write button and shows the one line that seeds are placed in your own session. (K394)

## Record

Under `### O7 intake` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` captures yes, the buffer path, patches 2 and 3 (and 1) yes or no, the Today brief yes or no (and the owner's rules paragraph, when given), the write button yes, no or no answer, and its mode;
- `did:` the note created and its filing-log line; the patches, by file;
- `verify:` the first `intake +` line, the copy test's `placed → drop` line, the morning line seen;
- `did:` for the Today brief, the `doctor` line, the dry run's project count and the first `built_at`;
- `did:` for the write button, where the schema reader was found (or `paths.schema_reader`), whether PyYAML was installed on the owner's yes and the line that installed it, the `vault-write-check` line, the first write's filing-log line and the undo check;
- the registry blocks added (the O7 addition, and the Today brief row when it is on);
- `ruled: default 12`, one note, saying whether the Today brief is on; `ruled: default 16`, one note, saying whether the write button is on and in which mode (only when question 6 was asked).

Tick `- [x] O7 intake` only when every check that applies passes. Next: the next `- [ ]` line under `## Stations`.
