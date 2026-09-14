# Station O10 · Standby machine

**If the always-on Mac dies, how does WhatsApp Live come back, and is there a second Mac that could take over?** What it costs: no model call. On the always-on Mac, two system switches the owner flips and one line in the registry. On a standby Mac, only when there is one: the same kit installed but never started, Claude Code logged in, the backup folder visible, and at takeover time WhatsApp Desktop linked to the same number (one more linked device, of at most four, with a thinner history than the always-on Mac). During a takeover the standby reads the same chats and writes into the same vault. ⛔ Two daemons never run for the same number at the same time: they would judge every message twice, push twice, open the same person note twice, and keep two states that cannot be merged.

The cold start itself is written down once, in the seed `playbooks/whatsapp-live-triage/standby-machine-runbook.md`, which station C1 copied into the owner's vault with its playbook folder; the vault copy is the one to fill in and follow. Why one machine with a nightly copy instead of two in sync: `references/defaults-and-why.md`, "11 · One machine, backed up nightly". The backup it restores from: station O5 and `references/interfaces.md`, "5 · Nightly backup destination".

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="$LABEL.intake"
PORT="<board.port from the progress file header>"
RUNBOOK="<the vault copy of standby-machine-runbook.md>"
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs.

## Ask

One question at a time. Read default 11 before question 2.

1. **May this Mac start by itself after a power cut, and open WhatsApp Desktop at login?** Two switches in System Settings that the owner flips; without them, a short power cut leaves the daemon down until someone walks to the Mac.
2. **Is there a second Mac that could take over?** A laptop is enough for a few days. Without one, the runbook still says how to rebuild on a new Mac from the backup, and this station ends after step 3.
3. **With a standby: link WhatsApp Desktop on it now, or only on the day of a takeover?** Now costs a linked device slot and a second copy of the chats syncing on the standby; later costs an hour of history sync on a bad day.
4. **Rehearse?** On one Mac, a restore into a test install (about half an hour, nothing touches the real install). With a standby, optionally the full runbook on a quiet afternoon: the always-on daemon is stopped for its length, and the board is down for a few minutes.

## Do

### 1 · The two switches, flipped by the owner

The owner opens System Settings and turns on "Start up automatically after a power failure" (Energy), and adds WhatsApp to "Open at Login" (General, Login Items). This session changes no system setting; it reads the result in Verify checks 1 and 2.

### 2 · The fingerprint

