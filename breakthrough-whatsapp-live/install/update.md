# Update

`npx skills update` replaces the payload whole: code, templates, seeds, this wizard. It never touches what the owner owns, and that is exactly why an update has steps of its own. The running daemon still has the old code in memory. The LaunchAgent may name files whose rendered copies are now stale. And every owner copy (the prompts and the topic map in `live/`, the playbooks in the vault) stays at the seed version it was copied from until the new seed text is merged into it, section by section, on the owner's yes.

⛔ Never overwrite an owner copy. ⛔ Never touch a playbook door's "What we have learned" or "Recent runs". ⛔ Only one session touches the daemon at a time: a restart kills the runs in flight.

**When the owner does not answer.** Every step below that asks the owner something has a safe default for silence: an owner who does not answer has said no. Nothing is changed for them, the question stays open, and the session writes one line under `## Updates` in `<home>/INSTALL-PROGRESS.md` saying what was asked and that it was not answered, so the next session (or the owner) can ask again. A step's own "No answer" line names its default; the one exception is step 6, where the default keeps what the owner already had.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="<launchd.intake_label from the progress file header>"
PORT="<board.port from the progress file header>"
SKILLS="$(dirname "$KIT")"                           # the skills folder the kit sits in
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs, filled from the header of the install being updated (`install/00-start-here.md`, "Shell conventions"). On a Mac with two installs, each is updated with its own values.

## 1 · Before

```sh
grep -m1 '^VERSION' "$KIT/daemon/wa_live.py"
"$PY" "$KIT/daemon/wa_setup.py" seed-status --config "$LIVE/config.json" --json > "$HOME_DIR/seed-status-before.json"
chmod 600 "$HOME_DIR/seed-status-before.json"
```

Writes `<home>/seed-status-before.json`. Log the current version under `## Updates` in the progress file.

## 2 · Update the payload

```sh
npx skills update breakthrough-whatsapp-live -g -y
grep -m1 '^VERSION' "$KIT/daemon/wa_live.py"
ls -L "$SKILLS/breakthrough-whatsapp-read/" "$SKILLS/breakthrough-person-dossier/"
```

It reaches the network, so the owner runs it or says yes to it. No answer: do not update; stop here and record `update not run: owner did not answer`. `-g -y` updates the install the README's `npx skills add -g` made without stopping at a scope question (a session's shell cannot answer one); a kit installed into one project instead is updated from inside that project with `-p -y`. When it answers `No installed skills found matching`, the kit was installed from a local folder, which `update` does not follow: run the same `npx skills add` again with that folder, then carry on here. Writes: the payload folder, replaced. The two companion links point into the payload and follow it; a companion installed as a copy instead stays at its old version until the owner agrees to refresh it. Your own adapters in `<live>/adapters/` are outside the payload and stay; run each one's `--selftest` once (`"$PY" "$LIVE/adapters/<file>" --selftest; echo "exit $?"`), since the kit code around it changed. Pass: `exit 0`; an adapter's selftest may print nothing else.

When `ls -L` answers `No such file or directory` for a companion, it was never linked: station C1 step 1 links the reader and station C4 the person notes; on an install that skipped those stations there is nothing to update for them.

⭐ The update may have changed this page. Before step 3, read `install/update.md` again from the updated payload and carry on from step 3 of that copy: an older copy can name a step this release replaced (for example a restart).

## 3 · What changed in the seeds

```sh
"$PY" "$KIT/daemon/wa_setup.py" seed-status --config "$LIVE/config.json"
```

Read only. One line per owner copy, with its status and `copy v<x>, payload v<y>`; the last line counts the copies that need a merge. An update that changed playbook seeds shows each of them as `seed newer`; the playbook seeds this release changed are listed in `playbooks/CHANGELOG.md`. Playbooks appear here only when they were copied into the vault (station C1, step 5); when none were, the changelog is where to read what changed in case the owner wants a copy now (step 5).

