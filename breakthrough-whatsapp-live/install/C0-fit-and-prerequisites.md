# Station C0 · Fit and prerequisites

Before anything is installed: is this kit right for the owner, is the Mac ready, how much traffic will the gate see, and do the playbooks go into the vault. C0 writes nothing except the working folder with its progress file and one measure file; everything else it asks the owner to switch on or install by hand.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"    # chosen and written in step 2
LIVE="$HOME_DIR/live"                                # created in C1
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"    # chosen and written in step 2
ILABEL="$LABEL.intake"
PORT="<board.port from the progress file header>"        # chosen and written in step 2
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs. The header does not exist before step 2, so nothing is created at any path, label or port until step 2 has checked the Mac for another install.

## Ask

One question at a time. Read `references/defaults-and-why.md`, default 1, before question 3. With question 1, say once to an owner who does not read Chinese: the reader's report lines and media labels, most daemon log lines, the brain prompt and the section names of person notes are in Chinese whatever `board.lang` says; the board itself follows `board.lang` (English or Chinese), and drafts follow each conversation's language.

1. **Is the number your own personal WhatsApp, and is it linked to WhatsApp Desktop on a Mac?** The kit reads WhatsApp Desktop's local database. It does not use the WhatsApp Business API or Cloud API, and it can only read a number linked to this Mac.
2. **About how many messages come in on a normal day, and in how many groups?** A rough answer is enough: the measure below gives the real number, and the number decides how much model usage the install will cost.
3. **Will you press send yourself, every time?** Nothing in this kit can send, and no setting will add it. A no ends the install here, kindly: the kit hands over drafts and is not built to reply for anyone. A yes is the owner's ruling on default 1.
4. **Is it fine that it works only through the official app's own database?** No unofficial WhatsApp library, no browser automation: those routes get numbers banned, and they will not be offered later as an option. A no ends the install.
5. **Which Mac stays on all day, signed in to WhatsApp Desktop and to Claude Code?** It runs the daemon. It must not sleep, and it should start again by itself after a power cut. An owner on Windows: say rule 7 of `SKILL.md` once and stop.
6. **Where should the working folder live?** Default `~/WhatsApp-Live`. Not inside Documents, Desktop, Downloads, iCloud Drive or a cloud storage folder: background jobs stall on privacy prompts there, and the folder holds whole conversations, so it should not sync anywhere. Step 2 checks the folder is not another install's before anything is created.
7. **Do you keep a second-brain vault, and where is it?** Person notes, the playbooks and the intake buffer live there. No vault is fine; those blocks are then skipped.
8. **The playbooks** (only with a vault). Three folders of method text that say why the system works the way it does and the pits to avoid. Offer one folder at a time, one sentence each, one yes each (`references/vault-proposals.md`, proposal e, move 1):
   * `whatsapp-live-triage`: the judgment and the pits behind the daemon, plus the cold start runbook for a dead Mac.
   * `resident-workers`: how to bound anything that runs while nobody watches (six cells, the approval card). Not limited to WhatsApp.
   * `person-dossier`: how a person note is written and checked. The note run reads it every time, so it is required if person notes are turned on at C4.

   Say when they land: in station C1, right after `wa_setup.py init` creates the record each copy is tracked in. A no is legal; say the three costs of declining out loud (proposal e, "Declined").

## Do

