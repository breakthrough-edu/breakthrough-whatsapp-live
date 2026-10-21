# Operating WhatsApp Live

How to run an installed WhatsApp Live from day to day: whether it is running, where every file lives, how to read the log, every command file, restarting and pausing, the board, the digest and what each push means. For something that looks broken, start at `references/troubleshooting.md`; for what a config key does, `references/config-reference.md`.

The code is the source of truth. Log lines are quoted exactly as the daemon writes them, in Chinese, with `<...>` where a value goes.

## Before you start

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="$LABEL.intake"
PORT="<board.port from the progress file header>"
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs. The install records the real values at the top of `<home>/INSTALL-PROGRESS.md`; `paths.home` is an absolute path there, and stays one here (a `~` inside quotes is not expanded). More probe helpers (`cfg`, `q`, `logs`, `apij`) are at the top of `references/feature-checklist.md`.

⛔ One session touches the daemon at a time (`references/defaults-and-why.md`, default 11): a restart kills the runs in flight, and two sessions restarting it step on each other.

## Is it running?

```sh
launchctl print gui/$(id -u)/$LABEL | grep -E "state = |pid = "
curl -s -w ' %{http_code}\n' "http://127.0.0.1:$PORT/health"
"$PY" "$KIT/daemon/wa_live.py" --status --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json"
```

- `state = running` with a pid: launchd runs it. `KeepAlive` brings it back within about 10 seconds after a crash.
- A body starting with `ok (last scan <n>s ago)`, then `200`: the board answers and the main loop scanned within its limit (three times `doorbell.full_rescan_seconds`, at least 30 seconds). `stalled <n> (last scan <n>s ago, limit <n>s)` and `503`: the board thread answers but the main loop is frozen (`references/pitfalls.md`, pit 16); see `references/troubleshooting.md`, "Nothing on the board".
- `--status` prints JSON: `version`, `last_pk` (the watermark, the newest WhatsApp message id seen), `pending` (bursts waiting for their quiet gap), `recent_runs` (the last ten) and `needs_me` (up to twenty conversations waiting on you). It reads the state database and starts nothing.
- `doctor` checks the Mac, the payload, the config, WhatsApp Desktop (`last database write: <age> ago`), the tools, Tailscale, the working folder and launchd, and ends with `VERDICT: READY`, `VERDICT: READY, with <n> note(s) above` or `VERDICT: NOT READY, first blocker: <what>`. It is read only and prints counts, paths and names, never message text.

The newest lines of the log tell the rest:

```sh
grep -e "起跑" -e "门铃: 新行" -e "完成" "$LIVE/wa_live.log" | tail -5
```

A healthy daemon shows a start line `wa_live <version> 起跑 · model=<model> · debounce <quiet>s/<max>s · 并发 <n> · dry_run=False · 门铃开`, then `门铃: 新行 <n>, 过闸 <n>, 水位 <pk>` whenever messages arrive, and `run #<id> 完成 <name>: <world>/<stage>/<priority> needs_me=<value> 读 <n> / 写 <n> tokens <ms>ms` after each run. `门铃关 (不自动叫脑, 命令档与板子照常)` at the end of the start line means `doorbell.enabled` is false: nothing runs by itself.

## Where everything lives

