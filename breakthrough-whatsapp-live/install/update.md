# Update

`npx skills update` replaces the payload whole: code, templates, seeds, this wizard. It never touches what the owner owns, and that is exactly why an update has steps of its own. The running daemon still has the old code in memory. The LaunchAgent may name files whose rendered copies are now stale. And every owner copy (the prompts and the topic map in `live/`, the playbooks in the vault) stays at the seed version it was copied from until the new seed text is merged into it, section by section, on the owner's yes.

⛔ Never overwrite an owner copy. ⛔ Never touch a playbook door's "What we have learned" or "Recent runs". ⛔ Only one session touches the daemon at a time: a restart kills the runs in flight.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="$LABEL.intake"
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

It reaches the network, so the owner runs it or says yes to it. `-g -y` updates the install the README's `npx skills add -g` made without stopping at a scope question (a session's shell cannot answer one); a kit installed into one project instead is updated from inside that project with `-p -y`. When it answers `No installed skills found matching`, the kit was installed from a local folder, which `update` does not follow: run the same `npx skills add` again with that folder, then carry on here. Writes: the payload folder, replaced. The two companion links point into the payload and follow it; a companion installed as a copy instead stays at its old version until the owner agrees to refresh it. Your own adapters in `<live>/adapters/` are outside the payload and stay; run each one's `--selftest` once (`"$PY" "$LIVE/adapters/<file>" --selftest; echo "exit $?"`), since the kit code around it changed. Pass: `exit 0`; an adapter's selftest may print nothing else.

When `ls -L` answers `No such file or directory` for a companion, it was never linked: station C1 step 1 links the reader and station C4 the person notes; on an install that skipped those stations there is nothing to update for them.

⭐ The update may have changed this page. Before step 3, read `install/update.md` again from the updated payload and carry on from step 3 of that copy: an older copy can name a step this release replaced (for example a restart).

## 3 · What changed in the seeds

```sh
"$PY" "$KIT/daemon/wa_setup.py" seed-status --config "$LIVE/config.json"
```

Read only. One line per owner copy, with its status and `copy v<x>, payload v<y>`; the last line counts the copies that need a merge. An update that changed playbook seeds shows each of them as `seed newer`; the playbook seeds this release changed are listed in `playbooks/CHANGELOG.md`. Playbooks appear here only when they were copied into the vault (station C1, step 5); when none were, the changelog is where to read what changed in case the owner wants a copy now (step 5).

This release (1.2.0), coming from 1.1.0: `prompts/brain-prompt.md` moved (1.1.0 to 1.2.0: the rule for a message the person edited, under input 1; the two quote rules under `captures`, with the one exception to "fewer is better"; the `stake` ending now 「怎么开口: …」), and `claude-settings.json` shows `template newer`, because the template gained one Read deny rule (`transcripts-cache/**`, step 6). One playbook seed changed, by one sentence: `resident-workers-playbook.md` 1.0.3 (the note on probing a guard with JSON is stated as a general rule; nothing in the method changed); it shows as `seed newer` only when the owner copied the resident-workers folder into the vault. Expect `seed newer` on the brain prompt and `template newer` on the settings, so `2 need a merge` (3 with the resident-workers copy). From this release on, a copy the owner merged by taking every section of the new seed as it was (byte for byte the payload seed) reads `unchanged` afterwards, not `edited by owner`.

For the brain prompt, step 4 walks three sections, each with the owner's yes: (a) the new sentences under input 1 of 你手上有什么 (a burst line ending ✏️ is an edited message, the current version wins, `reason` opens with 「对方改了讯息: …」); (b) under `captures`, the `quote` rule's "same message" words, the new ⛔ line saying a quote is copied from one message as one unbroken span because the program checks it against the pack, and the sentence after "已经交付 / 已经过期的不写" with the one exception for an edited message; (c) the `stake` ending in the first draft rule. A copy without (a) and (b) still runs, but edited messages are judged without their rule and quotes the brain joins or shortens land as ≈ entries; without (c) the board shows the old ending. The maintainer notes at the top of the seed (items 6 and 7) never reach the model and merge like any other section.

