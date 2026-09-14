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

It reaches the network, so the owner runs it or says yes to it. `-g -y` updates the install the README's `npx skills add -g` made without stopping at a scope question (a session's shell cannot answer one); a kit installed into one project instead is updated from inside that project with `-p -y`. When it answers `No installed skills found matching`, the kit was installed from a local folder, which `update` does not follow: run the same `npx skills add` again with that folder, then carry on here. Writes: the payload folder, replaced. The two companion links point into the payload and follow it; a companion installed as a copy instead stays at its old version until the owner agrees to refresh it. Your own adapters in `<live>/adapters/` are outside the payload and stay; run each one's `--selftest` once (`"$PY" "$LIVE/adapters/<file>" --selftest`), since the kit code around it changed.

## 3 · What changed in the seeds

```sh
"$PY" "$KIT/daemon/wa_setup.py" seed-status --config "$LIVE/config.json"
```

Read only. One line per owner copy, with its status and `copy v<x>, payload v<y>`; the last line counts the copies that need a merge.

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
4. **Doors.** "What we have learned" and "Recent runs" are the owner's history and are never touched; the rest of a door merges like any other section.
5. **Stamp it.** When every section of that version is settled, merged or declined, change the version in the copy's seed stamp comment to the payload's. The stamp records which seed text the owner has ruled on, not which text they took, so `seed-status` stops asking.
6. **Log it.** One filing-log line per merged vault file, and one line under `## Updates`.

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

`init` renders the file again and records its new hash; every other file is kept. Lines only in the old file are rules the owner added: carry each one over with the Edit tool on their yes, then `chmod 600` the file.

## 7 · Config

```sh
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" sync-reader-config --config "$LIVE/config.json"
```

New keys fill themselves with their defaults when the config loads; `references/config-reference.md` lists each key with its station, and the wizard reads any new one to the owner. `check-config` must show no new errors.

## 8 · The LaunchAgents

```sh
"$PY" "$KIT/daemon/wa_setup.py" render-launchd --config "$LIVE/config.json"
```

When both files report `kept ... (unchanged)`, there is nothing to reinstall. When a line says `update`, reinstall, which also restarts the daemon (so first wait for a prewarm or backfill in flight, as step 9 says):

```sh
"$PY" "$KIT/daemon/wa_setup.py" install-launchd --config "$LIVE/config.json"
```

When the evening intake job is installed, run both commands again with `--intake`.

## 9 · Restart, and the fingerprint

Skip the restart when step 8 reinstalled the daemon. A prewarm or backfill in flight is cut short and never run again: wait for its `prewarm 完成` or `backfill 完成` line in `$LIVE/wa_live.log` first.

```sh
LABEL="<launchd.label from the progress file header>"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
```

The daemon loads the new code. Runs in flight are killed and their bursts run again; a person note run that was cut off is marked failed and can be queued again.

Either way, refresh the fingerprint in the daemon's registry entry (C2 step 5): the new hash and version, with today's date and the Mac's name. A standby Mac compares against it on the day it takes over (the standby runbook's step 2), so a stale value sends it to the wrong version.

```sh
shasum -a 256 "$KIT/daemon/wa_live.py"
grep -m1 '^VERSION' "$KIT/daemon/wa_live.py"
```

## 10 · Verify

1. **The new code passes its own tests on this Mac.** Pass: `--- selftest PASS (<n> pass, 0 fail) ---`, `selftest: <n>/<n> 过` (the daemon prints in Chinese: all passed), and `dossier_core selftest: <n>/<n> ok`.
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" --selftest | tail -1
   "$PY" "$KIT/daemon/wa_live.py" --selftest | tail -1
   "$PY" "$KIT/skills/breakthrough-person-dossier/scripts/dossier_core.py" --selftest | tail -1
   ```
2. **The daemon runs the new version.** Pass: `state = running`; `ok`; the newest start line in the log names the new version.
   ```sh
   launchctl print "gui/$(id -u)/$LABEL" | grep "state = "
   curl -sS "http://127.0.0.1:$PORT/health"; echo
   grep -E "wa_live [0-9]+[.][0-9]+[.][0-9]+ " "$LIVE/wa_live.log" | tail -1
   ```
3. **Claude still answers from launchd.** Drop `probe-claude.json` exactly as station C2, step 8. Pass: `"is_error":false` and no `sample.txt`.
4. **Person notes read the owner's copies** (when C4 ran). Pass: `check-live: ok`, and the `playbook:` line does not say `PAYLOAD SEED`.
   ```sh
   "$PY" "$KIT/skills/breakthrough-person-dossier/scripts/dossier_core.py" --check-live --config "$LIVE/config.json"
   ```
5. **No seed waits for a merge.** Pass: `seed-status: 0 need a merge`.
6. **The command-base patches survived** (when C4 or O7 added them; a my-second-brain update can rewrite that skill). Pass: the rows added at C4 and O7 are still there.
   ```sh
   grep -nE "update-person|intake" "<the owner's command-base SKILL.md>"
   ```
7. **The registry records the running code** (after step 9). Pass: the hash printed equals the one now in the daemon's registry entry.
   ```sh
   shasum -a 256 "$KIT/daemon/wa_live.py"
   ```

## 11 · Record

Under `## Updates` in `<home>/INSTALL-PROGRESS.md`: the date, the version before and after, each seed merged or declined section by section, whether the LaunchAgent was reinstalled or restarted, the new fingerprint written into the registry, and each Verify check with the value it printed.
