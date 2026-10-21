# Interfaces

WhatsApp Live meets the owner's other tools in five places. Each place is a small contract: the daemon asks for one thing in one shape, and an implementation answers in that shape. The kit ships a few implementations for each, `none` is always one of them, and one config key picks which one runs. An install session reads this page to connect the owner's tools (station C5 and O1 to O5); a later session reads it to write an implementation the kit does not ship.

The code is the source of truth. Every contract below was read out of the module that owns it; where this page and the module disagree, the module is right and this page needs fixing.

| # | Interface | Picked by | Ships | Owner module | Station |
|---|---|---|---|---|---|
| 1 | Facts source | `facts.source` | `none` · `csv` · `lark` | `daemon/wa_facts.py` | O1 |
| 2 | Second message source | `second_source.source` | `none` · `lark` | `daemon/wa_second_source.py` | O2 |
| 3 | Calendar destination | `calendar.destination` | `none` · `ics` · `lark` | `daemon/wa_calendar.py` | O3 |
| 4 | Push outlet | `notify.outlet` | `none` · `telegram` · `command` | `daemon/wa_common.py` | O4 |
| 5 | Nightly backup destination | `backup.dest` | any folder | `daemon/wa_backup.py` | O5 |

Every key named here is described in `references/config-reference.md`. Why the defaults are what they are is in `references/defaults-and-why.md` (defaults 6, 10, 11 and 13); this page does not repeat it.

v1.3.0 adds no interface. The board's Today brief reads the vault through the same paths every other feature uses (`paths.vault`, `paths.doctrine`, `live/topic-map.json`), and reads the WhatsApp database only through the daemon, read only, like the rest of the daemon; it calls no facts source, second source, calendar or outlet of its own (a failed build is one `error` push, below).

## Before the probes

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the installed payload
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3                                  # bin.python
```

`HOME_DIR` is the absolute path the header of `<home>/INSTALL-PROGRESS.md` records (a `~` inside quotes is not expanded). `cfg <key>` in a probe is the helper defined in `references/pitfalls.md`, "Before the probes". Selftests use temp folders and fakes only. Probes marked **network** reach the tool itself (a CSV link, Lark, your push service); probes marked **model call** spend a little usage. A command file dropped into `$LIVE/commands/` runs inside the daemon, with the daemon's own permissions, which is the only proof that counts for anything launchd runs (`references/pitfalls.md`, pit 1).

## Choosing for the owner

Station C5 asks five questions, one at a time, and connects nothing it has not asked about. Each answer picks an implementation and says which optional station runs. Record each choice and its reason the way `references/defaults-and-why.md` describes ("How your ruling lands"), and in `<home>/INSTALL-PROGRESS.md`.

1. **Facts source (O1):** "When someone pays you, books, or gets served, where is that written down?"
2. **Second message source (O2):** "Besides WhatsApp, where do you talk with customers or your team?"
3. **Calendar destination (O3):** "Which calendar do you actually look at during the day?"
4. **Push outlet (O4):** "When something breaks, where should the alert reach you?"
5. **Nightly backup (O5):** "Which folder should get a copy of the daemon's state every night: a cloud folder or an external disk?"

| The owner uses | Pick | How it connects |
|---|---|---|
| Google Sheets | `facts.source: csv` | Publish the tab to the web as CSV and put the https link in `facts.csv.path`, or download the CSV to a fixed path |
| Airtable, Notion, GoHighLevel, Excel, a payment processor's dashboard (Stripe and the like) | `facts.source: csv` | Export a CSV to one fixed path and map its headers in `facts.csv.columns`; the facts are as fresh as the last export |
| A Lark Base | `facts.source: lark` | Table ids and field names in `facts.lark` |
| No record of payments anywhere | `facts.source: none` | Stages from `won` on are then set by hand on the board |
| Lark chats | `second_source.source: lark` | A whitelist file of chats, and the Lark id on person notes |
| Slack, Microsoft Teams, Telegram groups, Discord, email | `second_source.source: none`, or an adapter of the owner's own | No shipped implementation; see "When nothing fits yet" |
| Google Calendar, Apple Calendar, Outlook | `calendar.destination: ics` | The board opens a standard .ics file that the device imports |
| A Lark calendar | `calendar.destination: lark` | One target calendar id |
| No calendar, or no button wanted | `calendar.destination: none` | The board hides the button |
| Telegram | `notify.outlet: telegram` | A bot of its own; token and chat id in `live/telegram.json` |
| ntfy, Pushover, a Slack, Lark or Discord webhook, email through a script, anything with a command line | `notify.outlet: command` | A shell command that reads the text on stdin |
| Only the board | `notify.outlet: none` | Nothing is pushed; the digest is still written to `live/digest/` |
| iCloud Drive, Google Drive, Dropbox, an external disk, a network share | `backup.dest` | The folder path, plus `backup.require_process` when a sync app must be running |

Two things to say out loud while choosing:

- **A CSV needs a phone number on every row.** Matching is by phone. A payments export that carries only email addresses matches nobody until a phone column sits next to it (join the export with a customer list in the sheet first). When `facts.csv.path` lists several files, each one must carry every mapped header.
- **A secret inside a command or a link travels.** `live/config.json` goes into the nightly backup with only secret-looking key names and well-known token shapes redacted, so a webhook URL with a secret path or a token typed into `notify.command` can survive into the backup. Keep such secrets in a 0600 file the command reads (under `~/.config/<tool>/credentials`, which the brain's Read deny rules already cover; see `references/privacy.md`).

### When nothing fits yet

Prefer a bridge that needs no code, in this order: a scheduled export to a CSV (facts), the .ics file (calendar), a push command (alerts), a folder (backups). Only a second message source has no bridge. When the owner's tool really needs an implementation, say so, record the gap in `INSTALL-PROGRESS.md`, and offer to write a small adapter in a later session with this page as the brief (each interface below has an "Adding an implementation" part). That adapter is the owner's own file in `<live>/adapters/`, outside the payload, so an update never removes it ("Where a new implementation lives", at the end of this page).

## 1 · Facts source

### What the daemon uses it for, and when

The facts job runs once a night at `facts.at` (03:00) while `facts.enabled` is true, and whenever `$LIVE/commands/facts-now.json` appears (`{"dry_run": true}` builds without writing). It calls `wa_facts.build_facts(cfg)`, which asks the implementation for rows and does everything else itself: it groups rows into people by phone, attaches payments, finds each person's WhatsApp chat and person note, computes paid, attended and the mismatch lists, and the job writes `<live>/facts.json` atomically. Then:

- every one-to-one brain run puts up to three `facts_lines` into its prompt, and `stage_floor` lifts the judged stage (default 10);
- the job lifts one-to-one board rows in business worlds whose stage sits below the floor, and queues a person note for paying customers who have a chat but no note (at most `dossier.nightly_cap`, only while `dossier.enabled`);
- the person note writer renders `facts_section` into the note and uses the same floor in its stage check;
- the morning digest lists the mismatches and calls the implementation's `health`.

When the implementation reports any error, `build_facts` raises `FactsError`, the previous `facts.json` stays in place (an expired login never wipes yesterday's facts), and the failed job is pushed as an error.

### The contract

```
fetch(cfg) -> {"people": [person record], "payments": [payment record], "errors": [str], "warnings": [str],
               "sources": {label: rows read}, "config": {what was read}}