This release (1.4.0), coming from 1.3.0: no prompt or topic map changed, so `prompts/brain-prompt.md` and `topic-map.json` read as they did. The settings template changed: one new Read deny rule, for `<home>/vault-write-backups/**`, the folder where the Today page's optional write button keeps its backups, so `claude-settings.json` reads `template newer` (step 6). Two playbook seeds changed, both in the whatsapp-live-triage folder (`playbooks/CHANGELOG.md` has each entry): `whatsapp-live-triage-playbook.md` 1.3.0 (new move 12: after an OK on the board, write from the board when the write button is there, else place in your session as before; a board write can be undone) and `_WhatsApp-Live-Triage-Guide.md` 1.2.0 (the summary line says the brief writes only through the optional write button). Expect `3 need a merge` when the owner copied that folder into the vault (the two seeds and the settings template), else `1 need a merge`. For the playbook, step 4 walks one section, move 12, whole; for the door, only the summary line, and its "What we have learned" and "Recent runs" are never touched.

Coming from 1.2.0 or older, the 1.3.0 changes come too, and the playbook and door entries above then span both versions. What 1.3.0 changed, coming from 1.2.0: no prompt, topic map or settings template changed. Three playbook seeds changed, all in the whatsapp-live-triage folder (`playbooks/CHANGELOG.md` has each entry): `whatsapp-live-triage-playbook.md` 1.2.0 (new move 11: with the Today brief on, read the board's cards before placing, as a starting point and not an answer; move 10 now names two registry entries, the evening job being retired), `_WhatsApp-Live-Triage-Guide.md` 1.1.0 (the summary line names the Today brief), and `standby-machine-runbook.md` 1.1.0 (the evening job is retired, so a takeover has nothing to decide or stop about it; the Today brief comes back with the restored config). They show as `seed newer` only when the owner copied that folder into the vault.

For the 1.3.0 playbook changes, step 4 walks two sections, each with the owner's yes: (a) move 11, whole; (b) the change to move 10's list. A copy without (a) still works: the intake mode reads whatever its own `references/intake.md` says (step 6b), and the playbook is where the reason lives. For the door, only the summary line changes, and its "What we have learned" and "Recent runs" are never touched. For the runbook, three places: the paragraph at the end of step 2, the line after step 3's start commands, and the last sentence of step 4's stop note, plus one clause in the closing registry line.

| Status | What to do |
|---|---|
| `unchanged` | nothing |
| `edited by owner` | nothing: those edits are the owner's |
| `seed newer` (alone or with `edited by owner`) | merge it, step 4 |
| `owner copy missing (...)` | the code is running on the payload seed and saying so in the log and in the digest's health block; offer to copy it again, step 5 |
| `stamp removed from the copy` | ask the owner which version the copy came from before merging anything; no answer: merge nothing into that copy and record it |
| `not recorded, cannot tell` | compare the copy with the seed by hand before merging |
| `template newer (...)` | `claude-settings.json`: step 6 |
| `rendered file missing (...)` | `"$PY" "$KIT/daemon/wa_setup.py" init --config "$LIVE/config.json"` renders it again |
| a playbook folder in the vault that `seed-status` does not list | it was copied by hand, not with `copy-seed`, so it is not in `live/seeds.json` and `seed-status` cannot see it. Compare the seed stamp comment in each of its files with the payload's by hand, then merge as step 4 says; or record it first with `copy-seed` into an empty folder (on the owner's yes) and merge from there. No answer: leave it, and record that the folder is not tracked |

## 4 · Merge a newer seed, section by section

One owner copy at a time:

1. **Read what changed.** The changelog entries for that file between the copy's version and the payload's: `playbooks/CHANGELOG.md` for the three playbook folders, and `daemon/templates/CHANGELOG.md` for the prompts and the topic map when the payload carries one. When there is no entry, the new seed itself is the only record.
2. **Diff the copy against the new seed.**
   ```sh
   diff -u "<the owner copy>" "$KIT/<the seed path from seed-status>"
   ```
   When `seed-status` did not say `edited by owner`, every difference is the seed's. When it did, the changelog entries tell the seed's changes apart from the owner's own.
3. **One section at a time, with the owner.** Show what the seed changed, the changelog line that says why, and what their copy says now. Apply that section with the Edit tool only on their yes; a no keeps their text. No answer: keep their text, and leave that section unsettled (step 6 then leaves the stamp where it is, so `seed-status` asks again next time); record it.
4. **Frontmatter stays the owner's.** Keep every frontmatter value of the owner copy (`confirmed_by_owner`, `updated`, and anything else the owner set). The seed's frontmatter shows defaults such as `confirmed_by_owner: false`; those never overwrite the copy's values. Only a key the seed adds is offered, like any other section.
5. **Doors.** "What we have learned" and "Recent runs" are the owner's history and are never touched; the rest of a door merges like any other section.
6. **Stamp it.** When every section of that version is settled, merged or declined, change the version in the copy's seed stamp comment to the payload's. The stamp records which seed text the owner has ruled on, not which text they took, so `seed-status` stops asking.
7. **Log it.** One filing-log line per merged vault file, and one line under `## Updates`.

## 5 · A missing owner copy

- Prompts and the topic map: `"$PY" "$KIT/daemon/wa_setup.py" init --config "$LIVE/config.json"` copies what is missing and keeps everything that exists.
- A playbook file, on the owner's yes (which `--confirmed-by-owner` records in the copy's frontmatter, as at C1 step 5); no answer: nothing is copied, the code keeps reading the payload seed and says so, and the offer is recorded:
  ```sh
  "$PY" "$KIT/daemon/wa_setup.py" copy-seed --config "$LIVE/config.json" --seed "playbooks/<folder>/<file>" --to "<its folder in the vault>" --confirmed-by-owner
  ```

## 6 · The settings template changed

`template newer` on `claude-settings.json` means the cage's rules changed: the new rules must reach the running daemon, and any rule the owner added must survive.

```sh
mv "$LIVE/claude-settings.json" "$LIVE/claude-settings.json.old-$(date +%Y%m%d)"
"$PY" "$KIT/daemon/wa_setup.py" init --config "$LIVE/config.json"
diff "$LIVE/claude-settings.json.old-$(date +%Y%m%d)" "$LIVE/claude-settings.json"
```

`init` renders the file again and records its new hash; every other file is kept, and any folder of the working tree a release added is created (0700), and any deny probe marker a release added is written (this release, 1.4.0, adds one, `<home>/vault-write-backups/deny-canary.txt`, so the deny probe now tries three folders; `init` now also creates `<live>/today/` and `<home>/vault-write-backups/`, 0700). Lines only in the old file are rules the owner added: carry each one over with the Edit tool on their yes, then `chmod 600` the file. No answer: carry them over anyway, unchanged, and record that you did: they are rules the owner already had in force, and dropping a deny rule they wrote would open a read they chose to close. A no removes them. Lines only in the new file are the release's new rules. This release (1.4.0) adds one rule, `vault-write-backups/**` (rendered as `Read(//<home without its leading slash>/vault-write-backups/**)`). The backups hold the owner's task files from before each board write, and a run needs nothing in them; the rule is there before the button is ever switched on, so turning it on later needs no change to the cage. Coming from 1.2.0 or older, it is the only new rule too, since 1.3.0 did not change the template.

When `seed-status` showed no `template newer`, still run `"$PY" "$KIT/daemon/wa_setup.py" init --config "$LIVE/config.json"` once: it creates a folder or a probe marker a release added and keeps everything that exists.

## 6a · The Today brief, offered

