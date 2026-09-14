# Station O4 · Push outlet

**When something breaks, where should the alert reach you, and which kinds of alert should go out?** What it costs: no model call. Each push is a few lines of text that leave the Mac through the outlet you pick: the Telegram servers for `telegram`, whatever service your command reaches for `command`. An error push names the conversation and the error line; the morning digest names the people waiting on you; with `notify.mode` at `needs_me` or `all`, judgments go out too, with other people's names and the draft text (`references/privacy.md`, "What else leaves, and to whom"). The outlet writes nothing in the vault; `telegram` reads one secret file, `live/telegram.json` (0600, never backed up, denied to the brain). One outlet serves the daemon, the command line tools and the evening intake job.

The tool was chosen at `install/C5-your-tools.md`, question 4. The kinds, the modes, the dedupe rule and both outlets: `references/interfaces.md`, "4 · Push outlet". Why errors only: `references/defaults-and-why.md`, "6 · Pushes only for errors, plus the morning digest".

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="$LABEL.intake"
PORT="<board.port from the progress file header>"
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs.

## Ask

One question at a time. Read default 6 before question 2.

1. **Where should alerts reach you?** (from C5) `telegram`: a bot of its own, used for nothing else, so it is always clear which system is talking. `command`: any command that reads the text on stdin and exits 0 when delivered (an ntfy or Pushover call with curl, a webhook, a macOS notification). `none`: the log and the board only.
2. **Which kinds?** (default 6) `errors` (the default: failures, a broken tool cage, a WhatsApp database that stopped changing, plus the digest), `needs_me` (adds every judgment that needs you), `all` (every judgment), `off`. Some owners take `all` for the observation week at O11 and switch back; that choice is made there.
3. **For `telegram`: is there a bot for this alone, and have you pressed Start in it?** A new bot cannot message you until you press Start once; before that every send fails.
4. **For `command`: which command, and does it carry a secret?** A token typed into `notify.command` travels into the nightly backup; keep it in a 0600 file the command reads instead (`references/interfaces.md`, "Choosing for the owner").

## Do

### 1 · The secret, written by the owner

For `telegram` the owner creates the bot in Telegram and writes `live/telegram.json` in their own terminal or editor, in this shape, then presses Start in the bot:

```json
{"bot_token": "<the token BotFather gave>", "chat_id": "<your chat id with the bot>"}
```

This session never asks for the token, never types it and never prints the file. It only sets and checks the mode:

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
chmod 600 "$LIVE/telegram.json"
stat -f %Sp "$LIVE/telegram.json"
```

For `command`, prove the plumbing with a file first: `cat >> "<home>/push-test.txt"` as the command (the config loader fills in `<home>`, this install's `paths.home`), then the real one. The outlet ends every push with a newline, so each push lands on a line of its own.

### 2 · Config

With the config edit ritual (`install/00-start-here.md`), set `notify.outlet`, `notify.mode`, and `notify.command` for `command` (`notify.telegram_file` stays at its default `<live>/telegram.json`). Then:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
```

It refuses a missing `telegram.json` and an empty command, and warns when `telegram.json` is not 0600.

### 3 · A push from the terminal

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_common.py" --config "$LIVE/config.json" --notify --kind error <<< "WhatsApp Live test push"
```

The same outlet function the daemon uses, with the terminal's environment. It prints `notify: delivered` and exits 0, or `notify: not sent (<why>)` and exits 1.

### 4 · A push from inside the daemon

Restart, then let the daemon push with launchd's environment, which is the only proof that counts for real alerts:

```sh
LABEL="<launchd.label from the progress file header>"
launchctl kickstart -k gui/$(id -u)/$LABEL
```

Once the newest `起跑` line in `live/wa_live.log` is from after the restart:

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/notify-test.json"
```

The message `🧪 [WA live] notify-test: 出口通了 (<time>)` arrives within seconds. For `command` with the test file, switch to the real command afterwards (config edit ritual, restart, `notify-test.json` again).

### 5 · The registry

Under the daemon's entry, the O4 block from `references/vault-proposals.md`, proposal f: reads `live/telegram.json` or `notify.command`, writes pushes, switch `notify.mode: off`, proof `notify-test.json` reaches the phone.

## Verify

Run each probe with the station's opening block in front of it. Every check must pass.

1. **The outlet code runs on this Mac.** Pass: `wa_common selftest PASS (<n> checks)`. (K250)
   ```sh
   "$PY" "$KIT/daemon/wa_common.py" --selftest | tail -1
   ```
2. **The secret is private** (`telegram` only). Pass: `-rw-------`, and no `notify.telegram_file` line from `check-config`. (K248)
   ```sh
   stat -f %Sp "$LIVE/telegram.json"
   "$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
   ```
3. **The terminal push arrived** (step 3). Pass: `notify: delivered`, and the owner sees `WhatsApp Live test push` on the phone (or on a line of its own in the test file). (K253)
4. **The daemon pushes with its own environment** (step 4). Pass: `notify 出口: <outlet> (mode=<mode>)` from the start, then `notify 已推 (<outlet>): 🧪 [WA live] notify-test: 出口通了 (<time>)` and `notify-test: 已发`, and the message on the phone. (K252, K249)
   ```sh
   grep -e "notify 出口" -e "notify 已推" -e "notify-test:" "$LIVE/wa_live.log" | tail -3
   ```
5. **The mode is the one ruled.** Pass: the mode from question 2; with `errors`, the newest successful run left no `notify 已推` line of its own. (K247, K254)
   ```sh
   grep "notify 出口" "$LIVE/wa_live.log" | tail -1
   ```

## Record

Under `### O4 push outlet` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` the outlet, the mode, for `telegram` that the bot is its own and Start was pressed (never the token or the chat id), for `command` the command with any secret replaced by `<secret file>`;
- `did:` the terminal push line;
- `verify:` the daemon's `notify-test:` line and that the owner saw the message;
- the registry block added;
- `ruled: default 6`: when O6 is on the station list, the note lands at O6 with the digest's answers in it; otherwise it lands here.

Tick `- [x] O4 push outlet (<outlet>)` only when every check passes. Next: the next `- [ ]` line under `## Stations`.
