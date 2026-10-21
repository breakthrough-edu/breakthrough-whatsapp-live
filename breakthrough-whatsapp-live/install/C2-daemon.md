# Station C2 · The daemon

The daemon is the resident program. It watches WhatsApp Desktop's database, runs every new message through the gate, starts one headless Claude run per burst that passes, and serves the board. This station rules the gate and the vocabulary with the owner and writes them into config, installs the daemon under launchd with node as its parent, proves that Claude answers from launchd, runs one judgment by hand, and only then lets the daemon run on its own, with pushes limited to errors.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="<launchd.intake_label from the progress file header>"
PORT="<board.port from the progress file header>"
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs.

## Ask

One question at a time. Before each question that names a default, read that default in `references/defaults-and-why.md` and give the owner its reason in a sentence or two.

1. **The gate** (default 2). Show the groups from `<home>/measure-3d.json` with their three day inbound counts, busiest first, and ask for each: `all` (every message can wake the brain), `mention` (only when the owner is named or @ mentioned) or `skip`. Groups left off the list follow `unlisted_group_policy`, `skip` by default. One-to-one chats follow `direct_policy`: `all` (the default) or `skip`. Any chat to leave out entirely goes in `ignored_jids`. Say what the ticks cost: roughly, every burst that passes is one run, and quick messages from one chat within the quiet gap become one burst.
2. **Faster drafts or fewer runs?** (default 4) The defaults: a run starts after 45 quiet seconds, or 180 seconds after the first message at the latest, two runs at a time. `measure` prints no gaps between messages, so explain the trade in default 4 (a longer wait delays every draft and saves only the bursts whose gaps fall between the old and the new limit), then ask. With the same window, read default 15: a message that passed the gate is read again for 30 minutes (`edit_watch.minutes`), and an edit that changes it is judged again; ask whether that fits (`edit_watch.enabled`).
3. **How is Claude paid for on this Mac?** (default 3) Run `claude auth status` together. On a subscription, the dollar figure a run reports is not billed and the board shows tokens only; with API billing every run is real money and `board.show_usd` can show it. Then: may person notes use opus (`dossier.model`), or should a lighter model write them?
4. **The board's language**: English or Chinese (`board.lang`, `en` or `zh`). The vocabulary labels below go into the field that language shows: `label_en` for `en`, `label` for `zh`.
5. **Worlds** (default 8): which parts of your life and which businesses should conversations be sorted into? Each world gets a key (lowercase letters), a label in the owner's words, and `business` true or false. Only business worlds carry a stage.
6. **Stages and priorities** (default 8). Keep the seven stage keys (`new talking offered won active done parked`: station C4 makes the vault use the same keys) and ask what each is called in the owner's words, and whether 21 quiet days before `parked` fits their sales rhythm (`stage_rules.parked_after_days`). Priorities keep `now soon low`, with labels in the owner's words.
7. **What does each business sell?** (`vocab.business_context`) One paragraph written with the owner: what each business world sells, the price points, how people pay. The brain reads it on every run.
8. **Other people's conversations** (default 7). Read the five parts and ask the owner to confirm or narrow them. Then the topic map: which vault notes answer the questions people actually ask (project briefs, SOPs, decisions, a price and link registry), and which words point at each?
9. **A conversation for the first judgment**: one chat whose last message is from the other side, ideally one the gate passes.
10. **Later, once the daemon runs** (step 12): should it run on its own setup token? Ask it only after step 11 and its checks pass.

## Do

### 1 · Write the rulings into config

One ruling per edit, each with the config edit ritual (`install/00-start-here.md`):

- `direct_policy`, `unlisted_group_policy`, `ignored_jids`
- `debounce.quiet_seconds`, `debounce.max_wait_seconds`, `debounce.global_concurrency`, when changed
- `edit_watch.enabled`, `edit_watch.minutes`, when changed (question 2)
- `board.lang`, `board.show_usd`, `dossier.model`, when changed
- `vocab.worlds`, the labels in `vocab.stages` and `vocab.priorities`, `vocab.business_context`, `stage_rules.parked_after_days`

