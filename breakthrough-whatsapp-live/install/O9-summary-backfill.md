# Station O9 · Summary backfill

**Should the busy conversations that were never judged get a starting summary now, so the brain remembers them from their first run?** What it costs: the heaviest single use of the model in the kit. Every conversation the gate passes with at least `backfill.min_msgs` (10) content messages in the last `backfill.days` (30) and no summary yet is read through the reader (voice notes transcribed locally first, slow on voice-heavy accounts), trimmed to at most 8000 characters for a one-to-one chat and 6000 for a group, and sent in batches of `backfill.batch_size` (10) conversations per call on `brain.model`: about 70,000 characters of other people's messages per call, sent to Anthropic like any run. It writes only each conversation's `summary` and `grounded_at` in the state database (never a draft, a world or a stage, never over a summary the brain wrote), rewrites `live/summaries.md`, and leaves run folders under `live/runs/backfill-<time>-b<n>/`. It runs only when started, never by itself.

The gate it follows: `references/defaults-and-why.md`, "2 · The gate: every one-to-one chat passes, groups follow your whitelist" (`backfill.respect_gate`). Why a run reads what it reads: "4 · Speed and judgment quality over token savings". No default is ruled at this station, unless the gate passes no chat (step 1).

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

One question at a time, after step 1 has printed the count.

1. **Now, later, or never?** Show the candidate count and the number of calls it means (candidates divided by 10, rounded up). Without a backfill nothing breaks: each conversation builds its summary on its own first run.
2. **A small first batch?** Ten conversations (one call) first, read two summaries with the owner, then the rest.
3. **Many voice notes?** Then warm the transcript cache first, overnight: no model call, local CPU only, and the backfill's reader then finds every transcript ready.

## Do

### 1 · The candidates

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_backfill.py" --config "$LIVE/config.json" --list
```

Read only, writes nothing. It prints the count with the groups, the one-to-one chats and how many chats the gate kept out, then the first 15 candidates with their message counts, names and jids.

`0 candidates` while most chats are kept out by the gate (a gate that passes no chat on purpose, or an owner who judges only with the board's buttons): the station has nothing to read and checks 3 to 5 cannot pass. Either log `skipped: O9 because the gate passes no chat` and tick the line, or re-rule `backfill.respect_gate` first (default 2, with its own decision note), then run step 1 again.

### 2 · Warm the voice cache (only on a yes to question 3)

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/prewarm-30.json"
```

The daemon transcribes every voice note of the last 30 days into `<home>/transcripts-cache/` in the background and logs `prewarm 完成 <s>s: <summary>` when done. Wait for that line before step 5.

### 3 · The first prompt, from the terminal, without the model

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_backfill.py" --config "$LIVE/config.json" --dry-run --limit 2 --no-transcribe
```

Reads two conversations, builds the batch prompt from the owner copy `live/prompts/backfill-prompt.md`, and calls no model. Writes one run folder with `prompt.md` and the reading packs; nothing in the state database.

### 4 · The same inside the daemon

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{"dry_run": true, "limit": 2}' > "$LIVE/commands/backfill-summaries.json"
```

The daemon's own reader, under launchd, which is how the real batches run. No model call. Wait for the `backfill 完成` line.

### 5 · The first real batch

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{"limit": 10}' > "$LIVE/commands/backfill-summaries.json"
```

One model call. Opening an image or a file costs the model one turn. So a batch runs with its own turn limit, `backfill.max_turns` (20), and its reading packs keep the local path on at most `backfill.max_images` (4) image or file lines, newest first, taking turns across the batch's chats; every other media line keeps its label and caption but shows `(路径略)` instead of a path (a batch that had more logs `backfill: backfill b<n>: media paths kept <k>, dropped <d> (backfill.max_images <n>)`), so the model cannot open more. An image-heavy batch can still run out of turns: it logs `backfill: backfill b<n>: failed, all <k> retried next time: model returned is_error: error_max_turns: Reached maximum number of turns (<n>)`, its `runs` row carries the same reason in `error`, and its conversations stay candidates. Then lower `backfill.batch_size` or `backfill.max_images`, or raise `backfill.max_turns` (config edit ritual, restart), and drop the file again (`references/troubleshooting.md`, "A summary backfill batch fails").

When `backfill 完成` appears, read two of the new summaries with the owner in this session (they are other people's conversations: read, never copied into the progress file):

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
sqlite3 -readonly "$LIVE/state.sqlite" "select name, summary from conversations where last_run_id is null and trim(summary) <> '' order by grounded_at desc limit 2"
```