| Status | What to do |
|---|---|
| `unchanged` | nothing |
| `edited by owner` | nothing: those edits are the owner's |
| `seed newer` (alone or with `edited by owner`) | merge it, step 4 |
| `owner copy missing (...)` | the code is running on the payload seed and saying so in the log and in the digest's health block; offer to copy it again, step 5 |
| `stamp removed from the copy` | ask the owner which version the copy came from before merging anything |
| `not recorded, cannot tell` | compare the copy with the seed by hand before merging |
| `template newer (...)` | `claude-settings.json`: step 6 |
| `rendered file missing (...)` | `"$PY" "$KIT/daemon/wa_setup.py" init --config "$LIVE/config.json"` renders it again |

## 4 · Merge a newer seed, section by section

One owner copy at a time:

1. **Read what changed.** The changelog entries for that file between the copy's version and the payload's: `playbooks/CHANGELOG.md` for the three playbook folders, and `daemon/templates/CHANGELOG.md` for the prompts and the topic map when the payload carries one. When there is no entry, the new seed itself is the only record.
2. **Diff the copy against the new seed.**
   ```sh
   diff -u "<the owner copy>" "$KIT/<the seed path from seed-status>"
   ```
   When `seed-status` did not say `edited by owner`, every difference is the seed's. When it did, the changelog entries tell the seed's changes apart from the owner's own.
3. **One section at a time, with the owner.** Show what the seed changed, the changelog line that says why, and what their copy says now. Apply that section with the Edit tool only on their yes; a no keeps their text.
4. **Frontmatter stays the owner's.** Keep every frontmatter value of the owner copy (`confirmed_by_owner`, `updated`, and anything else the owner set). The seed's frontmatter shows defaults such as `confirmed_by_owner: false`; those never overwrite the copy's values. Only a key the seed adds is offered, like any other section.
5. **Doors.** "What we have learned" and "Recent runs" are the owner's history and are never touched; the rest of a door merges like any other section.
6. **Stamp it.** When every section of that version is settled, merged or declined, change the version in the copy's seed stamp comment to the payload's. The stamp records which seed text the owner has ruled on, not which text they took, so `seed-status` stops asking.
7. **Log it.** One filing-log line per merged vault file, and one line under `## Updates`.

## 5 · A missing owner copy

- Prompts and the topic map: `"$PY" "$KIT/daemon/wa_setup.py" init --config "$LIVE/config.json"` copies what is missing and keeps everything that exists.
- A playbook file, on the owner's yes (which `--confirmed-by-owner` records in the copy's frontmatter, as at C1 step 5):
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

`init` renders the file again and records its new hash; every other file is kept, and any folder of the working tree a release added is created (0700), and any deny probe marker a release added is written (this release, 1.2.0: `<home>/transcripts-cache/deny-canary.txt`, 0600, in a folder every install already has). Lines only in the old file are rules the owner added: carry each one over with the Edit tool on their yes, then `chmod 600` the file. Lines only in the new file are the release's new rules (this release: `transcripts-cache/**`, and the `_comment` line, which now names the transcript cache).

When `seed-status` showed no `template newer`, still run `"$PY" "$KIT/daemon/wa_setup.py" init --config "$LIVE/config.json"` once: it creates a folder or a probe marker a release added and keeps everything that exists.

## 6b · The command-base patches changed

A release can change the outline of a command-base patch (`references/vault-proposals.md`, proposal d). The owner's command-base skill is their file, written in their words, so an update never touches it: the session shows what changed and applies it only on the owner's yes.

