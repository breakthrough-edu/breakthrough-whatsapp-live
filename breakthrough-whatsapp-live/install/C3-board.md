# Station C3 · The board

The board is one page the daemon serves on `127.0.0.1:<board.port>`. It has no login, so it never listens on anything but this Mac; the owner's phone reaches it through the owner's own tailnet with `tailscale serve`. This station opens it on the Mac, puts it on the tailnet, and walks every feature on it with the owner, on the Mac and on the phone.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="<launchd.intake_label from the progress file header>"
PORT="<board.port from the progress file header>"
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs.

## Ask

One question at a time. Read `references/defaults-and-why.md`, default 5, before question 1.

1. **Who else is in your tailnet?** Only the owner's own devices: the default stands. Anyone else can open the board once it is served: every conversation, every draft, and buttons that spend model usage and write into the vault. Then the owner rules: restrict this Mac with Tailscale's access rules, or accept it.
2. **Plain http inside the tailnet, or https?** Tailscale encrypts the traffic between devices either way. https needs HTTPS certificates switched on in the Tailscale admin console, and the board's copy buttons work more reliably over it.
3. **Does anything already serve on this Mac's tailnet port 80 (or 443 for https)?** `tailscale serve status` shows it, and `tailscale-hint` (step 2) reads it too. If so, the board takes another tailnet port, such as 8443.
4. **Which phone will you test with?** It needs the Tailscale app, signed in to the same tailnet.
5. **Which conversations show the media?** One with a photo, one with a video and one with a voice note (the C1 voice chat works), all opened on the board by the end of this station.

## Do

### 1 · Open it on the Mac

```sh
PORT="<board.port from the progress file header>"
open "http://127.0.0.1:$PORT/"
```

Nothing is written. The page shows the conversations judged so far, the other chats active in the last three days, and a footer with today's runs.

### 2 · The tailnet command

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"; PY=/usr/bin/python3
"$PY" "$KIT/daemon/wa_setup.py" tailscale-hint --config "$LIVE/config.json"
```

Add `--https` for https, and `--serve-port <port>` to choose the tailnet port yourself. It changes nothing: it reads `tailscale serve status --json`, then prints the `tailscale serve` command for the board's port, the address the phone opens, the check, and the remove command.

- When the tailnet port it would use already has a handler for something else, it prints a `WARNING` naming that port, and a command on the first free port from 8443 upward instead. It never prints a command that replaces a handler.
- When this board is already served, it says so and prints no serve command.
- When it cannot read the serve status, it says so: read `tailscale serve status` together before anyone runs a serve command.

Before step 3, log the tailnet ports `tailscale serve status` lists now (ports only); check 11 compares against them.

### 3 · The owner serves it

The owner runs, in their own terminal, exactly the command the hint printed (its tailnet port may not be 80), then the check. The shape, with the board's port:

```sh
PORT="<board.port from the progress file header>"
tailscale serve --bg --http="<tailnet port from the hint>" "http://127.0.0.1:$PORT"
tailscale serve status
```

Writes: one serve rule in Tailscale's own configuration, which survives restarts until the hint's remove line (`tailscale serve --http=<tailnet port> off`) removes it. ⛔ A serve command on a tailnet port that already has a handler replaces that handler and takes the other service off the tailnet: never run one the hint did not print for this board, and never on a port `tailscale serve status` already lists. ⛔ Never `tailscale funnel`: it publishes the board, which has no login, to the whole internet. When `tailscale` is not on the PATH, the Mac app's own binary is `/Applications/Tailscale.app/Contents/MacOS/Tailscale`; the doctor's `[tailscale]` line names the one it found.

### 4 · The phone

With Tailscale switched on on the phone, open the address the hint printed (`http://<this-mac>.<your-tailnet>.ts.net/`, or the short `http://<this-mac>/` with MagicDNS). Add it to the home screen if the owner likes. Then switch Tailscale off on the phone and reload: the board must not open.

### 5 · Walk every feature

With the owner, on the phone first, then on the Mac. The labels below are the English board's (`board.lang: en`); the Chinese board shows the same things. The full list with a probe per feature is `references/feature-checklist.md`.

**Left column**
- Search, on top of the list (the `/` key on the Mac): every word typed must appear in a message or a file name, in any chat WhatsApp Desktop keeps on this Mac. Results show the chat, the time, the speaker in a group and an excerpt with the words marked; with a conversation open, a chip limits them to it. A click opens the context view, the conversation around that message with it marked, and "‹ Results" goes back. Search only reads.
- Buckets with counts: Open, Reply, Waiting, FYI, Ignore, Replied / done, All.
- The sort toggle in the list header: priority order (the default), or newest message first. The choice is remembered on that device.
- Rows: an initials avatar (a group glyph for groups), name, how long ago, one situation line clamped to two lines with "more" to open that row only, small chips, and a colored dot on the ones waiting for the owner.
- Other chats: every chat active in the last `board.others_days` days that was never judged, up to `board.others_limit`. Opening one offers "Judge now"; a chat the gate does not pass asks for confirmation first, because a judgment spends usage.
- Footer: today's runs and tokens, the watermark, and how many hours ago WhatsApp last wrote its database.
- A banner on top, only while the daemon cannot call Claude (a login or a usage limit): since when, what to do, when it checks next, and that pending messages stay queued (`references/operating.md`, "When the brain cannot be called"). Nothing to see on a healthy install.

