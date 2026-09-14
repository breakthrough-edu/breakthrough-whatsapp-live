# Troubleshooting

Start from what you see. Each section lists checks in the order that finds the cause fastest; each check says what its answer means and what fixes it. When the fix is not here, `references/pitfalls.md` has the thirteen traps this kit was built around, and `references/operating.md` has every file, log line and push.

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
WADB="$HOME/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs. Fill each `<...>` exactly as the header of `<home>/INSTALL-PROGRESS.md` records it; `paths.home` is an absolute path there, because a `~` inside quotes is not expanded.

- Change one thing at a time and run the check again. After three failed attempts on the same symptom, stop and write down the state instead of guessing.
- ⛔ One session touches the daemon at a time. A restart kills the runs in flight.
- Probe `claude` on the Mac itself: over ssh, `claude -p` reports `Not logged in` even when the Mac is logged in.
- `wa_setup.py doctor` is always safe: read only, counts and names only, one verdict line at the end.

## Nothing on the board

1. **Is the daemon running?**
   ```sh
   launchctl print gui/$(id -u)/$LABEL | grep -E "state = |pid = "
   curl -s -m 3 -w ' %{http_code}\n' "http://127.0.0.1:$PORT/health"
   tail -5 "$LIVE/stderr.log"
   ```
   - No service: it was uninstalled or never installed. `"$PY" "$KIT/daemon/wa_setup.py" install-launchd --config "$LIVE/config.json"`.
   - `state` not running, or a pid that keeps changing: read `stderr.log`. `python not found at <path>` or `daemon script not found at <path>` (node or python moved, the kit updated to a new place): `render-launchd`, then `install-launchd`. `python exited code=1` right after the start: the lines just above it in `stderr.log` name it (a missing config prints `config 不在: <path> (先照安装向导跑 wa_setup.py init, 再按 C2 站落 config)` there, before the log opens), otherwise the last lines of `wa_live.log`.
2. **Is the board serving?**
   ```sh
   grep -e "板子" -e "起跑" "$LIVE/wa_live.log" | tail -4
   lsof -nP -iTCP:$PORT -sTCP:LISTEN
   ```
   - `板子关闭 (config board.enabled=false)`: set `board.enabled` to `true`, restart.
   - `板子起不来 (127.0.0.1:<port>): <error>`: another program holds the port (`lsof` names it). Stop that program, or change `board.port` and the `tailscale serve` rule with it.
3. **The page loads but the list is empty.** The list shows judged conversations; "Other chats" shows recently active ones that were never judged. Both empty means the daemon cannot read WhatsApp's database:
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json"
   grep "recent_chats 失败" "$LIVE/wa_live.log" | tail -1
   ```
   `message table readable` missing or `PERMISSION DENIED` in the `[whatsapp desktop]` section: Full Disk Access (`references/pitfalls.md`, pit 1). A very old `last database write`: WhatsApp Desktop is closed or logged out; open it and check the phone's Linked devices.
4. **Other chats show, but nothing new gets judged.**
   ```sh
   grep "起跑" "$LIVE/wa_live.log" | tail -1
   sqlite3 -readonly "$LIVE/state.sqlite" "select decision, count(*) from inbound_log where seen_at >= date('now', 'localtime') group by decision"
   ```
   - The start line ends `门铃关 (不自动叫脑, 命令档与板子照常)`: `doorbell.enabled` is false.
   - Only `skip:` decisions: the gate is doing what it was told; see "A message is missing".
   - `pass:` rows but no runs: see "Runs are stuck with zero output".
   - No rows at all today, and a push `⚠️ [WA live] WhatsApp 的数据库已经 <h> 小时没动, <Mac> 上的 WhatsApp Desktop 可能掉线了。` arrived: WhatsApp Desktop stopped syncing. Open it on the Mac; relink it from the phone when it asks.

## A message is missing

A message you expected on the board was not judged. Find its conversation's last rows in the inbound log:

```sh
sqlite3 -readonly "$LIVE/state.sqlite" "select pk, msg_at, decision, policy from inbound_log where jid = '<jid>' order by pk desc limit 5"
```

| Decision | Why | Fix |
|---|---|---|
| no row at all | The doorbell never saw it: WhatsApp Desktop had not written it yet, or the watermark moved past it | Compare `sqlite3 -readonly "$WADB" "select max(Z_PK) from ZWAMESSAGE"` with `last_pk` from `wa_live.py --status`; open the chat in WhatsApp Desktop; press Judge now on the board |
| `pass:all`, `pass:mention` | It passed; the run is waiting, running, failed, or the row sits under "Replied / done" | `sqlite3 -readonly "$LIVE/state.sqlite" "select id, started_at, ok, error from runs where jid = '<jid>' order by id desc limit 3"`; a failure has a `run #<id> 失败` line and a push |
| `skip:policy` | A group set to `skip`, or not listed and `unlisted_group_policy` is `skip` | Change that group's policy (C2 whitelist edit), restart |
| `skip:no_mention` | A `mention` group, and the message did not name you | Move the group to `all`, or accept it; a reply to your message does not count as a mention |
| `skip:ignored` | The chat is in `ignored_jids` | Remove it, `sync-reader-config`, restart |
| `skip:too_old` | When it reached the database it was older than `doorbell.max_message_age_hours` (after WhatsApp Desktop linked again) | Judge now on the board |
| `skip:before_install` | It came before the first start of this state database, or before a restore | Judge now on the board |
| `skip:doorbell_off` | `doorbell.enabled` was false | Judge now on the board |
| `skip:status_or_broadcast`, `skip:system` | A status update, a broadcast list or a system notice | Nothing: never judged by design |
| `outbound:replied`, `outbound:replied:burst_cancelled` | You replied first, so the waiting burst was dropped | Nothing: by design |