The whitelist has one entry per ticked group, keyed by its jid: `{"policy": "all", "name": "<group name>", "in30": <inbound messages in 30 days>, "defaulted": false}`. Take the 30 day counts from a second measure, then write the entries:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"; PY=/usr/bin/python3
"$PY" "$KIT/daemon/wa_setup.py" measure --days 30 --top 0 --json --config "$LIVE/config.json" > "$HOME_DIR/measure-30d.json"
chmod 600 "$HOME_DIR/measure-30d.json"
cp -p "$LIVE/config.json" "$LIVE/config.json.bak-$(date +%Y%m%d-%H%M%S)"
"$PY" - "$KIT" "$LIVE/config.json" "$HOME_DIR/measure-30d.json" <<'EOF'
import json, sys
sys.path.insert(0, sys.argv[1] + "/daemon")
import wa_common
TICKS = {}   # the owner's ticks, filled in by this session: {"<group jid>": "all" | "mention" | "skip"}
cfg = json.load(open(sys.argv[2], encoding="utf-8"))
m30 = dict((g["jid"], g) for g in json.load(open(sys.argv[3], encoding="utf-8"))["top_groups"])
gp = cfg.setdefault("group_policies", {})
for jid, policy in TICKS.items():
    g = m30.get(jid, {})
    gp[jid] = {"policy": policy, "name": g.get("name", ""), "in30": g.get("inbound", 0), "defaulted": False}
wa_common.write_json_atomic(sys.argv[2], cfg, mode=0o600)
print("group_policies:", len(gp))
EOF
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
```

Writes: `<home>/measure-30d.json` (0600), a config backup, and `live/config.json` (0600, rewritten whole with the new entries). Groups the owner skips in bulk without looking at them may be written with `"policy": "skip"` and `"defaulted": true`, or left off the list.

### 2 · The topic map

Edit `live/topic-map.json`, the owner copy, with the Edit tool. Its example rules describe an invented baking school: replace them. Each rule is `{"keys": [words people use], "files": [paths relative to the vault root]}`; when a burst contains a key, the listed notes are offered to the brain, which opens at most two per run. The daemon reads the file on every run, so no restart is needed.

### 3 · The brain prompt

`live/prompts/brain-prompt.md` is the owner copy of the prompt every judgment runs on. Its placeholders (`{OWNER_LABEL}`, `{WORLDS_BLOCK}`, `{BUSINESS_CONTEXT}` and the rest) are filled from config on every run, so the vocabulary and the business paragraph live in config only. The prompt is written in Chinese; drafts still follow the language of each conversation, so it needs no translation. Editing it is legal, and every later update turns the edit into a merge (`install/update.md`), so leave it unless the owner has a reason.

### 4 · The cage

`init` rendered `live/claude-settings.json`: every tool but Read, Glob and Grep denied, all hooks off, and 27 absolute Read deny rules for the push token, the config, the state database, other runs, the reader's working copies, the document text cache, the voice transcript cache, the `secrets/` folder, WhatsApp's own databases, SSH keys, the keychain and credential files (the full list: `references/privacy.md`, "The Read deny rules, and why"). Check it with the Verify rows below. The proof that a deny rule really blocks on this Mac is the canary in `references/pitfalls.md`, pit 10 (one model call, run in the terminal on this Mac, never over ssh); offer it. Once the daemon runs (after step 8), `echo '{"read_canary": true}' > "$LIVE/commands/probe-claude.json"` proves the same from launchd for the `secrets/` folder and the `transcripts-cache/` folder, using the two markers `init` wrote (one tiny model call per folder; K333, K351).

### 5 · The registry entry, before the install

Write the daemon's six cells (read, write, limits, stop, proof, rollback) into the owner's machine registry note, or into `<home>/INSTALLED.md` when they keep none: `references/vault-proposals.md`, proposal f, "The daemon", with the real label, port and paths. Installing a resident job and writing its entry are one act.

The entry's first line also records which code this Mac runs, with today's date: a standby Mac compares against it on a day this Mac cannot be asked (the standby runbook's step 2; station O10 checks it, and `install/update.md` refreshes it after every update).

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live
shasum -a 256 "$KIT/daemon/wa_live.py"
grep -m1 '^VERSION' "$KIT/daemon/wa_live.py"
```