When the install already has the brief on (`today.enabled` true, from a 1.3.0 update or a fresh install), skip this step. Coming from 1.2.0 or older, the brief is new: 1.3.0 brought it as an optional feature. Offer it with station O7's question 5 (`install/O7-intake.md`), read as written there: what it does (per project of the owner's, what today's conversations mean, as cards answered OK or No on the board), what it costs (one extra model call, at most every 30 minutes, only when its inputs changed (today's judgments, the open intake entries, a source you took out of a project, or your replies in conversations its cards cover); it reads a few vault files), what it reads, and that it never writes into the vault and is never pushed. It needs `paths.vault`.

- **Yes:** with the config edit ritual (`install/00-start-here.md`), set `today.enabled` to `true` (and `today.extra_rules` when the owner gives a paragraph), then `check-config`. The restart in step 9 starts it; O7's Do step 4 has the dry run that shows which projects it found, and Verify 8 to 13 of that station are this update's checks too.
- **No:** leave `today.enabled` at `false`. The board shows no Today page, and nothing else changes.
- **No answer:** the same as no: leave it off, and record `Today brief not offered to completion: owner did not answer`.

Either way, record the answer in the default 12 decision note (one line) and under `## Updates`.

## 6b · The command-base patches changed

A release can change the outline of a command-base patch (`references/vault-proposals.md`, proposal d). This release (1.4.0) changes none: a card the board writes places its own entries by key, and the intake mode reads them in the placed zone like any other. Coming from 1.3.0, skip this step. Coming from 1.2.0 or older, the 1.3.0 change below still applies. The owner's command-base skill is their file, written in their words, so an update never touches it: the session shows what changed and applies it only on the owner's yes.

1.3.0 changed Patch 2, the outline of `<command base>/references/intake.md`, for the Today brief: step 2 lists with `--list-open --with-brief http://127.0.0.1:<board.port>/api/today` and reads the board's card under each entry as a starting point, not an answer; step 5 adds that a card whose entries are all placed retires by itself within a minute; the new step 5c handles the cards with no entry and settles each with `--settle-card <id> --rev <n> --to "<where>" --with-brief <url>` (a `rev` refusal means the card changed: list again); step 6 says "placed N, settled K, still open M". The sentence that pointed the evening sort at this file is gone, since the evening push is retired. When O7 never ran, there is no such file: skip this step. When the owner said no to the Today brief in step 6a, offer only the removal of the evening sentence: the new steps read cards that do not exist (the list still works, printing one line that the brief could not be read).

1. Read the owner's `<command base>/references/intake.md` whole, and the new Patch 2 outline in `references/vault-proposals.md`.
2. Show the owner the difference, step by step of the outline: what their file says now, what the new outline says, and why (the changelog line above). Their own wording stays theirs; only the meaning of steps 2, 5, 5c and 6 is offered.
3. Apply it with the Edit tool only on a yes, one step at a time, with this install's absolute paths. A no keeps their file as it is: the plain `--list-open` and `--mark-placed-key` still work, and cards whose entries get placed still retire by themselves. No answer: the same as a no; nothing is applied, and the diff offer is recorded as not answered.
4. Log it under `## Updates`: offered, and applied, declined or not answered, per step.

## 6c · The write button, offered

This release (1.4.0) brings one optional feature: the write button on the Today page, which puts a card the owner pressed OK on into its task file, by program and never by the model, with a backup and an undo. It is offered only when the Today brief is on (step 6a, or `today.enabled` already true); with the brief off, skip this step and record `write button not offered: Today brief off`.