| Path | What it is | Private | In the backup |
|---|---|---|---|
| `~/Library/LaunchAgents/<label>.plist` | The daemon's LaunchAgent; node runs `run.mjs`, which runs python | 0644 | no |
| `<home>/launchd/run.mjs`, `<home>/launchd/<label>.plist` | The rendered copies `install-launchd` installs | 0644 | no |
| `<home>/launchd/intake-evening.mjs`, `<home>/launchd/<intake_label>.plist` | The evening intake job, when installed | 0644 | no |
| `<home>/config.json` | The reader config, generated from `live/config.json` by `sync-reader-config` and at every daemon start | 0600 | no |
| `<home>/transcripts-cache/` | Voice transcripts, one JSON file per voice note, shared by the reader, the daemon and the board | 0700 | no |
| `<home>/doc-text-cache/` | Text of Word, Excel, PowerPoint, RTF, ODT and Pages attachments, one JSON file per document, shared by every read (`reader.read_documents`) | 0700 | no |
| `<home>/secrets/` | `deny-canary.txt`, a random marker the deny probe tries to read (0600, written by `init`); `claude-bot.env`, the optional setup token, written by the owner (0600). Every run is denied reading this folder | 0700 | no |
| `<home>/bin/claude-bot` | The optional setup token wrapper `render-claude-wrapper` writes; `bin.claude` points at it | 0700 | no |
| `<home>/runs-work/<key>/` | The reader's working folder per conversation (config copy, snapshots while reading, packs) | 0700 | no |
| `<home>/prewarm/` | The working folder of `prewarm-<days>.json` | 0700 | no |
| `<home>/brain-cwd/` | The empty folder headless `claude` runs in | 0700 | no |
| `<home>/sweeps/`, `<home>/last-sweep.json` | Packs from manual reads through the reader skill | 0600 | no |
| `<home>/INSTALL-PROGRESS.md` | The install's progress file and log | 0600 | no |
| `<live>/config.json` | The config; `config.json.bak-<time>` copies before each change | 0600 | yes, secrets redacted (the dated copies no) |
| `<live>/state.sqlite` | Conversations, runs, pending bursts, the inbound log, person note jobs, intake dedupe, `meta` | 0600 | yes |
| `<live>/wa_live.log` (`.1` to `.3`) | The daemon's log, rotated at 5 MB, three old copies | 0600 | no |
| `<live>/stderr.log` | `run.mjs` lines (`start: node`, `received SIGTERM, forwarding to python pid`, `python exited code=`) and anything python prints before its log opens | 0600 (launchd creates it; the daemon sets 0600 at every start) | no |
| `<live>/intake-evening.log` | The evening intake job's lines | 0600 (launchd creates it; the job sets 0600 each time it runs) | no |
| `<live>/claude-settings.json` | The rendered cage: tool denies, hooks off, Read deny rules | 0600 | no (init renders it) |
| `<live>/summaries.md` | One summary line per conversation, what the brain reads instead of other chats | 0600 | yes |
| `<live>/prompts/` | Owner copies of `brain-prompt.md`, `second-source-prompt.md`, `backfill-prompt.md` | 0600 | yes |
| `<live>/topic-map.json` | Words that point the brain at vault notes; read on every run | 0600 | yes |
| `<live>/seeds.json` | Install-time hashes of the owner copies, for `seed-status` | 0600 | yes |
| `<live>/adapters/` | Your own implementations of an interface (`references/interfaces.md`, "Where a new implementation lives"); kept through updates | yours | yes, as they are |
| `<live>/commands/` | Drop command files here (below) | 0700 | no |
| `<live>/runs/` | One folder per run (below); never cleaned by the kit | 0700 | no |
| `<live>/digest/<date>.md` | Morning digests | 0600 | yes |
| `<live>/facts.json` | The facts job's result (O1) | 0600 | yes |
| `<live>/second-source-whitelist.json`, `<live>/second-source-last.json` | The chats the second source pulls, and the last pull's result (O2) | 0600 | the whitelist only |
| `<live>/backup-last.json` | The last backup's result (O5) | 0600 | no |
| `<live>/voice-pairs.jsonl` | Draft and sent pairs (O8) | 0600 | yes |
| `<live>/calendar/<key>.ics` | Calendar files the board serves (O3, `ics`) | 0600 | no |
| `<live>/dossier-backups/<note>/` | The last three versions of each person note, taken before every placement | 0700 | yes |
| `<live>/dossier-cli/<time>-<name>/` | Drafts from `dossier.py prepare` | | no |
| `<live>/variants/<name>.html` | Board variants served at `/v/<name>` | 0700 | no |
| `<live>/telegram.json` | The Telegram bot token and chat id (O4) | 0600 | never |
| `<live>/acceptance.json` | The observation week's daily questions (O11) | | yes |

The daemon sets `umask 077` at start, so everything it and the programs it starts create is private whatever the column says: files 0600, folders 0700.

In the vault, the daemon writes only person notes and their recent lines (`paths.people_dir`), intake entries (`intake.buffer`) and filing-log lines (`paths.filing_log`); decision notes and playbook folders are written by sessions with the owner.

## Reading the log

`live/wa_live.log` holds conversation names, so it is 0600 and stays on the Mac. Each line is `<date> <time>,<ms> <LEVEL> <message>`.

