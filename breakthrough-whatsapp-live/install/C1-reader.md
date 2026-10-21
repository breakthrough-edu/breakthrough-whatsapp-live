# Station C1 · The reader

The reader is two scripts: `wa_sweep.py` turns WhatsApp Desktop's database into a reading pack (every message in a window, voice notes transcribed on the Mac, the text of Word, Excel, PowerPoint, RTF, ODT and Pages attachments, media paths listed), and `wa_context.py` lists the vault notes that may be about each chat. The daemon calls both on every run; the owner's own sessions call them through the `breakthrough-whatsapp-read` skill. This station links that skill, installs the voice tools and the model, creates the working tree and the config with `wa_setup.py init`, lands the playbooks the owner said yes to at C0, and proves one read with a voice note in it.

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

Claude Code keeps no shell variables between commands: start each command with the lines it needs.

## Ask

One question at a time. Read `references/defaults-and-why.md`, default 1 (its last paragraph), before question 1.

1. **How many Macs do you work from, and where do your Claude Code sessions usually run?** With one Mac, reads use its local database. With a second Mac, reads from there go to the always-on Mac over ssh and stop with a plain message when it cannot be reached, never falling back to a copy that may be days behind.
2. **Your own WhatsApp number**, digits with the country code, no plus sign (`my_phone`). Asked, never guessed: the reader's guess is wrong for anyone often added to other people's groups.
3. **Every way people address you in messages**: names, nicknames, in every script you are called by (`my_names`), and **what prompts and notes should call you** (`owner_label`). Very short forms (two letters) match inside other words; say so when the owner gives one.
4. **The main language of your voice notes**, or none to let whisper detect it (`brain.whisper_language`, for example `en` or `zh`).
5. **One conversation with a voice note in the last three days**, to prove transcription. A friend who agrees to send one now is fine.

Before the first read, say once: what this session reads lands in this session's own transcript on this Mac (under `~/.claude/projects/`), private conversations included.

## Do

### 1 · Link the reader skill

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; SKILLS="$(dirname "$KIT")"
ls -la "$SKILLS/breakthrough-whatsapp-read" 2>/dev/null || ln -s "$KIT/skills/breakthrough-whatsapp-read" "$SKILLS/breakthrough-whatsapp-read"
```

Then, as a separate command (a my-second-brain frontmatter guard can refuse a call that writes and names a `.md` path at once; `install/00-start-here.md`, "Shell conventions"):

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; SKILLS="$(dirname "$KIT")"
ls -L "$SKILLS/breakthrough-whatsapp-read/"
```

Writes: one link, `<skills folder>/breakthrough-whatsapp-read`, pointing into the payload, so `npx skills update` carries it along. The check lists `SKILL.md` and `scripts`.

⛔ Check first, never clobber. When the first `ls` prints an entry, leave it: say what it is (a link and where it points, or a folder, for instance a copy that `npx skills add` installed from this repo). For a folder, compare it with the payload's copy: `diff -rq "$SKILLS/breakthrough-whatsapp-read" "$KIT/skills/breakthrough-whatsapp-read"`. A copy works but stays at its version through updates; replacing it is the owner's call. ⛔ Never remove a link with `rm -r`; plain `rm` removes the link itself.

### 2 · Voice tools

```sh
command -v whisperkit-cli ffmpeg
```

Two paths: both are there, nothing to install. For each one missing, on the owner's yes, `brew install` only that one (`brew install whisperkit-cli`, `brew install ffmpeg`), then run `command -v` again. Say first that `brew install` can update Homebrew and upgrade formulas that other tools on this Mac already use. Writes: at most two Homebrew formulas.

### 3 · The whisper model, outside Documents

The model is about 1.5 GB. `brain.whisper_model` expects it at `~/.cache/whisper-models/openai_whisper-large-v3-v20240930`. It must not live in Documents, Desktop or Downloads: under launchd, whisperkit-cli opening a model there waits forever on a privacy prompt nobody can answer (`references/pitfalls.md`, pit 1).

Look for a copy already on the Mac first:

```sh
find ~/.cache ~/Documents/huggingface -maxdepth 7 -type d -name "openai_whisper-large-v3-v20240930" 2>/dev/null
```

- **Found under `~/.cache/whisper-models/`**: nothing to do.
- **Found elsewhere**: `mkdir -p ~/.cache/whisper-models && ditto "<the folder found>" ~/.cache/whisper-models/openai_whisper-large-v3-v20240930`
- **Not found**: download it, on the owner's yes. whisperkit-cli has no download command: the model arrives as a side effect of transcribing, which needs an audio file, so a one second silent file does it.