### 1 · This Mac and this kit

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live
scutil --get ComputerName
grep -m1 '^VERSION' "$KIT/daemon/wa_live.py"
```

Read only: the Mac's name and the kit version for the header. Nothing is created yet, because step 2 settles where the working folder goes; keep the answers given so far in this session until step 2 writes them down.

### 2 · Another install on this Mac? Then this install's home, label and port

```sh
find "$HOME" -maxdepth 1 -name 'WhatsApp-Live*'      # default on purpose
ls ~/Library/LaunchAgents | grep -i whatsapp
launchctl list | grep -i whatsapp
lsof -nP -iTCP:8787 -sTCP:LISTEN                     # default on purpose
```

Read only. When any line comes back, that folder, label or port is taken: this install gets its own home, label and port (for example `~/WhatsApp-Live-2`, `local.whatsapp-live-2` and `8797`). When nothing comes back, the defaults stand, with the folder the owner chose at question 6. The intake label is always the label with `.intake` added. ⛔ Never stop, change or remove what was found (rule 3 of `SKILL.md`).

Prove the chosen values are free before they go into the header, with every placeholder filled (the first line stops on one that is not):

```sh
HOME_DIR="<paths.home chosen in this step>"; LABEL="<launchd.label chosen in this step>"; ILABEL="$LABEL.intake"; PORT="<board.port chosen in this step>"
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
ls -A "$HOME_DIR"
ls "$HOME/Library/LaunchAgents/$LABEL.plist" "$HOME/Library/LaunchAgents/$ILABEL.plist"
launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && echo "label in use" || echo "label free"
lsof -nP -iTCP:"$PORT" -sTCP:LISTEN
```

Pass: `ls -A` says `No such file or directory`, or prints nothing (an empty folder made earlier, which is fine to use), or, when a later session resumes C0, lists only this install's own `INSTALL-PROGRESS.md` and `measure-3d.json`; both plists `No such file or directory`; `label free`; `lsof` prints nothing. Anything else: choose another value and run the block again.

Then create the folder:

```sh
HOME_DIR="<paths.home chosen in this step>"
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
mkdir -p "$HOME_DIR" && chmod 700 "$HOME_DIR" && ls -ld "$HOME_DIR"
```

Writes: the folder, mode 0700 (`chmod` also corrects a folder that already existed, whose mode `mkdir -p` keeps). Then write `<home>/INSTALL-PROGRESS.md` with the Write tool from the template in `install/00-start-here.md`: the kit version and the Mac's name from step 1, `paths.home` as the absolute path, `launchd.label`, `launchd.intake_label`, `board.port` and the vault. `chmod 600` it, and log the answers given so far and what this step found. From here on every block fills its placeholders from that header, and station C1 writes the same values into `live/config.json` right after `init`.

### 3 · Tools and the always-on Mac

```sh
sw_vers -productVersion; uname -m
command -v brew node claude tailscale
ls -d /Applications/WhatsApp.app /Applications/Tailscale.app
pgrep -x WhatsApp
pmset -g | grep -E "^ *(sleep|autorestart) "
```

Read only. A missing piece is installed on the owner's yes:

| Missing | What to do |
|---|---|
| Homebrew | the install command on `https://brew.sh`, run by the owner (it asks for their password) |
| node | `brew install node` |
| Claude Code | already there: this session runs in it. The daemon's own use of it is proven from launchd at C2 |
| WhatsApp Desktop | install it from the Mac App Store, open it, link it from the phone (WhatsApp, Linked devices), and leave it open |
| Tailscale | the Mac app from `https://tailscale.com/download` or the Mac App Store, signed in; used from C3 |

`pmset`: `sleep 0` means the Mac never sleeps on its own, and `autorestart 1` means it starts again after a power cut. Other values are the owner's call in System Settings; say what they cost (a sleeping Mac judges nothing until it wakes, and a Mac that stays off after a power cut is silent until someone switches it on).

### 4 · Full Disk Access for the app this session runs in

`wa_setup.py doctor` and `measure` read the WhatsApp database, which macOS protects. The owner does this, not the session:

1. System Settings, Privacy and Security, Full Disk Access.
2. Switch on the app this Claude Code session runs in (Terminal, iTerm, an editor, or the Claude app). When it is not in the list, add it with the + button.
3. Quit that app completely and open it again.

⚠️ Quitting ends this session. Log where the station stands first; the next session resumes at step 5 (`install/00-start-here.md`, "Resuming").

### 5 · The doctor

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"; PY=/usr/bin/python3
"$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json"
```

Read only; always exits 0; ends with one `VERDICT:` line. Before C1 there is no config yet, which is a note, not a blocker. Without a config the doctor cannot know this install's home, label or port: the first line under `[tailscale]`, `[working folder]` and `[launchd]` reads `(defaults, no config yet: ...)`, and those three sections describe the defaults until C1 writes the config. When step 2 chose other values, a home, a label or a `board port ... served: yes` in those sections belongs to another install, never to this one. Fix every `BLOCKER:` line, one at a time, and run the doctor again after each fix.

### 6 · Full Disk Access for node, the daemon's parent

The doctor's `[tools]` section prints `Full Disk Access: grant it to exactly <node path>`: the first node on the PATH, which `init` writes into `bin.node` at C1. The same section lists other node binaries at `/opt/homebrew/bin/node` and `/usr/local/bin/node` when they differ from that one, and says so when node resolves inside `~/.nvm`. Under launchd, node starts python, and every process below it (python, ffmpeg, whisperkit-cli, claude) works with node's access. A grant to the terminal does not carry over to launchd, and without this grant those processes hang silently instead of failing (`references/pitfalls.md`, pit 1).

Pick the node before anyone grants anything:

- **A node binary on this Mac already holds Full Disk Access** (another resident job runs under it, or the owner granted one of the listed binaries before): keep that grant rather than adding a second. Log its path now; C1 pins `bin.node` to it right after `init` and runs the doctor again.
- **The node on the PATH sits in a version folder under `~/.nvm`**: avoid it. `nvm install` or `nvm uninstall` changes or removes that path, and the daemon then starts under a node without the grant and hangs silently. Use a listed binary outside `~/.nvm`, or `brew install node` on the owner's yes, and pin it at C1 the same way.
- **Otherwise**: the path the doctor printed.

Then, unless the chosen node already holds the grant, the owner:

1. System Settings, Privacy and Security, Full Disk Access, the + button.
2. In the file dialog press Command, Shift and G together, paste the chosen node's path, choose Open, and check that the new entry is switched on.

It cannot be tested until the daemon runs: `probe-claude` at station C2 is the proof. ⚠️ Upgrading node later (`brew upgrade node`) can change the binary behind that path; after any node upgrade, run the C2 probe again and grant again if it hangs.

### 7 · Measure three days of traffic

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; PY=/usr/bin/python3
"$PY" "$KIT/daemon/wa_setup.py" measure --days 3 --config "$HOME_DIR/live/config.json"
"$PY" "$KIT/daemon/wa_setup.py" measure --days 3 --top 0 --json --config "$HOME_DIR/live/config.json" > "$HOME_DIR/measure-3d.json"
chmod 600 "$HOME_DIR/measure-3d.json"
```