### 6 · A quiet first start

Set `doorbell.enabled` to `false` (config edit ritual). The daemon then starts, serves the board, answers command files and logs every inbound message, but runs nothing by itself until the probe and the first judgment below have passed. Nothing is lost: messages that arrive meanwhile are logged, and the owner can judge any chat from the board later.

### 7 · Render and install the LaunchAgent

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"; PY=/usr/bin/python3
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" render-launchd --config "$LIVE/config.json" --dry-run
"$PY" "$KIT/daemon/wa_setup.py" render-launchd --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" install-launchd --config "$LIVE/config.json" --dry-run
"$PY" "$KIT/daemon/wa_setup.py" install-launchd --config "$LIVE/config.json"
```

`check-config` must show 0 errors before anything renders. `render-launchd` writes `<home>/launchd/run.mjs` and `<home>/launchd/<label>.plist` (0644) and checks the plist it rendered against its own rules: node is the program, `KeepAlive`, `ExitTimeOut` 90, stdout to `/dev/null`, stderr to `live/stderr.log` (launchd creates that file; the daemon sets it to 0600 when it starts, so it needs no chmod), a working folder outside Documents. `install-launchd` copies the plist to `~/Library/LaunchAgents/<label>.plist`, enables the label, boots out an already loaded copy of this same label, bootstraps it, and prints `state` and `pid`. For a label it refuses, it exits 3 and runs nothing.

### 8 · Prove Claude answers from launchd

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/probe-claude.json"
```

Within seconds the daemon takes the file, deletes it, and calls one minimal `claude -p` with the same cage flags as a real run (one tiny model call). Wait about a minute. Writes: `live/runs/probe-<time>/` with `cmd.txt`, `stdout.jsonl` and `stderr.txt`, plus `sample.txt` only when the probe hung for 90 seconds.

- `sample.txt` exists and its stack sits in `__open`: node lacks Full Disk Access (C0 step 6, `references/pitfalls.md`, pit 1). Grant it to the node `bin.node` names, restart with `wa_setup.py restart` (step 11 shows the command), probe again.
- The output names a login problem: open Claude Code in the terminal on this Mac itself (not over ssh), log in, probe again.

### 9 · Warm the voice cache (voice-heavy accounts)

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/prewarm-7.json"
```

Transcribes every voice note in the brain's seven day window into the shared cache in the background, so the first runs on voice-heavy chats do not hit the reader's time limit. It covers every chat in the window, whatever the gate says, so the Mac transcribes voice notes from chats that will never get a run. No model call, and it does not block runs, but on a busy account it takes minutes. Writes `live/runs/prewarm-<time>-7d.json` and transcript files in `<home>/transcripts-cache/`.

Wait for `prewarm 完成` (done), or `prewarm 失败` (failed), in `$LIVE/wa_live.log` before any restart, step 11's included: a restart takes a running prewarm down with the daemon, and the next start runs it again from the beginning (`prewarm 7 天上次没跑完 (多半被重启切断), 补跑`: cut short by a restart, run again), so the minutes already spent are spent twice. Only a prewarm killed mid-run like this is run again: one that returned, finished, failed or timed out, clears its mark and is not. Log the line and its seconds.

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
grep -E "prewarm (开始|完成|失败)" "$LIVE/wa_live.log" | tail -3
```

