# Station O5 · Nightly backup

**Should the daemon's state be copied into a folder of your choice every night?** What it costs: no model call. At `backup.at` the daemon copies into `<backup.dest>/<YYYY-MM-DD>/` (folder 0700, files 0600): the state database (every judgment, draft, summary and the log of inbound messages with names), the config with secret-looking values redacted, the summary table, `facts.json`, the voice pairs, the second source whitelist, the topic map, `seeds.json`, the observation week's `acceptance.json`, the owner copies of the prompts, the digests, the three backups of every person note, and your own adapters in `live/adapters/` (copied as they are). The copy is not encrypted; a cloud folder puts all of it with that provider. The newest `backup.keep` (7) dated folders stay. It reaches only that folder, and writes `live/backup-last.json` for the digest. Push secrets, run folders, logs, command files, the rendered `claude-settings.json`, the two result files (`backup-last.json`, `second-source-last.json`) and the person note write lock (`people-write.lock`) are never copied.

The tool was chosen at `install/C5-your-tools.md`, question 5. What is copied, the never list, the destination table and the proof: `references/interfaces.md`, "5 · Nightly backup destination". Why one machine and a nightly copy: `references/defaults-and-why.md`, "11 · One machine, backed up nightly". A backup that copies a fixed list silently misses files added later: `references/pitfalls.md`, pit 12.

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

One question at a time. Read default 11 before question 1.

1. **Which folder?** (from C5) Take the path from the destination table in `references/interfaces.md` for iCloud Drive, Google Drive, Dropbox or an external disk. A folder only you can see: the copy holds names, numbers, summaries and drafts. Say default 11 out loud: one machine runs the daemon, never two at once, and one session touches the daemon at a time.
2. **Does a sync app have to be running for that folder to sync?** Its process name as Activity Monitor shows it goes into `backup.require_process` (for example `Dropbox` or `Google Drive`); iCloud Drive and a disk leave it empty. With it set, a night when the app is not running fails loudly instead of writing into a folder that never syncs.
3. **When, and how many nights?** `backup.at` (03:30, after the facts job and the second source, before the digest) and `backup.keep` (7).

## Do

### 1 · The destination folder

The parent of the destination must exist (an unmounted disk or a missing sync folder then fails instead of growing a local copy), and `check-config` wants the destination itself to exist. On the owner's yes:

```sh
mkdir -p "<backup.dest>"
chmod 700 "<backup.dest>"
```

Writes: one empty folder at the destination.

### 2 · Config

With the config edit ritual (`install/00-start-here.md`), set `backup.dest`, `backup.require_process`, `backup.at`, `backup.keep`, and `backup.enabled` to `true`.

### 3 · The plan, from the terminal

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_backup.py" --config "$LIVE/config.json" --dry-run
```

Writes nothing, not even `backup-last.json`. It prints the live folder, the destination, every item it would copy tagged `[db]`, `[config]`, `[file]` or `[dir]`, a `[later]` line for each known item that does not exist yet (`not there yet, copied once it exists`: on a fresh install `voice-pairs.jsonl`, which appears after the owner's first reply to a drafted chat), the never list, the rotation and the required process. Read it with the owner. Do not use `--run` from the terminal as the proof: a cloud folder sits inside a privacy protected location, and only the daemon's node parent holds Full Disk Access (`references/pitfalls.md`, pit 1).

### 4 · Run it inside the daemon

Restart, then a planned run, then a real one:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
```

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{"dry_run": true}' > "$LIVE/commands/backup-now.json"
```

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/backup-now.json"
```

Wait for `排班 backup 完成` in the log between the two. Writes: `<backup.dest>/<today>/` and `live/backup-last.json` (0600). A restart after `backup.at` marks tonight's run as done for today; this command file is how a backup runs now.

### 5 · The registry

Under the daemon's entry, the O5 block from `references/vault-proposals.md`, proposal f: reads `live/`, writes `<backup.dest>/<date>/`, switch `backup.enabled`, proof `backup-last.json` shows `"ok": true`.

## Verify

Run each probe with the station's opening block in front of it. Every check must pass.

1. **The backup code runs on this Mac.** Pass: `wa_backup selftest PASS (<n> checks)`. (K258)
   ```sh
   "$PY" "$KIT/daemon/wa_backup.py" --selftest | tail -1
   ```
2. **The plan is complete** (step 3). Pass: `voice-pairs.jsonl` among the items, or on a `[later]` line (`not there yet, copied once it exists`) until the first paired reply writes the file; `never copied: telegram.json · runs/ · commands/ · variants/ · dossier-cli/ · calendar/ · *.log · claude-settings.json (rendered by wa_setup.py init) · backup-last.json · second-source-last.json · people-write.lock (an empty lock file)`; `(dry run: nothing written, backup-last.json not written)`; no `warning: the destination's parent folder is missing, a real run would fail`. (K256)
3. **The daemon backed up** (step 4). Pass: `backup-now 指令: {'dry_run': True}` and a `backup: <dest>/<today> · 0 档 · 0.0 MB (dry-run)` line, then `backed up <dest>/<today>: <n> files, <n> KB; deleted old folders: <names>` from the real run, and no `排班 backup 失败`. (K255)
   ```sh
   grep -e "backup-now 指令" -e "backup: " -e "排班 backup" "$LIVE/wa_live.log" | tail -6
   ```
4. **The last result says ok.** Pass: `True None <today>`. (K261)
   ```sh
   "$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["ok"], d["error"], d["at"][:10])' "$LIVE/backup-last.json"
   ```
5. **Today's copy restores and holds no secret.** Pass: `ok`; the folder lists `state.sqlite` and `config.json` and no `telegram.json`; `drwx------`. (K258, K257, K263)
   ```sh
   B="<backup.dest>/$(date +%F)"
   sqlite3 -readonly "$B/state.sqlite" "PRAGMA integrity_check"
   ls "$B"; stat -f %Sp "$B"
   ```
6. **Every file in `live/` is backed up or left out on purpose.** Pass: each name `ls "$LIVE"` prints is in the plan's items or on its never list (logs such as `stderr.log` fall under `*.log`; `people-write.lock`, the empty lock the person note writer creates at C4, is on the never list), or is one of the files the kit does not need back: the `config.json.bak-*` copies, the rotated `wa_live.log.<n>` files, and `state.sqlite-wal` and `state.sqlite-shm`, whose content the online backup already holds. Anything else is a new state file for the list (pit 12): report it. (K262)
   ```sh
   ls "$LIVE"
   ```

After eight nights: seven dated folders, or `backup.keep` (K259). When the owner agrees, prove the required process once: quit the sync app, drop `backup-now.json`, see `required process not running: <name>` in `backup-last.json` and one error push, start the app again (K260).

## Record

Under `### O5 nightly backup` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` the destination kind and path, `require_process`, `backup.at`, `backup.keep`;
- `did:` the folder created;
- `verify:` the `backed up` line (files and KB), `integrity_check` `ok`, the coverage check's extra names or "none";
- the registry block added;
- `ruled: default 11`: when O10 is on the station list, the note lands at O10 with the standby answer in it; otherwise it lands here, with the one-session rule and the fact that the backup is not encrypted.

Tick `- [x] O5 nightly backup` only when every check passes. Next: the next `- [ ]` line under `## Stations`.