| You see | It means |
|---|---|
| `首跑: 水位定在 Z_PK=<n>, 从现在开始, 不补旧讯息 (往回看的 <n> 个编号记成 skip:before_install)` | The first start of a fresh state database; nothing older is judged |
| `门铃: 新行 <n>, 过闸 <n>, 水位 <pk>` | New rows in WhatsApp's database, how many passed the gate, the new watermark |
| `过闸 <kind> [<type>] <name>` | One message passed the gate |
| `主人回了 <name>, 取消该对话待跑的 burst (<n> 封)` | You replied before the quiet gap ended; that burst will not run |
| `run #<id> 开始 <name> (<n> 封, <source>)` | A run started: `live`, `test-run`, `board` or `board-full` |
| `run #<id> 对话包 <n> 天 · 接地` or `· 增量` | The reading window: a full read, or recent days plus the summary |
| `run #<id> 完成 <name>: ...` | A judgment landed |
| `run #<id> 第 <n> 次失败: <reason>`, `run #<id> 失败 <name>` | An attempt failed; after the retry the run failed and one push went out |
| `run #<id> intake +<n> (<name>)`, `run #<id> 群发言人近况 +<n> 份档 (<group>)` | Captures appended; recent lines added to speakers' notes |
| `dossier: 排队 <name> (<reason>)`, `dossier run #<id> 完成 <name> → <file> (...)` | A person note run was queued, then placed |
| `排班 <job> 完成 (<s>s)`, `排班 <job> 失败` | A nightly job (`facts`, `second_source`, `backup`, `digest`) finished or failed |
| `排班 <job>: 启动时已过 <time>, 今天记成跑过 (要现在跑就丢 commands/<job>-now.json)` | A start after that job's time; it waits for tomorrow |
| `<job>-now 指令: <parameters>`, `backfill-summaries 指令: <parameters>` | A command file was taken |
| `notify 出口: <outlet> (mode=<mode>)` | The push outlet in use, printed at start |
| `notify 已推 (<outlet>): <first line>`, `notify 不推 (mode=<mode>, 这一则是 <kind>): <first line>` | A push went out, or the mode held it back |
| `板子在 http://127.0.0.1:<port>/ (tailscale serve 把它挂上 tailnet)` | The board is up |
| `板子: 已处理`, `板子: 改草稿`, `板子: 改 <field>=<value>`, `板子: 手动跑`, `板子: 加进日历`, `板子: 交棒包` | A board button was pressed |
| `模组: 全部载入` or `模组: 缺 <modules>` | Which optional modules loaded; a missing one stops only its own block |
| `收到停止讯号, 等 <n> 个 run 收尾` | A stop or restart; runs get up to 90 seconds |
| `prewarm <n> 天上次没跑完 (多半被重启切断), 补跑` | A prewarm whose process a restart killed mid-run is run again, from the start, in the background |
| `主循环恢复了 (<the /health body>)` | The main loop is moving again after a stall (`/health` is back to `200`) |
| `probe-claude: init 没有 memory_paths (Claude 记忆关着)`, `probe-claude: MEMORY LOADED, init 带 memory_paths <paths>: Claude 记忆没关掉`, `probe-claude: 没有 init 事件, 记忆开关查不到` | The probe's memory check: off (good), loaded (`references/troubleshooting.md`, "MEMORY LOADED in the log"), or not readable because claude did not start properly |
| `MEMORY LOADED: 这个 run 载入了 Claude 记忆 (<paths>), 环境变数没生效; …`, `MEMORY LOADED (档案 run): …` | A triage or person note run loaded Claude memory; never expected (`references/pitfalls.md`, pit 14) |
| `CAGE BREACH: 载入了不该有的工具: <tools>` | A run loaded a tool outside Read, Glob and Grep; see `references/troubleshooting.md` |

Useful filters:

```sh
grep -e "失败" -e "ERROR" -e "CAGE BREACH" "$LIVE/wa_live.log" | tail -20
grep "<a contact's name>" "$LIVE/wa_live.log" | tail -20
tail -5 "$LIVE/stderr.log"
```

### Run folders

Every model call leaves a folder under `live/runs/`, named by time:

| Folder | Holds |
|---|---|
| `<YYYYMMDD-HHMMSS>-<jid>/` | A judgment: `sweep.json`, `sweep.md`, `context.json`, `context.md`, `prompt.md`, `claude.cmd.txt`, `claude.stdout.jsonl`, `judgment.json`; `claude.stderr.txt` when claude wrote an error, `claude.attempt2.stdout.jsonl` for the retry |
| `dossier-<time>-<jid>/` | A person note run: `sweep.json`, `sweep.md`, `prompt.md`, `claude.stdout.jsonl`, `note.md` |
| `second-source-<time>/` | A second source pull: `prompt.md` and `result.txt` when the model was called |
| `backfill-<time>-b<n>/` | One backfill batch: `sweep-<jid>.json` and `.md`, `prompt.md`, `result.txt` |
| `probe-<time>[-<label>]/` | A `probe-claude.json`: `cmd.txt`, `stdout.jsonl`, `stderr.txt`, `sample.txt` when it hung |
| `prewarm-<time>-<days>d.json` | A prewarm's summary (a file, not a folder) |