### 10 · The first judgment, by hand

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/test-run.<jid>.json"
```

For example `test-run.60123456789@s.whatsapp.net.json`. The chat's last three inbound messages run as one burst, gate or no gate (one model call on `brain.model`). Writes: `live/runs/<time>-<jid with @ as _>/` holding `sweep.json`, `sweep.md`, `prompt.md`, `claude.cmd.txt`, `claude.stdout.jsonl` and `judgment.json` (plus `context.json` and `context.md` with a vault), one row in the `runs` table, the conversation's row in `live/state.sqlite`, and `live/summaries.md`.

Open `http://127.0.0.1:$PORT/` with the owner. The conversation shows its three chips, the situation and the draft. Read the draft together: facts only from the chat, anything only the owner can supply left in `[brackets]`, no money, date or promise made on their behalf.

### 11 · Observation: the daemon on its own

With the config edit ritual, set `doorbell.enabled` to `true` and leave `notify.mode` at `errors`. Until an outlet is connected at O4, errors are written to `live/wa_live.log` only. When step 9 started a prewarm, restart only after it has logged `prewarm 完成` (or `prewarm 失败`):

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
```

This is the restart every later station uses. It stops the python child with SIGTERM, gives running jobs up to `ExitTimeOut` (90 seconds) to finish, SIGKILLs a python that is still alive after that (a frozen one never runs its stop handler), lets launchd start a fresh node and python, and proves the result: its `after:` line names one python, a child of the new node and the only listener on the board port, then the `/health` answer, and the last line is `proof: one daemon, the new code is loaded (...)`. Exit 1 is an error: each one prints as an `  ERROR …` line and no `proof:` line follows; exit 3 is a refusal (for example the board port held by a program that is not this daemon), and nothing was signalled. `--dry-run` prints the plan and runs nothing. Never restart the daemon with `launchctl kickstart -k`: it stops only the node parent and can leave a frozen python holding the port (`references/pitfalls.md`, pit 15).

Then wait for a real message in a chat the gate passes, or ask a friend to send one. A run follows within a minute of the last message of the burst. When the owner's gate passes nothing on purpose (`direct_policy` `skip`, and no group at `all` or `mention`), no message can start a run: wait for any real inbound message instead, and check 12 reads its branch for that gate.

### 12 · Optional: the daemon on its own setup token

Only after step 11 and Verify checks 1 to 12 pass. Optional, and the default is no: the daemon keeps using the Claude Code login of this Mac user.

**Ask.** One question: **should the daemon run on its own long lived token, so that a login that expires on the desk does not stop it?** The reason: the login Claude Code keeps for the desktop can expire or be signed out, and a background job cannot sign in again by itself, so every run fails until someone logs in on this Mac (the circuit breaker holds the queue meanwhile, and pushes once). What it costs: the owner runs `claude setup-token` once, in their own terminal, and writes the token into the file below, never into this chat (rule 9 in `SKILL.md`); and renews it before it expires, about a year later. The risk: a long lived secret on disk that can use their Claude account and shares its usage. A no is logged and nothing below runs. Only on a yes:

**Do.**

1. The owner, in their own terminal on this Mac, runs `claude setup-token` and keeps the token it prints on screen. Then the owner creates the file, so the token never goes on a command line:
   ```sh
   HOME_DIR="<paths.home from the progress file header>"
   mkdir -p -m 700 "$HOME_DIR/secrets" && touch "$HOME_DIR/secrets/claude-bot.env" && chmod 600 "$HOME_DIR/secrets/claude-bot.env"
   open -e "$HOME_DIR/secrets/claude-bot.env"
   ```
   and writes exactly one line in it, `CLAUDE_CODE_OAUTH_TOKEN=<the token>`, saves, closes the editor, and runs the `chmod 600` above once more (an editor may save a new file with a wider mode). ⛔ This session never reads, prints, pastes or asks for that file's content; it only checks its mode.
2. Render the wrapper:
   ```sh
   KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"; PY=/usr/bin/python3
   "$PY" "$KIT/daemon/wa_setup.py" render-claude-wrapper --config "$LIVE/config.json" --dry-run
   "$PY" "$KIT/daemon/wa_setup.py" render-claude-wrapper --config "$LIVE/config.json"
   ```
   A missing file, a mode other than 600, a link, or a file without the token line is refused (exit 3) with the exact fix, and nothing is written. Writes: `<home>/bin/` (0700) and `<home>/bin/claude-bot` (0700), which loads the token file and runs the real `claude`; `bin.claude` in `live/config.json` (0600, every other key kept); `<home>/secrets/deny-canary.txt` (0600, a random marker for the deny probe), when `init` has not written it already; the reader config, refreshed. It prints `previous bin.claude: <value>` (record it: it is the way back), the proof command and the next steps; its first `next:` line says whether the plist must be rendered and installed again or a restart is enough.
3. Reload the daemon, then probe: follow the first `next:` line `render-claude-wrapper` printed: normally `wa_setup.py render-launchd --config "$LIVE/config.json"`, then `wa_setup.py install-launchd --config "$LIVE/config.json"` (with `bin.path_env` at `<auto>` the daemon's PATH follows `bin.claude`, so the plist changes; `install-launchd` restarts the daemon), or `wa_setup.py restart --config "$LIVE/config.json"` when it says the plist does not change (the daemon reads `bin.claude` only at start). Then `echo '{}' > "$LIVE/commands/probe-claude.json"`, and once that has answered, `echo '{"read_canary": true}' > "$LIVE/commands/probe-claude.json"` (two tiny model calls).

**Verify.**

- The owner runs the proof command `render-claude-wrapper` printed, in their own terminal: `env -i HOME="$(mktemp -d)" PATH=/usr/bin:/bin "<home>/bin/claude-bot" auth status --json`. Pass: the answer holds `"authMethod": "oauth_token"` (a login from the Keychain cannot be seen from that empty home, so only the token can pass). (K332)
- `"$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json"`. Pass: `authentication: setup token through <home>/bin/claude-bot (token file present, 0600, last changed <date>)` and no blocker. (K332)
- The plain probe, read as check 9 below. Pass: `"is_error":false` in the newest `runs/probe-*` folder.
- The deny probe. Pass: one line per folder in `$LIVE/wa_live.log`, `probe-claude read_canary [secrets]: 读取被挡下 (secrets 夹的 deny 规则有效; runs.denials 记了 <n> 笔, 脑回 DENIED)` and the same with `[transcripts-cache]` (the read was blocked), and the two newest `probe` rows' `denials` each name a `deny-canary.txt`: `sqlite3 -readonly "$LIVE/state.sqlite" "select id, ok, denials from runs where kind = 'probe' order by id desc limit 2"`. `没挡住` fails it, with a push naming the folder: run `init` again and check `live/claude-settings.json`. A line `脑回 DENIED, 但 runs.denials 没有读 deny-canary 的那笔` means the brain answered without trying the read: it proves nothing, so drop the command file again. (K333, K351)
- The token appears nowhere it should not: `grep -c "sk-ant-" "$HOME_DIR/bin/claude-bot" "$LIVE/wa_live.log"` prints `0` for both. (K334)

**Record.** Under `### C2 daemon`: the answer; on a yes, the previous `bin.claude` value the command printed, the date the token was made and its renewal date (about a year later), the proof's `authMethod`, the doctor line, the deny probe's line and its run id. The renewal date also goes into the daemon's registry entry as its `expires` cell (`references/vault-proposals.md`, proposal f). ⛔ Never the token, not even its first characters.