health(cfg) -> (ok, why)                                   optional
```

**Input.** `cfg` is the whole config as `wa_common.load_config` returns it: every default filled, `~` and placeholders resolved. The implementation reads its own block `facts.<name>`, and `facts.label` and `facts.currency` when it needs them.

**Output.**

| Key | What it must hold |
|---|---|
| `people` | One record per row about a person, from any table |
| `payments` | One record per payment |
| `errors` | One readable line per table or file that could not be read. Any entry fails the whole build and keeps the old `facts.json`. Never return a partial list without an error |
| `warnings` | Row level oddities (a value not understood, an unknown mapped field); the first 50 are kept in `facts.json` |
| `sources` | `{table label: rows read}`, printed by `--dry-run` |
| `config` | What was read: paths, table ids, the column mapping. It is stored in `facts.json` and so in the nightly backup: never a token, a password or a private link |

**Person record** (every field may be missing or null):

| Field | Shape | What `build_facts` does with it |
|---|---|---|
| `table` | text | Line label for the brain and the person note; records group per table on one line |
| `kind` | `enrollment`, `lead` or `contact` | Informational |
| `record_id` | text, unique within the source | Target of another record's `via_record` and of a payment's `person_record` |
| `name` | text | The first name seen becomes the person's name |
| `phone` | text, as written in the source | Normalized with `facts.phone_default_cc`; a record without a usable number is listed as unparsable and dropped |
| `date` | `YYYY-MM-DD` (other spellings are cleaned) | Sorts records and lines |
| `product` | text | Shown |
| `role` | text | Shown (a tier, a seat type) |
| `via_record` | a `record_id` | Someone else's purchase covers this person: `paid_via` |
| `attended` | true, false or null | Three states; any true makes the person attended, which decides the `paid_attended` and `via_attended` floors |
| `attendance` | `[[label, true/false/null]]` | Display only, for example one pair per session |
| `tag` | text | Shown in brackets |
| `flags` | `[[label, true/false/null]]` | Display only |
| `external_id` | text | Kept |
| `show` | false to hide | An identity only row: matched, never rendered |

**Payment record:**

| Field | Shape | What `build_facts` does with it |
|---|---|---|
| `table` | text | Label |
| `record_id` | text | Kept |
| `date` | `YYYY-MM-DD` | Sorts payments |
| `amount` | number, or text such as `"1,200.00"` | Cleaned to a number |
| `currency` | text | Else `facts.currency` |
| `method`, `product` | text | Shown |
| `participants` | integer | Shown as a head count |
| `person_record` | a `record_id` | The payer, when that record has a phone |
| `phone` | text | Used when `person_record` is empty or its record has no phone |
| `name` | text | Used when there is no linked record to name the payer |
| `external_id`, `remark` | text | Kept |

Return what the source says and nothing more: no WhatsApp lookups, no person note lookups, no merging by phone, no stage logic. `build_facts` does all of that once, for every implementation.

**An invented example.** A garden design business keeps one sheet of jobs. The implementation returns:

```json
{"people": [{"table": "Jobs sheet", "kind": "contact", "record_id": "Jobs sheet#7", "name": "Dana Okafor",
             "phone": "+1 202 555 0143", "date": "2031-02-20", "product": "Garden redesign", "role": null,
             "via_record": null, "attended": true, "attendance": [], "tag": null, "flags": [],
             "external_id": null, "show": true}],
 "payments": [{"table": "Jobs sheet", "record_id": "Jobs sheet#7", "date": "2031-02-20", "amount": 1200,
               "currency": "USD", "method": "Card", "product": "Garden redesign", "participants": null,
               "phone": "+1 202 555 0143", "name": "Dana Okafor", "person_record": "Jobs sheet#7",
               "external_id": null, "remark": null}],
 "errors": [], "warnings": [], "sources": {"Jobs sheet": 42}, "config": {"paths": ["~/Exports/jobs.csv"]}}
```

With `board.lang: en` the brain then sees these two lines for Dana's chat, and `stage_floor` answers `active` (paid and attended):

```
Paid: 2031-02-20 Card USD 1,200 (Garden redesign)
Jobs sheet: 2031-02-20 Garden redesign, attended ✓
```

### Shipped implementations

**`none`** (`facts.enabled: false`, the default). No job runs; the prompt's system records section says there are none.

**`csv`** (`daemon/adapters/facts_csv.py`): a local CSV file or a published `https://` CSV link.