A push `⚠️ [WA live] 水位 <old> 比 WhatsApp 库最大编号 <max> 还大 ...` means WhatsApp's database was rebuilt or linked again: the watermark was reset to the newest message, older rows moved to `inbound_log_archive`, and messages from before the reset are not judged by themselves. Judge the ones that matter from the board.

## Runs are stuck with zero output

A `run #<id> 开始` with no `完成` for many minutes, or failures that say nothing was produced.

1. **What did the run say?**
   ```sh
   grep -e "开始" -e "失败" -e "超时" "$LIVE/wa_live.log" | tail -6
   ```
   - `claude -p 超时 <n>s (半途输出 0 字, 看 claude.stdout.jsonl)`: claude produced nothing; go to check 2.
   - `wa_sweep 超时 <n>s (语音多的对话第一次要转很久; 先丢 commands/prewarm-<days>.json 把快取喂饱)`: the reader is transcribing a voice-heavy chat for the first time. `echo '{}' > "$LIVE/commands/prewarm-7.json"`, wait for `prewarm 完成`, then Judge now.
   - `claude -p 没有回 result (rc=<rc>): <tail>`: the tail names it (a usage limit, a login problem, a crash).
   - `claude 回 is_error: <text>`: claude answered with an error; the text names it.
2. **Can claude answer from launchd at all?**
   ```sh
   echo '{}' > "$LIVE/commands/probe-claude.json"
   ```
   A minute later: `grep "probe-claude 结束" "$LIVE/wa_live.log" | tail -1`, and `ls` of the newest `runs/probe-*` folder.
   - `rc=killed after 90s` and a `sample.txt` whose stack sits in `__open`: the node binary launchd starts lacks Full Disk Access, so every child hangs on a privacy prompt nobody can answer (`references/pitfalls.md`, pit 1). `plutil -extract ProgramArguments.0 raw ~/Library/LaunchAgents/$LABEL.plist` names the binary; the owner grants it in System Settings, Privacy & Security, Full Disk Access; `launchctl kickstart -k gui/$(id -u)/$LABEL`; probe again. A node upgrade changes the path, so the grant must be given again.
   - `stderr.txt` names a login problem: log in to Claude Code in the terminal on the Mac itself, then probe again.
   - `rc=0` in a few seconds: claude is fine; the stall is in the reader (check 3) or the run's size.