They hold other people's messages. The kit never deletes them (see "Housekeeping").

### The state database

Read it only with `-readonly`:

```sh
sqlite3 -readonly "$LIVE/state.sqlite" "select id, started_at, kind, name, ok, error from runs order by id desc limit 10"
sqlite3 -readonly "$LIVE/state.sqlite" "select decision, count(*) from inbound_log where seen_at >= date('now', 'localtime') group by decision"
sqlite3 -readonly "$LIVE/state.sqlite" "select name, world, stage, priority, tag, needs_me from conversations order by updated_at desc limit 10"
sqlite3 -readonly "$LIVE/state.sqlite" "select id, name, reason, ok, error from dossier_jobs order by id desc limit 5"
sqlite3 -readonly "$LIVE/state.sqlite" "select key, value from meta where key like 'daily_%' or key = 'dossier_nightly_last' or key like 'seed_fallback:%'"
sqlite3 -readonly "$LIVE/state.sqlite" "select id, kind, started_at, ended_at, substr(detail, 1, 80) from brain_outages order by id desc limit 5"
```

`runs.kind` is `direct` or `group` for judgments, `dossier`, `second_source`, `backfill` or `probe` (the deny probe) for the rest. `brain_outages` holds one row per time the circuit breaker opened: `kind` `auth` or `limit`, `ended_at` empty while it is still open. `inbound_log.decision` is one of `pass:all`, `pass:mention`, `skip:no_mention`, `skip:policy`, `skip:ignored`, `skip:status_or_broadcast`, `skip:system`, `skip:too_old`, `skip:before_install`, `skip:doorbell_off`, `outbound:replied`, `outbound:replied:burst_cancelled`.

## Command files

Drop a JSON file into `live/commands/`. Within a few seconds the running daemon takes it, deletes it, and logs that it did. A file that is not valid JSON counts as `{}`. A name may carry a suffix (`facts-now-2.json`) to queue a second one. Nothing happens while the daemon is stopped; `doctor` reports command files still waiting.

| File | Parameters | What it does | Look for |
|---|---|---|---|
| `probe-claude.json` | `{}`; `"label"`, `"drop": ["--flag"]`, `"add": ["--flag", "value"]` to bisect | One minimal `claude -p` with the cage flags and the daemon's environment, under launchd; samples and kills it after 90 seconds (one tiny model call). It runs while the circuit breaker is open too | `probe-claude 结束 rc=<rc>, <s>s, stdout <n> 字 -> <folder>`, then `probe-claude: init 没有 memory_paths (Claude 记忆关着)` |
| `probe-claude.json` | `{"read_canary": true}` | The deny probe: asks the brain, in the same cage, to Read `<home>/secrets/deny-canary.txt` and answer only DENIED or READ_OK; recorded as a run of kind `probe`, so a blocked read lands in `runs.denials` (one tiny model call). `wa_setup.py init` writes the marker on every install (`render-claude-wrapper` too), so the probe works with or without the setup token; a missing marker is logged and the probe stops. It never targets the token file | `probe-claude read_canary: 读取被挡下 (secrets 夹的 deny 规则有效; runs.denials 记了 <n> 笔, 脑回 DENIED)`; `没挡住` instead is a failure and pushes an alert |
| `notify-test.json` | none | One test push through the outlet, as kind `error` | `notify-test: 已发` |
| `test-run.<jid>.json` | none (the jid is in the name) | Judges that chat's last three inbound messages now, gate or no gate (one model call) | `test-run 指令: <name>` |
| `prewarm-<days>.json` | none (the days are in the name) | Transcribes every voice note in that window into the cache, in the background; no model call. One whose process a restart killed mid-run runs again at the next start; one that returned (finished, failed or timed out) does not | `prewarm 完成 <s>s: <summary>` |
| `facts-now.json` | `{"dry_run": true}` | The facts job now; without `dry_run`, while `dossier.enabled` is true, it also queues person notes for paying customers without one, up to `dossier.nightly_cap`, and those runs start at once on `dossier.model` | `facts: people <n> · paid <n> · ...` |
| `second-source-now.json` | `{"dry_run": true}`, `{"day": "YYYY-MM-DD"}` | The second source pull now, for yesterday or that day | `second source <source>: pulled <n> chats ...` |
| `backup-now.json` | `{"dry_run": true}`, `{"dest": "<folder>"}` | The backup now, or into another folder once | `backup: <dest> · <n> 档 · <MB> MB` |
| `digest-now.json` | `{"dry_run": true}`, `{"for": "YYYY-MM-DD"}`, `{"send": false}` | The digest now: built only, for another day, or written without a push | `digest <date>: 已推 (<n> 行)` |
| `backfill-summaries.json` | `{"limit": <n>}`, `{"dry_run": true}` | Summary backfill (station O9); only one at a time | `backfill 完成 (<s>s): 候选 <c> · <b> 批 · 写 <w> 条 · 失败 <f> 条` |