```json
"facts": {"enabled": true, "source": "csv", "at": "03:00", "label": "Jobs sheet", "currency": "USD",
          "csv": {"path": "~/Exports/jobs.csv", "day_first": false,
                  "columns": {"phone": "Mobile", "name": "Client", "amount": "Paid (USD)", "date": "Date",
                              "product": "Job", "attended": "Done"}}}
```

- Every row becomes one person record. `record_id` comes from the mapped `record_id` column, else `<label>#<line number>`.
- A row is paid when the mapped `paid` column says so (yes, true, 1, paid, succeeded, ✓ and their opposites; any other value is a warning and counts as not paid), or, with no `paid` column, when `amount` is above 0. A paid row also becomes a payment linked to its own row.
- A paid row with no attended value is identity only (`show: false`), so the brain is not told the same purchase twice.
- Map only headers the file really has (case and surrounding spaces are ignored); `phone` is required. A mapped header missing from a file is an error for that file.
- `path` may be a list: rows are concatenated, and each file is labeled `<label> <n>`.
- A link is fetched only by the job, `--dry-run` and `--write`, never by `health` or the selftest, and is shown in logs and `facts.json` as scheme and host only. `http://` is refused. Files up to 20 MB; comma, semicolon and tab are sniffed.

**`lark`** (`daemon/adapters/facts_lark.py`): tables in a Lark Base, read with `lark-cli` as the logged in user.

```json
"facts": {"enabled": true, "source": "lark", "label": "Sales base", "currency": "USD",
          "lark": {"people_tables": [{"label": "Clients", "base": "<base token>", "table": "<table id>", "kind": "contact",
                                      "fields": {"name": "Name", "phone": "Mobile", "date": "Created", "product": "Service"}}],
                   "payment_tables": [{"label": "Payments", "base": "<base token>", "table": "<table id>",
                                       "fields": {"date": "Paid on", "amount": "Amount", "method": "Method",
                                                  "person_link": "Client"}}]}}
```

- Pages of 200 through `lark-cli base +record-list` until the table ends (at most 20000 rows a table), with `bin.lark_cli` run by `bin.node` when it is a node script.
- People tables need `base`, `table` and `fields.phone`. `attended` may be one field, a list of fields (any true), or `{"<label>": "<field>"}` pairs shown per label. `flags` is `{"<label>": "<field>"}` or `{"<label>": {"field": "<field>", "empty_is_false": true}}`. `via_link` and `person_link` are link fields. Every field name is in the adapter's docstring.
- A table that cannot be read (expired login, no permission, wrong id) is an error. `health` reads `lark-cli auth status`.

### Adding an implementation

1. Write `facts_<name>.py` with `fetch(cfg, reader=None)` (or `runner=None` for a command line tool): the injectable argument lets the selftest feed fake data, as `facts_csv.fetch(cfg, reader=...)` and `facts_lark.fetch(cfg, runner=...)` do. Add `health(cfg)` if a cheap check exists; it must not fetch data.
2. Write `selftest()` returning 0 or 1 with `wa_common._Checks("facts_<name>")`, copying `facts_csv.selftest`: fake rows through the injected reader, then check the record fields, an unreadable source as an error, and a missing mapping as an error. End the file the way `facts_csv.py` does, so `--selftest` runs it.
3. Put it in `<live>/adapters/facts_<name>.py` and set `facts.source` to `<name>` ("Where a new implementation lives"). Offered upstream instead, it also needs its registry lines: `ADAPTERS` in `daemon/wa_facts.py`, a `check(... .selftest() == 0, ...)` line in `wa_facts._selftest`, `<name>` in `ENUMS["facts.source"]` in `daemon/wa_setup.py`, and its config block with a `_help` line per key in `daemon/templates/config.example.json`.
4. Rules: Python 3.9 standard library only; no network at import; `errors` for anything unreadable; the phone as written; nothing secret in `config`.

### Verify

```sh
"$PY" "$KIT/daemon/wa_facts.py" --selftest | tail -1
"$PY" "$KIT/daemon/wa_facts.py" --config "$LIVE/config.json" --dry-run --quiet          # network for a link or lark
"$PY" "$KIT/daemon/wa_facts.py" --config "$LIVE/config.json" --write
"$PY" "$KIT/daemon/wa_facts.py" --config "$LIVE/config.json" --for-jid '<the chat jid of a paying customer>'
echo '{}' > "$LIVE/commands/facts-now.json"; sleep 60; grep 'facts: people' "$LIVE/wa_live.log" | tail -1
```

Pass: the selftest prints `wa_facts selftest PASS (<n> checks)`. The dry run prints `source csv (Jobs sheet): Jobs sheet <rows>`, a `people=<n> · paid=<n> ...` line and four `mismatch.<list>: <n>` lines, and exits 0; a line starting `facts:` is the error, with exit 1. `--for-jid` prints at most three lines, the section between `<!-- facts:begin -->` and `<!-- facts:end -->`, and `--- stage_floor: won` or `active`. After the command file, the log shows the daemon's own `facts: people <n> · paid <n>` line and `facts.json` has today's `written_at`.

## 2 · Second message source

### What the daemon uses it for, and when

Once a night at `second_source.at` (03:10), for yesterday, while `second_source.enabled` is true and `second_source.source` is not `none`; also on `$LIVE/commands/second-source-now.json` (`{"day": "YYYY-MM-DD"}` picks the day, `{"dry_run": true}` pulls without a model call). `wa_second_source.run_nightly` pulls the day through the interface, and only when someone other than the owner spoke:

1. builds one prompt from the owner copy `live/prompts/second-source-prompt.md` (the payload seed with a warning when that copy is missing) and makes one model call on `brain.model`;
2. keeps only people who really spoke that day and quotes that really are in the messages;
3. writes one news line per person into the recent block of the person note carrying that sender id under the id key (only when exactly one note carries it; a person without the key is never matched by name);
4. appends captures to the intake buffer while `intake.enabled`.

It always writes `<live>/second-source-last.json` for the digest's health line. Nothing reaches the board and nothing is sent. A night where every pullable chat failed, or the model output could not be read, is not ok and is pushed as an error.

### The contract