**Middle column**
- The three chips, World, Stage and Priority. World and Stage change with a tap (choosing a world that is not a business clears the stage); Priority belongs to the brain and is judged again on every run.
- The confidence line, the situation, "What is at stake for you", the timeline.
- "Brain memory", the rolling summary, with "Full re-judge".
- "Reply draft": edit it and it saves by itself. On a one-to-one chat "Open in WhatsApp" opens WhatsApp with the edited text in the box and sends nothing; a group gets "Copy draft".
- "Take to Claude Code": copies a self-contained packet of this one conversation for a deeper session.
- "Worth borrowing · related": the vault notes the brain pointed at.
- The last run's tokens and seconds.
- Once their stations are done: the person note line with "Start note" or "Rewrite note" (C4), and "Add to calendar" (O3).

**Right column** (below the draft on a phone)
- Recent messages: text bubbles, photos, videos that play, voice notes with a play button and their transcript (no picture above them).

**Done**
- Mac: hover a row and press ✓, or select it and press D. Inside the draft box D is just a letter.
- Phone: swipe the row left.

**Layout**: three columns on a wide screen, two on a medium one, one on the phone with "‹ List" to go back. On a wide screen the grips between the columns resize them (double-click a grip to reset); the widths are remembered on that device. The action row under a conversation wraps instead of cutting a button off.

**Today** (only once station O7 turns the brief on): a Today button in the rail opens the board's brief, per project of yours, with cards to answer OK or No and a refresh button that builds now and follows the build until it lands. On the phone it is a full page with a way back. By default nothing on it writes into the vault: placing happens in the intake mode (`install/O7-intake.md`, question 5). With the optional write button (question 6 of the same station), a card you pressed OK on also shows Write to vault, which writes that one task file by program, with a backup and an undo.

## Verify

Run each probe with the station's opening block in front of it. Every check must pass. `<jid>` is a conversation on the board; `<photo pk>` and `<video pk>` come from check 5.

1. **The board listens on this Mac only.** Pass: one listener, on `127.0.0.1:<port>`, never `*:<port>`.
   ```sh
   lsof -nP -iTCP:"$PORT" -sTCP:LISTEN
   ```
2. **The page carries its first data inline.** Pass: `1`.
   ```sh
   curl -sS "http://127.0.0.1:$PORT/" | grep -c 'id="boot"'
   ```
3. **Language and vocabulary come from config.** Pass: the `board.lang` ruled at C2, the owner's world keys, and `none` until O3 connects a calendar.
   ```sh
   curl -sS "http://127.0.0.1:$PORT/api/board" | "$PY" -c 'import json,sys; u=json.load(sys.stdin)["ui"]; print(u["lang"], [w["key"] for w in u["vocab"]["worlds"]], u["calendar"])'
   ```
4. **Rows carry the chips and the draft; one-to-one rows get the WhatsApp button, groups never do.** Pass: a row count above 0 and `True`; then the number of one-to-one rows, the number of those with a `whatsapp://send?phone=` link (equal, or a little lower when a number cannot be resolved and the row copies instead), and `0` groups with a link.
   ```sh
   curl -sS "http://127.0.0.1:$PORT/api/board" | "$PY" -c 'import json,sys; r=json.load(sys.stdin)["rows"]; print(len(r), bool(r) and all(k in r[0] for k in ("world","stage","stage_auto","priority","needs_me","situation","draft","timeline","refs")))'
   curl -sS "http://127.0.0.1:$PORT/api/board" | "$PY" -c 'import json,sys; r=json.load(sys.stdin)["rows"]; d=[x for x in r if x["kind"]=="direct"]; print(len(d), sum(1 for x in d if (x["link"] or "").startswith("whatsapp://send?phone=")), sum(1 for x in r if x["kind"]=="group" and x["link"]))'
   ```
5. **The thread shows text, photos, videos and voice transcripts, and sends no file paths.** Pass, across the media conversations: message and text counts above 0, photo pks, video pks, a transcript count above 0 on the voice chat, and `False` (no message carries a `media` path; the board uses `media_url` only).
   ```sh
   curl -sS "http://127.0.0.1:$PORT/api/thread?jid=<jid>&n=200" | "$PY" -c 'import json,sys; m=json.load(sys.stdin)["messages"]; pk=lambda p: [x["pk"] for x in m if (x["media_ctype"] or "").startswith(p)][:3]; print(len(m), sum(1 for x in m if x["text"]), pk("image/"), pk("video/"), sum(1 for x in m if x["transcript"]), any("media" in x for x in m))'
   ```