Examples:

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{"dry_run": true}' > "$LIVE/commands/facts-now.json"
echo '{"for": "2031-03-03", "send": false}' > "$LIVE/commands/digest-now.json"
echo '{}' > "$LIVE/commands/test-run.60123456789@s.whatsapp.net.json"
```

A daemon started with `--dry-run` calls no model, pushes nothing and writes no real judgment for any of them; `prewarm` still transcribes, since that calls no model.

## Restart, stop, pause

| To | Run | What happens |
|---|---|---|
| Restart (after a config change) | `"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"` (never `launchctl kickstart -k`, which stops only the node parent and can orphan a frozen python: `references/pitfalls.md`, pit 15) | Runs in flight get up to 90 seconds (`ExitTimeOut`), then a python still alive is killed; their bursts stay pending and run again after the start. A person note job cut off is marked failed (`中断: daemon 重启时这个 job 没跑完`) and can be queued again; a prewarm the restart kills mid-run runs again; a backfill cut off does not. A start after a nightly job's time marks it done for today. It ends with `proof: one daemon, the new code is loaded (...)`; exit 3 is a refusal (the board port held by another program) with nothing signalled |
| Stop (pause, also across reboots) | `"$PY" "$KIT/daemon/wa_setup.py" uninstall-launchd --config "$LIVE/config.json"` | Boots the job out, then stops any python of this install still running (SIGTERM, then SIGKILL after `ExitTimeOut` if it is frozen), disables the label, removes the plist from `~/Library/LaunchAgents/`; it prints `kept:` for the rendered copy and `live/`: state, logs and config stay |
| Resume | `"$PY" "$KIT/daemon/wa_setup.py" install-launchd --config "$LIVE/config.json"` | Installs the rendered plist again and starts it; it continues from the watermark it had |
| Restart after a code or template update | `render-launchd`, then `install-launchd` | `install/update.md` walks through it |
| The evening intake job | the same `uninstall-launchd` and `install-launchd` with `--intake`; `launchctl kickstart -k gui/$(id -u)/$ILABEL` runs it once now | It never fires at load; it reads config at every run |

Every `--dry-run` form prints the plan without doing it. Stop the daemon with `uninstall-launchd`, never a raw `launchctl bootout`: that stops only the node parent, so a frozen python can be orphaned and keep the board port (`references/pitfalls.md`, pit 15), and the plist it leaves in `~/Library/LaunchAgents/` starts the daemon again at the next login.

## Pausing one block

With the config edit ritual from `install/00-start-here.md` (backup, edit, `chmod 600`, `check-config`), then the restart it needs. The rest of the daemon keeps working.

| To pause | Set | Then | Still works |
|---|---|---|---|
| Automatic runs (a board-only mode) | `doorbell.enabled` `false` | restart | Logging (`skip:doorbell_off`), replies marking chats, the board and its buttons, command files, nightly jobs |
| One-to-one runs | `direct_policy` `skip` | restart | Groups, board buttons, test runs |
| One group | its `group_policies` entry `skip` (or `mention`) | restart | Everything else |
| A chat entirely | add its jid to `ignored_jids` | restart, `sync-reader-config` | Everything else; that chat is never read |
| The board | `board.enabled` `false` | restart | Runs, pushes, nightly jobs |
| Pushes | `notify.mode` `off` | restart | The log records what would have gone out |
| The digest push only | `digest.push` `false` | restart | The digest file and the board's Yesterday card |
| The digest | `digest.enabled` `false` | restart | Everything else; silence becomes ambiguous |
| Person notes (all writes, recent lines too) | `dossier.enabled` `false` | restart | Judgments; the board's note buttons disappear |
| New notes opening by themselves | `dossier.auto_open_worlds` `[]` | restart | Rewrites, the nightly batch, the buttons |
| The facts job | `facts.enabled` `false` | restart | The last `facts.json` stays and is still read |
| The second source pull | `second_source.enabled` `false` | restart | Everything else |
| The backup | `backup.enabled` `false` | restart | Everything else; the digest reports the last result as old |
| Intake captures and the evening push | `intake.enabled` `false` | restart | Everything else; the evening job exits by itself |
| The evening push only | `intake.evening_push` `false` | none (read at each run) | Captures |
| The voice loop | `voice.enabled` `false` | restart | Everything else |
| Voice transcription | `brain.transcribe` `false` | restart, `sync-reader-config` | Cached transcripts still show |
| The calendar button | `calendar.destination` `none` | restart | Everything else |
| Moving quiet chats to parked | `stage_rules.parked_after_days` `0` | restart | Everything else |

`live/topic-map.json` and the owner copies in `live/prompts/` are read on every run: an edit lands with the next run, no restart.

## When the brain cannot be called

Two model calls in a row that fail because Claude cannot be called open the circuit breaker: `auth` when the login expired or was refused, `limit` when the usage limit is reached. Any other failure is a problem of that one conversation and resets the count; any call that succeeds closes it.

| While it is open | What happens |
|---|---|
| New triage runs | None start. Every burst stays in the pending queue, nothing is dropped, and the queue runs in order once the breaker closes |
| Person note runs, the second source's nightly pull, summary backfill batches, `test-run.<jid>.json`, the board's Judge now | Not started. Judge now answers `{"ok": false, "reason": "brain_down", ...}` and the board says the brain cannot be called; the nightly pull waits and runs once the brain answers again |
| The doorbell and the gate, the facts job, the nightly backup, the morning digest, the board, other command files | Keep running |
| A probe | Every `brain.gate_probe_seconds` (900) the daemon makes the smallest call it can (same cage, one turn, "reply with exactly: PONG"); an empty answer does not count. Probes are not runs: no row, no run folder. A daemon started with `--dry-run` never probes |

What the owner sees: a banner on top of the board (`The brain cannot log in to Claude` or `Claude usage limit reached`, in Chinese `脑登不进 Claude` or `Claude 用量到顶了`) with since when, what to do, and when the next probe is; `status.brain_down` in `/api/board` (`null` while the brain answers, else `kind`, `since`, `minutes`, `detail`, `next_probe_at`); exactly two pushes, one when it opens (at most one in six hours) and one when it closes, sent only when the first one went out; one `brain_outages` row; and the next morning's digest health line. A restart while it is open closes the old row at the start time (`断路器: 上次留着没关的 <n> 段停摆, 起跑时记成结束`); if Claude still cannot be called, the next two failures open a new one.

What to do: for `auth`, log in to Claude Code once in the terminal on the always-on Mac itself (or, when `bin.claude` is the setup token wrapper, renew the token in `<home>/secrets/claude-bot.env`); for `limit`, wait. No restart is needed: the next probe that answers closes the breaker. `references/troubleshooting.md`, "Runs are stuck with zero output", check 1, reads the log lines.

## The board

On this Mac `http://127.0.0.1:<port>/`; on the phone, the tailnet address `tailscale serve status` shows. It has no login: the tailnet is its lock (`references/privacy.md`, "Tailscale, and a board with no login").