Two layers. **The interface** `wa_second_source.fetch_day(cfg, day=None, runner=None)` owns the whitelist: it reads it, keeps entries with `pull` true, drops `skip_chat_ids` and built-in helper chats, calls the implementation, drops chats with no messages, and adds `world_guess` and the totals. A missing, unreadable or empty whitelist is fatal, never a silent zero. It returns `day`, `source`, `chats` (each with `chat_id`, `title`, `kind`, `world_guess`, `messages`), `n_chats`, `n_msgs`, `errors`, `fatal`, `skipped`, `owner_ids`, `pullable` and `failed_chats`.

**The implementation** is a module with these names:

| Name | Required | Meaning |
|---|---|---|
| `NAME` | yes | The value of `second_source.source` |
| `ID_KEY` | yes, unless `second_source.id_key` is set | Frontmatter key on person notes holding this source's sender ids (`lark_id` for lark) |
| `fetch_day(cfg, chats, day, runner=None)` | yes | One local day of the given chats |
| `members(cfg, chats, runner=None)` | no | Who is in the chats, for `--probe-members` |
| `SENDER_ID_EXAMPLE`, `CHAT_ID_EXAMPLE` | no | Id shapes shown to the model, default `<id>` |
| `SYSTEM_NAME_PATTERNS` | no | Words in the names of helper chats that are never pulled |
| `SOURCE_NAME` | no | Fallback display name; `second_source.label` or the source name normally wins |

**Input to `fetch_day`.** `cfg` as above. `chats`: the pullable whitelist entries as the whitelist file writes them, each with `chat_id` and usually `name` or `title`, `kind`, `pull`, `world`. `day`: `YYYY-MM-DD`, a local calendar day (midnight to midnight in the Mac's zone). `runner`: `None` in production, a fake in the selftest.

**Output of `fetch_day`:**

```json
{"chats": [{"chat_id": "C07GARDEN", "title": "Garden crew", "kind": "group",
            "messages": [{"at": "2031-03-03 09:14", "sender_id": "U0THEO", "sender_name": "Theo Brandvold",
                          "is_owner": false, "text": "The planters arrive Thursday, can you confirm the soil order by Wednesday?"},
                         {"at": "2031-03-03 09:20", "sender_id": "U0SAM", "sender_name": "Sam",
                          "is_owner": true, "text": "Yes, I will send it tonight"}]}],
 "errors": ["Supplier chat: ok=false login expired"],
 "failed_chats": 1,
 "skipped": [{"chat_id": "D0BOT", "title": "Reminder bot", "why": "one to one chat with a bot"}],
 "owner_ids": ["U0SAM"]}
```

| Message field | What it must hold |
|---|---|
| `at` | Local time `YYYY-MM-DD HH:MM` (seconds may follow); the prompt shows characters 12 to 16 as the clock time |
| `sender_id` | The source's own id for the person: the same id a person note stores under the id key |
| `sender_name` | Display name |
| `is_owner` | True for the owner's own messages (they never become a news line) |
| `text` | Plain text, never empty; media as short placeholders such as `[image]` or `[voice 20s]` |

Rules: only messages people sent (no bots, no system notices), not recalled, inside the day, oldest first. Chats with no messages may be left out. One chat failing adds a line to `errors`, adds 1 to `failed_chats`, and the pull goes on; never raise. Report the owner's ids in `owner_ids`, and add an error when they cannot be found (the owner's messages would otherwise look like other people's). Chats left out on purpose go to `skipped` with a reason, and so do chats the source can never read (an encrypted chat, for instance): a chat that would fail every night is a known boundary, not an error, and counting it in `errors` and `failed_chats` would hide the real failures.

`members(cfg, chats, runner=None)` returns `{"users": {id: {"name", "chats": [titles]}}, "bots": {id: {"name", "app_id", "chats"}}, "errors": [str]}`. It only feeds `--probe-members`, which prints candidates for a person to map; nothing is written.

**The whitelist file** (`second_source.lark.whitelist`, default `<live>/second-source-whitelist.json`):

```json
{"rule": "work chats only", "decided_at": "2031-02-28",
 "chats": [{"chat_id": "C07GARDEN", "name": "Garden crew", "kind": "group", "pull": true, "world": "work"},
           {"chat_id": "D0BOT", "name": "Reminder bot", "kind": "direct", "pull": false}]}
```

`pull` accepts true or the strings `true`, `1`, `yes`. `world` is a world key; it is only a guess on intake entries and loses to `second_source.world_by_chat`, and beats `second_source.world_keywords`.

### Shipped implementations

**`none`** (the default): no job runs.

**`lark`** (`daemon/adapters/second_source_lark.py`):

```json
"second_source": {"enabled": true, "source": "lark", "at": "03:10",
                  "lark": {"whitelist": "<live>/second-source-whitelist.json"}}
```

- One `lark-cli im +chat-messages-list ... --order asc --page-all --as user` call per chat for the local day (`second_source.lark.page_limit` 50, `timeout_seconds` 120).
- Owner ids come from `second_source.lark.owner_ids`, else `lark-cli auth status`.
- One-to-one chats whose other side is a bot are skipped; voice messages carry only a duration placeholder; long texts are cut at 2000 characters.
- An encrypted chat (Lark answers API error `231203`; `lark-cli` may print that answer on stderr, which is read too) is listed in `skipped` with `why` `encrypted chat, the Lark API cannot read it`: never an error and never a failed chat. Take it off the whitelist (`pull` false) to stop seeing it.
- Lark ids differ per app: the `lark_id` values on person notes must come from the same `lark-cli` login that pulls (`--probe-members` lists them).

### Adding an implementation

1. Write `second_source_<name>.py` with the names in the table. Copy the plumbing of `second_source_lark`: a `default_runner(cfg)` built on `wa_common.subprocess_env`, a `runner` argument on every function that reaches the tool, and output parsing that tolerates progress text before JSON.
2. Write `selftest_fixture()` (a whitelist, a day of invented messages, a fake runner) and `_selftest(check=None, tmp=None)` where `check(name, cond)`, copying `second_source_lark._selftest`: owner flags, dropped bots and recalls, one failing chat as an error, the exact message keys. End the file the way `second_source_lark.py` does, so `--selftest` runs it.
3. Put it in `<live>/adapters/second_source_<name>.py` and set `second_source.source` to `<name>` ("Where a new implementation lives"). Keep the whitelist at the default `<live>/second-source-whitelist.json`, or name another file in `second_source.<name>.whitelist`; the nightly backup copies whichever file that key names. Offered upstream instead, it also needs its registry lines: `SOURCES` in `daemon/wa_second_source.py`, a call to its `_selftest` in `wa_second_source._selftest` next to the lark one (`L._selftest(lambda name, cond: check(cond, name), tmp)`), `<name>` in `ENUMS["second_source.source"]` in `daemon/wa_setup.py`, and its keys in `config.example.json`.
4. On the vault side: the id key joins the doctrine's `person` family (`references/vault-proposals.md`, proposal a), and `second_source.id_key` names it when it differs from `ID_KEY`.

### Verify

```sh
"$PY" "$KIT/daemon/wa_second_source.py" --selftest | tail -1
"$PY" "$KIT/daemon/wa_second_source.py" --config "$LIVE/config.json" --dry-run --day 2031-03-03   # network
"$PY" "$KIT/daemon/wa_second_source.py" --config "$LIVE/config.json" --probe-members                # network
echo '{}' > "$LIVE/commands/second-source-now.json"                                                # model call
cat "$LIVE/second-source-last.json"
```

Pass: `selftest: <n>/<n> passed`. The dry run prints `whitelist <n> · pullable <n> · excluded <n>`, then `day <day> · <c> chats <m> messages · owner id <id>` with a real id (not `?`), a line per chat with its speakers and world guess, and `errors 0`; count one chat's messages of that day in the tool itself and compare. After the command file (or a night), `second-source-last.json` has `"ok": true`, the right `day`, and `claude_called` true when someone other than you spoke.

## 3 · Calendar destination

### What the daemon uses it for, and when

Only when the owner presses "Add to calendar" on the board. A judgment may carry a `calendar_hint` (`{title, date, time, duration_min, place, who}`), which `wa_calendar.validate_hint` reads tolerantly (`2031-3-6`, `3pm`, `"90 min"`) and refuses when there is no year or no title. While `calendar.destination` is not `none`, the board shows the button, prefills a form from the hint, and posts it to `POST /api/calendar`. The daemon then:

1. checks the form again (`validate_hint`);
2. builds the description with `compose_description(note, conversation name, phone)`;
3. derives an idempotency key from the chat, the date and the time as typed (a uuid5), so a double press creates one event;
4. calls `wa_calendar.create_event(...)` and stores `calendar_done`, so the button stays off until the hint's date or time changes (`hint_key`). A second Create for an appointment already created (the chat's hint, or, when the chat carries no hint any more, the same date and time in the form) answers `{"ok": true, "url": "<the first url>", "already": true}` and logs `板子: 加进日历 ... → already`, without calling the destination again.