To go back to the Keychain login: set `bin.claude` to the previous `bin.claude` value (the `previous bin.claude:` line `render-claude-wrapper` printed, recorded at C2 step 12; usually `<auto>`) with the config edit ritual, then `wa_setup.py render-launchd --config "$LIVE/config.json"` and follow its `next:` line (`install-launchd` when the plist changes, otherwise `wa_setup.py restart`); `references/privacy.md`, "The setup token file", says how to remove the token.

## Verify

Run each probe with the station's opening block in front of it, at the point its title names: several checks read a state that later steps change. Every check must pass.

1. **Config is clean** (after step 1, and again after step 6). Pass: `check-config: 0 error(s)`.
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
   ```
2. **The whitelist has the right shape** (after step 1). Pass: the number of groups, policies only from `all`, `mention` and `skip`, then `True`.
   ```sh
   "$PY" -c 'import json,sys; g=json.load(open(sys.argv[1]))["group_policies"]; print(len(g), sorted(set(v["policy"] for v in g.values())), all(set(v)=={"policy","name","in30","defaulted"} for v in g.values()))' "$LIVE/config.json"
   ```
3. **The topic map points at real notes** (after step 2). Pass: `[]`.
   ```sh
   "$PY" -c 'import json,sys,pathlib; tm=json.load(open(sys.argv[1])); print([f for r in tm["rules"] for f in r["files"] if not (pathlib.Path(sys.argv[2])/f).exists()])' "$LIVE/topic-map.json" "<vault>"
   ```
4. **The deny rules were rendered** (step 4). Pass: two equal numbers (`27 27` in this version: the template's `Read(/{...})` rules, then the rendered `Read(//...)` rules, counted in the JSON so the file's comment is not counted), then `0`.
   ```sh
   "$PY" -c 'import json,sys; n=lambda p, pre: sum(1 for r in json.load(open(p))["permissions"]["deny"] if r.startswith(pre)); print(n(sys.argv[1], "Read(/"), n(sys.argv[2], "Read(//"))' "$KIT/daemon/templates/claude-settings.json" "$LIVE/claude-settings.json"
   grep -cE '[{](LIVE_DIR|WA_HOME|HOME)[}]' "$LIVE/claude-settings.json"
   ```
5. **The registry entry exists** (before step 7). Pass: the owner's registry note (or `<home>/INSTALLED.md`) has an entry for this label with all six cells filled, and its fingerprint equals what `shasum -a 256 "$KIT/daemon/wa_live.py"` prints.
6. **The LaunchAgent is installed and running under node** (after step 7). Pass: `OK`; `state = running` and the node path as `program`; the node path again for the process launchd started; then python's command line naming `daemon/wa_live.py`, the only child of that node. (`pgrep -f "daemon/wa_live.py"` would find node first: its own command line names the script too.)
   ```sh
   plutil -lint "$HOME/Library/LaunchAgents/$LABEL.plist"
   launchctl print "gui/$(id -u)/$LABEL" | grep -E "state = |program = "
   nodepid() { launchctl print "gui/$(id -u)/$LABEL" | awk '$1 == "pid" && $2 == "=" {print $3; exit}'; }
   ps -o comm= -p "$(nodepid)"; ps -o command= -p "$(pgrep -P "$(nodepid)")"
   ```
7. **The board answers, and the main loop is alive** (after step 7). Pass: a body starting with `ok (last scan`, then `200`. A body starting with `stalled` and `503` means the main loop has stopped while the board thread still answers (`references/pitfalls.md`, pit 16; `references/troubleshooting.md`, "Nothing on the board").
   ```sh
   curl -sS -w ' %{http_code}\n' "http://127.0.0.1:$PORT/health"
   ```
8. **The first start replayed nothing** (after step 7, before step 10; `references/pitfalls.md`, pit 8). Pass: the rows from before the first start show as `skip:before_install`, messages since then only as `skip:` values (mostly `skip:doorbell_off`) with no `pass:` row, and `0` runs.
   ```sh
   sqlite3 -readonly "$LIVE/state.sqlite" "select decision, count(*) from inbound_log group by decision"
   sqlite3 -readonly "$LIVE/state.sqlite" "select count(*) from runs"
   ```
9. **Claude answers from launchd, inside the cage, without Claude memory** (after step 8). Pass: `cmd.txt stderr.txt stdout.jsonl` and no `sample.txt`; `"is_error":false`; a tools list holding only `Read`, `Glob` and `Grep` (`TodoWrite` may appear); `0` cage breaches; the memory line `probe-claude: init 没有 memory_paths (Claude 记忆关着)` (no memory paths in the init event: Claude memory is off). `probe-claude: MEMORY LOADED` fails this check (`references/pitfalls.md`, pit 14), and so does `probe-claude: 没有 init 事件, 记忆开关查不到` (no init event: claude did not start properly, so memory cannot be judged).
   ```sh
   P=$(ls -td "$LIVE"/runs/probe-* | head -1); ls "$P"
   tail -1 "$P/stdout.jsonl" | grep -o '"is_error":[a-z]*'
   grep -ho '"tools":\[[^]]*\]' "$P/stdout.jsonl" | head -1
   grep -c "CAGE BREACH" "$LIVE/wa_live.log"
   grep "probe-claude: " "$LIVE/wa_live.log" | grep -E "memory_paths|MEMORY LOADED|init 事件" | tail -1
   ```
10. **The first judgment landed** (after step 10). Pass: the newest run has `ok` `1`, a token count and no error; `judgment.json` holds a world, stage and priority from the owner's vocabulary (stage is `None` when the world is not a business world, and that passes), the board shows the same chips, and the owner agrees the draft keeps the draft rules.
    ```sh
    curl -sS "http://127.0.0.1:$PORT/api/runs" | "$PY" -c 'import json,sys; r=json.load(sys.stdin)[0]; print(r["id"], r["ok"], r["input_tokens"], r["error"])'
    R=$(ls -td "$LIVE"/runs/2* | head -1); ls "$R"
    "$PY" -c 'import json,sys; j=json.load(open(sys.argv[1])); print(j["world"], j["stage"], j["priority"], j["needs_me"], bool(j["draft"]))' "$R/judgment.json"
    ```
11. **The doorbell is on** (after step 11). Pass: `True False` (doorbell on, not a dry run).
    ```sh
    curl -sS "http://127.0.0.1:$PORT/api/board" | "$PY" -c 'import json,sys; u=json.load(sys.stdin)["ui"]; print(u["doorbell"], u["dry_run"])'
    ```
12. **The first real run happened** (after step 11 and a real message). Pass: `pass:all` (or `pass:mention`) rows in the last hour, and the runs probe from check 10 shows a newer run with `ok` `1` about a minute after that message. When the owner's gate passes nothing on purpose (`direct_policy` `skip`, and no group at `all` or `mention`), pass instead: a real inbound message after step 11 is logged with the gate's own `skip:` decision (`skip:policy`, or `skip:ignored` for a chat in `ignored_jids`; never `skip:doorbell_off`), and the runs probe shows no run newer than check 10's.
    ```sh
    sqlite3 -readonly "$LIVE/state.sqlite" "select decision, count(*) from inbound_log where seen_at >= strftime('%Y-%m-%dT%H:%M:%S','now','localtime','-1 hour') group by decision"
    ```

## Record

Under `### C2 daemon` in `<home>/INSTALL-PROGRESS.md`:

- the gate: `direct_policy`, `unlisted_group_policy`, how many groups at `all`, `mention` and `skip`, how many chats ignored;
- debounce values, `board.lang`, `board.show_usd`, `dossier.model`, the subscription or API answer;
- the worlds (keys and which are business), that stage and priority labels and the business paragraph are written, `parked_after_days`;
- the topic map: how many rules;
- the registry entry's location, and the fingerprint and version it records;
- `install-launchd` state and pid lines;
- the probe: its folder, `"is_error"`, the tools list, the memory line, how many seconds it took;
- the prewarm, when step 9 ran: its `prewarm 完成` (or `prewarm 失败`) line with the seconds, logged before the restart in step 11;
- the first judgment: run id, the chips, and whether the owner agreed with the draft;
- the time observation started (`doorbell.enabled` true) and the first real run's id, or, for a gate that passes nothing on purpose, the `skip:` decision the first real message got;
- step 12: the answer, and on a yes the token's date and renewal date (never the token), the proof and doctor lines and the deny probe line;
- `ruled:` defaults 2, 3, 4, 7 and 8, one note each.

Tick `- [x] C2 daemon` only when every check passes. Next: `install/C3-board.md`.