- **The list.** Every conversation the brain judged, those waiting on you first, then "Other chats": chats active in the last `board.others_days` days that were never judged, at most `board.others_limit`. The chips on top filter it: Open, Reply, Waiting, FYI, Ignore, Replied / done, All (in Chinese when `board.lang` is `zh`).
- **A conversation.** The situation, the chips for world, stage and priority (tap world or stage to change it; only you move a stage back), the confidence line, what is at stake, the timeline, the brain's memory, the draft box, related vault notes, the person note line, and the recent messages with photos, videos and voice notes with their transcripts.
- **Buttons and what they write:**

| Button | Calls | Log line |
|---|---|---|
| ✓, the D key, a long left swipe on the phone | `POST /api/dismiss` (`GET /dismiss?jid=` when fetch is blocked) | `板子: 已处理 <jid> (<n> 行)` |
| Typing in the draft box | `POST /api/draft` | `板子: 改草稿 <jid> (<n> 字)` |
| The world or stage chip | `POST /api/field` | `板子: 改 <field>=<value> <jid> (<n> 行)` |
| Judge now, Full re-judge | `POST /api/run` (a chat outside the gate asks first) | `板子: 手动跑 <name>`, with `(全量重判)` or `(闸外, 主人确认)` |
| Start note (a person with no note), Rewrite note | `POST /api/dossier` | `dossier: 排队 <name> (板上要求开档)` for Start note, `dossier: 排队 <name> (板上要求重写)` for Rewrite note |
| Add to calendar | `POST /api/calendar` | `板子: 加进日历 <name> <date> <time> → <result>`; the same appointment pressed again answers `"already": true` with the first link and logs `→ already`, with or without a hint on the chat |
| Take to Claude Code | `GET /api/packet` | `板子: 交棒包 <jid>` |
| The green WhatsApp button, Copy draft | nothing: it opens WhatsApp prefilled, or copies | none; nothing is ever sent by the kit |