No model is involved, and a daemon started with `--dry-run` creates nothing.

### The contract

**The interface** `wa_calendar.create_event(cfg, title, date, time, duration_min=60, place="", note="", dry_run=False, runner=None, idempotency_key=None)` returns `{"ok", "url", "event_id", "error"}` and never raises. It validates (title one line of at most 80, date `YYYY-MM-DD`, duration clamped to 15 to 600 minutes), refuses a typed time it cannot read (never a silent all day event), builds the spec with `event_spec`, and calls the implementation.

**The implementation** is a module with `NAME`, `create(cfg, spec, dry_run=False, runner=None) -> dict`, `probe(cfg) -> exit code` (read only: print what the owner needs to pick a target), and `_selftest(check=None, tmp=None)`.

**The spec `create` receives:**

| Field | What it holds |
|---|---|
| `title` | One line, at most 80 characters |
| `date` | `YYYY-MM-DD` |
| `time` | `HH:MM`, or `""` for an all day event |
| `duration_min` | 15 to 600 |
| `place` | At most 120 characters, may be `""` |
| `who` | `""` (the board form does not send it) |
| `description` | Finished plain text, at most 2000: the owner's note, a blank line, `WhatsApp: <conversation name>`, and a `wa.me` link for one-to-one chats |
| `all_day` | True when `time` is empty |
| `tz_name` | The owner's IANA zone (`calendar.timezone`, else `TZ`, else the zone macOS uses), or `""` |
| `start`, `end` | Timezone aware datetimes in that zone; `None` for all day |
| `key` | A file and URL safe form of the idempotency key (letters, digits, dash, underscore, at most 64); never empty |
| `idempotency_key` | The caller's key, or `None` |
| `reminder_min` | 5 |

An invented spec, with the datetimes written as ISO text:

```json
{"title": "Site visit at the shop", "date": "2031-03-06", "time": "10:00", "duration_min": 90,
 "place": "Brandvold Hardware, back lot", "who": "",
 "description": "Bring the planter sketches\n\nWhatsApp: Theo Brandvold\nhttps://wa.me/12025550178",
 "all_day": false, "tz_name": "America/New_York",
 "start": "2031-03-06T10:00:00-05:00", "end": "2031-03-06T11:30:00-05:00",
 "key": "873e6e25-4841-5f10-b9e7-725e9a53f595", "idempotency_key": "873e6e25-4841-5f10-b9e7-725e9a53f595",
 "reminder_min": 5}
```

**The result `create` returns:**

| Key | What it must hold |
|---|---|
| `ok` | True only when the event exists (or would, in a dry run) |
| `url` | What the board opens: a link to the event, a path the daemon serves, or `""` |
| `event_id` | The destination's id; never empty when `ok` is true, unless `dry_run` |
| `error` | One readable line when `ok` is false |

Extra keys are allowed (`already` and `path` from `ics`, `cmd` from `lark`, `preview` in a dry run). The `ics` answer to the spec above:

```json
{"ok": true, "url": "/calendar/873e6e25-4841-5f10-b9e7-725e9a53f595.ics",
 "event_id": "873e6e25-4841-5f10-b9e7-725e9a53f595", "error": "", "already": false}
```

Rules: the same `key` gives the same single event; timed events use `tz_name` with a `reminder_min` alert; all day events sit on `date`; no video meeting and no attendees; a dry run creates nothing; never raise.

### Shipped implementations

**`none`** (the default): the board hides the button; `create_event` answers `calendar destination is none`.

**`ics`** (`daemon/adapters/calendar_ics.py`):

```json
"calendar": {"destination": "ics", "timezone": "America/New_York"}
```

- Writes `<live>/calendar/<key>.ics` once (file 0600, folder 0700); the first writer wins, so a second press returns the same url with `already: true`.
- The daemon serves it at `GET /calendar/<key>.ics` as `text/calendar`. It is standard iCalendar (RFC 5545): Apple Calendar, Google Calendar and Outlook import it. The import happens on the device that opens the link, and each device asks differently, so test one event on the phone you use with the board.
- Timed events carry `TZID` and a `VTIMEZONE` when the zone name is known, UTC times otherwise; all day events use `VALUE=DATE` with the next day as the end; timed events get a 5 minute display alarm.
- `live/calendar/` is not in the nightly backup (it is on the never list).

**`lark`** (`daemon/adapters/calendar_lark.py`):

```json
"calendar": {"destination": "lark", "lark": {"calendar_id": "<calendar id>"}}
```

- `lark-cli calendar events create --as user` into `calendar.lark.calendar_id` only; an empty id refuses without calling `lark-cli`.
- Passes `--idempotency-key`, so Lark itself creates at most one event per key; `url` is the event's app link.
- `wa_calendar.py --probe` lists the calendars and suggests the single writable primary one, writing nothing.

### Adding an implementation

1. Write `calendar_<name>.py` with `NAME`, `create`, `probe` and `_selftest`, copying `calendar_lark`: a runner argument for the external call, parsing that tolerates progress text, every failure turned into `error`.
2. In `_selftest(check=None, tmp=None)` (where `check(name, cond)`), build a real config with `wa_calendar.selftest_cfg(base, {"destination": "<name>", ...})` and cover: a timed event with the right zone and duration, an all day event, the idempotency key passed on, a dry run that creates nothing, a missing target refused without an external call, a raising runner turned into an error. End the file the way `calendar_lark.py` does, so `--selftest` runs it.
3. Put it in `<live>/adapters/calendar_<name>.py` and set `calendar.destination` to `<name>` ("Where a new implementation lives"). The board's calendar form names the destination by that name, and `wa_calendar.py --dry-run` exercises it; `wa_calendar.py --probe` accepts only the shipped destinations. Offered upstream instead, it also needs its registry lines: `DESTINATIONS` and `_IMPLEMENTATIONS` in `daemon/wa_calendar.py`, `implementation("<name>")._selftest(check, tmp)` in `wa_calendar._selftest`, `<name>` in `ENUMS["calendar.destination"]` in `daemon/wa_setup.py`, its keys in `config.example.json`, and optionally a board label in `_CAL_LABELS` in `daemon/wa_live.py`.

### Verify

```sh
"$PY" "$KIT/daemon/wa_calendar.py" --selftest | tail -1
"$PY" "$KIT/daemon/wa_calendar.py" --config "$LIVE/config.json" --probe      # network for lark
"$PY" "$KIT/daemon/wa_calendar.py" --config "$LIVE/config.json" --dry-run    # network for lark
```

Pass: `selftest: <n>/<n> passed`. The probe prints `calendar.destination: <name> · owner timezone: <zone>`, then for `ics` the folder and the count of files, and for `lark` the calendars with `current` next to the configured one. The dry run prints `[timed] ok=True error=none` and `[all day] ok=True error=none`, each with its preview, and exits 0. Then on the board: press "Add to calendar" on one test conversation, open the link on your phone, check the date, the time and the zone in your calendar app, and delete the test event.

## 4 · Push outlet

### What the daemon uses it for, and when

| Kind | Sent when | Sent by |
|---|---|---|
| `error` | A brain run failed (at most once per conversation per 30 minutes), a person note run failed, a scheduled job failed (once per job per hour), the tool cage broke, the WhatsApp database has not changed for `doorbell.stale_alert_hours`, the watermark was reset, backfill failed, a Today brief build failed (at most once every six hours), `commands/notify-test.json` | The daemon |
| `needs_me` | A judgment that needs the owner | The daemon |
| `info` | Any other judgment | The daemon |
| `digest` | The morning digest (short form); the evening intake list, only from an evening job an earlier release installed and the owner kept | The daemon's digest job and `wa_digest.py --send`; a kept evening job through `wa_common.py --notify --kind digest` |

`notify.mode` decides which kinds go out: `off` nothing · `errors` `error` and `digest` · `needs_me` adds `needs_me` · `all` everything. An unknown mode counts as `errors`. A daemon started with `--dry-run` pushes nothing.

### The contract

```
wa_common.notify_wanted(cfg, kind) -> bool
wa_common.notify_send(cfg, text, parse_mode=None, key=None, min_gap=0, kind=None) -> bool     True when delivered
wa_common.notify_deliver(cfg, text, parse_mode=None) -> (ok, why)                            the outlet dispatch
wa_common.py --notify [--config P] [--html] [--kind error|needs_me|digest|info]              text on stdin
```

There is one outlet for the daemon and the command line tools (and an evening job kept from an earlier release). What an outlet receives: `text`, cut to 3800 characters with an ellipsis, several lines, emoji allowed; `parse_mode` `None` or `"HTML"` (Telegram HTML: `<b>`, `<i>`, escaped content). What it returns: `(ok, why)`, where `ok` is true only when the message was accepted for delivery and `why` is a short reason that never contains a secret. It never raises.

