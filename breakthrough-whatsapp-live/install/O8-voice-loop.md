# Station O8 · Voice loop

**Should drafts learn your voice from what you actually send?** It is already on. What it costs: no model call of its own. When you send a message in a chat within 30 minutes of a fresh draft the brain wrote there, that draft and up to three of your sent texts become one line in `live/voice-pairs.jsonl` (0600, in the nightly backup): the draft, what you sent, the chat's jid, its world, the run id and three times, never the other side's words and never a name. Every judgment in the same world then carries up to `voice.examples` (12) of those pairs in section 8 of its prompt, a few hundred tokens more per run. It reaches nothing outside `live/`.

Why only your own words are kept: `references/defaults-and-why.md`, "7 · Other people's chats never leave the judgment". The switch for each writer: `references/privacy.md`, "Switching each flow off". No default is ruled at this station.

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

1. **Keep it on?** Say what is kept, in the words above. Only text the owner sends is kept (a voice note or an image never joins a pair), and a draft typed on the board without a judgment behind it never opens one.
2. **Only when kept: how many examples per run?** `voice.examples`, default 12. Fewer saves a little of every prompt; more teaches more once enough pairs exist.

## Do

### 1 · Look at what exists now

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_voice.py" --config "$LIVE/config.json" --show
```

Read only. It prints how many legal lines `voice-pairs.jsonl` holds (or `not yet`), the count per world, the pairs open right now, and how many conversations would open a pair if the owner sent a message this minute. It prints jids and counts only, no text.

### 2 · Switch it off, or change the count

Only when the owner said so: with the config edit ritual (`install/00-start-here.md`), set `voice.enabled` to `false` (or `voice.examples`), then restart:

```sh
LABEL="<launchd.label from the progress file header>"
launchctl kickstart -k gui/$(id -u)/$LABEL
```

Switched off, no pair opens, no line is written, section 8 of every prompt reads `(还没有范本)`, and the board's `ui.features.voice` is `false`. The file already written stays; delete it with the owner if they want the kept pairs gone.

## Verify

Run each probe with the station's opening block in front of it. Every check that applies must pass.

1. **The voice code runs on this Mac.** Pass: `selftest: <n>/<n> passed`, the two numbers equal. (K285)
   ```sh
   "$PY" "$KIT/daemon/wa_voice.py" --selftest | tail -1
   ```
2. **The switch is as ruled.** Pass: `True` when kept, `False` when switched off.
   ```sh
   curl -s "http://127.0.0.1:$PORT/api/board" | "$PY" -c 'import json, sys; print(json.load(sys.stdin)["ui"]["features"]["voice"])'
   ```
3. **A pair opens and closes** (kept only; after the owner replies from the phone to a chat whose fresh draft is on the board, then 31 minutes). Pass: `语气对 <name>: opened` (and `added` for a second message), then `语气对: 关了 <n> 个 (30 分钟到)`. (K286, K287)
   ```sh
   grep "语气对" "$LIVE/wa_live.log" | tail -3
   ```
4. **A kept line holds only the contract** (after check 3). Pass: every line prints `['closed', 'closed_at', 'draft', 'jid', 'opened_at', 'run_id', 'sent', 'world']`, and the file is `-rw-------`. (K288)
   ```sh
   "$PY" -c 'import json, sys; [print(sorted(json.loads(l))) for l in open(sys.argv[1]) if l.strip()]' "$LIVE/voice-pairs.jsonl"
   stat -f %Sp "$LIVE/voice-pairs.jsonl"
   ```
5. **The brain sees the same world's pairs** (the next run in that world). Pass: `section 8 would get (world=<world>, <n> pairs):` with the pair, and the same text under `## 8 · 语气范本` in that run's `prompt.md`. (K289)
   ```sh
   "$PY" "$KIT/daemon/wa_voice.py" --config "$LIVE/config.json" --show --world '<world key>'
   ```

## Record

Under `### O8 voice loop` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` kept or switched off (with the owner's reason), `voice.examples` when changed;
- `did:` the `--show` counts (lines, open pairs);
- `verify:` the `语气对` lines seen, or `skipped: check 3 to 5 because switched off`.

Tick `- [x] O8 voice loop` (or `- [x] O8 voice loop (switch off)`) only when every check that applies passes; checks 3 to 5 may be ticked during O11's observation week when no reply happened today. Next: the next `- [ ]` line under `## Stations`.