- **Search.** The search box on top of the list (or the `/` key) finds messages in every chat WhatsApp Desktop keeps on this Mac, by message text or file name: every word typed must match, case does not matter, newest first, status and broadcast chats never. Each result shows the chat, the time, the speaker in a group, and an excerpt with the words marked; with a conversation open, the results can be limited to it. A click opens the context view, the conversation around that message with the hit marked, and a way back to the results. Search only reads: it changes nothing, and its log line `板子: 搜寻 <n> 个词, <n> 笔` never carries the words searched.
- **The banner.** While the brain cannot be called, a banner on top says why and what to do ("When the brain cannot be called", above).
- **The footer.** Today's runs and tokens (read, cached, written), the watermark, and how long ago WhatsApp's database changed. Dollars show only with `board.show_usd` `true`.
- **Other endpoints.** `/health` (`GET` and `HEAD`: `200` with `ok (last scan <n>s ago)` while the main loop's heartbeat is younger than three times `doorbell.full_rescan_seconds`, at least 30 seconds; `503` with `stalled <n> (last scan <n>s ago, limit <n>s)` after that; a monitor checks that the body starts with `ok`, or uses `curl -sf`), `/api/board`, `/api/thread?jid=<jid>&n=<n>` (with `&around=<pk>`, the messages before and after that one, in time order, for the context view), `/api/search?q=<words>&jid=<optional jid>&n=<5 to 200, default 60>` (`{"q", "hits", "more"}`, read only), `/api/runs` (the last 30 runs), `/api/digest`, `/media?pk=<pk>`, `/calendar/<key>.ics`, and `/v/` listing the variants in `live/variants/`.

## The morning digest

At `digest.at` (08:00) the daemon writes `live/digest/<yesterday>.md` and pushes its short form when `digest.push` is true and an outlet exists. On the board it is the "Yesterday" card. Its blocks, at most twelve lines: yesterday's volume (messages through the gate, runs by kind, and bursts saved because you replied before their run started), who still waits on you, stage moves, person note jobs, facts mismatches, and health (the WhatsApp database's age, the facts source, the backup, the second source pull, open intake entries, `brain was down <n> minutes (<auth|limit>)` on a day the breaker was open, and a warning when an owner copy is missing). A quiet day still gets a digest. During the observation week a `🧪` question may follow (station O11).

To send it now: `digest-now.json`. A restart after `digest.at` marks today's as sent without sending it.

## What each push means

| Push starts with | Meaning | First look |
|---|---|---|
| `📋 WhatsApp Live · <MM/DD>` | The morning digest | The board's Yesterday card for the full text |
| `💬 [WA live #<id>] <name> · <world> / <stage> / <priority> (<n>%)` | A judgment that needs you (only with `notify.mode` `needs_me` or `all`); the draft follows, and a `wa.me` link for one-to-one chats | The board |
| `👀 [WA live #<id>] ...` | A judgment that needs nothing from you (only with `all`) | Nothing to do |
| `⚠️ [WA live] run #<id> 失败 (<name>): <reason>` and `看 <run folder>` | A judgment failed after its retry; at most once per chat per 30 minutes; not sent while the circuit breaker is open (its two pushes say it instead) | Troubleshooting, "Runs are stuck with zero output" |
| `⚠️ [WA live] 档案 run #<id> 失败 (<name>): <reason>` | A person note run failed | Troubleshooting, "A person note is refused" |
| `⚠️ [WA live] 排班 <job> 失败: <reason>` | A nightly job failed; at most once per job per hour | The reason names it; Troubleshooting per job |
| `⚠️ [WA live] WhatsApp 的数据库已经 <h> 小时没动, <Mac> 上的 WhatsApp Desktop 可能掉线了。` | WhatsApp Desktop wrote nothing for `doorbell.stale_alert_hours`; repeated at most every 6 hours | Troubleshooting, "Nothing on the board" |
| `⚠️ [WA live] 主循环已经 <h> 小时没动 (/health 回 503)。先看这台 Mac 的萤幕有没有没按的权限弹窗, 再用 wa_setup.py restart 重启。` | The main loop has not scanned for half of `doorbell.stale_alert_hours` while the board still answers (push key `daemon_stalled`, at most one in six hours): look at the Mac's screen for an unanswered permission dialog, then `wa_setup.py restart` | Troubleshooting, "Nothing on the board" |
| `⚠️ [WA live] 水位 <old> 比 WhatsApp 库最大编号 <max> 还大 ...` | WhatsApp's database was rebuilt or linked again; the watermark was reset and older messages are not judged | Troubleshooting, "A message is missing" |
| `⚠️ [WA live] 摘要回填失败, 看 wa_live.log` | A summary backfill stopped on an error | The log's `backfill 失败` traceback |
| `🚨 [WA live] run #<id> (<name>) cage breach: 载入了不该有的工具 <tools>`, `🚨 [WA live] 档案 run cage breach: ...`, `🚨 [WA live] <kind> run #<id> cage breach: ...` | A run loaded a tool outside the cage | Troubleshooting, "Cage breach" |
| `🔌 [WA live] 脑叫不动了: Claude Code 的登入失效。…` or `🔌 [WA live] 脑叫不动了: Claude 的用量额度到顶。…` | The circuit breaker opened: Claude cannot be called (login, or usage limit); pending messages stay queued. At most one in six hours | "When the brain cannot be called", above |
| `✅ [WA live] 脑恢复了, 停了 <n> 分钟。排队的讯息现在接着判。` | The breaker closed; the queue runs. Sent only after the opening push | Nothing to do |
| `🚨 [WA live] 拒读探测没挡住: 脑读得到 secrets 夹。…` | The deny probe read the marker in `<home>/secrets/`: the Read deny rules do not hold | `init`, then the probe again (`install/C2-daemon.md`, step 12) |
| `🧪 [WA live] notify-test: 出口通了 (<time>)` | The answer to `notify-test.json` | Nothing to do |
| `🗂 intake pre-sort · <n> open` (`🗂 intake 预分类 · <n> 条等归位`) | The evening intake list, a suggested place per entry | Say "intake" in a session |

