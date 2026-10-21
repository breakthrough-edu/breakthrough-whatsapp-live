# Uninstall

Removing WhatsApp Live takes both jobs out of launchd, takes the board off the tailnet, removes the two companion links and the payload, and, on the owner's yes, moves the working folder to the Trash. The vault keeps everything it holds: person notes, the intake buffer, the playbook folders, the decision notes, the registry entries. Those are the owner's records; the kit stops writing them and deletes none of them.

⛔ Only this install's own labels and port (rule 3 of `SKILL.md`). ⛔ Nothing is deleted without the owner's yes, and nothing is deleted that the owner cannot get back from the Trash.

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

Claude Code keeps no shell variables between commands: start each command with the lines it needs, filled from the header of the install being removed (`install/00-start-here.md`, "Shell conventions"). Read the header before step 6 moves it to the Trash, and keep its values in this session for the Verify probes. The steps run in this order because steps 1 to 3 still need the payload.

## Ask

Show the owner the whole list first, then take their answers one at a time:

1. **A last copy of the daemon's state before it goes?** Into which folder?
2. **The working folder** (`<home>`): it holds every run's full conversation pack, the state database, the logs, the reader's snapshots, voice transcripts, the text of documents people sent, the config with the owner's number and names, the push token, and, when the daemon ran on a setup token, that token (`secrets/claude-bot.env`). To the Trash, or kept? Anything to copy out first (drafts history in `live/voice-pairs.jsonl`, `live/summaries.md`, the digests)?
3. **The command-base patches** (update-person, intake, the morning intake line): remove them, since the commands they call will be gone, or keep them?
4. **The whisper model** (about 1.5 GB in `~/.cache/whisper-models/`): does another tool use it?
5. **The push bot** (when O4 used Telegram): revoke it, or keep it for something else?

## Do

### 1 · A last copy (optional)

```sh
"$PY" "$KIT/daemon/wa_backup.py" --config "$LIVE/config.json" --dry-run --dest "<folder>"
"$PY" "$KIT/daemon/wa_backup.py" --config "$LIVE/config.json" --run --dest "<folder>"
```

Add `--no-process-check` when `backup.require_process` names a sync app that is not running. The folder's parent must exist. Writes `<folder>/<date>/` with the state database, the redacted config, summaries, facts, voice pairs, the second source whitelist, prompts, the topic map, `seeds.json`, `acceptance.json`, digests, note backups and your own adapters, never the push token, run folders or logs.

### 2 · Stop both jobs

```sh
"$PY" "$KIT/daemon/wa_setup.py" uninstall-launchd --config "$LIVE/config.json" --dry-run
"$PY" "$KIT/daemon/wa_setup.py" uninstall-launchd --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" uninstall-launchd --intake --config "$LIVE/config.json"
```

Each boots its job out, disables its label (so a restart cannot bring it back) and removes its plist from `~/Library/LaunchAgents`. The rendered copies in `<home>/launchd/` and everything in `live/` stay until step 6. When the evening job was never installed, the `--intake` run says it was not loaded, which is fine. For a label it refuses, it exits 3 and runs nothing.

### 3 · Off the tailnet

The owner runs the remove line that `wa_setup.py tailscale-hint --config "$LIVE/config.json"` prints for the scheme and the tailnet port recorded at C3, in this shape:

```sh
tailscale serve --http="<tailnet port recorded at C3>" off
tailscale serve status
```

⛔ `off` on a tailnet port removes whatever serves there: first check in `tailscale serve status` that the port proxies to `http://127.0.0.1:<this install's board port>`, and never touch a handler that points anywhere else.

### 4 · The vault

Nothing in the vault is deleted. On the owner's yes:

- **Registry entries** for both jobs and the note writer: mark them removed, with today's date, rather than deleting them.
- **Command-base patches** (`references/vault-proposals.md`, proposal d): remove the update-person row, the intake row, the compile sentence and the morning intake line, their description words and their `references/` files. The owner's other edits in that skill stay exactly as they are.
- **Auto-memory**: pointers to playbook folders in the vault stay valid. A pointer to the payload's own playbooks goes.

The person notes' recent blocks and their `wa_jid` keys are harmless without the daemon and stay.