Reads a snapshot copy of the WhatsApp database (deleted again) and counts inbound content messages. It prints no message text and no person names. Writes `<home>/measure-3d.json`: every group with its jid, name and inbound count, which station C2 turns into the whitelist. Tell the owner the numbers that matter: one-to-one inbound and in how many chats, group inbound and in how many groups, the top five groups, and the line with messages per day before the gate. Say what the gate will do with them at C2 (every one-to-one burst can become a run; groups only when ticked).

## Verify

| Check | Probe | Passing looks like |
|---|---|---|
| Fit | the progress file | four answers logged: personal number, rough volume, the owner presses send (yes), official database only (yes) |
| Working folder | `ls -ld "$HOME_DIR"`; `ls -l "$HOME_DIR/INSTALL-PROGRESS.md"` | `drwx------`; `-rw-------` |
| No collision | step 2 | the first block found nothing, or the header holds other values than what it found; the proof block passed for the header's home, label and port |
| Platform | doctor `[system]` | a macOS version and no blocker |
| WhatsApp Desktop linked and fresh | doctor `[whatsapp desktop]` | `readable by this process: yes`, `message table readable: yes (<n> message ids)`, `last database write:` a few minutes ago, `WhatsApp app running: yes` |
| Full Disk Access for this session's app | the same section | no `PERMISSION DENIED` line and no Full Disk Access blocker |
| Tools | doctor `[tools]` | node, python and claude found, Homebrew found; ffmpeg, whisperkit-cli and the whisper model may still be missing (C1 brings them) |
| Tailscale | doctor `[tailscale]` | a `tailscale:` binary and `serve status: readable`; the board port on that line is the default's until C1 (`(defaults, no config yet: ...)`), and this board not served yet is expected |
| The whole report | the doctor's last line | `VERDICT: READY` or `VERDICT: READY, with <n> note(s) above`; never `NOT READY` |
| Node grant | the progress file | the chosen node's path (the doctor's, or a node that already holds the grant, to be pinned at C1), not inside `~/.nvm`, and the owner's confirmation that its grant is switched on (the proof comes at C2) |
| Always on | `pmset -g` from step 3 | `sleep 0`, or the owner's ruling on another value logged |
| Traffic | step 7; `ls -l "$HOME_DIR/measure-3d.json"` | the per day line printed; the file present with `-rw-------` |
| Playbooks | the progress file | one ruling per folder (yes or no), or `no vault` |

## Record

Under `### C0 fit and prerequisites` in `<home>/INSTALL-PROGRESS.md`:

- the eight answers, and the header values (kit version, Mac name, `paths.home`, `launchd.label`, `launchd.intake_label`, `board.port`, vault);
- step 2: what the first block found (folder names, labels and ports only) and the proof block's lines for the chosen values;
- the doctor's `VERDICT:` line and each note it printed;
- the measure: one-to-one inbound and chats, group inbound and groups, messages per day before the gate, the top five groups with their counts;
- the chosen node's path, whether it already held the grant (then `bin.node` is pinned at C1) or was granted now, and when;
- per playbook folder: yes or no, with the owner's reason when they gave one (the yeses land at C1);
- `ruled: default 1`, as a decision note (`install/00-start-here.md`, "Rulings"), or logged here when there is no Decisions room.

Tick `- [x] C0 fit and prerequisites` only when every Verify row passes. Next: `install/C1-reader.md`.