```sh
DL=~/.cache/whisper-download; mkdir -p "$DL"
ffmpeg -y -f lavfi -i anullsrc=r=16000:cl=mono -t 1 -ar 16000 -ac 1 "$DL/silence.wav"
whisperkit-cli transcribe --audio-path "$DL/silence.wav" --model large-v3-v20240930 --download-model-path "$DL"
M="$(find "$DL" -type d -path "*whisperkit-coreml/openai_whisper-large-v3-v20240930" | head -1)"; echo "$M"
mkdir -p ~/.cache/whisper-models && mv "$M" ~/.cache/whisper-models/
ls -d ~/.cache/whisper-models/openai_whisper-large-v3-v20240930/AudioEncoder.mlmodelc
```

Judge by the folder, not by whisperkit-cli's exit code (transcribing silence may complain). Writes: `~/.cache/whisper-models/openai_whisper-large-v3-v20240930/`. Once the last line prints its path, `~/.cache/whisper-download` holds only leftovers; `rm -r ~/.cache/whisper-download` on the owner's yes.

### 4 · The working tree: `wa_setup.py init`

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; PY=/usr/bin/python3
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
"$PY" "$KIT/daemon/wa_setup.py" init --home "$HOME_DIR" --dry-run
"$PY" "$KIT/daemon/wa_setup.py" init --home "$HOME_DIR"
```

Show the owner the dry run's plan (its first line names the home: it must be the header's), then run it. `init` never overwrites a file that exists: it reports it as kept and moves on. Writes:

- folders, 0700: `runs-work/`, `prewarm/`, `transcripts-cache/`, `doc-text-cache/` (text of documents people sent, converted on this Mac), `secrets/` (holds `deny-canary.txt`, a random marker for the deny probe, and later the optional setup token if the owner chooses it at C2, step 12), `brain-cwd/`, `launchd/`, `live/`, `live/prompts/`, `live/digest/`, `live/runs/`, `live/commands/`, `live/dossier-backups/`, `live/variants/`
- `live/config.json` (0600): from `daemon/templates/config.example.json`, with the tools found on this Mac and the whisper model folder filled in
- the owner copies `live/prompts/brain-prompt.md`, `live/prompts/second-source-prompt.md`, `live/prompts/backfill-prompt.md` and `live/topic-map.json`, each keeping its seed stamp
- `live/claude-settings.json` (0600): the settings every headless run uses, with absolute Read deny rules for the secrets (C2 checks them)
- `live/seeds.json`: the hash of every copy at install time, so `seed-status` can later tell an owner edit from a newer seed

`init` writes the default label, intake label and port. Read what it wrote next to the header (config writes the home with `~`, the header with the full path; both must name the same folder):

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"; PY=/usr/bin/python3
"$PY" -c 'import json,sys; c=json.load(open(sys.argv[1])); print(c["paths"]["home"], c["launchd"]["label"], c["launchd"]["intake_label"], c["board"]["port"], c["bin"]["node"])' "$LIVE/config.json"
```

When the header records another label, intake label or port, write the header's values now with the config edit ritual (`install/00-start-here.md`): `launchd.label`, `launchd.intake_label`, `board.port`. When C0 step 6 chose a node other than the one on the PATH (one that already holds Full Disk Access, or one outside `~/.nvm`), pin `bin.node` to its absolute path in the same way, then run the doctor again (`"$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json"`): its `Full Disk Access: grant it to exactly` line must name that node.

### 5 · Land the playbooks the owner said yes to at C0