Offer it with station O7's question 6 (`install/O7-intake.md`), read as written there: what it writes (an update's allowed frontmatter values and one dated section, a create's new task file, a drop's placing), what it never writes (a seed, a quote, a stretch of a cited message), what refuses it whole, what it costs (no model call; a backup folder the kit never cleans), and what it needs (the owner's my-second-brain schema reader and PyYAML for the system Python, installed in this session on their yes). Read default 16 (`references/defaults-and-why.md`) with it.

- **Yes:** O7's Do step 4b, whole (a card you pressed OK on before this update gets its pin at the first start of the new daemon, so it can be written too; a card that still shows no Write button after the restart was answered on a page from before the update: reload it): set `today.write_mode` (`direct` or `queue`), find the schema reader, run `vault-write-check`, and on `no_yaml` show the install line it prints and run it only on the owner's yes. The restart in step 9 starts it; Verify 13 below checks it.
- **No:** leave `today.write_mode` at `off`. The brief keeps proposing, and nothing else changes.
- **No answer:** the same as no: leave it off, and record `write button not offered to completion: owner did not answer`. A PyYAML install that got no answer is not run; the mode may stay set, and the Today page's header then says why the button is missing.

Either way, record the answer in a default 16 decision note (one line) and under `## Updates`.

## 7 · Config

```sh
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" sync-reader-config --config "$LIVE/config.json"
```

New keys fill themselves with their defaults when the config loads; `references/config-reference.md` lists each key with its station, and the wizard reads any new one to the owner. `check-config` must show no new errors. This release (1.4.0) adds two, both working at their defaults: `today.write_mode` (`off`, unless step 6c set it) and `paths.schema_reader` (empty: the two default places of `references/config-reference.md`). Coming from 1.2.0 or older, 1.3.0 added seven, all under `today` and all working at their defaults: `today.enabled` (false, unless step 6a set it), `today.model`, `today.every_minutes` (30), `today.manual_min_gap_seconds` (60), `today.max_reads` (6), `today.quote_min_chars` (6) and `today.extra_rules` (empty). Read default 12 and default 16 (`references/defaults-and-why.md`) to the owner when steps 6a and 6c did not already; a ruling lands as one decision note, and no answer leaves the defaults and no note. 1.3.0 retired two: `intake.evening_push` and `intake.evening_at`. Every config an earlier `init` wrote carries both, so `check-config` now prints a warning for each (`retired in v1.3.0: the board's Today brief replaces the evening push`, naming this page); they are warnings, never errors, and step 8b removes them. None of the new keys reaches the reader config (`sync-reader-config` may still report a per-run copy changed; that is the refresh, not a new key).

## 8 · The LaunchAgents

```sh
"$PY" "$KIT/daemon/wa_setup.py" render-launchd --config "$LIVE/config.json"
```

Read the `next:` line. `next: nothing to reinstall, the installed LaunchAgent already matches this plist` means the plist is unchanged: a script reported as `update` is used from the next start on, so step 9's restart is enough. `next: wa_setup.py install-launchd ...` means the plist changed: reinstall, which also restarts the daemon (so first wait for a backfill in flight, as step 9 says). After launchd lets go of the old job, it stops any python of this install still running (SIGTERM, then SIGKILL after ExitTimeOut if it is frozen), so no python is left holding the board port:

```sh
"$PY" "$KIT/daemon/wa_setup.py" install-launchd --config "$LIVE/config.json"
```

A release that changes only `run.mjs` prints `update <path>` for it and still `next: nothing to reinstall, ...`: the next start uses the new script. This release (1.4.0), like 1.3.0, changed neither `run.mjs` nor the daemon's plist template, so the run keeps every file and prints `next: nothing to reinstall, the installed LaunchAgent already matches this plist`, unless a tool path in the config moved since the last render. `render-launchd --intake` and `install-launchd --intake` now refuse: the evening job is retired (step 8b).

## 8b · The retired evening job

The evening push was retired in 1.3.0 (an install that already went through this step on a 1.3.0 update has nothing more to do here), and the board's Today brief (step 6a) replaces it: two proactive surfaces that may disagree are worse than one (`references/defaults-and-why.md`, default 12). The job's templates left the payload, so no install can render or install it again. An installed one keeps working from the code that was kept for it, until it is removed.

```sh
ILABEL="<launchd.intake_label from the progress file header>"
case "$ILABEL" in ""|*"<"*) echo "fill in ILABEL first (launchd.intake_label from the progress file header)" >&2; exit 1;; esac
ls ~/Library/LaunchAgents/$ILABEL.plist
"$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json" | grep -i evening
```

**When the plist exists** (`doctor` says `evening intake job <label>: still installed (retired; update.md removes it on your yes)`), tell the owner it is retired and replaced by the Today brief, show them both halves of the removal (unloading the job, and the config edit that takes `intake.evening_push` and `intake.evening_at` out of the `intake` block), and ask once. One yes covers both halves; a no leaves both.

- **Yes:** remove the job, then, on the same yes, the two retired keys:
  ```sh
  "$PY" "$KIT/daemon/wa_setup.py" uninstall-launchd --intake --config "$LIVE/config.json" --dry-run
  "$PY" "$KIT/daemon/wa_setup.py" uninstall-launchd --intake --config "$LIVE/config.json"
  ls ~/Library/LaunchAgents/$ILABEL.plist
  ```
  It boots the label out, removes the plist from `~/Library/LaunchAgents/` and the rendered plist and installed script under `<home>/launchd/`, and prints `removed: <paths>`; it works although the templates left the payload; `ls` then answers `No such file or directory`. Then apply the config edit already shown, with the config edit ritual, and run `check-config`, which no longer warns. (`intake.rules_file` stays unless the owner asks to drop it too.) In the registry, mark the evening job's entry retired, with today's date and the `ls` line as proof (`references/vault-proposals.md`, proposal f).
- **No:** leave both, the job and the keys. It keeps running every evening from the kept code path, `doctor` keeps its line, and `check-config` keeps warning about the two keys. Record `evening job kept: owner said no`; a later update asks again.
- **No answer:** leave both in place, exactly as for a no, and record `evening job left in place, owner did not answer` in `INSTALL-PROGRESS.md`.

**When there is no plist** (most installs: the evening push was off by default), there is nothing to remove. Offer only the config edit above for the two retired keys, on a yes; no answer leaves them (warnings only), recorded.


## 9 · Restart, and the fingerprint

Skip the restart when step 8 reinstalled the daemon. A backfill in flight is cut short and never run again: wait for its `backfill 完成` line in `$LIVE/wa_live.log` first. A prewarm whose process the restart kills mid-run runs again from the start at the next start (`prewarm <n> 天上次没跑完 (多半被重启切断), 补跑`); one that already returned (finished, failed or timed out) is not run again. Waiting for its `prewarm 完成` or `prewarm 失败` line only saves repeating it.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
```

The daemon loads the new code. Runs in flight are killed and their bursts run again; a person note run that was cut off is marked failed and can be queued again. Pass: the last line is `proof: one daemon, the new code is loaded (...)` (station C2, step 11, reads the rest of the output).

Either way, refresh the fingerprint in the daemon's registry entry (C2 step 5). When the install has no registry entry (no machine registry note in the vault and no `<home>/INSTALLED.md`), say so, skip the refresh, record `no registry entry` under `## Updates`, and offer to write one now as station C2, step 5 does; no answer: none is written, and that is recorded. The refresh: the new hash and version, with today's date and the Mac's name. A standby Mac compares against it on the day it takes over (the standby runbook's step 2), so a stale value sends it to the wrong version.

```sh
shasum -a 256 "$KIT/daemon/wa_live.py"
grep -m1 '^VERSION' "$KIT/daemon/wa_live.py"
```

**Something new to offer.** A release can bring an optional step an existing install never saw. After the restart, name it to the owner once, with its cost, and run it only on their yes; never assume it. This release (1.4.0) brings one, the write button, already offered in step 6c; coming from 1.2.0 or older, the Today brief too, offered in step 6a; when it was turned on, the first build waits for the first change after this restart, and `echo '{}' > "$LIVE/commands/today-now.json"` asks for one now (one model call). When the install never took the optional setup token of 1.1.0 (`install/C2-daemon.md`, step 12) and the owner was never asked, it can be offered now, once. No answer to any offer: leave it off and record it.

## 10 · Verify

1. **The new code passes its own tests on this Mac.** Pass: `--- selftest PASS (<n> pass, 0 fail) ---`, `selftest: <n>/<n> 过` (the daemon prints in Chinese: all passed), and `dossier_core selftest: <n>/<n> ok`.
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" --selftest | tail -1
   "$PY" "$KIT/daemon/wa_live.py" --selftest | tail -1
   "$PY" "$KIT/daemon/wa_today.py" --selftest | tail -1
   "$PY" "$KIT/daemon/wa_vault_write.py" --selftest | tail -1
   "$PY" "$KIT/skills/breakthrough-person-dossier/scripts/dossier_core.py" --selftest | tail -1
   ```
   `wa_vault_write.py` is new in this release; its last line reads `wa_vault_write selftest PASS (<n> checks)`. It runs in temporary folders and never touches the vault.
2. **The daemon runs the new version.** Pass: `state = running`; a `/health` body starting with `ok (last scan`; the newest start line in the log names the new version.
   ```sh
   launchctl print "gui/$(id -u)/$LABEL" | grep "state = "
   curl -sS "http://127.0.0.1:$PORT/health"; echo
   grep -E "wa_live [0-9]+[.][0-9]+[.][0-9]+ " "$LIVE/wa_live.log" | tail -1
   ```
3. **Claude still answers from launchd, with Claude memory off.** Drop `probe-claude.json` exactly as station C2, step 8, and read it as C2's check 9. Pass: `"is_error":false`, no `sample.txt`, and `probe-claude: init 没有 memory_paths (Claude 记忆关着)` as the newest memory line in `$LIVE/wa_live.log` (C2's grep also shows `MEMORY LOADED` and `没有 init 事件` lines; either one fails).
4. **Person notes read the owner's copies** (when C4 ran). Pass: `check-live: ok`, and the `playbook:` line does not say `PAYLOAD SEED`.
   ```sh
   "$PY" "$KIT/skills/breakthrough-person-dossier/scripts/dossier_core.py" --check-live --config "$LIVE/config.json"
   ```
5. **No seed waits for a merge.** Pass: `seed-status: 0 need a merge`.
6. **The command-base patches survived** (when C4 or O7 added them; a my-second-brain update can rewrite that skill). Pass: the rows added at C4 and O7 are still there.
   ```sh
   grep -nE "update-person|intake" "<the owner's command-base SKILL.md>"
   ```
7. **The registry records the running code** (after step 9). Pass: the hash printed equals the one now in the daemon's registry entry. With no registry entry, this check reads `no registry entry` (step 9) and is skipped, unless the owner had one written.
   ```sh
   shasum -a 256 "$KIT/daemon/wa_live.py"
   ```
8. **The new cage rules reached the rendered settings** (when step 6 ran). Pass: the template's count and the rendered count are equal, as in station C2's check 4 (`28 28` in this release, one more than 1.3.0 for the backups folder, more when the owner carried rules of their own over; the rendered file holds one line for `vault-write-backups/**`).
   ```sh
   "$PY" -c 'import json,sys; n=lambda p, pre: sum(1 for r in json.load(open(p))["permissions"]["deny"] if r.startswith(pre)); print(n(sys.argv[1], "Read(/"), n(sys.argv[2], "Read(//"))' "$KIT/daemon/templates/claude-settings.json" "$LIVE/claude-settings.json"
   ```
9. **The transcript cache is denied, on this Mac, from launchd** (after the restart; one tiny model call per folder). Drop `echo '{"read_canary": true}' > "$LIVE/commands/probe-claude.json"`, wait a minute. Pass: `probe-claude read_canary [secrets]: 读取被挡下 (secrets 夹的 deny 规则有效; ...)` `probe-claude read_canary [transcripts-cache]: 读取被挡下 (transcripts-cache 夹的 deny 规则有效; ...)` and `probe-claude read_canary [vault-write-backups]: 读取被挡下 (vault-write-backups 夹的 deny 规则有效; ...)` among the newest `read_canary` lines; `没挡住` on any fails it (feature checklist K351, K393).
   ```sh
   grep -F "read_canary [" "$LIVE/wa_live.log" | grep -v " 开始 (" | tail -3
   ```
10. **The new config keys load.** Pass for this release: `today.write_mode` as step 6c ruled (`off` when it was not offered or not answered) and `paths.schema_reader` as set (empty unless the owner's copy lives elsewhere): `"$PY" -c 'import sys; sys.path.insert(0, sys.argv[1] + "/daemon"); import wa_common as W; c = W.load_config(sys.argv[2]); print(c["today"]["write_mode"], repr(c["paths"]["schema_reader"]))' "$KIT" "$LIVE/config.json"`. Coming from 1.2.0 or older, also: `today.enabled` as step 6a ruled, then the owner's own `today.every_minutes`, `today.manual_min_gap_seconds` and `today.max_reads` (the defaults `30 60 6` unless the owner set others): compare with the `today` block of `$LIVE/config.json`, where a key that is absent means its default.
   ```sh
   "$PY" -c 'import sys; sys.path.insert(0, sys.argv[1] + "/daemon"); import wa_common as W; t = W.load_config(sys.argv[2])["today"]; print(t["enabled"], t["every_minutes"], t["manual_min_gap_seconds"], t["max_reads"])' "$KIT" "$LIVE/config.json"
   ```
11. **The evening job is as ruled in step 8b.** Pass: removed, `No such file or directory` and no evening line in `doctor`; kept (a no, or no answer), the plist still there and `doctor` saying it is still installed and retired. Either way `render-launchd --intake` refuses in one line and exits 3. (K278, K279)
   ```sh
   case "$ILABEL" in ""|*"<"*) echo "fill in ILABEL first (launchd.intake_label from the progress file header)" >&2; exit 1;; esac
   ls ~/Library/LaunchAgents/$ILABEL.plist
   "$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json" | grep -i -e evening -e "today:"
   "$PY" "$KIT/daemon/wa_setup.py" render-launchd --intake --config "$LIVE/config.json" --dry-run
   ```
12. **The brief works** (only when step 6a turned it on): station O7's Verify 8 to 13 (`install/O7-intake.md`), after the restart.
13. **The write button answers as ruled** (after the restart). Pass: `vault write: off` in `doctor` when step 6c ended in no or no answer; `available (direct)` or `available (queue)` when it ended in yes, with `vault-write-check` printing the same and `exit 0`; when the mode is set but something is missing, `unavailable: <code> <reason>` and `exit 2`, the reason the Today page's header shows (`references/troubleshooting.md`, "The write button is missing"). On a yes, station O7's Verify 14 to 20 follow. (K383)
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json" | grep -i "vault write"
   "$PY" "$KIT/daemon/wa_setup.py" vault-write-check --config "$LIVE/config.json"; echo "exit $?"
   ```

## 11 · Record

Under `## Updates` in `<home>/INSTALL-PROGRESS.md`: the date, the version before and after, each seed merged or declined section by section, the Today brief offered and the answer (step 6a), the write button offered and the answer, its mode, and whether PyYAML was installed on the owner's yes (step 6c), each command-base patch change offered and applied, declined or not answered (step 6b), the evening job removed, kept, or left for want of an answer (step 8b), whether the LaunchAgent was reinstalled or restarted, the new fingerprint written into the registry, each Verify check with the value it printed, and the owner's answer to anything new that was offered, including every question that got no answer.