Dedupe: with `key` and `min_gap`, the same key goes out at most once per `min_gap` seconds; only a successful send starts the gap, and a send already in flight with the same key suppresses a second one. The memory is in process only, so a restart clears it.

The command line form exits 0 when delivered, 1 when not sent (the mode blocks that kind, or the outlet failed), 2 when it cannot start (config missing, empty stdin).

### Shipped implementations

**`none`** (the default): logs the first line, delivers nothing, returns `(False, "notify.outlet is none")`.

**`telegram`:**

```json
"notify": {"outlet": "telegram", "mode": "errors", "telegram_file": "<live>/telegram.json"}
```

`live/telegram.json` (0600) holds `{"bot_token": "...", "chat_id": "..."}` and is read at every push, so editing it needs no restart. A token still holding the placeholder word `PASTE` counts as not set. An HTML send Telegram refuses is retried once as plain text. When Python's certificate chain is broken, the send falls back to `/usr/bin/curl` with the URL passed on stdin, never on the command line. A new bot can only message you after you press Start in it once.

**`command`:**

```json
"notify": {"outlet": "command", "mode": "errors",
           "command": "/usr/bin/curl -fsS -m 20 -H 'Title: WhatsApp Live' --data-binary @- https://ntfy.example.net/<your-private-topic>"}
```

- Runs `/bin/sh -c "<notify.command>"` with the text on stdin as UTF-8 plain text ending in a newline (tags removed and entities decoded when the caller sent HTML), so pushes appended to a file land one per line.
- Environment: the daemon's children's (`PATH` from `bin.path_env`, `HOME`, `LANG`, `WA_LIVE_CONFIG`); use absolute paths for anything outside that PATH.
- Exit 0 means delivered; any other exit is a failure whose reason is the last 300 characters of stderr or stdout; 30 second timeout. With curl, keep `-f` so an HTTP error exits non zero.
- A test outlet that writes to a file: `"command": "cat >> \"<home>/push-test.txt\""` (the config loader fills in `<home>`, as in every config string).
- Anything carrying a secret: a script of your own that reads the secret from a 0600 file, for example `"command": "\"<home>/push.sh\""` (see "A secret inside a command or a link travels" above). What leaves the Mac through each outlet is listed in `references/privacy.md`.

### Adding an outlet

Almost every service is reachable through `command`, which needs no code. A native outlet means changing `daemon/wa_common.py`: a function `_<name>(cfg, text, parse_mode) -> (ok, why)` that never raises; a branch in `notify_deliver`; the name in `NOTIFY_OUTLETS` and `_SOURCE_NAMES`; the name in `ENUMS["notify.outlet"]` in `daemon/wa_setup.py`; cases in `wa_common._selftest` (an HTTP outlet uses the `_HTTP_POST` hook, as the Telegram cases do, so no request leaves the selftest). A new secret file belongs in `live/`, 0600, and needs a Read deny rule in `daemon/templates/claude-settings.json` and a place on the never list in `wa_backup.py`.

### Verify

```sh
"$PY" "$KIT/daemon/wa_common.py" --selftest | tail -1
echo "WhatsApp Live test push" | "$PY" "$KIT/daemon/wa_common.py" --config "$LIVE/config.json" --notify --kind error   # network
echo '{}' > "$LIVE/commands/notify-test.json"; sleep 10; grep 'notify-test:' "$LIVE/wa_live.log" | tail -1              # network
```

Pass: `wa_common selftest PASS (<n> checks)`. The test push prints `notify: delivered`, exits 0, and the message arrives. With `notify.mode: off` the same line prints `notify: not sent (notify.mode is off)` and exits 1. The command file makes the daemon itself push a test line through the same outlet, with launchd's environment: the log gets a `notify-test:` line and the message arrives.

## 5 · Nightly backup destination

### What the daemon uses it for, and when

Once a night at `backup.at` (03:30) while `backup.enabled`, and on `$LIVE/commands/backup-now.json` (`{"dry_run": true}` plans only; `{"dest": "<folder>"}` overrides the destination once). The job calls `wa_backup.run_backup(cfg)`; a result that is not ok is pushed as an error. The morning digest reads `backup-last.json` and marks the backup stale when that result is dated before the day the digest is sent.

### The contract