## Usage

Tokens, not dollars, are the reading that matters on a subscription (`references/defaults-and-why.md`, default 3). Today's totals are in the board's footer. By kind, today:

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
sqlite3 -readonly "$LIVE/state.sqlite" "select kind, count(*), sum(input_tokens + cache_read_tokens + cache_create_tokens), sum(output_tokens) from runs where started_at >= date('now', 'localtime') group by kind"
```

The evening intake pre-sort and a `wa_backfill.py --run` from the terminal are not in the `runs` table.

## Throwaway runs

`wa_live.py --scan-once` runs the doorbell and the gate once, prints `new_rows`, `passed`, `last_pk` and `pending`, starts no model and pushes nothing, but it writes a state database and reader configs. Never point it at the real config. Give it a copy that lives in a throwaway folder:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
T=$(mktemp -d); mkdir -p "$T/live"
"$PY" - "$LIVE/config.json" "$T" <<'EOF'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
t = sys.argv[2]
cfg.setdefault("paths", {}).update({"home": t, "live_dir": t + "/live"})
for block, key, value in (("board", "enabled", False), ("notify", "outlet", "none"), ("dossier", "enabled", False),
                          ("intake", "enabled", False), ("facts", "enabled", False), ("second_source", "enabled", False),
                          ("backup", "enabled", False), ("digest", "enabled", False)):
    cfg.setdefault(block, {})[key] = value
json.dump(cfg, open(t + "/live/config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
EOF
chmod 600 "$T/live/config.json"
"$PY" "$KIT/daemon/wa_live.py" --scan-once --config "$T/live/config.json"
```

Its first scan is a first start: the lookback rows are recorded as `skip:before_install`, so `passed` is 0 until a new message arrives and it runs again. Afterwards `stat -f %m "$LIVE/state.sqlite"` shows the real database untouched. The folder holds a copy of the owner's number and names: move it to the Trash when done. For a daemon that keeps running beside the real one, use the test install from `install/O11-observation-and-acceptance.md`.

## Housekeeping

- **`live/runs/` grows forever.** The kit never deletes run folders. Check the size with `du -sh "$LIVE/runs"`, and count old ones with `find "$LIVE/runs" -maxdepth 1 -mtime +30 | wc -l`. They hold other people's messages; removing old ones is the owner's decision, made with them.
- **`<home>/transcripts-cache/`, `<home>/doc-text-cache/` and `<home>/runs-work/`** grow with voice notes, documents and conversations; they rebuild themselves when removed, at the cost of transcribing and converting again.
- **Config backups** (`live/config.json.bak-<time>`) pile up, one per change; keep the recent ones.
- **After a kit update**, `"$PY" "$KIT/daemon/wa_setup.py" seed-status --config "$LIVE/config.json"` lists owner copies whose seed moved; `install/update.md` merges them.
- **The fingerprint** of the running code goes into the registry at install (station C2, step 5) and after every update (`install/update.md`, step 9); station O10 checks it.
