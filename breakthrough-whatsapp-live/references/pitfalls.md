# Pitfalls

Each pit on this page is a way a resident system like this one fails quietly, and each can cost hours, model usage or data. The kit ships a guard for each. Every section says what the pit looks like from outside, why it happens, where its guard lives in the kit, and a probe that shows the guard still holds on your install.

Run a pit's probe again whenever you, or a session working for you, change code or config near its guard. A guard nobody has tested is a belief, not a guard.

## Before the probes

Every probe assumes these lines in the shell. `KIT`, `HOME_DIR` and `PY` are the only values you fill in (`HOME_DIR` exactly as the header of `<home>/INSTALL-PROGRESS.md` records `paths.home`, an absolute path, because a `~` inside quotes is not expanded); the rest is read from your config through the same loader the daemon uses.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the installed payload
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3                                  # bin.python
cfg() { "$PY" -c 'import sys
sys.path.insert(0, sys.argv[1] + "/daemon")
import wa_common
c = wa_common.load_config(sys.argv[2] + "/config.json")
for k in sys.argv[3].split("."):
    c = c[k]
print(c)' "$KIT" "$LIVE" "$1"; }
LABEL=$(cfg launchd.label); ILABEL=$(cfg launchd.intake_label); PORT=$(cfg board.port)
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
```

Probes marked **model call** spend a little usage. Probes marked **test install** must run on a separate install (its own `paths.home`, `launchd.label` and `board.port`, as station O11 sets up), never on the one that reads your real chats. Everything else is read only.

| # | Pit | What it looks like from outside |
|---|---|---|
| 1 | Children of a launchd job cannot open protected folders | runs hang with no output and no error |
| 2 | The dollar figure is not money, and the harness is most of every run | a scary cost reading, savings that never arrive |
| 3 | A daytime restart runs the nightly job | person notes rewritten in the afternoon |
| 4 | A full rewrite quietly drops a section | a hand-written section gone, every check green |
| 5 | A verifier that only accepts the house spelling | correct notes rejected again and again |
| 6 | One rule computed in two places | two parts of the system disagree, all tests pass |
| 7 | An allowlist that only adds | a locked-down run that is not locked down |
| 8 | A fresh state database replays old messages | hundreds of old messages judged on first start |
| 9 | A new note silently replaces an existing one | a person note overwritten with no backup |
| 10 | The brain can read your secrets | nothing visible at all |
| 11 | A dry run that is not dry | a trial run that writes, pushes and spends |
| 12 | A backup that misses a file | a green backup that cannot restore everything |
| 13 | Voice transcription that depends on another tool's config | no voice note is ever transcribed |
| 14 | Claude memory in a headless run | runs that wander through unrelated notes and end at the turn limit |
| 15 | A restart that orphans a frozen python | the board shows the old process, or nothing, after a restart |
| 16 | A health check answered by the wrong thread | `/health` says ok while nothing has been judged for hours |

## 1 · Children of a launchd job cannot open protected folders

**What happens** The daemon starts and looks healthy: the board loads and the WhatsApp database is read. But every child process it starts (`claude`, `ffmpeg`, `whisperkit-cli`, a command line adapter) hangs with no output and no CPU until its timeout, and nothing prints an error. Judgments never finish, voice notes never get a transcript, and from outside it looks as if the daemon is missing messages. Because nothing errors, it is easy to spend hours changing code in the wrong place while voice notes quietly never reach the board.

**Why** macOS privacy protection (TCC). When launchd starts the system python directly, python itself can use its Full Disk Access, but the processes it starts cannot: each one that touches a protected folder (Documents, or another app's Group Containers, where WhatsApp keeps its database and media) waits for a permission prompt that nobody can answer in a background job. With a node binary that holds Full Disk Access as the program launchd starts, the whole process tree works. The same command run over ssh or in Terminal works fine, which is what makes this so misleading: those paths carry different permissions and a different keychain.

**Where the guard lives** `daemon/templates/run.mjs` (the node parent that starts python with `daemon/wa_live.py`) and `daemon/templates/daemon.plist` (node is the first program argument), rendered by `wa_setup.py render-launchd` and installed by `wa_setup.py install-launchd`; `wa_setup.py doctor` reports Full Disk Access for the python and the node parent. Three independent belts stay on: `brain.cwd` defaults to `<home>/brain-cwd`, outside Documents; `Brain.call_claude` passes no `--add-dir`; the whisper model is a copy under `~/.cache/whisper-models/` (`brain.whisper_model`). `Daemon.probe_claude` (the `probe-claude.json` command file) saves a stack sample when a probe has not answered in 90 seconds.

**How to check it still holds** (one **model call**)

```sh
plutil -extract ProgramArguments.0 raw ~/Library/LaunchAgents/$LABEL.plist    # the node path
nodepid() { launchctl print "gui/$(id -u)/$LABEL" | awk '$1 == "pid" && $2 == "=" {print $3; exit}'; }
ps -o comm= -p "$(nodepid)"                                                    # node: the process launchd started
DPID=$(pgrep -P "$(nodepid)"); ps -o command= -p "$DPID"                      # python running daemon/wa_live.py, node's child
echo '{}' > "$LIVE/commands/probe-claude.json"; sleep 60
ls "$(ls -td "$LIVE"/runs/probe-* | head -1)"                                  # no sample.txt in the newest probe folder
```

Pass: node is the program launchd started and python's parent, and the probe answers in seconds. (`pgrep -f "daemon/wa_live.py"` is no shortcut: node's own command line names that script too, so it finds node first.) If a run ever hangs again, the first cut is `pgrep -P "$DPID"` to find the stuck child, the second is `sample <child pid> 3 | grep -m3 open`: a stack stuck in `__open` is this pit, not a code bug.

## 2 · The dollar figure is not money, and the harness is most of every run

**What happens** Every run shows a dollar amount, and it feels like spending. Separately, shrinking the conversation pack the kit sends looks as if it should halve the cost of a run, yet the total per run barely moves: what the kit sends is the smaller share of a run, and the harness overhead stays. A few dozen runs a day can read millions of tokens.

**Why** On a subscription login, `total_cost_usd` from `claude -p` is what the API would have charged; nothing was billed, and the real limit is the subscription's usage window, which follows tokens. Most of every run is Claude Code's own system prompt and tool definitions, tens of thousands of tokens (the board footer shows your own figure), rebuilt on each run because the prompt cache expires when runs are more than about five minutes apart (lots of cache creation and little cache reading is that shape). "What we send got half as big" and "the run got half as big" are different sentences; the only big lever is running fewer runs.

**Where the guard lives** `Brain.call_claude` records input, output, cache read and cache creation tokens from each run's usage; `State.finish_run` stores them and keeps `cost_usd`; `State.board_status` sums tokens for the board footer, and the dollar sum rides along in the board payload; `board.html` prints a dollar figure only when `ui.show_usd` is true, and `board.show_usd` defaults to false; `wa_digest.build_digest` prints no dollars. `runs.kind` keeps triage apart from person note, second source and backfill runs so the footer and digest can count them; the runs that are not recorded are listed in `references/troubleshooting.md`, "Known limits".

**How to check it still holds**

```sh
curl -s "http://127.0.0.1:$PORT/api/board" | "$PY" -c 'import json, sys; print(json.load(sys.stdin)["ui"]["show_usd"])'   # False
grep -c "UI.showUsd &&" "$KIT/daemon/board.html"                              # 2: both dollar spots wait for show_usd
grep -ci "usd" "$KIT/daemon/wa_digest.py"                                      # 0: the digest has no dollar figure
sqlite3 -readonly "$LIVE/state.sqlite" "select kind, count(*), sum(input_tokens), sum(cache_read_tokens), sum(cache_create_tokens), sum(output_tokens) from runs where started_at >= date('now','localtime') group by kind"
claude auth status                                                             # subscription, or an API key
```

Pass: `False`, `2` and `0`, so no dollar figure reaches the screen (the payload still carries `cost_usd`, for an owner who turns the display on); the first three sums add up to the footer's "read" figure and the fourth to "written". Before estimating any saving, split one run into those four parts and change only the largest part you actually control.

## 3 · A daytime restart runs the nightly job

**What happens** The daemon is restarted in the middle of the day after a code change, and the nightly person note rewrite starts at once: if a daytime restart counts as "not yet run today", expensive runs rewrite notes from half a day of messages (and a full rewrite can lose a section, pit 4). Every further restart that day triggers it again.

**Why** A schedule that says "run once a day after the scheduled time if there is no record for today" holds for a process that has been running all night and fails right after a start: the time has passed and there is no record. A schedule that reads an empty "last ran" value as "due" fires right after every start.

**Where the guard lives** `Dossier.__init__` records the nightly run as done for today when the daemon starts after `dossier.nightly_at`; `Daemon.__init__` does the same for every daily job in the `Daemon.daily` table (facts, second source, backup, digest) and logs that it did; `Daemon.daily_tick` and `Dossier.nightly` read only those records. Catching up is explicit: drop `commands/<job>-now.json`. The Today brief follows the same rule: at start and at midnight it records its current inputs as seen, so a restart never builds a brief by itself; the first build waits for the first change, or for `today-now.json`. An evening intake job kept from an earlier release has `RunAtLoad` false in its installed plist. A new scheduled job added as a row in `Daemon.daily` inherits the guard; a job with its own clock check does not.

**How to check it still holds** (restarting kills runs in flight; their bursts run again afterwards)

```sh
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"; sleep 30   # in the daytime, after the scheduled times
sqlite3 -readonly "$LIVE/state.sqlite" "select key, value from meta where key like 'daily_%' or key = 'dossier_nightly_last'"
sqlite3 -readonly "$LIVE/state.sqlite" "select count(*) from dossier_jobs where requested_at >= strftime('%Y-%m-%dT%H:%M:%S','now','localtime','-2 minutes')"
plutil -extract RunAtLoad raw ~/Library/LaunchAgents/$ILABEL.plist   # only when a kept evening job is installed
sqlite3 -readonly "$LIVE/state.sqlite" "select max(built_at) from today_brief"   # only with today.enabled
```

Pass: every record shows today's date, no note job was queued by the restart (unless you pressed a board button), a kept evening job prints `false`, and the newest `built_at` of the Today brief is older than the restart until something changes. Any job that spends usage or writes files gets smoke tested at three moments before it is trusted: just after a start, just after its time, and across midnight.

## 4 · A full rewrite quietly drops a section

**What happens** A person note rewritten by the model passes every check (front matter legal, ids unchanged, every link still present) and is placed, yet a whole subsection the owner wrote by hand, with no links in it, is gone. Models tend to merge or drop note bullets they consider redundant: the links survive and the words do not.

**Why** "Keep what the owner wrote" in a prompt is a request, not a guarantee. A link check proves the references survived, not the words.

**Where the guard lives** `dossier_core.finalize` checks content, not only structure: every bullet and every `###` subsection of the original note's notes section must be found again; lock segments marked `<!-- <owner_marker> -->` must match word for word; the owner's own lines and their `🗣 <owner_marker>` marks must still be in place; `roles` may gain values but never lose one; `wa_jid` and `since` never change. Any miss rejects the whole candidate. `dossier_core.place_note` backs up what is on disk to `<live>/dossier-backups/<note>/` before writing (three versions kept) and appends one filing-log line. `Dossier.run_job` and `dossier.py place` share both functions.

**How to check it still holds**

```sh
D="$KIT/skills/breakthrough-person-dossier/scripts"
$PY "$D/dossier_core.py" --selftest | tail -1
$PY "$D/dossier_core.py" --check-live --config "$LIVE/config.json"
```

Then a content test: copy one real person note to a folder outside the vault with the Read and Write tools (the frontmatter guard refuses a shell `cp` of a `.md` note), delete one bullet from its notes section in the copy, and run `$PY "$D/dossier.py" verify --config "$LIVE/config.json" --name <Note-Name> --candidate <the edited copy>`. Pass: the selftest passes, the live check reads your real doctrine and playbook, `verify` fails and names the missing bullet, and `ls "$LIVE/dossier-backups/<Note-Name>/" | wc -l` never shows more than 3.

## 5 · A verifier that only accepts the house spelling

**What happens** A queue of new person notes is rejected again and again, although the notes were written correctly. A verifier written against one spelling rejects legal variants: `roles` written as a YAML block list (one value per line) reads as no roles, a quoted `wa_jid` does not equal the unquoted id, a harmless extra key such as `tags` is rejected as if it were a retired key. Each rejection costs a full model run plus a failure push, and each fix needs a restart before the next note can land. JSON parsers fail the same way: a reply that looks like good JSON saves nothing when the model leaves English double quotes unescaped inside a string (a summary in Chinese that quotes a phrase, for example).

**Why** Validators written against the house convention get test fixtures written by hand in that same convention, so they always pass. The model writes legal but different YAML and JSON. A dry run that skips the model never sees real model output, so the parser meets it for the first time in production.

**Where the guard lives** `wa_common.parse_frontmatter` and `dossier_core.parse_frontmatter` read flow and block lists and quoted and bare values; `dossier_core.finalize` strips harmless extra keys and logs them, and still rejects retired and unknown keys; `wa_common.repair_inner_quotes` is the one repair every model output parser (triage, backfill, second source) falls back to when the first parse fails; the backfill and second source prompts ask for 「」 around any quotation (`daemon/templates/backfill-prompt.md`, `second-source-prompt.md`); a failed parse keeps the model's raw text in the run folder (`result.txt`). The standing rule: before a new parser or verifier goes live, call the real model once on a minimal batch (for example `wa_backfill.py --run --limit 1`), keep that output, and make it a selftest fixture.

**How to check it still holds**

```sh
$PY -c 'import sys, json
sys.path.insert(0, sys.argv[1] + "/daemon")
import wa_common as W
fm, body = W.parse_frontmatter("---\nroles:\n  \x2d client\nwa_jid: \"123@lid\"\ntags: []\n---\nbody\n", lists=True)
print(fm)
bad = "{\"a@lid\": \"she said \"see you friday\" and left\"}"
print(json.loads(W.repair_inner_quotes(bad)))' "$KIT"
```

Pass: `roles` prints as a list holding `client`, `wa_jid` prints without quotes, and the broken JSON loads with the inner quotes kept as text. For the verifier, take a candidate that passes `dossier.py verify`, rewrite its `roles` as a block list, put quotes around `wa_jid`, add `tags: []`, and verify again: it still passes, with a line saying `tags` was stripped.

## 6 · One rule computed in two places

**What happens** Two parts of the system disagree about the same fact: the board says a customer is `active`, the command line says `won`, and the written spec says a third thing. Every part's own tests are green and every report says it passed. The same shape shows up as a check that can never fire (its condition is never true on real data, and a test run without that data cannot show it), or a resident job installed without its registry entry.

**Why** If one rule is implemented in two places, both copies can pass their own tests and still disagree. Only running both paths against the same real data, and reading the files instead of each path's own report, shows it.

**Where the guard lives** One rule, one function: `wa_facts.stage_floor` (the payment floor; the brain, the facts job and the person note command line all call it, and the note checks receive its answer), `wa_common.load_config` with `apply_defaults` (every default filled in one place for every module), `Dossier.note_for_speaker` (matching a chat or a group speaker to a person note), `wa_common.notify_send` (the one push path for the daemon and the command lines), `wa_common.CONTENT_TYPES` (the one content type table in the daemon), the quote rule itself, `wa_intake.resolve_quote` (the WhatsApp intake and the second source, through `wa_second_source.quote_in_chat`, both call it, so a quote is accepted or refused the same way on both paths, and the Today brief's quote check calls it too), and `wa_intake.entry_key`, the only place an intake entry's key is computed (listing, placing by key and dedupe alike). The reader is a separate skill and implements `transcript_key` with the identical algorithm, so its check is behavioral (pit 13). The process guard: after any change near a rule, a fresh session reviews the files and real data, never the report of whoever made the change; and installing a resident job and writing its registry entry are one act (`references/vault-proposals.md`, proposal f).

**How to check it still holds**

```sh
grep -rn --include='*.py' "def stage_floor" "$KIT/daemon" "$KIT/skills"      # one line: daemon/wa_facts.py
grep -rn --include='*.py' -e "WF.stage_floor" -e "mod.stage_floor" "$KIT/daemon" "$KIT/skills"   # the callers
grep -rn --include='*.py' "^def notify_send" "$KIT/daemon" "$KIT/skills"     # one line: daemon/wa_common.py
grep -rln --include='*.py' "api.telegram.org" "$KIT/daemon" "$KIT/skills"    # one file: daemon/wa_common.py
grep -rn --include='*.py' -e "^def resolve_quote" -e "^def entry_key" "$KIT/daemon" "$KIT/skills"   # two lines: daemon/wa_intake.py
```

Pass: one definition of each function, in the file named; the callers are two lines in `daemon/wa_live.py` (the brain and the facts job) and one in `skills/breakthrough-person-dossier/scripts/dossier.py`, each handing the work to `wa_facts`; only `wa_common.py` talks to Telegram; `resolve_quote` and `entry_key` are defined once, in `wa_intake.py`. Grep the code folders only, never the whole payload: these pages quote the same strings. The `^` matters for `notify_send`: the selftest in `wa_live.py` holds a fake with that name, an indented method, which the anchor leaves out. Then, for one paying test contact, the stage on the board (`curl -s "http://127.0.0.1:$PORT/api/board"`) equals the stage `dossier.py verify` reports for that person's note.

## 7 · An allowlist that only adds

**What happens** A model's refusal is not evidence of a locked tool set; compare the tool list the CLI reports. That list can still include tools that start sub-agents, schedule jobs on the machine and message other sessions. A rule written only into one program's notes protects only that program.

**Why** `--allowedTools` adds tools; it does not restrict the session to that list. What closes the door is `--disallowedTools` plus the settings file's deny list, and a deny list only closes the names you know, while the platform keeps adding tools. A model saying "I don't have that tool" is the model repeating its prompt, not the harness reporting what is loaded.

**Where the guard lives** `Brain.call_claude` passes `--disallowedTools` with the `DENY_TOOLS` list, `--settings` with `live/claude-settings.json` (rendered from `daemon/templates/claude-settings.json`: the same deny list, all hooks disabled) and `--strict-mcp-config` (no MCP servers); it reads the tool list from the stream's init event, compares it with `EXPECTED_TOOLS`, and anything extra is logged as `CAGE BREACH`, stored in `runs.cage_breach` and pushed even in `errors` mode. The list in code and the list in the settings template change together. The same check on person note runs (`Dossier.call_claude`) is listed in `references/troubleshooting.md` if your version lacks it.

**How to check it still holds** (one **model call**)

```sh
echo '{}' > "$LIVE/commands/probe-claude.json"; sleep 60
P=$(ls -td "$LIVE"/runs/probe-* | head -1)
grep -ho '"tools":\[[^]]*\]' "$P"/*.jsonl | head -1
grep -c "CAGE BREACH" "$LIVE/wa_live.log"
sqlite3 -readonly "$LIVE/state.sqlite" "select count(*) from runs where cage_breach is not null and cage_breach not in ('', '[]')"
```

Pass: the loaded list holds only `Read`, `Glob` and `Grep` (`TodoWrite` may appear), and both counts are 0. Ask the harness what is loaded; never ask the model.

## 8 · A fresh state database replays old messages

**What happens** Without the guard, the very first start with a new state database (a new install, a `--scan-once` acceptance test, or a standby Mac whose watermark was reset to take over) would treat roughly the last 300 messages as new: they would queue as bursts, mark conversations as replied, pair voice samples, and the brain would start running on all of them, even though the log says it starts from now and will not replay anything.

**Why** The watermark was set at the newest message id, but every scan looks back `doorbell.lookback` ids for messages written late and skips only ids already recorded in `inbound_log`, and on a fresh database that log is empty. WhatsApp Desktop linked again and syncing history, or a rebuilt id sequence, is the same trap from the other side.

**Where the guard lives** `Doorbell.scan`: when `inbound_log` is empty at the first scan, the lookback rows are recorded as `skip:before_install` and never considered. `Doorbell` also logs inbound older than `doorbell.max_message_age_hours` (48; 0 turns it off) as `skip:too_old`, and a watermark above the database's highest message id resets the watermark and pushes one error.

**How to check it still holds** (right after a first start, before any new message arrives)

```sh
sqlite3 -readonly "$LIVE/state.sqlite" "select decision, count(*) from inbound_log group by decision"
sqlite3 -readonly "$LIVE/state.sqlite" "select (select count(*) from runs), (select count(*) from pending)"
```

Pass: the lookback rows show as `skip:before_install` and the second query prints `0|0`. Then send one test message that the gate passes: exactly one new `pass:` row and one run.

## 9 · A new note silently replaces an existing one

**What happens** A person already has a note, but the daemon does not recognize it: the note carries the other form of the WhatsApp id, or no id, or belongs to a different person with the same name. A new note run starts, the model picks the same file name, and the existing note is replaced whole, with nothing in the backup folder and a filing-log line calling it new. A second form of the same pit: a rewrite that trusts the copy it read at the start of a run (which can last many minutes) loses recent lines appended in the meantime and edits the owner made by hand, and its backup holds only the start version. A third: a job cut off by a restart can stay "in progress" forever, so that person can never be queued again.

**Why** Exact id matching misses the other id form and the phone. A writer that does not re-read the target right before writing overwrites what changed, and so does a rewrite that trusts the copy it read before the model ran. And a job a restart interrupted stays in progress unless the next start cleans it up.

**Where the guard lives** `Dossier.note_for_speaker` is the one matching function (both WhatsApp id forms plus the phone), used by one-to-one runs, the board's note info, note jobs and intake. `dossier_core.place_note` re-reads the target right before writing and overwrites only when what the run started from is still what is on disk; when the target exists and the run believed there was no note, it refuses (`DossierError`: the job fails and pushes an error); when only the machine-written recent block changed during the run, it carries the new lines into the candidate; it always backs up what is on disk first. `Dossier.run_job` calls it. `Dossier.__init__` marks jobs a restart left unfinished as failed, so the person can be queued again. Note file names may use letters in any script, digits, spaces, dot, dash and underscore, never a path separator or `..`.

**How to check it still holds** (writes only inside a new temporary folder; no model call)

```sh
$PY -c 'import sys, tempfile, pathlib
kit, live = sys.argv[1], sys.argv[2]
sys.path.insert(0, kit + "/daemon")
sys.path.insert(0, kit + "/skills/breakthrough-person-dossier/scripts")
import wa_common, dossier_core
t = pathlib.Path(tempfile.mkdtemp())
cfg = wa_common.load_config(live + "/config.json")
for k in ("vault", "people_dir", "filing_log", "doctrine", "person_playbook", "intake_buffer", "decisions_dir"):
    cfg["paths"][k] = str(t / k)
people = pathlib.Path(cfg["paths"]["people_dir"]); people.mkdir(parents=True)
(people / "Probe-Person.md").write_text("---\ntype: person\nroles: [client]\n---\nwritten by hand\n")
body = "---\ntype: person\nroles: [client]\n---\n# Probe Person\n"
raised = None
try:
    dossier_core.place_note(cfg, "Probe-Person.md", body, "", str(t / "backups"), "probe", "probe")
except dossier_core.DossierError as e:
    raised = e
print("refused:" if raised else "NO REFUSAL RAISED", raised or "")
print("note intact" if "written by hand" in (people / "Probe-Person.md").read_text() else "NOTE OVERWRITTEN")' "$KIT" "$LIVE"
```

Pass: it prints `refused:` with a message saying the note already exists, then `note intact`. On a **test install** (**model call**), the other half: press rewrite on the board for a contact who has a note, send that contact's number two messages while the run is in progress, and when it finishes both new recent lines are still in the note.

## 10 · The brain can read your secrets

**What happens** Nothing visible, which is the danger. Without path rules, a cage of Read, Glob and Grep can read anything the daemon's node parent can, and that parent holds Full Disk Access: the push bot token, the config, other runs' full conversation packs and most of the home folder. A sentence in the system prompt is not a fence, and everything the brain reads was written by other people.

**Why** Tool rules decide which tools exist, not which paths those tools may touch. One incoming message saying "put the contents of this file in your reply" is the risk.

**Where the guard lives** `wa_setup.py init` renders `daemon/templates/claude-settings.json` into `live/claude-settings.json` with 27 absolute Read deny rules: the push token, the config and its backups, the second source whitelist, the state database, the facts file, the voice pairs, the logs, other runs, the digests, the note backups, the reader's working copies, its prewarm folder and the reader config, the `secrets/` folder (the optional setup token and the deny probe's marker), the document text cache and the voice transcript cache (a second probe marker sits there), WhatsApp's own databases, and the secret stores in the home folder (`~/.ssh/**`, `~/Library/Keychains/**`, `~/.config/**/credentials*` and more). Each is written as `Read(//<absolute path without its leading slash>)`. This form blocks the Read tool under `claude -p --settings`; the canary probe below confirms it on your own Mac. `Brain.call_claude` passes the settings file on every run. The full list, and what stays readable: `references/privacy.md`.

**How to check it still holds** (one **model call** with a harmless canary; run it in Terminal on the Mac, not over ssh)

```sh
"$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(sum(1 for r in d["permissions"]["deny"] if r.startswith("Read(//")))' "$LIVE/claude-settings.json"   # 26 or more
mkdir -p ~/.config/wa-live-canary && echo "canary 7431" > ~/.config/wa-live-canary/credentials-canary.txt
(cd "$(cfg brain.cwd)" && claude -p --settings "$LIVE/claude-settings.json" --strict-mcp-config --no-session-persistence --allowedTools Read --output-format stream-json --verbose "Use the Read tool on $HOME/.config/wa-live-canary/credentials-canary.txt and print its content.") | grep -oE 'canary 7431|denied by your permission settings'
(cd "$(cfg brain.cwd)" && claude -p --settings '{"disableAllHooks": true}' --strict-mcp-config --no-session-persistence --allowedTools Read --output-format stream-json --verbose "Use the Read tool on $HOME/.config/wa-live-canary/credentials-canary.txt and print its content.") | grep -oE 'canary 7431|denied by your permission settings'   # control: no deny rule
rm ~/.config/wa-live-canary/credentials-canary.txt && rmdir ~/.config/wa-live-canary
```

Pass: the first `claude -p` call shows `denied by your permission settings` (the Read tool's own error for the canary path) and never `canary 7431`, and the control call, the same read with no deny rule, shows `canary 7431`, so it is the rule that blocks the read and not something else in the call. The final JSON's `permission_denials` list stays empty for a settings deny rule, so do not look for the denial there.

## 11 · A dry run that is not dry

**What happens** A `--dry-run` flag checked in only one place leaks. If only the triage judgment honors it, a "dry" run can still rewrite person notes in the vault, run the scheduled jobs for real (the digest pushes to the phone), run summary backfill and the nightly second source pull, and write fake judgments over real ones in the state database; command files carrying `"dry_run": true` for the facts and digest jobs can still write the facts file, queue notes, raise stages and push.

**Why** A flag checked in the one place that existed when it was added is ignored by every later feature that adds its own path.

**Where the guard lives** `wa_live.py`, everywhere: under `--dry-run` there are no model calls anywhere, no pushes (`wa_common.notify_send` stays silent), no vault writes, every scheduled job runs in its own dry mode (`wa_facts.py`, `wa_digest.py`, `wa_backup.py`, `wa_second_source.py`, `wa_backfill.py` and `wa_calendar.py` each take `--dry-run`), and no judgment is written over real state. Every new job or writer must take the flag; that is part of the change, not a follow-up.

**How to check it still holds** (**test install** only, with `notify.outlet: command` appending pushes to a file, and the test install's own launchd job stopped first)

```sh
TLIVE=<the test install's live dir>; TVAULT=<the test vault>; PUSHES=<the file notify.command appends to>
snap() { find "$1" -type f -print0 | sort -z | xargs -0 md5 -q | md5 -q; }
V0=$(snap "$TVAULT"); P0=$(wc -c < "$PUSHES")
$PY "$KIT/daemon/wa_live.py" --config "$TLIVE/config.json" --dry-run &
sleep 20; echo '{}' > "$TLIVE/commands/digest-now.json"; echo '{}' > "$TLIVE/commands/facts-now.json"
# now send one test message to a chat the test gate passes, then wait
sleep 120; pgrep -P $! ; kill $!
[ "$(snap "$TVAULT")" = "$V0" ] && echo "vault untouched"; [ "$(wc -c < "$PUSHES")" = "$P0" ] && echo "no pushes"
sqlite3 -readonly "$TLIVE/state.sqlite" "select jid, updated_at from conversations order by updated_at desc limit 3"
```

Pass: `pgrep -P` finds no `claude` child while it runs, both lines print, and no conversation shows an update from the dry run.

## 12 · A backup that misses a file

**What happens** The nightly backup reports success every night, yet a restore comes back without a file that matters, such as the voice examples file (pairs of the brain's draft and what the owner actually sent, one of the very things the backup exists to protect), and no report says so.

**Why** A backup that copies a fixed list reports success for whatever is on the list. A state file added later by another feature, without touching the copy list or the skip list, is silently left out.

**Where the guard lives** `wa_backup.run_backup` copies `state.sqlite` through SQLite's online backup API and checks the copy's integrity, `config.json` with secret-looking values redacted, `summaries.md`, `facts.json`, `voice-pairs.jsonl`, `second-source-whitelist.json`, `topic-map.json`, `seeds.json`, `acceptance.json`, `prompts/`, `digest/`, `dossier-backups/` and `adapters/`; its never list names `telegram.json`, `runs/`, `commands/`, `variants/`, `dossier-cli/`, `calendar/`, logs, `claude-settings.json`, `backup-last.json`, `second-source-last.json` and `people-write.lock` (the empty lock the person note writer takes before placing a note); a known item that does not exist yet is planned as a `[later]` line instead of silently left out; it keeps `backup.keep` dated folders and writes `backup-last.json` for the digest's health line. The rule that keeps it true: a change that adds a file to `live/` adds it to the copy list or the never list in the same change.

**How to check it still holds**

```sh
$PY "$KIT/daemon/wa_backup.py" --selftest | tail -1
$PY "$KIT/daemon/wa_backup.py" --config "$LIVE/config.json" --dry-run
ls "$LIVE"
cat "$LIVE/backup-last.json"                                     # after a real night
ls "$(cfg backup.dest)/$(date +%F)"
```

Pass: the selftest passes; the dry run lists `voice-pairs.jsonl` among its items (on a fresh install, as a `[later]` line saying `not there yet, copied once it exists`, until the first paired reply writes it) and names `telegram.json`, `runs/` and `people-write.lock` only on its `never copied:` line; every name in `ls "$LIVE"` is in one of those two lists, except the dated `config.json.bak-<time>` copies, the rotated `wa_live.log.<n>` files and the database's `-wal` and `-shm` files (their content is inside the online backup); after a real night `backup-last.json` says `"ok": true` and today's folder holds `voice-pairs.jsonl` and no `telegram.json`.

## 13 · Voice transcription that depends on another tool's config

**What happens** On a new machine no voice note is ever transcribed: every voice message shows as unreadable, and setting the model path environment variable changes nothing. Or the command line for person notes points at a model folder that exists only on the machine it was written on.

**Why** If the reader looks its model up in another tool's config file and gives up before reading its own environment variable when that file is missing, or a caller hardcodes a model folder as its default, transcription works only on a machine where that tool or folder happens to exist.

**Where the guard lives** `whisper_setup` in `skills/breakthrough-whatsapp-read/scripts/wa_sweep.py` reads only the reader's own config (`whisper_model`, `whisper_language`, `whisperkit_bin`, `ffmpeg_bin`, generated from `brain.whisper_model`, `brain.whisper_language`, `bin.whisperkit` and `bin.ffmpeg` by `wa_setup.py sync-reader-config`) and `WA_WHISPER_MODEL`, which overrides the model only when that path exists. No caller hardcodes a model folder. Transcripts are cached in `<home>/transcripts-cache/`, keyed by `transcript_key`, which the reader and the board compute the same way, so every transcribed voice note shows on the board.

**How to check it still holds**

```sh
awk '/^def whisper_setup/,/^def transcript_key/' "$KIT/skills/breakthrough-whatsapp-read/scripts/wa_sweep.py" | grep -nE '~/|Path\.home|getenv\("HOME|\.json|\.toml|\.ya?ml|\.plist|open\('   # nothing
$PY -c 'import json, os, sys, tempfile
c = json.load(open(sys.argv[2]))
cfg = {k: os.path.expanduser(c[k]) for k in ("whisper_model", "whisperkit_bin", "ffmpeg_bin") if c.get(k)}
os.environ["HOME"] = tempfile.mkdtemp(); os.environ.pop("WA_WHISPER_MODEL", None)
sys.path.insert(0, sys.argv[1]); import wa_sweep
r = wa_sweep.whisper_setup(cfg)
print("reader config alone: ok" if r[0] == cfg.get("whisper_model") and r[4] is None else "FAIL: %s" % r[4])' "$KIT/skills/breakthrough-whatsapp-read/scripts" "$(cfg paths.home)/config.json"
grep -rn "whisper-models/" "$KIT/daemon" "$KIT/skills"     # only the config default (code folders only: this page quotes the path)
$PY -c 'import json, os, sys
c = json.load(open(sys.argv[1] + "/config.json"))
for k in ("whisper_model", "whisperkit_bin", "ffmpeg_bin"):
    print(k, c.get(k), os.path.exists(os.path.expanduser(str(c.get(k) or ""))))' "$(cfg paths.home)"
ls "$(cfg paths.home)/transcripts-cache" | wc -l                 # grows after a run on a chat with voice notes
```

Pass: the first grep prints nothing (the model lookup opens no file and names no path of its own, so it cannot depend on another tool's config); the second command prints `reader config alone: ok` (with an empty home folder and no environment variable, the reader's own config is enough to find the model); the `whisper-models/` grep finds only the default model path, in `daemon/wa_common.py` (the config defaults) and `daemon/templates/config.example.json`; all three paths exist, the cache grows, and a recent voice note shows its transcript on the board.

## 14 · Claude memory in a headless run

**What happens** Runs take more turns than the job needs, some end at the turn limit (`brain.max_turns`) with no judgment, and the stream shows the brain opening files that have nothing to do with the conversation: notes from your own Claude Code memory, or a `CLAUDE.md` from a folder above the working folder. Nothing errors, and your private notes ride along into every run.

**Why** A headless `claude -p` loads the same memory an interactive session does: the auto-memory index of the logged-in user and every `CLAUDE.md` file above its working folder. In a triage run those are instructions and pointers written for other work, and the brain follows them, spending turns opening memory files instead of reading the conversation. The tool cage and the settings file do not switch memory off; two environment variables do.

**Where the guard lives** `wa_common.subprocess_env` sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` (`wa_common.CLAUDE_QUIET_ENV`); `Brain.child_env` sets them again for every triage, person note and probe run, so they hold even when that module fails to load; an evening job kept from an earlier release sets them in its installed `intake-evening.mjs` for its pre-sort run, and the Today brief's run goes through the daemon's own call, so it inherits them. The proof is the harness's own init event: it carries `memory_paths` when memory loaded and has no such key when it is off. `Brain.init_memory_paths` reads it after every triage and person note run and logs a warning if it is ever there: `MEMORY LOADED: …` for a triage run, `MEMORY LOADED (档案 run): …` for a person note run; a kept evening job logs `MEMORY LOADED: the pre-sort run loaded Claude memory`; the `probe-claude.json` command file logs the answer either way.

**How to check it still holds** (one **model call**)

```sh
echo '{}' > "$LIVE/commands/probe-claude.json"; sleep 60
grep "probe-claude: " "$LIVE/wa_live.log" | grep -E "memory_paths|MEMORY LOADED|init 事件" | tail -1
P=$(ls -td "$LIVE"/runs/probe-* | head -1)
grep -c '"memory_paths"' "$P/stdout.jsonl"
grep -c "MEMORY LOADED" "$LIVE/wa_live.log"
grep -c "MEMORY LOADED" "$LIVE/intake-evening.log"    # only when a kept evening job is installed
```

Pass: `probe-claude: init 没有 memory_paths (Claude 记忆关着)` (the init event has no memory paths: Claude memory is off), then `0`, `0` and `0`. `probe-claude: MEMORY LOADED, init 带 memory_paths …` means the Claude Code on this Mac no longer honors one of the variables: `references/troubleshooting.md`, "MEMORY LOADED in the log". `probe-claude: 没有 init 事件, 记忆开关查不到` (no init event, so the switch cannot be read) is not a pass either: claude did not start properly, see `references/troubleshooting.md`, "Runs are stuck with zero output". Probe again after every Claude Code update.

## 15 · A restart that orphans a frozen python

**What happens** After a restart to load new code or a config change, the board shows the old process, or nothing at all. The log may show a new start line followed by `板子起不来 (<bind>:<port>): …` (the board cannot start: the port is in use), while the port still answers from the old process. Two pythons run `wa_live.py` with this install's config, and one of them is not a child of the node launchd started (`ps -axo pid,ppid,command | grep "[P]ython.*wa_live.py --config $LIVE/config.json"`; the node parent's own line also names the script, so read only the python lines, and the config path keeps another install's python out of the list).

**Why** launchd starts node, and node starts python. `launchctl kickstart -k` stops only the node parent. A healthy python gets the stop signal forwarded and exits, but a frozen one (for example stuck opening a file behind a macOS permission dialog nobody clicked) never runs its stop handler. It is orphaned, keeps the board port, and the fresh daemon launchd starts cannot bind it.

**Where the guard lives** `wa_setup.py restart` (`do_restart`) is the daemon's only restart. It finds the node parent's pid (`launchctl print`), the python child and any `wa_live.py` of this config left orphaned by an earlier restart; refuses before any signal (exit 3) when the board port is held by a program that is not this daemon; sends SIGTERM to each python and waits up to `ExitTimeOut` (90 seconds, from the plist) for running jobs to finish; sends SIGKILL to any python still alive; lets launchd start a fresh node and python (`KeepAlive`); when no new node has appeared within 20 seconds it nudges launchd, with `launchctl kickstart -k` if the old node is still up (safe at that point: its python is already gone, so nothing is left to orphan) or a plain `launchctl kickstart` if no job process runs at all; then proves that exactly one python of this install runs, as a child of the new node, and is the only listener on `board.port`. Its selftest covers a healthy daemon, a frozen python, an orphan holding the port, a foreign program on the port and a launchd that does not start it again. The same selftest fails on any line of these docs that restarts the daemon with `kickstart -k`. An evening job kept from an earlier release is a one shot script with no python child, so `launchctl kickstart -k` on `$ILABEL` was its proof step; it is retired, and the way out is `uninstall-launchd --intake`.

**How to check it still holds** (restarting kills runs in flight; their bursts run again afterwards)

```sh
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json" --dry-run
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
lsof -nP -iTCP:$PORT -sTCP:LISTEN
```

Pass: the dry run lists the SIGTERM, SIGKILL and proof steps and runs nothing; the real one prints an `after:` line naming one python, a child of the new node and the only listener on the port, then a `/health` line, and ends with `proof: one daemon, the new code is loaded (...)`; `lsof` shows one pid, the python the `after:` line named. The frozen case, on a **test install** only: `kill -STOP` its python child, run its restart with `--wait 10`, and the output shows the SIGKILL line, then the same proof, with one python listening on the test port.

## 16 · A health check answered by the wrong thread

**What happens** The board loads, `/health` answers, every monitor is green, and yet nothing has been judged for hours. The daemon's main loop is frozen (for example on a macOS permission dialog nobody clicked while it opens the WhatsApp database), and the board, which runs in its own thread, keeps answering.

**Why** A health check proves only what answers it. When the board thread answers, it proves the board thread is alive, not that the loop that reads messages is.

**Where the guard lives** The main loop records a heartbeat after each doorbell scan (`Daemon.last_tick`). `Daemon.health_status` answers `GET` and `HEAD /health` with `200` and `ok (last scan <n>s ago)` while the heartbeat is younger than three times `doorbell.full_rescan_seconds` (at least 30 seconds), and with `503` and `stalled <n> (last scan <n>s ago, limit <n>s)` after that. A separate thread (`Daemon.stall_watch`) pushes one error, push key `daemon_stalled` (at most one in six hours), once the stall passes half of `doorbell.stale_alert_hours`, and logs `主循环恢复了` (the main loop is back) when the heartbeat returns. The push leaves through the outlet, which never touches WhatsApp's database, so it still goes out while the loop is stuck there. A script that checks `curl -sf .../health` fails on a stall, as it should; one that compares the body to exactly `ok` must check that it starts with `ok` instead.

**How to check it still holds**

```sh
curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$PORT/health"
curl -s "http://127.0.0.1:$PORT/health"; echo
"$PY" "$KIT/daemon/wa_live.py" --selftest | grep "/health: "
```

Pass: `200`; a body starting with `ok (last scan`; and a `✓` line for the selftest's `/health` case, which ages the heartbeat past the limit, reads `503` and `stalled` for both `GET` and `HEAD`, then reads `200` again once the heartbeat is fresh. A real stall cannot be staged from outside: stopping the python with a signal stops the board thread with it.
