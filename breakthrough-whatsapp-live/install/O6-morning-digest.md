# Station O6 · Morning digest

**When should yesterday's summary arrive, and should it reach your phone, or should the digest be switched off?** It is already on: 08:00, written to `live/digest/`, shown in the board's "Yesterday" card, and pushed once an outlet exists. What it costs: no model call. At `digest.at` the daemon reads the state database and the result files in `live/` and writes `live/digest/<yesterday>.md` (0600, in the nightly backup): how many messages passed the gate and how many runs they cost, who still waits on you (names and a situation line each), stage moves, person note jobs, facts mismatches (names), and a health block for every connected part. The pushed short form carries the same names through the outlet. It is the only scheduled push, and on a quiet day it still arrives, which is what makes silence trustworthy.

Why one digest and errors only: `references/defaults-and-why.md`, "6 · Pushes only for errors, plus the morning digest". The outlet it pushes through: `references/interfaces.md`, "4 · Push outlet", set at station O4.

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

One question at a time. Read default 6 before question 1.

1. **When should it arrive?** `digest.at`, default `08:00`. The nightly jobs it reports on run earlier (facts 03:00, second source 03:10, backup 03:30), so a time before those leaves their health lines a day behind.
2. **Pushed, or only on the board and in the file?** `digest.push`, default `true`. Without an outlet (O4 skipped) it is written and shown on the board only.
3. **Only when the owner asked for it at C5: switch it off?** `digest.enabled` `false`. Say what is lost: a quiet day and a dead daemon then look the same from the phone, and the board's "Yesterday" card stays empty.

The digest's language follows `board.lang`.

## Do

### 1 · Config

With the config edit ritual (`install/00-start-here.md`), set `digest.at` and `digest.push`, or `digest.enabled` to `false` when switched off. Then restart:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
```

A restart after `digest.at` marks today's digest as sent without sending it (the log says `排班 digest: 启动时已过 <time>, 今天记成跑过`); step 3 sends it now instead. When switched off, the log says `排班 digest 没开 (config enabled=false 或模组没载入)`, and the station ends at Verify check 1.

### 2 · Yesterday's digest, from the terminal

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_digest.py" --config "$LIVE/config.json"
```

The default is a dry run: it prints the text, `---- <n> lines, empty=<True or False> ----` and the data behind it, and writes and pushes nothing. `--for YYYY-MM-DD` builds another day. Read it with the owner: the names in "still waiting on you" should be the people they really owe a reply.

### 3 · Written by the daemon, then pushed

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{"send": false}' > "$LIVE/commands/digest-now.json"
```

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/digest-now.json"
```

The first writes `live/digest/<yesterday>.md` and pushes nothing; the board's "Yesterday" card appears. The second writes it again and pushes the short form. Wait for `排班 digest 完成` in the log between the two.

### 4 · The registry

Under the daemon's entry, the O6 block from `references/vault-proposals.md`, proposal f: reads the state database and `live/`, writes `live/digest/` and one push, switch `digest.enabled` and `digest.push`, proof the digest arrives, even on a quiet day.

## Verify

Run each probe with the station's opening block in front of it. Every check must pass.

1. **The digest code runs on this Mac, and the schedule is right.** Pass: `wa_digest selftest PASS (<n> checks)`; the newest `排班 digest` line from the restart names the ruled time or says the digest is off. (K271)
   ```sh
   "$PY" "$KIT/daemon/wa_digest.py" --selftest | tail -1
   grep "排班 digest" "$LIVE/wa_live.log" | tail -1
   ```
2. **The text is yesterday's business** (step 2). Pass: the header with yesterday's date; a volume line (`Yesterday <n> messages through the gate · <n> runs (ok <n> / failed <n>) · <n> saved`, or in Chinese `昨天 <n> 封进闸`), or the quiet day line; a `Health:` (`健康: `) line; at most 12 lines; the owner agrees with the names. (K267, K268, K270)
3. **The daemon wrote it without pushing** (step 3, first file). Pass: `digest-now 指令: {'send': False}`, then `digest <date>: 只写档不推 (<n> 行; send=False · digest.push=<value> · notify.mode=<mode>)`; the file is `-rw-------`. (K266)
   ```sh
   grep -e "digest-now 指令" -e "digest 20" "$LIVE/wa_live.log" | tail -4
   ls -l "$LIVE/digest"
   ```
4. **The board shows it.** Pass: `200`, then the date and the number of lines; the "Yesterday" card opens to the full text. (K265)
   ```sh
   curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$PORT/api/digest"
   curl -s "http://127.0.0.1:$PORT/api/digest" | "$PY" -c 'import json, sys; d = json.load(sys.stdin); print(d["date"], len(d["lines"]))'
   ```
5. **The push arrived** (step 3, second file; skip when `digest.push` is false or there is no outlet). Pass: `digest <date>: 已推 (<n> 行)`, and the phone shows the short form with bold block titles, ending with the line that points at the board's yesterday card. (K272)
6. **Every kind of run is counted.** Pass: one row per kind that ran yesterday (`direct`, `group`, `dossier`, `second_source`, `backfill`), matching the digest's first block. (K264)
   ```sh
   sqlite3 -readonly "$LIVE/state.sqlite" "select kind, count(*) from runs where started_at >= date('now', 'localtime', '-1 day') and started_at < date('now', 'localtime') group by kind"
   ```

The next morning: the digest arrives at the ruled time without a command file, and its health block has a part for every station connected so far (K269).

## Record

Under `### O6 morning digest` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` `digest.at`, `digest.push`, or switched off with the owner's reason;
- `verify:` the `digest-now` result lines, the `/api/digest` date, that the push arrived;
- the registry block added;
- `ruled: default 6`, one note holding the outlet and mode from O4 and the digest's time and push from here (when O4 was skipped, the note says alerts stay in the log).

Tick `- [x] O6 morning digest` (or `- [x] O6 morning digest (switch off)`) only when every check that applies passes. Next: the next `- [ ]` line under `## Stations`.
