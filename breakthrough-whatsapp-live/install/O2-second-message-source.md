# Station O2 · Second message source

**Should yesterday's conversations in your work chat app reach your person notes and your intake buffer, once a night?** What it costs: at most one model call a night on `brain.model`, and only when someone other than the owner wrote in a whitelisted chat that day. At `second_source.at` the daemon reads yesterday's messages of the chats you whitelist, through that app's own command line tool logged in as you. It writes one recent line per person into the person note carrying that person's id key (only while `dossier.enabled` is true), captures into the intake buffer (only while `intake.enabled` is true), and `live/second-source-last.json` for the digest. Nothing appears on the board and nothing is sent. It reaches only the whitelisted chats. This version ships one implementation, `lark`; for any other app the station is skipped with `adapter wanted:` logged at C5, until an adapter of the owner's own sits in `live/adapters/` (`references/interfaces.md`, "Where a new implementation lives").

The tool was chosen at `install/C5-your-tools.md`, question 2. The contract, the whitelist file and the Lark implementation: `references/interfaces.md`, "2 · Second message source". Why nightly and never on the board: `references/defaults-and-why.md`, "13 · A second message source is pulled nightly".

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

## Before the questions

`lark-cli` must be installed and logged in as the owner, by the owner, in the terminal on this Mac (never over ssh). This session never handles that login. When the binary is missing, `wa_setup.py doctor` names it as a blocker as soon as `second_source.source` is `lark`, and `check-config` reports `bin.lark_cli: not found` once `second_source.enabled` is true; the dry run in step 3 reports the login.

## Ask

One question at a time. Read default 13 before question 1.

1. **Which chats hold work worth remembering?** Go through them with the owner, one by one, and decide `pull` for each: a group or one-to-one chat where customers, partners or the team talk. Helper chats (bots, customer support, assistants) are never pulled whatever the file says. The kit has no chat lister: the owner reads each chat's id (it starts with `oc_`) from Lark.
2. **The rule in one sentence**, for the whitelist's `rule` field, for example "work chats with customers and the team".
3. **Which world does each chat belong to?** A world key from `vocab.worlds`; it is only a guess written on intake entries.
4. **Who in those chats has a person note?** A person is matched only by the id stored under `lark_id` in their note, never by name. Ask whether the doctrine's `person` family may carry `lark_id` (proposal a in `references/vault-proposals.md`); without it the frontmatter guard refuses the key and no recent line is ever written.
5. **When should it run?** `second_source.at`, default `03:10`, after the facts job and before the backup and the digest.

## Do

### 1 · The whitelist file

Write `live/second-source-whitelist.json` with the Write tool, in the shape `references/interfaces.md` shows under "The whitelist file": `rule`, `decided_at` (today), and one entry per chat with `chat_id`, `name`, `kind` (`group` or `direct`), `pull` and `world`. Chats the owner decided against stay in the file with `"pull": false`, so the decision is recorded. Then:

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
chmod 600 "$LIVE/second-source-whitelist.json"
```

Writes: the whitelist (0600). It is in the nightly backup.

### 2 · Config, still switched off

With the config edit ritual (`install/00-start-here.md`), set `second_source.source` to `lark`, `second_source.at`, and `second_source.lark.whitelist` only when the file lives somewhere other than the default `<live>/second-source-whitelist.json`. Leave `second_source.enabled` at `false`.

### 3 · Pull one day from the terminal

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_second_source.py" --config "$LIVE/config.json" --dry-run
```

It pulls yesterday (or `--day YYYY-MM-DD`), calls no model and writes nothing. It prints the whitelist counts, the excluded chats with their reasons, one line per chat with message counts, speakers and the world guess, the skipped chats, and whether the nightly run would call the model. Pick one chat and count that day's messages in Lark with the owner: the numbers must agree.

### 4 · Map people to their Lark ids

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_second_source.py" --config "$LIVE/config.json" --probe-members --chat-id '<one chat id>'
```

Read only. It lists the members of that chat (chats up to 300 people), then every person note without `lark_id` with its name candidates. For each person the owner confirms, add one line `lark_id: <the ou_ id>` to that note's frontmatter, with the owner's yes, one note at a time. Lark ids differ per app login: they must come from this same `lark-cli` login.

### 5 · Switch it on and run it inside the daemon

With the config edit ritual, set `second_source.enabled` to `true`, run `check-config`, and restart:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
```