Only folders with a yes, one at a time. `copy-seed` needs `live/config.json`, because it records each copy in `live/seeds.json`; that is why this runs here and not at C0. Read the owner's doctrine (section 9) for where playbook folders live in their vault; the path below is the my-second-brain shape.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"; PY=/usr/bin/python3
DEST="<vault>/<business wing>/04_Methodology/Playbooks/whatsapp-live-triage"
"$PY" "$KIT/daemon/wa_setup.py" copy-seed --config "$LIVE/config.json" --seed playbooks/whatsapp-live-triage --to "$DEST" --confirmed-by-owner --dry-run
"$PY" "$KIT/daemon/wa_setup.py" copy-seed --config "$LIVE/config.json" --seed playbooks/whatsapp-live-triage --to "$DEST" --confirmed-by-owner
```

The same for `playbooks/resident-workers` and `playbooks/person-dossier`, each into its own folder of the same name. `--confirmed-by-owner` is the owner's yes from C0, written down: never pass it for a folder the owner has not said yes to. Writes: the folder's files with their seed stamps (0644), never over an existing file, and each missing folder on the way 0755, like the rest of the vault; in each copy's frontmatter `confirmed_by_owner: true` where the seed says `false` and today's date in the door's `updated`; and one record per file in `live/seeds.json`, holding the hash of what landed, so `seed-status` reads `unchanged` for every copy.

Then finish each folder exactly as `references/vault-proposals.md`, proposal e, moves 3 to 7 say: one Recent runs row in the whatsapp-live-triage door registering this install (its address is `<home>/INSTALL-PROGRESS.md`; the expectation columns hold the owner's expectation or stay blank), the folder's line in `Home.md`, one filing-log line per folder, `paths.person_playbook` pointed at the vault copy, and one auto-memory pointer per folder naming the folder when the owner keeps an index. Vault files are edited with the Edit or Write tool, never through the shell.

A declined folder follows proposal e, "Declined". No vault: skip this step and log it.

### 6 · The owner's number and names

With the config edit ritual (`install/00-start-here.md`), set:

- `my_phone`: digits only, with the country code
- `my_names`: every form the owner gave
- `owner_label`
- `brain.whisper_language`, when the owner named one
- `paths.vault`: the vault root from C0, or leave it empty

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"; PY=/usr/bin/python3
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" sync-reader-config --config "$LIVE/config.json"
```

`check-config` writes nothing. Warnings about keys later stations fill (`vocab.business_context` and the like) are expected here; errors are not. `sync-reader-config` writes `<home>/config.json` (0600), the reader's own config generated from the live config (never edit it by hand), and refreshes every `<home>/runs-work/*/config.json`.

### 7 · The first read: three days, text only

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; PY=/usr/bin/python3
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
"$PY" "$KIT/skills/breakthrough-whatsapp-read/scripts/wa_sweep.py" --work-dir "$HOME_DIR" --days 3 --no-transcribe
```

`--no-transcribe` keeps this first read quick; with it on, every voice note of three days would be transcribed first. Writes: snapshot copies of the WhatsApp database in `<home>` (deleted after the read), `<home>/sweeps/sweep-<date>-3d.json` and `.md` (0600), `<home>/last-sweep.json`. The last stdout line is a JSON summary with the paths. Its warnings include one saying voice was not transcribed (`语音没有现转`); that one is expected.

The reader speaks Chinese whatever `board.lang` says: its warnings, the pack's report lines and its media labels (`图片` photo, `语音` voice note, `影片` video) are fixed Chinese text. Translate what matters for an owner who does not read Chinese.

Read the top of the md with the owner: how many chats and messages, media counts, document text counts (`文件转字 <n> 份`: documents converted to text), the warnings. A document someone sent shows under its message as `📄 文件内文 (机器转出):` and its text quoted: machine converted text of the document, never words the person typed. With a vault set, look the chats up in it:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; PY=/usr/bin/python3
"$PY" "$KIT/skills/breakthrough-whatsapp-read/scripts/wa_context.py" --sweep "<the json path from the summary>" --config "$HOME_DIR/config.json"
```

Writes `context-<...>.json` and `.md` beside the sweep: candidate vault notes per chat. Read only on the vault.

### 8 · Voice proof on one conversation

Find the chosen conversation's jid in the sweep json, matching the name with the owner:

```sh
PY=/usr/bin/python3
"$PY" -c 'import json,sys; [print(c["jid"], c["kind"], c["counts"]["total"], c["name"]) for c in json.load(open(sys.argv[1]))["chats"]]' "<the json path>"
```