3. **Are children hanging?**
   ```sh
   ps -A -o pid,etime,%cpu,command | grep -e "claude -p" -e ffmpeg -e whisperkit -e wa_sweep.py | grep -v grep
   ```
   `ffmpeg`, `whisperkit-cli` or `claude` minutes old at 0% CPU is the same privacy stall as check 2.
4. **Is the working folder in a protected place?** A log line `brain.cwd 在 ~/<folder> 底下 (<path>): launchd 底下的 claude 开 TCC 保护的夹会卡死, 换到家目录下的一个空夹` means `brain.cwd` sits under Documents, Desktop or Downloads. Point it at an empty folder under the home folder (the default `<home>/brain-cwd`) and restart.
5. **Is the cage file there?** `claude-settings.json 不在 (<path>): 先跑 wa_setup.py init 渲染, 不然每个 run 都会失败` means every run fails at once: `"$PY" "$KIT/daemon/wa_setup.py" init --config "$LIVE/config.json"` renders it without overwriting anything else.

## Voice notes have no transcript

1. **Is transcription on, and does the reader find its tools?**
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
   "$PY" -c 'import json, os, sys; c = json.load(open(sys.argv[1])); print(c.get("transcribe"), [(k, os.path.exists(os.path.expanduser(str(c.get(k) or "")))) for k in ("whisper_model", "whisperkit_bin", "ffmpeg_bin")])' "$HOME_DIR/config.json"
   ```
   - `False`: `brain.transcribe` is off.
   - A `False` path, or `brain.whisper_model: model folder not found; voice notes stay untranscribed`: fix `brain.whisper_model` or `bin.whisperkit` / `bin.ffmpeg`, then `sync-reader-config` and restart.
   - `brain.whisper_model: inside a privacy protected folder; copy it under ~/.cache`: a model under Documents stalls under launchd; copy it under `~/.cache/whisper-models/` and point the key there.
2. **Did a read ever reach that voice note?** The board shows transcripts only from `<home>/transcripts-cache/`, filled by runs, prewarm and manual reads. A voice note in a chat no run has read since it arrived has none yet: `echo '{}' > "$LIVE/commands/prewarm-7.json"`, or Judge now on that chat.
3. **Did transcription fail?** Read the voice counts of a fresh read of that chat:
   ```sh
   "$PY" "$KIT/skills/breakthrough-whatsapp-read/scripts/wa_sweep.py" --work-dir "$HOME_DIR" --days 1 --only-jid '<jid>'
   ```
   The last line's `voice` shows `fresh`, `cache_hits` and `failed`. `failed` above 0 with processes hanging is the privacy stall (`references/pitfalls.md`, pit 1). A voice note WhatsApp Desktop never downloaded counts as a media gap and cannot be transcribed.
4. **Another tool's config?** `grep -rn "meeting-transcripts" "$KIT/daemon" "$KIT/skills"` prints nothing on a sound payload (`references/pitfalls.md`, pit 13).

Messages from the second source carry no transcript at all (known limit).

## Images or videos do not show

1. **Is the file on this Mac?**
   ```sh
   curl -s "http://127.0.0.1:$PORT/api/thread?jid=<jid>&n=60" | "$PY" -c 'import json, sys; [print(m["pk"], m["type"], m["media_ctype"], m["media_is_thumb"], bool(m["media_url"])) for m in json.load(sys.stdin)["messages"] if m["type"] != "文字"]'
   ```
   - `media_url` false: WhatsApp Desktop never downloaded the file (only phones did). Open the chat in WhatsApp Desktop and download it, or turn on automatic media download there.
   - `media_is_thumb` true: only the thumbnail is on this Mac; the board says so under the picture.
2. **Does the media endpoint serve it?**
   ```sh
   curl -s -o /dev/null -w "%{http_code} %{content_type}\n" "http://127.0.0.1:$PORT/media?pk=<pk>"
   curl -s -D - -o /dev/null -r 0-99 "http://127.0.0.1:$PORT/media?pk=<pk>"
   ```
   `404` (`no media`): the path WhatsApp recorded is gone, or the type is not one the board shows inline (documents are listed, never shown). `413`: larger than 40 MB, not served. A video that plays on the desktop but not on the phone needs `206 Partial Content` with `Content-Range`; anything else is a network or proxy problem between the phone and the Mac.
3. **A log line** `media <pk> 送到一半失败: <error>` is a dropped connection while sending; reload.

## The board is unreachable from the phone

1. **Does it answer on the Mac?** `curl -s -w ' %{http_code}\n' "http://127.0.0.1:$PORT/health"`. Not `ok 200`: see "Nothing on the board".
2. **Is the tailnet rule there, for this port?**
   ```sh
   tailscale serve status
   "$PY" "$KIT/daemon/wa_setup.py" tailscale-hint --config "$LIVE/config.json"
   ```
   The hint reads `tailscale serve status` itself, read only, and runs nothing. `this board (http://127.0.0.1:<port>) is already on your tailnet, tailnet port <n>. Nothing to run.`: the rule is there; go to check 3. Otherwise it prints the `tailscale serve --bg` command for the current `board.port`, on tailnet port 80, or, when that port already serves something else (another install's board, another service), on the first free port from 8443 up with a `WARNING:` line saying so: it never prints a command that replaces another handler. No rule: show the owner the hint's command and let them run it (it changes their tailnet). A stale rule for this board's old port also holds port 80: the owner removes it first (`tailscale serve --http=80 off`), then runs the hint again.
3. **Is the phone on the same tailnet?** The Tailscale app is connected on the phone, and `tailscale status` on the Mac lists the phone. Use the address `tailscale serve status` prints, never the Mac's Wi-Fi address: the board listens on `127.0.0.1` only, by design.
4. **Is the Mac awake?** An always-on Mac must not sleep: `pmset -g` shows `sleep 0` when system sleep is off.
5. **The page opens, but "Take to Claude Code" or "Copy draft" fails** on some phones: copying needs a secure page. `tailscale-hint --https` prints the `https` form of the rule.

## Pushes are not arriving

1. **Which outlet and mode did the daemon start with?**
   ```sh
   grep "notify 出口" "$LIVE/wa_live.log" | tail -1
   ```
   `mode=off` sends nothing; `mode=errors` sends only failures and the digest, so a normal judgment never pushes; `outlet none` only logs. ` · dry-run 一律不推` at the end: the daemon was started with `--dry-run`.
2. **Send a test through the daemon and read what happened:**
   ```sh
   echo '{}' > "$LIVE/commands/notify-test.json"
   ```
   Then `grep -e "notify" "$LIVE/wa_live.log" | tail -4`:
   - `notify-test: 已发` and nothing on the phone: the outlet accepted it; look at the phone side (a muted chat, the wrong Telegram chat, the ntfy topic).
   - `notify failed: telegram file missing or unreadable: <path>` or `notify failed: telegram file has no bot_token or chat_id yet: <path>`: the owner fixes `live/telegram.json` (0600); it is read at each push, no restart.
   - `notify failed: <Telegram's reason>` saying the chat was not found: the owner has not pressed Start in the bot, or `chat_id` is wrong.
   - `notify failed: notify.command exit <n>: <output>`: the command failed under launchd. Its `PATH` is `bin.path_env`, so use absolute paths in `notify.command`; a command that works in the terminal can still fail here.
   - `notify failed: notify.command timed out after 30s`: the service did not answer.
   - `notify suppressed (<key>, gap not reached)`: the same alert went out less than its gap ago (30 minutes per chat for run failures, an hour per nightly job); by design.
3. **From the terminal**, the same outlet with the terminal's environment:
   ```sh
   "$PY" "$KIT/daemon/wa_common.py" --config "$LIVE/config.json" --notify --kind error <<< "WhatsApp Live test push"
   ```
   `notify: delivered` here but a failure inside the daemon points at the environment (PATH, a file only the terminal can read).
4. **Only the digest is missing:** see "The digest is stale or missing".

## A person note is refused

The push `⚠️ [WA live] 档案 run #<id> 失败 (<name>): <reason>` names the reason; the job row and the candidate the model wrote keep it:

```sh
sqlite3 -readonly "$LIVE/state.sqlite" "select id, name, reason, error, run_dir from dossier_jobs order by id desc limit 3"
"$PY" "$KIT/skills/breakthrough-person-dossier/scripts/dossier_core.py" --check-live --config "$LIVE/config.json"
```

The candidate is `note.md` in the job's run folder, and `dossier.py verify --name <note> --candidate <that note.md>` repeats every check from the terminal, writing nothing.

| The reason says | Fix |
|---|---|
| `frontmatter keys outside the doctrine person family: <keys>` | The playbook skeleton or the model uses a key the doctrine does not allow. Add it to the `person` family through the vault guardian, or remove it from the skeleton |
| `frontmatter roles <roles> are not in the closed list <list>` | A role the doctrine does not have; add it to the doctrine with the owner, or fix the note |
| `required frontmatter keys are missing or empty: <keys>` | The doctrine requires a key the note lacks; usually a new required key: fill it in the existing note |
| `missing section <name> (section names are read from the playbook skeleton)` | The playbook's skeleton was renamed; rename `person.section_roles` in the same breath |
| `the notes section (<heading>) lost <n> item(s), e.g.: <item>` | The model dropped items the owner wrote; press Rewrite note again; when it repeats, look at the note's length with the owner |
| `links of the old note are gone: <links>`, `roles of the old note were removed (roles may be added, never removed): <roles>` | The same kind of loss; rewrite again |
| `wa_jid was changed: ...`, `since was changed: ...` | Those keys never change; rewrite again |
| `the machine recent block markers are missing (<!-- wa-live:begin ... <!-- wa-live:end -->)` | Someone removed the markers by hand; put the two marker lines back around the recent block |
| `person.roles_with_stage <roles> shares no value with the doctrine roles <roles>, ...` | Set `person.roles_with_stage` to roles the doctrine really has |
| `<file> already exists but this run treated the person as new; refusing to overwrite (match the person to that note by wa_jid or wa_phone, then rewrite it)` | The daemon did not recognize an existing note. Add `wa_jid` or `wa_phone` to that note, then press Rewrite note |
| `<file> changed on disk since this run read it; not overwriting (run it again from the current file)` | The owner edited the note during the run; the job is queued again once by itself |
| `<file> was read when this run started but is gone now (moved or renamed?); not writing` | The note moved; point the person at the new file (its `wa_jid`) and rewrite |
| `people folder not found: <path>`, `People 房不在 (paths.people_dir=<path>), 不写档` | Fix `paths.people_dir`, restart |
| `cannot back up <file> before writing (<error>); not writing` | `live/dossier-backups/` is not writable: disk space or permissions |
| `档案 run 超时 <n>s` | A long history; raise `dossier.run_timeout_seconds` or lower `dossier.pack_days` |
| `中断: daemon 重启时这个 job 没跑完` | A restart cut the run; queue it again from the board |
| `人物档没开 (dossier.enabled=false)` (on the board) | Notes are switched off |

A `FAIL` line from `--check-live`, or a `playbook:` line saying `PAYLOAD SEED`, is a setup problem that refuses every note until fixed. The same person is not queued by itself again for `dossier.min_gap_hours` (6) after a failure; "Rewrite note" on the board skips that wait.

## The backup is failing

The push `⚠️ [WA live] 排班 backup 失败: <reason>` and `live/backup-last.json` carry the same reason:

```sh
"$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["at"], d["ok"], d["error"])' "$LIVE/backup-last.json"
"$PY" "$KIT/daemon/wa_backup.py" --config "$LIVE/config.json" --dry-run
```

| Reason | Fix |
|---|---|
| `backup.dest is not set` | Set `backup.dest` (station O5), restart |
| `required process not running: <name>` | Start the sync app. When it is running, the name is wrong: `pgrep -x "<name>"` must find it; copy the name exactly as Activity Monitor shows it into `backup.require_process` |
| `destination not reachable, its parent folder is missing: <path>` | The disk is not mounted, or the sync app moved or removed its folder. Mount it or fix `backup.dest` |
| `state.sqlite copy failed integrity_check: <result>` | Check the source: `sqlite3 -readonly "$LIVE/state.sqlite" "PRAGMA integrity_check"`. `ok` there: the destination disk is at fault; anything else: stop the daemon and restore from the newest good backup |
| `read back: <file> is <n> bytes, the source had <m>` | Something changed the copy while it was written (a sync app rewriting it, a full disk) |
| a permission error | Only when run from the terminal: a cloud folder is privacy protected and only the daemon's node parent holds Full Disk Access. Prove it with `backup-now.json`, never with `wa_backup.py --run` |

After the fix: `echo '{}' > "$LIVE/commands/backup-now.json"`, then `backup-last.json` must say `true`.

## The digest is stale or missing

1. **Did it run this morning?**
   ```sh
   grep -e "排班 digest" -e "digest 20" "$LIVE/wa_live.log" | tail -3
   ls -t "$LIVE/digest" | head -2
   ```
   - `排班 digest: 启动时已过 <time>, 今天记成跑过 (要现在跑就丢 commands/digest-now.json)`: the daemon restarted after `digest.at`. `echo '{}' > "$LIVE/commands/digest-now.json"`.
   - `排班 digest 没开 (config enabled=false 或模组没载入)`: `digest.enabled` is false, or `模组: 缺 wa_digest` at the start says the module failed to import (the line before it names the error).
   - `digest <date>: 只写档不推 (<n> 行; send=True · digest.push=<value> · notify.mode=<mode>)`: written, not pushed, because `digest.push` is false or the mode is `off`.
   - `digest <date>: 没推出去 (看出口的 log)`: the outlet failed; see "Pushes are not arriving".
   - Nothing at all: the daemon was not running at `digest.at`, or the Mac slept through the day.
2. **It arrived, but a health part looks old.** Each part reads its own job's last result and says so when that result is from an earlier night:
   - `<label> ✗ (stopped at <date>)`, `<label> mismatches: the facts job has not run yet`: the facts job did not run or failed; `grep "排班 facts" "$LIVE/wa_live.log" | tail -2`, then "The facts job fails".
   - `backup has not run yet`, `backup ✗ (<time>, <reason>)`: "The backup is failing".
   - `last pull was <day>, not <day>`, `<label> pull ✗ (<reason>)`: "The second source pull fails".
   - `⚠️ owner copy missing, running on the payload seed: <what>`: an owner copy in `live/prompts/`, `live/topic-map.json` or the person playbook is gone. `"$PY" "$KIT/daemon/wa_setup.py" seed-status --config "$LIVE/config.json"` names it; `init` renders a missing prompt again without overwriting anything.
   - A job whose time is after `digest.at` always reports yesterday's result: keep `facts.at`, `second_source.at` and `backup.at` before `digest.at`.
3. **The board's Yesterday card is old.** It shows the newest file in `live/digest/`; no new file means check 1.

## The facts job fails

`⚠️ [WA live] 排班 facts 失败: facts source <label> could not be read: <errors>`. The previous `facts.json` stays in use. Run the source from the terminal to see the whole reason:

```sh
"$PY" "$KIT/daemon/wa_facts.py" --config "$LIVE/config.json" --dry-run --quiet
```

- `cannot read <file or host>: <error>`: the file moved, or the link stopped being published.
- `<label>: header has no column for <field> (header: <headers>)`: a column was renamed in the source; update `facts.csv.columns`.
- A link that starts with `http://`: only `https://` links are read.
- For `lark`: a table error per table (login expired, no permission, wrong id). The owner logs `lark-cli` in again in the terminal on the Mac.
- It works from the terminal but not at night: prove it inside the daemon with `echo '{"dry_run": true}' > "$LIVE/commands/facts-now.json"`; a difference is launchd's environment (`bin.lark_cli`, `bin.path_env`).
- `facts: People 房读不到 (<reason>), 这一轮不排开档`: the facts were written, but no note was queued because the people folder could not be read.

## The second source pull fails

`⚠️ [WA live] 排班 second_source 失败: <reason>`, and `live/second-source-last.json` with `"ok": false` and its errors:

```sh
"$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["day"], d["ok"], d["errors"])' "$LIVE/second-source-last.json"
"$PY" "$KIT/daemon/wa_second_source.py" --config "$LIVE/config.json" --dry-run
```

- `whitelist not found: <path>`, `whitelist unreadable: <path> (<error>)`, `whitelist has no chats list: <path>`: the file is missing or broken; a missing whitelist is an error on purpose, never zero chats.
- `lark-cli not found (set bin.lark_cli)`: set `bin.lark_cli` to its absolute path, restart.
- Every chat failing: the owner's `lark-cli` login expired; they log in again in the terminal on the Mac.
- `owner id ?` in the dry run: the owner's id could not be read; set `second_source.lark.owner_ids`.
- `model or writing failed: <error>`: the model call or the note writes failed; the run folder's `result.txt` has the model's answer.
- It pulled, but no recent line appeared in a note: `dossier.enabled` is false, the person's note has no `lark_id`, or two notes carry the same id (`! person note id conflict: lark_id <id> on <notes>` in the dry run).

## A summary backfill batch fails

`backfill 完成 (<s>s): 候选 <c> · <b> 批 · 写 <w> 条 · 失败 <f> 条` with `<f>` above 0. Each failed batch has its own line, and a batch that reached the model has a `backfill` row in the runs table with the same reason in `error`:

```sh
grep "backfill: backfill b" "$LIVE/wa_live.log" | tail -6
sqlite3 -readonly "$LIVE/state.sqlite" "select id, started_at, ok, error from runs where kind = 'backfill' order by id desc limit 3"
```

- `failed, all <k> retried next time: model returned is_error: error_max_turns: Reached maximum number of turns (<n>)`: the model spent every turn before answering, usually opening images and files (each one costs a turn). The batch runs with `backfill.max_turns` (20) and can open at most `backfill.max_images` (4) paths, the rest of its media lines showing `(路径略)`. Lower `backfill.batch_size` (10) or `backfill.max_images`, or raise `backfill.max_turns`, with the config edit ritual and a restart, then drop `backfill-summaries.json` again with a small `limit`. The batch's conversations stayed candidates, so nothing is lost.
- `failed, all <k> retried next time: model returned is_error: <other reason>`: the reason names it (a usage limit, a login problem); fix that and drop the file again.
- `failed, all <k> retried next time: no summary could be parsed (<n> chars returned)`: the answer was not JSON the parser could read; the raw answer is in that batch's `result.txt` (`ls -td "$LIVE"/runs/backfill-*`, `references/pitfalls.md`, pit 5).
- `the model left out <n>, retried next time: <jids>`: the batch wrote the others; the left out ones stay candidates.
- `reader failed for <name> (<jid>): <error>` or `empty reading pack for <name> (<jid>)`: that conversation's reading pack failed, as in "Runs are stuck with zero output" (a voice-heavy chat: warm the cache with `prewarm-30.json` first).
- A failed row in `runs` with an empty `error` is a bug, never expected: checklist row K254 is the check.

## The evening intake push does not arrive

```sh
"$PY" "$KIT/daemon/wa_intake.py" --config "$LIVE/config.json" --evening-status
tail -5 "$LIVE/intake-evening.log"
launchctl print gui/$(id -u)/$ILABEL | grep -E "state = |runs = "
```

- `"run": false` with a `why`: `intake.enabled is false, evening push skipped`, `intake.evening_push is false, evening push skipped`, `intake.buffer is not set, evening push skipped`, `intake buffer not found: <path>`, or `notify.outlet is none or notify.mode is off, evening push skipped`.
- `open entries: 0, nothing sent`: nothing was open; by design.
- `push: notify: not sent (<why>)`: the outlet refused it; see "Pushes are not arriving".
- `pre-sort unusable, sending the raw list:`: the model's answer could not be read, so the plain list went out instead.
- No service: the job was never installed, or `intake.evening_at` changed without `render-launchd --intake` and `install-launchd --intake`.

## Cage breach

A push `🚨 [WA live] ... cage breach: 载入了不该有的工具 <tools>` means a run loaded a tool outside Read, Glob and Grep (`TodoWrite` is tolerated). The run still happened; the tool list is the finding.

```sh
sqlite3 -readonly "$LIVE/state.sqlite" "select id, kind, name, cage_breach from runs where cage_breach not in ('', '[]') order by id desc limit 5"
grep -e "CAGE BREACH" -e "claude-settings.json" "$LIVE/wa_live.log" | tail -5
"$PY" "$KIT/daemon/wa_setup.py" seed-status --config "$LIVE/config.json"
```

- `claude-settings.json 的 deny 少了 <tools> (命令列照挡; 下次 wa_setup.py init 补齐)`: the rendered settings are older than the code. Move `live/claude-settings.json` aside, run `init` to render it again, merge any edit the owner made, restart.
- `claude-settings.json 没有任何 Read(...) 路径 deny: 脑读得到推播钥匙与设定档, 重跑 wa_setup.py init`: the Read deny rules are gone; the same fix, then the canary in `references/pitfalls.md`, pit 10.
- A new tool name after a Claude Code update: the kit's deny list does not know it yet. Report it upstream with the tool name; until a fixed kit ships, pause the doorbell (`doorbell.enabled` false) if the tool can write or reach the network.

## Known limits

What this version does not do, so nobody hunts for a bug that is a boundary.

- **Usage from two paths is not in the `runs` table**: the evening intake pre-sort's model call, and `wa_backfill.py --run` from the terminal. A terminal backfill also rewrites `live/summaries.md` without the gate filter until the daemon's next run rewrites it. Use the `backfill-summaries.json` command file instead.
- **`live/runs/` is never cleaned**, and neither are `<home>/runs-work/` and `<home>/transcripts-cache/` (`references/operating.md`, "Housekeeping").
- **Nothing technical stops two Macs from running at once.** The rule lives in the runbook and in `uninstall-launchd`; a standby that was installed and not uninstalled comes back at its next login.
- **The board has no login.** The tailnet is its only lock; anyone in the tailnet can open it (`references/privacy.md`).
- **Board buttons and `test-run` skip the concurrency limit**: Judge now, Full re-judge and `test-run.<jid>.json` start at once, beyond `debounce.global_concurrency` (only a run already going on that same chat holds them back).
- **"Other chats" still lists chats in `ignored_jids`**; they are never judged or read into packs, only listed.
- **A reply to your own message in a `mention` group is not a mention**; only your name or an @ tap is.
- **The second source whitelist is written by hand**: the kit has no chat lister, so chat ids come from the owner's Lark.
- **Second source voice messages carry no transcript**, only their length.
- **Recent lines need person notes switched on**: the group speaker lines and the second source's lines are written only while `dossier.enabled` is true.
- **The age gate covers only messages older than `doorbell.max_message_age_hours` (48)**: after WhatsApp Desktop links again, the last two days of synced history can be judged once.
- **Times are local clock strings.** Around a daylight saving change or a move to another time zone, ordering and "yesterday" can be off by the size of the shift.
- **WhatsApp Desktop's database is undocumented.** An app update can change its shape; `doctor`'s `message table readable` line and a reader run are the first place it shows.
- **Media WhatsApp Desktop did not download is a gap**, counted in every pack and never fetched from the phone; documents are listed, not shown inline.
- **The backup leaves out a few files in `live/` without naming them on its never list**: the dated `config.json.bak-<time>` copies (the current config is backed up, redacted), the rotated `wa_live.log.<n>` files, and the database's `-wal` and `-shm` files, whose content the online backup already holds.
- **Your own adapters are backed up as they are**: `live/adapters/` is copied without redaction, so a token kept in a file there lands in every backup folder.
- **`check-config` knows your own adapter by name only**: it accepts `facts.source`, `second_source.source` or `calendar.destination` naming a file in `live/adapters/`, but reports a config block the example does not list (such as `facts.<name>`) as an unknown key; and `wa_calendar.py --probe` accepts only the shipped destinations (`references/interfaces.md`, "Where a new implementation lives").
- **A side stage should keep the key `parked`**: the judgment rules, the time rule and the person note writer honor `side: true` on another key, but `check-config` asks for a stage keyed `parked` while `stage_rules.parked_after_days` is above 0, and the board draws only `parked` as the gray side stage.
- **Probing `claude` over ssh gives a false `Not logged in`**; every claude probe runs on the Mac itself.