6. **Media is served, and video can seek** (iPhone Safari plays video only with a 206 answer). Pass: `200 image/jpeg` (or png, webp); `206`.
   ```sh
   curl -sS -o /dev/null -w '%{http_code} %{content_type}\n' "http://127.0.0.1:$PORT/media?pk=<photo pk>"
   curl -sS -o /dev/null -H 'Range: bytes=0-99' -w '%{http_code}\n' "http://127.0.0.1:$PORT/media?pk=<video pk>"
   ```
7. **Only World and Stage are editable, and nothing changes state through a GET.** Pass: `400`, then `404`. On the board, a World or Stage change shows "Changed" and survives a reload.
   ```sh
   curl -sS -o /dev/null -w '%{http_code}\n' -X POST "http://127.0.0.1:$PORT/api/field" -d '{"jid":"<jid>","field":"priority","value":"now"}'
   curl -sS -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$PORT/draft?jid=<jid>"
   ```
8. **A draft edit is saved on the Mac.** Edit the draft of `<jid>` on the phone, then pass: the length follows the edit, and the edit is still there after a reload.
   ```sh
   sqlite3 -readonly "$LIVE/state.sqlite" "select length(draft), draft_owner_at from conversations where jid='<jid>'"
   ```
9. **Done works.** Mark the test row done on the board (✓, D, or a swipe on the phone), then pass: `done|0`.
   ```sh
   sqlite3 -readonly "$LIVE/state.sqlite" "select tag, needs_me from conversations where jid='<jid>'"
   ```
10. **The handoff packet holds this one conversation.** Pass: a text packet about `<jid>` only; the board's button says the packet was copied.
    ```sh
    curl -sS "http://127.0.0.1:$PORT/api/packet?jid=<jid>" | head -5
    ```
11. **The tailnet serves the board, and nothing more.** Pass: one proxy to `http://127.0.0.1:<port>`, marked tailnet only; every handler `tailscale serve status` listed before step 3 still listed, on its own port; and the doctor's `board port <port> served: yes`.
    ```sh
    tailscale serve status
    "$PY" "$KIT/daemon/wa_setup.py" doctor --config "$LIVE/config.json"
    ```
12. **The phone.** Pass, confirmed by the owner holding the phone: with Tailscale on the board opens, a photo shows, a video plays, a voice note plays and shows its transcript with no broken picture above it, the WhatsApp button opens the right chat with the edited draft and sends nothing; with Tailscale off the board does not open.
13. **Every feature in step 5 was seen.** Pass: each line of step 5 seen by the owner, or logged with what was missing.
14. **Search answers, read only, and logs no words.** Search on the Mac for a word from a recent message (`<word>`, plain letters). Pass: `['hits', 'more', 'q']` and a count above 0; then one `板子: 搜寻 1 个词, <n> 笔` line (the search, by term and hit count) and `0` (the word is not in that line). On the board: results with chat, time and an excerpt with the word marked. (K336, K338, K339)
    ```sh
    curl -sS "http://127.0.0.1:$PORT/api/search?q=<word>&n=5" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(sorted(d), len(d["hits"]))'
    grep "板子: 搜寻" "$LIVE/wa_live.log" | tail -1
    grep "板子: 搜寻" "$LIVE/wa_live.log" | tail -1 | grep -c "<word>"
    ```
15. **The context view.** Click a result on the Mac. Pass: the conversation opens around that message, which is marked `Search hit` (zh `搜到的这条`), and `‹ Results` returns to the same list; with `<hit jid>` and `<hit pk>` taken from that hit in the search answer (`jid`, `pk`), the probe prints the messages in time order (by `at`) with `<hit pk>` among them. (K337)
    ```sh
    curl -sS "http://127.0.0.1:$PORT/api/thread?jid=<hit jid>&n=20&around=<hit pk>" | "$PY" -c 'import json,sys; print([(m["pk"], m["at"]) for m in json.load(sys.stdin)["messages"]])'
    ```
16. **Search on the phone.** Pass, confirmed by the owner holding the phone: the search panel opens full screen, a search lists results, a result opens the context view and back works, and nothing scrolls sideways. (K340)

## Record

Under `### C3 board` in `<home>/INSTALL-PROGRESS.md`:

- the answers: who is in the tailnet and the ruling, http or https, the tailnet port, which phone;
- the handlers `tailscale serve status` listed before step 3 (ports only), and any `WARNING` the hint printed;
- the serve command the owner ran (scheme and tailnet port only; the Mac's tailnet name is not needed);
- each Verify check with the value it printed;
- the features from step 5 that were seen, and any that were missing with the reason;
- `ruled: default 5`.

Tick `- [x] C3 board` only when every check passes. Next: `install/C4-person-notes.md` (or, when C4 is skipped, `install/C5-your-tools.md`).