### 5 · The companion links and the payload

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; SKILLS="$(dirname "$KIT")"
for s in breakthrough-whatsapp-read breakthrough-person-dossier; do
  if [ -L "$SKILLS/$s" ]; then ls -l "$SKILLS/$s"; rm "$SKILLS/$s"; else ls -ld "$SKILLS/$s" 2>/dev/null; fi
done
npx skills remove breakthrough-whatsapp-live
```

A link is removed with plain `rm`, which removes the link and nothing it points at. A real folder at either name was not put there by this kit's links: leave it and tell the owner. ⛔ Never `rm -r` a path under the skills folder. The payload goes through the same tool that installed it; `npx skills --help` shows the remove command and its flags for the version on this Mac (a global install may need `-g`).

### 6 · The working folder

Only on the owner's yes, and after copying out anything they want to keep:

```sh
HOME_DIR="<paths.home from the progress file header>"
mv "$HOME_DIR" "$HOME/.Trash/$(basename "$HOME_DIR")-removed-$(date +%Y%m%d)"
```

The folder goes to the Trash; emptying the Trash is the owner's own act. The progress file goes with it, which is why step 4 records the removal in the registry.

### 7 · What else the install left on the Mac

Tell the owner each one; they decide:

- `~/.cache/whisper-models/openai_whisper-large-v3-v20240930`: remove it when no other tool uses it.
- Full Disk Access for node and for the terminal app: System Settings, Privacy and Security, Full Disk Access. Switch them off when nothing else needs them.
- The Telegram bot: its token stops mattering once revoked through BotFather in Telegram.
- The setup token, when the daemon ran on one (station C2, step 12): it stays valid until it expires, even in the Trash. The owner revokes it from their Claude account, or lets it run out.
- The nightly backups in `backup.dest`: the owner's, with state and copies of person notes inside.
- The disabled labels: `launchctl print-disabled "gui/$(id -u)" | grep -F -e "\"$LABEL\"" -e "\"$ILABEL\""` lists this install's two labels as disabled. That is harmless, and a later install enables them again.

## Verify

These probes pass on an absence, so each block starts with the fill check (`install/00-start-here.md`, "Shell conventions"): with a placeholder left in, they would pass on nothing.

1. **Both jobs are gone.** Pass: `launchctl print` answers that the service cannot be found, and `ls` finds neither plist.
   ```sh
   case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
   launchctl print "gui/$(id -u)/$LABEL"
   ls "$HOME/Library/LaunchAgents/$LABEL.plist" "$HOME/Library/LaunchAgents/$ILABEL.plist"
   ```
2. **Nothing of this install is running or listening.** Pass: no output from either. The `pgrep` pattern is this install's own config path, which both node and python carry on their command lines, so another install's daemon on the same Mac is not counted.
   ```sh
   case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
   pgrep -fl "$LIVE/config.json"
   lsof -nP -iTCP:"$PORT" -sTCP:LISTEN
   ```
3. **The board is off the tailnet.** Pass: no proxy to `http://127.0.0.1:<port>`.
   ```sh
   tailscale serve status
   ```
4. **The links and the payload are gone.** Pass: `No such file or directory` for each of the three, except a folder the owner chose to keep.
   ```sh
   ls -ld "$SKILLS/breakthrough-whatsapp-live" "$SKILLS/breakthrough-whatsapp-read" "$SKILLS/breakthrough-person-dossier"
   ```
5. **The working folder is in the Trash, or kept by choice.** Pass: `ls -d "$HOME_DIR"` says `No such file or directory` (with `HOME_DIR` filled in), unless the owner kept it.
6. **The vault is intact.** Pass: the People room, the intake buffer and the playbook folders hold the same notes as before the uninstall.
7. **No headless run left a transcript behind** (`references/privacy.md`, "The old session transcript hazard"). Pass: `0`. The name counted is the transcript folder Claude Code would make for this install's `brain-cwd`, so another install's is not counted.
   ```sh
   case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
   ls ~/.claude/projects | grep -cF -e "$(printf '%s' "$HOME_DIR/brain-cwd" | tr -c '[:alnum:]' '-')"
   ```

## Record

The progress file has left with the working folder. Record the uninstall in the owner's registry entries (step 4): the date, both labels removed, the tailnet rule removed, what was kept (working folder, model, backups) and why. When the owner keeps a Decisions room and wants the choice recorded, one decision note.