Then read that one chat with transcription on:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; PY=/usr/bin/python3
case "$HOME_DIR $LABEL $PORT" in *"<"*|"~"*) echo "fill in HOME_DIR, LABEL and PORT first (HOME_DIR as an absolute path)" >&2; exit 1;; esac
ls "$HOME_DIR/transcripts-cache" | wc -l
"$PY" "$KIT/skills/breakthrough-whatsapp-read/scripts/wa_sweep.py" --work-dir "$HOME_DIR" --days 3 --only-jid "<jid>"
ls "$HOME_DIR/transcripts-cache" | wc -l
```

Writes one more sweep pair, and one transcript file per voice note into `<home>/transcripts-cache/`, the cache the board reads too. Read the voice lines in the new md with the owner and ask whether the words match what was said. Transcripts are raw and uncorrected.

## Verify

| Check | Probe | Passing looks like |
|---|---|---|
| Reader skill linked | `ls -L "$SKILLS/breakthrough-whatsapp-read/"`; then, as its own command, `head -1 "$SKILLS/breakthrough-whatsapp-read/SKILL.md"` | `SKILL.md` and `scripts`; `---` |
| Voice tools | `command -v whisperkit-cli ffmpeg` | two paths |
| Model copy | `ls -d ~/.cache/whisper-models/openai_whisper-large-v3-v20240930/AudioEncoder.mlmodelc` | the path |
| init | its last line | `init: <n> created, <n> already there, <n> warning(s), 0 error(s)` |
| Header and config agree | the read-back probe in step 4 | the header's home (written with `~`), label, intake label and port; `bin.node` is the node C0 step 6 chose |
| Permissions | `stat -f '%Sp %N' "$HOME_DIR" "$LIVE" "$LIVE/config.json" "$LIVE/claude-settings.json"`; `find "$HOME_DIR" -perm -004 -type f` | `drwx------` twice, `-rw-------` twice; the find prints nothing |
| Config | `wa_setup.py check-config --config "$LIVE/config.json"` | `check-config: 0 error(s), <n> warning(s)` |
| Reader config | `"$PY" -c 'import json,sys; c=json.load(open(sys.argv[1])); print(c["my_phone"], len(c["my_names"]), bool(c["whisper_model"]), bool(c["whisperkit_bin"]), bool(c["ffmpeg_bin"]))' "$HOME_DIR/config.json"` | the owner's number, the number of names, `True True True` |
| Doctor now | `wa_setup.py doctor --config "$LIVE/config.json"` | `[tools]` shows ffmpeg, whisperkit-cli, `whisper model folder: present` and `Full Disk Access: grant it to exactly` the chosen node; no `(defaults, no config yet` line; last line `VERDICT: READY` (with notes) |
| First read | its summary line | `"ok": true` and `"chats"` above 0 |
| The owner's number recognized | `"$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["source"]["my_phone"])' "<the json path>"` | the owner's number, confirmed with them |
| Voice | the `--only-jid` summary line; the two cache counts | `"voice"` with `"transcribed"` 1 or more and `"failed": 0`; the second count higher than the first; the owner agrees the words match |
| Vault lookup (with a vault) | the `wa_context.py` summary line | `"ok": true` |
| Document text (when a chat in the window holds a Word, Excel, PowerPoint, RTF, ODT or Pages file) | `grep -A3 "📄" "<the .md path of a read>"`; that read's summary line | `  📄 文件内文 (机器转出):` (document text, machine converted) with the document's text below it as `  > ` lines; the summary's `"documents"` with `"enabled": true` and `"failed": 0`. To turn it off: `reader.read_documents` false and `sync-reader-config` (K324, K325) |
| Playbooks (with yeses) | `"$PY" "$KIT/daemon/wa_setup.py" seed-status --config "$LIVE/config.json"`; `grep -h "^confirmed_by_owner:" <each destination>/*.md`; `ls` of each destination | every landed file listed as `unchanged` with `copy v<x>, payload v<x>`, except the whatsapp-live-triage door, which reads `edited by owner` once its Recent runs row is in (expected); `confirmed_by_owner: true` for every file that carries the key; each destination folder `drwxr-xr-x`; the door's Recent runs holds exactly one row |

## Record

Under `### C1 reader` in `<home>/INSTALL-PROGRESS.md`:

- the two companion links: where each points (`ls -l` of the link, as step 1 printed it);
- the answers: how many Macs and where sessions run, that `my_phone` and `my_names` are set (the count of names, not the names), the voice language;
- the model: found or downloaded, and its path;
- the `init` last line, the read-back of home, label, intake label, port and `bin.node`, and each value written because it differed from the header or from C0 step 6's node;
- per playbook folder: landed (with its `seed-status` line) or declined, plus the Recent runs row;
- the first read: chats, messages, `media_missing`, and the `documents` counts (converted, cached, failed), or that no document was in the window;
- the voice proof: transcribed, failed, and the owner's yes on the words;
- `ruled: default 1` (one Mac or two, and the stop when unreachable rule for a second Mac).

Tick `- [x] C1 reader` only when every Verify row passes. Next: `install/C2-daemon.md`.