This release (1.2.0) changed Patch 2, the outline of `<command base>/references/intake.md`: step 2 now reads each entry's quote mark (🗣, 🎙 and 📄 are the person's words; ≈ is a gist, checked against the chat before a date or an amount is used) and treats a ✏️ entry as a correction that replaces the older one (place the ✏️ entry, merge or drop the older); step 5 marks placed by key (`wa_intake.py --config <live>/config.json --mark-placed-key KEY --to "<where it went>"`, KEY a full key or a unique prefix of at least 8 hex from `--list-open`), because numbers shift after every placement and when a correction lands while the owner decides, and keys do not. When O7 never ran, there is no such file: skip this step.

1. Read the owner's `<command base>/references/intake.md` whole, and the new Patch 2 outline in `references/vault-proposals.md`.
2. Show the owner the difference, step by step of the outline: what their file says now, what the new outline says, and why (the changelog line above). Their own wording stays theirs; only the meaning of steps 2 and 5 is offered.
3. Apply it with the Edit tool only on a yes, one step at a time, with this install's absolute paths. A no keeps their file as it is: `--mark-placed <n>` still works, and the marks still show in the buffer.
4. Log it under `## Updates`: offered, and applied or declined, per step.

Also offer the legend line for the buffer's rules at the top (`references/vault-proposals.md`, proposal c, "The legend line"), on a yes, with the Edit tool; nothing reads it but the owner.

## 7 · Config

```sh
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" sync-reader-config --config "$LIVE/config.json"
```