The destination is a folder. There is no registry: whatever the folder is (a sync app's folder, a mounted disk, a network share) is the implementation.

```
wa_backup.run_backup(cfg, dest=None, dry_run=False, now=None, require_process=None, process_check=None, out=print)
    -> {"at", "ok", "dest", "bytes", "files", "error", "dry_run"}
```

1. `backup.dest` (or `dest`) empty: not ok, `backup.dest is not set`.
2. `backup.require_process` set and not running: not ok. A plain name is matched exactly, then as the last part of an executable path; a value with a slash is matched against full command lines.
3. The destination's **parent** must exist; the destination folder itself is created. An unmounted disk or a stopped sync app fails instead of growing a local copy at that path.
4. Copies into `<dest>/<YYYY-MM-DD>/` (folder 0700, files 0600), reading every file back:

| Copied | How |
|---|---|
| `state.sqlite` | SQLite online backup; `PRAGMA integrity_check` on the copy must say ok |
| `config.json` | Secret-looking values redacted (key names containing token, secret, password, credential, api key, auth, cookie, webhook and the like; bot tokens, bearer tokens and passwords inside URLs) |
| `summaries.md`, `facts.json`, `voice-pairs.jsonl`, `second-source-whitelist.json` (the file `second_source.<source>.whitelist` names), `topic-map.json`, `seeds.json`, `acceptance.json` | Copied when present |
| `prompts/`, `digest/`, `dossier-backups/`, `adapters/` | Copied, without `.log` files |

A known item that does not exist yet is planned as `[later]` (`not there yet, copied once it exists`), never silently left out: on a fresh install `voice-pairs.jsonl`, until the owner's first reply to a drafted chat writes it. `adapters/` is listed only when it exists, and the whitelist only while a second source is chosen.

Never copied: `telegram.json`, `runs/`, `commands/`, `variants/`, `dossier-cli/`, `calendar/`, `*.log`, `claude-settings.json` (`wa_setup.py init` renders it again), `backup-last.json`, `second-source-last.json`, `people-write.lock` (the empty lock the person note writer takes).

5. Keeps the newest `backup.keep` dated folders directly under the destination; never deletes today's, never touches other folders.
6. Writes `<live>/backup-last.json` with exactly seven keys, except in a dry run, which writes nothing at all:

```json
{"at": "2031-03-04T03:30:02", "ok": true, "dest": "~/Library/CloudStorage/Dropbox/WhatsApp-Live-backup/2031-03-04",
 "bytes": 5382011, "files": 23, "error": null, "dry_run": false}
```

(The real file holds the absolute path.)

### Destinations for common tools

```json
"backup": {"enabled": true, "at": "03:30", "keep": 7,
           "dest": "~/Library/CloudStorage/Dropbox/WhatsApp-Live-backup", "require_process": "Dropbox"}
```

| Tool | `backup.dest` | `backup.require_process` |
|---|---|---|
| iCloud Drive | `~/Library/Mobile Documents/com~apple~CloudDocs/WhatsApp-Live-backup` | Empty; there is no separate app to check, so confirm in Finder that the folder syncs |
| Google Drive for desktop | `~/Library/CloudStorage/GoogleDrive-<account>/My Drive/WhatsApp-Live-backup` | The app's process name as Activity Monitor shows it (`Google Drive`) |
| Dropbox | `~/Library/CloudStorage/Dropbox/WhatsApp-Live-backup` | `Dropbox` |
| External disk | `/Volumes/<disk name>/WhatsApp-Live-backup` | Empty; an unmounted disk fails because `/Volumes/<disk name>` is missing |

Use a folder only you can see: the backup is not encrypted and holds names, numbers, summaries and drafts (`references/privacy.md`). Cloud folders sit inside macOS privacy protected locations; the job runs inside the daemon, whose node parent holds Full Disk Access, so a terminal run of `wa_backup.py --run` proves only the terminal's permissions. The proof is `backup-now.json`.

### Adding a destination

- A destination that is not a folder (object storage, a remote server): make it a folder (the provider's sync app, a mount), or add a small job of your own that runs after `backup.at`, reads `backup-last.json`, and copies `dest` only when `ok` is true and `dry_run` is false. Register that job as its own resident job (the six cells of the resident-workers playbook); it lives outside the payload and survives updates.
- A new file in `live/` that must be restored: add it to `EXTRA_FILES` or `COPY_DIRS` in `daemon/wa_backup.py`, or to its `NEVER` list, in the same change that creates the file (`references/pitfalls.md`, pit 12).

### Verify

```sh
"$PY" "$KIT/daemon/wa_backup.py" --selftest | tail -1
"$PY" "$KIT/daemon/wa_backup.py" --config "$LIVE/config.json" --dry-run
echo '{}' > "$LIVE/commands/backup-now.json"; sleep 60; cat "$LIVE/backup-last.json"
ls "$(cfg backup.dest)/$(date +%F)"
```

Pass: `wa_backup selftest PASS (<n> checks)`. The dry run lists `voice-pairs.jsonl` among the items (or as a `[later]` line until the file exists), prints `never copied: telegram.json · runs/ · commands/ · variants/ · dossier-cli/ · calendar/ · *.log · claude-settings.json (rendered by wa_setup.py init) · backup-last.json · second-source-last.json · people-write.lock (an empty lock file)` and `(dry run: nothing written, backup-last.json not written)`, ends with a JSON line holding `"dry_run": true`, exits 0, and does not warn that the destination's parent folder is missing. After the command file, `backup-last.json` says `"ok": true` with today's `at`, and today's folder holds `state.sqlite` and `config.json` and no `telegram.json`.

## Where a new implementation lives

Everything under the payload (`~/.claude/skills/breakthrough-whatsapp-live/`) is replaced whole by `npx skills update`, so an adapter file added to `daemon/adapters/` disappears with the next update. The owner's own adapters live outside it, in `<live>/adapters/`:

- **Prefer a bridge that needs no code:** a CSV export, the .ics file, a push command, a folder. They live in config and survive every update.
- **When an adapter is needed**, write it as `<live>/adapters/facts_<name>.py`, `second_source_<name>.py` or `calendar_<name>.py`, and set `facts.source`, `second_source.source` or `calendar.destination` to `<name>`. `wa_facts.load_adapter`, `wa_second_source.implementation` and `wa_calendar.implementation` import that file first, at every use, through `adapters.owner_module` (a file named like a shipped implementation replaces it). `wa_setup.py check-config` accepts `<name>` because the file exists, and the nightly backup copies `<live>/adapters/`. An update touches none of it; after one, run the file's own `--selftest` once (`"$PY" "$LIVE/adapters/<file>" --selftest`), since the kit code around it changed.
- **Its imports:** inside the daemon the payload's `daemon/` folder is already on the path; a standalone `--selftest` run does not have it, so the file adds `~/.claude/skills/breakthrough-whatsapp-live/daemon` to `sys.path` before `import wa_common` or `import adapters` (the shipped adapters add their own parent folder, which for a file in `<live>/adapters/` is the wrong one).
- **Its settings:** `check-config` reports a config block the example does not list (such as `facts.<name>`) as an unknown key. Keep the adapter's settings in a JSON file beside it, for example `<live>/adapters/facts_<name>.json`, read with `wa_common.read_json`, or accept that one reported key. Never a token in that file: the folder is copied into the nightly backup as it is, without redaction.
- **Or offer it upstream**, with the registry lines each "Adding an implementation" part lists, so it ships with the kit.

Whoever writes one follows the kit's code rules: Python 3.9 standard library only (the daemon runs on `/usr/bin/python3`), no network at import, every external call behind an injectable runner, a selftest that uses fakes only, and no real names, numbers or ids anywhere in the file.