Record which code this Mac runs, so a standby can install the same version on a day the always-on Mac cannot be asked (the runbook's step 2 compares against it):

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live
shasum -a 256 "$KIT/daemon/wa_live.py"
grep '^VERSION' "$KIT/daemon/wa_live.py"
```

C2 step 5 wrote both values, with the date, into the daemon's registry entry, naming the Mac, and `install/update.md` step 9 replaces them after every update. Compare them now, and write them in when the entry has none; a stale fingerprint sends a standby to the wrong version.

### 3 · The runbook in the vault, filled in

Open the vault copy of `standby-machine-runbook.md` in the whatsapp-live-triage playbook folder. When C1 declined that folder, offer it now (`references/vault-proposals.md`, proposal e): the payload seed is replaced on every update, so its values cannot be filled in place. Then, with the owner and the Edit tool, fill the values block at its top: `KIT`, `WL` (`paths.home`), `L` (`paths.live_dir`), `LABEL`, `ILABEL`, `PORT`, `PRIMARY` (the ssh alias a standby uses for this Mac), `RL` (the live folder relative to the home folder) and `B` (`backup.dest`). Read its four steps through with the owner once: confirm the Mac is really down, install on the standby without starting it, restore last night's backup, and never run both. Writes: the vault copy, one filing-log line.

### 4 · The standby Mac, prepared but never started (only with a standby)

On the standby, with the owner, one item at a time (the runbook's step 2 lists the same, for the day itself):

- the same tailnet: `tailscale status` on the standby lists the always-on Mac;
- an ssh alias for the always-on Mac in the standby's `~/.ssh/config`, the name written as `PRIMARY` in the runbook;
- the backup folder visible: the sync app running and signed in, or the disk it lives on reachable;
- Claude Code installed and logged in in the standby's own graphical session (a login check over ssh falsely reports `Not logged in`);
- the tools: `/usr/bin/python3`, node, ffmpeg, whisperkit-cli and a whisper model under `~/.cache/`; Full Disk Access granted ahead to the node binary the plist would name;
- the kit installed the same way as on the always-on Mac, with the same fingerprint as step 2; then only `wa_setup.py init` and `wa_setup.py doctor`;
- WhatsApp Desktop linked now or later, as question 3 decided.

⛔ Never `install-launchd` on the standby outside a takeover. Record the standby in the owner's registry as "prepared, not installed", with its name, so nobody later thinks it runs something or runs nothing.

### 5 · The rehearsal (only on a yes to question 4)

**On one Mac**, restore into the test install that `install/O11-observation-and-acceptance.md` describes in its step 5, never into the real `live/`: copy last night's `state.sqlite` and data files from `<backup.dest>/<date>/` into the test install's `live/` as the runbook's step 3 lists them, but keep the test install's own `config.json` (the owner's frontmatter guard refuses a command that holds `cp` and a `.md` path it cannot place outside the vault, such as the bare `summaries.md` in that step's loop or a path starting with a shell variable: copy `summaries.md` in a command of its own with both paths written out in full); clear the watermark, the inbound log and the pending bursts as step 3 says; start the test install; see its first start line; push `notify-test.json` into its test file; then remove the test install as that step says. This proves the backup restores and a restored database replays nothing.

**With a standby**, the full runbook on a quiet afternoon, step by step from the vault copy: stop the always-on daemon first (step 4's rule), take over on the standby (steps 2 and 3), check the board and one test run, then hand back (step 4) and restart the always-on daemon. Write down every place the runbook was wrong or unclear and fix the vault copy with the owner.

## Verify

Run each probe with the station's opening block in front of it. Every check that applies must pass.

1. **The Mac starts after a power cut.** Pass: a line `autorestart 1`. (K301)
   ```sh
   pmset -g | grep autorestart
   ```
2. **WhatsApp Desktop opens at login.** Pass: the list includes `WhatsApp` (macOS may ask once to let the terminal read System Events; the owner may read the Login Items list instead). (K302)
   ```sh
   osascript -e 'tell application "System Events" to get the name of every login item'
   ```
3. **The fingerprint in the registry is the running code.** Pass: the hash printed equals the one in the daemon's registry entry. (K305)
   ```sh
   shasum -a 256 "$KIT/daemon/wa_live.py"
   ```
4. **The runbook is ready to follow.** Pass: the values block holds no `<` placeholder left, and `seed-status` lists the playbook folder's copy (or the owner confirms the vault path). (K304)
   ```sh
   sed -n '/^KIT=/,/^B=/p' "$RUNBOOK"
   "$PY" "$KIT/daemon/wa_setup.py" seed-status --config "$LIVE/config.json"
   ```
5. **A restored database replays nothing.** Pass: both checks start with `✓`. (K307)
   ```sh
   "$PY" "$KIT/daemon/wa_live.py" --selftest | grep "还原过的库"
   ```
6. **Silence stays trustworthy.** Pass: the digest's quiet day check (O6) and the stale database alert (C2) are ticked in the checklist. (K303)
7. **The standby can reach what a takeover needs** (only with a standby; run on the standby, with the opening block filled from the runbook's values block, which names the standby's install the same way). Pass: dated folders in the backup root; an `uptime` line from the always-on Mac; the always-on Mac in `tailscale status`; `doctor` ends with a `VERDICT:` line; and `launchctl print` finds no service for the label. (K306, K314)
   ```sh
   case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
   ls "<backup root>"
   ssh '<PRIMARY>' 'uptime'
   tailscale status
   "$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json" | tail -1
   launchctl print gui/$(id -u)/$LABEL
   ```
8. **The rehearsal restored cleanly** (only after step 5). Pass on one Mac: the test install's log shows one `首跑: 水位定在 Z_PK=<n>` line after the restore, its inbound log holds only `skip:before_install` and newer rows, and its test file received the notify-test line. With a standby, the checklist's rows under "Not verifiable on a single machine" are ticked with the rehearsal's date.

## Record

Under `### O10 standby machine` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` the two switches (flipped by the owner, yes or no), standby yes or no and which Mac, WhatsApp linked now or later, rehearsal yes or no;
- `verify:` `autorestart 1`, the login item, the fingerprint and the version written into the registry;
- `did:` the runbook's vault path and that its values block is filled;
- `did:` the standby's registry line "prepared, not installed", when there is a standby;
- `did:` the rehearsal's date and every correction made to the runbook, or `pending: no standby Mac` for the rows that need one;
- `ruled: default 11`, one note with the backup answers from O5, the standby answer, and the rule that one session touches the daemon at a time.

Tick `- [x] O10 standby machine` only when every check that applies passes. Next: `install/O11-observation-and-acceptance.md`, always last.