New keys fill themselves with their defaults when the config loads; `references/config-reference.md` lists each key with its station, and the wizard reads any new one to the owner. `check-config` must show no new errors. This release (1.2.0) adds three, all working at their defaults with nothing to write: `edit_watch.enabled` (true: a message that passed the gate is read again while its window lasts, so an edit is judged again), `edit_watch.minutes` (30: that window, 1 to 1440) and `intake.verify_quotes` (true: every intake quote is looked up in the run's own pack and marked 🗣 when found, ≈ when not). Read default 15 (`references/defaults-and-why.md`) to the owner; a ruling lands as one decision note. None of them reaches the reader config (`sync-reader-config` may still report a per-run copy changed; that is the refresh, not a new key).

## 8 · The LaunchAgents

```sh
"$PY" "$KIT/daemon/wa_setup.py" render-launchd --config "$LIVE/config.json"
```

Read the `next:` line. `next: nothing to reinstall, the installed LaunchAgent already matches this plist` means the plist is unchanged: a script reported as `update` is used from the next start on, so step 9's restart is enough. `next: wa_setup.py install-launchd ...` means the plist changed: reinstall, which also restarts the daemon (so first wait for a backfill in flight, as step 9 says). After launchd lets go of the old job, it stops any python of this install still running (SIGTERM, then SIGKILL after ExitTimeOut if it is frozen), so no python is left holding the board port:

```sh
"$PY" "$KIT/daemon/wa_setup.py" install-launchd --config "$LIVE/config.json"
```

When the evening intake job is installed, run `render-launchd` again with `--intake`, and `install-launchd --intake` only when its `next:` line names it. A release that changes only a script (`run.mjs` or `intake-evening.mjs`) prints `update <path>` for it and still `next: nothing to reinstall, ...`: the next start, or the next evening run, uses the new script. This release (1.2.0) changed neither the scripts nor the plist templates, so both runs keep every file and print `next: nothing to reinstall, the installed LaunchAgent already matches this plist`, unless a tool path in the config moved since the last render.

## 9 · Restart, and the fingerprint

Skip the restart when step 8 reinstalled the daemon. A backfill in flight is cut short and never run again: wait for its `backfill 完成` line in `$LIVE/wa_live.log` first. A prewarm whose process the restart kills mid-run runs again from the start at the next start (`prewarm <n> 天上次没跑完 (多半被重启切断), 补跑`); one that already returned (finished, failed or timed out) is not run again. Waiting for its `prewarm 完成` or `prewarm 失败` line only saves repeating it.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
```

The daemon loads the new code. Runs in flight are killed and their bursts run again; a person note run that was cut off is marked failed and can be queued again. Pass: the last line is `proof: one daemon, the new code is loaded (...)` (station C2, step 11, reads the rest of the output).

Either way, refresh the fingerprint in the daemon's registry entry (C2 step 5). When the install has no registry entry (no machine registry note in the vault and no `<home>/INSTALLED.md`), say so, skip the refresh, record `no registry entry` under `## Updates`, and offer to write one now as station C2, step 5 does. The refresh: the new hash and version, with today's date and the Mac's name. A standby Mac compares against it on the day it takes over (the standby runbook's step 2), so a stale value sends it to the wrong version.

```sh
shasum -a 256 "$KIT/daemon/wa_live.py"
grep -m1 '^VERSION' "$KIT/daemon/wa_live.py"
```

**Something new to offer.** A release can bring an optional step an existing install never saw. After the restart, name it to the owner once, with its cost, and run it only on their yes; never assume it. This release (1.2.0) brings no optional step: the edit watch, the quote marks and the entry keys work once the new code runs, and the Patch 2 change was offered in step 6b. When the install never took the optional setup token of 1.1.0 (`install/C2-daemon.md`, step 12) and the owner was never asked, it can be offered now, once.

## 10 · Verify

1. **The new code passes its own tests on this Mac.** Pass: `--- selftest PASS (<n> pass, 0 fail) ---`, `selftest: <n>/<n> 过` (the daemon prints in Chinese: all passed), and `dossier_core selftest: <n>/<n> ok`.
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" --selftest | tail -1
   "$PY" "$KIT/daemon/wa_live.py" --selftest | tail -1
   "$PY" "$KIT/skills/breakthrough-person-dossier/scripts/dossier_core.py" --selftest | tail -1
   ```
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
8. **The new cage rules reached the rendered settings** (when step 6 ran). Pass: the template's count and the rendered count are equal, as in station C2's check 4 (`27 27` in this release, more when the owner carried rules of their own over).
   ```sh
   "$PY" -c 'import json,sys; n=lambda p, pre: sum(1 for r in json.load(open(p))["permissions"]["deny"] if r.startswith(pre)); print(n(sys.argv[1], "Read(/"), n(sys.argv[2], "Read(//"))' "$KIT/daemon/templates/claude-settings.json" "$LIVE/claude-settings.json"
   ```
9. **The transcript cache is denied, on this Mac, from launchd** (after the restart; one tiny model call per folder). Drop `echo '{"read_canary": true}' > "$LIVE/commands/probe-claude.json"`, wait a minute. Pass: `probe-claude read_canary [secrets]: 读取被挡下 (secrets 夹的 deny 规则有效; ...)` and `probe-claude read_canary [transcripts-cache]: 读取被挡下 (transcripts-cache 夹的 deny 规则有效; ...)` among the newest `read_canary` lines; `没挡住` on either fails it (feature checklist K351).
   ```sh
   grep -F "read_canary [" "$LIVE/wa_live.log" | grep -v " 开始 (" | tail -2
   ```
10. **The new config keys load.** Pass: `True 30 True`.
   ```sh
   "$PY" -c 'import sys; sys.path.insert(0, sys.argv[1] + "/daemon"); import wa_common as W; c = W.load_config(sys.argv[2]); print(c["edit_watch"]["enabled"], c["edit_watch"]["minutes"], c["intake"]["verify_quotes"])' "$KIT" "$LIVE/config.json"
   ```
11. **Quotes are marked** (when O7 ran). Run the hand-built replay in `references/feature-checklist.md` (K344 to K346; temp files under `$HOME_DIR/acceptance/replay/`, no model call). Pass: the lines that section names, and `first entry unchanged True`. After the first real run with a capture, the new entry's second line opens with 🗣, 🎙, 📄 or ≈.

## 11 · Record

Under `## Updates` in `<home>/INSTALL-PROGRESS.md`: the date, the version before and after, each seed merged or declined section by section, each command-base patch change offered and applied or declined (step 6b), whether the LaunchAgent was reinstalled or restarted, the new fingerprint written into the registry, each Verify check with the value it printed, and the owner's answer to anything new that was offered.