Then pull inside the daemon, first without the model, then for real (one model call when others spoke yesterday):

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{"dry_run": true}' > "$LIVE/commands/second-source-now.json"
```

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/second-source-now.json"
```

Wait for `排班 second_source 完成` in the log between the two. Writes: `live/second-source-last.json` (0600); `live/runs/second-source-<time>/` with `prompt.md` and `result.txt` when the model was called; recent lines in mapped notes; captures in the intake buffer.

### 6 · The registry

Under the daemon's entry, the O2 block from `references/vault-proposals.md`, proposal f: reads yesterday's whitelisted chats, writes recent lines, intake entries and `live/second-source-last.json`, switch `second_source.enabled`, proof `second-source-last.json` shows `ok`.

## Verify

Run each probe with the station's opening block in front of it. Every check must pass.

1. **The code runs on this Mac.** Pass: `selftest: <n>/<n> passed` twice, the two numbers of each line equal. (K236)
   ```sh
   "$PY" "$KIT/daemon/wa_second_source.py" --selftest | tail -1
   "$PY" "$KIT/daemon/adapters/second_source_lark.py" --selftest | tail -1
   ```
2. **The whitelist is the owner's decision** (after step 1). Pass: `['chats', 'decided_at', 'rule']`, the number of chats, the number with `pull` true, then `-rw-------`. (K225)
   ```sh
   "$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(sorted(d), len(d["chats"]), sum(1 for c in d["chats"] if c.get("pull") is True))' "$LIVE/second-source-whitelist.json"
   stat -f %Sp "$LIVE/second-source-whitelist.json"
   ```
3. **One day pulls with the owner's own id** (step 3). Pass: `whitelist <n> · pullable <p> · excluded <e>`; `day <day> · <c> chats <m> messages · owner id <id>` with a real id, never `?`; each chat line ends `world <key>`; `errors 0`; the counted chat agrees with Lark. (K224, K227, K226, K228)
4. **Ids are unique** (after step 4). Pass: the number of mapped notes, then `0` conflicts. (K231)
   ```sh
   "$PY" -c 'import sys; sys.path.insert(0, sys.argv[1] + "/daemon"); import wa_common as W, wa_second_source as S; c = W.load_config(sys.argv[2]); print(len(S.notes_by_source_id(c)), len(S.source_id_conflicts(c)))' "$KIT" "$LIVE/config.json"
   ```
5. **The daemon pulled** (after step 5). Pass: `second-source-now 指令: {'dry_run': True}` with a `second_source: 拉 <n> 会话 <m> 封 · dry-run 不叫模型 (errors 0)` line, then `second source lark: pulled <n> chats <m> msgs · recent +<k> · captures +<j>` from the real pull, with no `NOT OK` and no `排班 second_source 失败`. (K223, K232)
   ```sh
   grep -e "second-source-now 指令" -e "second_source: " -e "second source lark" -e "排班 second_source" "$LIVE/wa_live.log" | tail -6
   ```
6. **The last pull is recorded** (after step 5). Pass: `True`, yesterday's date, and `claude_called` `True` when someone other than the owner wrote. (K233, K234)
   ```sh
   "$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["ok"], d["day"], d.get("claude_called"), d["recent"], d["captures"])' "$LIVE/second-source-last.json"
   ```
7. **Nothing reached the board.** Pass: `[]`. (K237)
   ```sh
   curl -s "http://127.0.0.1:$PORT/api/board" | "$PY" -c 'import json, sys; print([r["jid"] for r in json.load(sys.stdin)["rows"] if not r["jid"].endswith(("@s.whatsapp.net", "@lid", "@g.us"))])'
   ```

After the first night: the digest's health block reads `Lark pull ✓ (<day>, <n> chats <m> messages)` (station O6).

## Record

Under `### O2 second message source` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` the whitelist rule, how many chats pulled and how many decided against, `second_source.at`;
- `did:` the dry run's `day` line and `errors` count;
- `did:` how many notes got `lark_id`, and that the doctrine allows the key;
- `verify:` the daemon's `second source lark: pulled` line and `second-source-last.json` `ok`;
- the registry block added;
- `ruled: default 13`: station C5 already landed its note; write a superseding note only when the answer changed here (for example the owner decided at this station not to pull after all).

Tick `- [x] O2 second message source (lark)` only when every check passes. Next: the next `- [ ]` line under `## Stations`.