### 6 · The rest

On the owner's yes, drop the command file without a limit, or with a larger one in steps:

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/backfill-summaries.json"
```

Batches run one after another, and the summary table is rewritten after each. The conversations of a batch whose answer cannot be read stay candidates for the next command file, with the raw answer kept in that batch's `result.txt`; a backfill that stops on an exception pushes one error, at most once an hour. Only one backfill runs at a time: a second file meanwhile logs `backfill: 已经在跑, 这次指令略过`.

### 7 · The registry

Under the daemon's entry, the O9 block from `references/vault-proposals.md`, proposal f: reads chats the gate passes, writes summaries in the state database, switch "only runs when started", proof `wa_backfill.py --list` shows no skipped groups.

## Verify

Run each probe with the station's opening block in front of it. Every check must pass.

1. **The backfill code runs on this Mac.** Pass: `selftest: <n>/<n> passed`, the two numbers equal. (K295)
   ```sh
   "$PY" "$KIT/daemon/wa_backfill.py" --selftest | tail -1
   ```
2. **The candidates follow the gate** (step 1). Pass: the count line ends with `gate respected, <n> chats kept out by the gate)`, and no group the owner set to `skip` is listed. (K291, K292)
3. **The prompt builds without the model** (steps 3 and 4). Pass: the terminal's last line `<c> candidates · 2 this time · prompt [<n>] chars · reader failures 0 · <s>s · run_dir [<folder>]`; that folder holds `prompt.md` with no placeholder left and no `result.txt`; the daemon logs `backfill-summaries 指令: {'dry_run': True, 'limit': 2}` and `backfill 完成 (<s>s): 候选 <c> · 1 批 · 写 0 条 · 失败 0 条 (dry-run)`. (K299, K298, K294, K293)
   ```sh
   R=$(ls -td "$LIVE"/runs/backfill-* | head -1); ls "$R"; grep -c "{JID_LIST}" "$R/prompt.md"
   grep -e "backfill-summaries 指令" -e "backfill 完成" "$LIVE/wa_live.log" | tail -2
   ```
4. **The first batch wrote summaries and nothing else** (step 5). Pass: `backfill: backfill: <c> candidates, processing <n> in 1 batches`, `backfill: backfill b1: wrote <w>`, `backfill 完成 (<s>s): 候选 <c> · 1 批 · 写 <w> 条 · 失败 <f> 条`; the count of summaries without a judgment grew by `<w>`; a `backfill` row in the runs table; `0` failed rows without a reason (a failed batch names its reason, for example `error_max_turns: ...`, and step 5 says what to change). (K297, K296, K254, K318)
   ```sh
   grep "backfill" "$LIVE/wa_live.log" | tail -4
   sqlite3 -readonly "$LIVE/state.sqlite" "select count(*) from conversations where last_run_id is null and trim(summary) <> ''"
   sqlite3 -readonly "$LIVE/state.sqlite" "select count(*), sum(ok) from runs where kind = 'backfill'"
   sqlite3 -readonly "$LIVE/state.sqlite" "select count(*) from runs where ok = 0 and (error is null or error = '')"
   ```
5. **The summary table grew.** Pass: `# 对话摘要表 (daemon 生成, <time>, <n> 条)` with a larger `<n>` than before step 5.
   ```sh
   head -1 "$LIVE/summaries.md"
   ```
6. **Only failures remain** (after step 6). Pass: `--list` shows zero candidates, or only the conversations of failed batches. (K292)
   ```sh
   "$PY" "$KIT/daemon/wa_backfill.py" --config "$LIVE/config.json" --list
   ```

## Record

Under `### O9 summary backfill` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` now, later or never; the size of the first batch; prewarm yes or no;
- `did:` the `--list` count line before (counts only);
- `verify:` each `backfill 完成` line, and that the owner read two summaries and agreed;
- `verify:` the `--list` count line after;
- the registry block added.

Tick `- [x] O9 summary backfill` only when every check passes, or log `skipped: O9 because <reason>` when the owner chose later or never, or when the gate passes no chat (step 1). Next: the next `- [ ]` line under `## Stations`.
