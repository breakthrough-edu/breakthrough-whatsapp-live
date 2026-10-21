# Station O3 · Calendar

**Should the board's "Add to calendar" button put an appointment from a chat into your calendar?** What it costs: no model call. Nothing is created until the owner presses the button and then "Create" in the form it opens, prefilled from the judgment's appointment hint and editable. With `ics`, the daemon writes one standard calendar file per event into `live/calendar/` (0600, not in the backup) and serves it at `/calendar/<key>.ics` on the board's address, so it reaches only the devices on your tailnet that open it, and the import happens on that device. With `lark`, the daemon creates one event through `lark-cli`, as you, in the one calendar id written into config and in no other. The event carries the title, the time, your note, the conversation's name and, for a one-to-one chat, a `wa.me` link.

The tool was chosen at `install/C5-your-tools.md`, question 3. The contract, the spec an event is built from and both implementations: `references/interfaces.md`, "3 · Calendar destination". No default is ruled at this station.

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

One question at a time.

1. **Which calendar do you look at during the day?** (from C5) Apple Calendar, Google Calendar or Outlook: `ics`. A Lark calendar: `lark`, which needs `lark-cli` installed and logged in by the owner in the terminal on this Mac.
2. **Which time zone are your appointments in?** Empty uses the zone macOS uses; a traveller who books in a home zone names it (`calendar.timezone`, an IANA name such as `Asia/Kuala_Lumpur`).
3. **For `lark`: which calendar should receive events?** Step 2 lists them. Only one id is ever written to.
4. **Which phone or computer will open the test event?** Each device imports an `.ics` file its own way, so the test runs on the device the owner uses with the board.

## Do

### 1 · Config

With the config edit ritual (`install/00-start-here.md`), set `calendar.destination` (`ics` or `lark`), `calendar.timezone` when question 2 named a zone, and for `lark` `calendar.lark.calendar_id` after step 2.

### 2 · Probe the destination

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_calendar.py" --config "$LIVE/config.json" --probe
```

Read only. For `ics` it names the folder and how many files are in it. For `lark` it lists the calendars with the current and writable ones marked, and suggests a `calendar.lark.calendar_id` when exactly one writable primary calendar exists; the owner picks, the session writes the id with the config edit ritual.

### 3 · A dry run

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_calendar.py" --config "$LIVE/config.json" --dry-run
```

Builds one timed and one all day test event and creates nothing: `ics` previews the file it would write, `lark` passes `--dry-run` to `lark-cli`.

### 4 · Restart

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
```

The daemon reads `calendar.destination` at start; after the restart the board shows the button on rows with an appointment hint.

### 5 · One test event from the board

The button shows only on a row whose judgment carries an appointment hint and that has no event yet; the board has no other way to open the form. So this step needs a chat the gate passes plus one model run: ask a friend to write "Can we talk tomorrow at 3pm?" in a one-to-one chat the gate passes; its run (one model call on `brain.model`) leaves the hint. When no chat carries a hint today, creating the event and pressing twice can still be proven from the terminal with the button's own request, the `POST /api/calendar` with a `jid`, `title`, `date` and `time` body that Verify 5 shows; the prefilled form and its survival through a refresh still need the hinted chat. On the phone, open that row, press "Add to calendar", wait through one board refresh (30 seconds) with the form open, change the title to `Calendar probe`, and press "Create". With `ics`, tap "Open .ics" and import the event; with `lark`, open the link. The owner checks the date, the time and the zone in the calendar app, then deletes the test event there.

Writes: for `ics`, `live/calendar/<key>.ics`; for `lark`, one event (deleted by the owner); the conversation's `calendar_done` in the state database, which keeps the button off until the hint's date or time changes.

### 6 · The registry

Under the daemon's entry, the O3 block from `references/vault-proposals.md`, proposal f: reads nothing, writes events at `calendar.destination`, switch `calendar.destination: none`, proof the button returns a link and pressing it twice makes one event.

## Verify

Run each probe with the station's opening block in front of it. Every check must pass.

1. **The calendar code runs on this Mac.** Pass: `selftest: <n>/<n> passed`, the two numbers equal. (K240)
   ```sh
   "$PY" "$KIT/daemon/wa_calendar.py" --selftest | tail -1
   ```
2. **The destination answers** (steps 2 and 3). Pass: `calendar.destination: <name> · owner timezone: <zone>` with the zone the owner expects; `[timed] ok=True error=none` and `[all day] ok=True error=none`; exit 0. (K244)
3. **Config is clean.** Pass: `check-config: 0 error(s)`; with `lark`, no `calendar.lark.calendar_id: is empty` line. (K245)
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
   ```
4. **A time the backend cannot read creates nothing.** Pass: a JSON answer with `"ok": false` and the error `cannot read the time: afternoon (write 15:00, or leave it empty for an all day event)`, and the log line `板子: 加进日历 <name> <date> (全天) → cannot read the time: afternoon (write 15:00, or leave it empty for an all day event)`. Use the jid of a conversation that has no event created yet (a chat whose appointment hint already has its event answers `"already": true` before the time is read) and tomorrow's date. (K242)
   ```sh
   curl -s -X POST "http://127.0.0.1:$PORT/api/calendar" -d '{"jid":"<test jid>","title":"Calendar probe","date":"<tomorrow YYYY-MM-DD>","time":"afternoon"}'
   grep "板子: 加进日历" "$LIVE/wa_live.log" | tail -1
   ```
5. **The test event landed once, where the owner looks** (step 5). Pass: the hint prefilled the form and the form survived the refresh; "Created · link" (or the `.ics` message with "Open .ics"); the owner saw the right date, time and zone; the button is gone from that row; posting the same appointment again answers `{"ok": true, "url": "<the same url>", "already": true}` and creates nothing, whether or not the chat still carries its hint; the log shows one `→ ok` line for that chat, then a `→ already` line for the repeat. Without a hinted chat, run the post below twice with a date the owner picks: the first is the Create itself (`"ok": true` with a `url`, logged `→ ok`; the owner opens that link and checks the event), the second is the repeat; the prefilled form, the refresh and the button (K238, K239) wait for the hinted chat. (K238, K239, K241, K243)
   ```sh
   curl -s -X POST "http://127.0.0.1:$PORT/api/calendar" -d '{"jid":"<that jid>","title":"Calendar probe","date":"<the same date>","time":"15:00"}'
   grep "板子: 加进日历" "$LIVE/wa_live.log" | tail -3
   ```
6. **For `ics`, the file is served as a calendar and kept private** (after step 5). Pass: `200 text/calendar; charset=utf-8`, and every file `-rw-------`. (K246)
   ```sh
   K=$(ls -t "$LIVE/calendar" | head -1); curl -s -o /dev/null -w "%{http_code} %{content_type}\n" "http://127.0.0.1:$PORT/calendar/$K"
   ls -l "$LIVE/calendar"
   ```

## Record

Under `### O3 calendar` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` the destination, the zone, for `lark` which calendar (its name, and that the id is in config);
- `did:` the probe's first line and the dry run's two result lines;
- `verify:` the refused time's error line, the test event's date and time as the owner saw them, and that the test event was deleted;
- the registry block added.

Tick `- [x] O3 calendar (<destination>)` only when every check passes. Next: the next `- [ ]` line under `## Stations`.
