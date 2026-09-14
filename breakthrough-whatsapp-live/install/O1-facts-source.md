# Station O1 · Facts source

**Should your payment record, not the conversation, decide who reached `won` and beyond?** What it costs: the nightly facts job calls no model. It reads the source you name every night at `facts.at` (over the network when that source is a link or Lark), writes `live/facts.json` (names, phone numbers and payments, 0600, copied into the nightly backup), lifts stages on the board, puts up to three facts lines into each paying person's judgment prompt and a system records section into their person note, and, only while `dossier.enabled` is true, queues a person note run on `dossier.model` for every paying customer who has a chat but no note, up to `dossier.nightly_cap` (8) each time the job runs. The first real run (step 6) starts those runs at once, in the daytime, one after another on opus by default, minutes each. It reaches only the file, the link or the Lark tables written into config.

The tool was chosen at `install/C5-your-tools.md`, question 1. The contract, the shipped implementations and their fields: `references/interfaces.md`, "1 · Facts source". Why the floor exists: `references/defaults-and-why.md`, "10 · The payment record sets the stage floor".

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

One question at a time. Read default 10 before question 4.

1. **Where exactly is the record?** A CSV file at one fixed path, a published `https://` CSV link (Google Sheets: the tab published to the web as CSV), or a Lark Base (the base token and one table id per table). Say the cost of a link out loud: anyone who holds a published link can read the sheet, and the link sits in `live/config.json`, which the nightly backup copies.
2. **Which column holds the phone number?** Read the header row together (step 1). Matching is by phone, so `phone` is required; then `name`, `paid` (or `amount` above 0 counts as paid), `amount`, `currency`, `method`, `date`, `product`, `attended` and `record_id`, each only when the file really has that column. A payments export without phone numbers matches nobody until a phone column is joined to it.
3. **Which country code do local numbers belong to?** Digits only, for numbers written with a leading 0 (`facts.phone_default_cc`). Optionally the number shapes that are valid in your country (`facts.phone_valid`, for example `60:11-12`); numbers outside them are listed as mismatches and still match.
4. **What do `won` and `active` mean in your business?** (default 10) The floor maps three cases to stage keys: `paid` (their own payment, default `won`), `paid_attended` (paid and attended or served, default `active`) and `via_attended` (someone else paid and they attended, default `active`). A business that records no attendance only ever reaches the `won` floor. Someone saying "paid" in a chat never counts.
5. **What should the brain call this source, and what currency do bare amounts carry?** `facts.label` (for example `Sales sheet`), `facts.currency` (for example `USD`), and for a CSV whether dates are written day first (`facts.csv.day_first`).
6. **When should it run?** `facts.at`, default `03:00`: before the backup at `03:30` and the digest at `08:00`, which both read its result.

## Do

### 1 · Read the header, not the rows

For a local CSV, print only its first line and read it with the owner:

```sh
head -1 "<the CSV path>"
```

For a link, the owner opens the sheet and reads the column names out. For Lark, the owner opens each table and names its fields. Nothing else of the file is printed into this session.

### 2 · Write the facts block, still switched off

With the config edit ritual (`install/00-start-here.md`), write `facts.source`, `facts.label`, `facts.currency`, `facts.phone_default_cc`, `facts.phone_valid` when given, `facts.floor`, `facts.at`, and the implementation's block, in the shape `references/interfaces.md` shows under "Shipped implementations": `facts.csv` (`path`, `columns`, `day_first`) or `facts.lark` (`people_tables`, `payment_tables`). Leave `facts.enabled` at `false`: the command line reads the source without it, and the daemon does not act on it yet.

Writes: a config backup and `live/config.json`.

### 3 · Read the source from the terminal

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_facts.py" --config "$LIVE/config.json" --dry-run
```

It reads the source now and writes nothing. The first lines are counts (`source ...`, `people=...`); under each `mismatch.<list>: <n>` line it prints the rows of that list, with names and numbers: read them with the owner and never copy them into the progress file. A line starting `facts:` is the reason the source could not be read, with exit 1. Fix the mapping and run it again until the counts look like the owner's business.

### 4 · One paying customer end to end

Pick a customer who paid and has a WhatsApp chat (the jid from the board, or `apij` in `references/feature-checklist.md`):

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_facts.py" --config "$LIVE/config.json" --for-jid '<their jid>'
```

The owner checks the lines against the source (dates, amounts, product) and agrees with the floor printed last. Writes nothing.

### 5 · Switch it on

With the config edit ritual, set `facts.enabled` to `true`, then run `check-config` and restart:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
LABEL="<launchd.label from the progress file header>"
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
launchctl kickstart -k gui/$(id -u)/$LABEL
```

`check-config` refuses a missing CSV file (`file not found`), a mapping without the phone column, and a Lark block with no tables. A restart after `facts.at` marks tonight's job as run for today; step 6 runs it now instead.

### 6 · Run it inside the daemon

A link or Lark is read with the daemon's own environment under launchd, which is the only proof that counts for the nightly job. First without writing, then for real:

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{"dry_run": true}' > "$LIVE/commands/facts-now.json"
```

⚠️ The real run starts person note runs now, not tonight. While `dossier.enabled` is true (station C4 switched it on), it queues everyone the dry run in step 3 counted under `mismatch.paid_chat_no_note_queued`, up to `dossier.nightly_cap` (8), and the note writer starts them at once, one after another, on `dossier.model` (opus by default, minutes each), in the daytime. On an established business this is the heaviest moment of the install: say the number and the cost to the owner before the second file. To spread them over nights, lower `dossier.nightly_cap` first (config edit ritual, restart; the rest wait with `facts: 排队开档到上限 <n>, 其余 <n> 人明晚再排`), or switch facts on before `dossier.enabled` is true (the real run then queues nobody, and each night's facts job queues up to the cap once notes are on).

```sh
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
echo '{}' > "$LIVE/commands/facts-now.json"
```

Wait for `排班 facts 完成` in `live/wa_live.log` between the two. Writes: `live/facts.json` (0600); stage lifts on judged one-to-one rows in business worlds whose stage sits below the floor; `dossier: 排队 <name> (开新档: 事实表, 付过钱有对话没档)` queue entries while `dossier.enabled` is true, and the note runs they start right away.

### 7 · The registry

Under the daemon's entry in the registry, the O1 block from `references/vault-proposals.md`, proposal f, "Additions when an optional station connects": reads the facts source, writes `live/facts.json`, stage raises and the note queue, switch `facts.enabled`, proof `facts.json` `written_at` is today.

## Verify

Run each probe with the station's opening block in front of it. Every check must pass. The rows in brackets are the matching lines of `references/feature-checklist.md`.

1. **The facts code runs on this Mac.** Pass: `wa_facts selftest PASS (<n> checks)`. (K218)
   ```sh
   "$PY" "$KIT/daemon/wa_facts.py" --selftest | tail -1
   ```
2. **Config is clean** (after step 5). Pass: `check-config: 0 error(s)`.
   ```sh
   "$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
   ```
3. **The source reads, and the numbers are the owner's business** (step 3). Pass: `source <source> (<label>): <table> <rows>` with rows above 0; `people=<n> · paid=<n> (own payment <n>, partner purchase only <n>) · with chat=<n> · with note=<n> · payments assigned <n> (unassigned <n>)` with `unassigned` 0 or equal to the payment rows that carry neither a link nor a phone; four `mismatch.` lines; the owner recognizes the numbers. (K210, K213, K215)
   ```sh
   "$PY" "$KIT/daemon/wa_facts.py" --config "$LIVE/config.json" --dry-run --quiet
   ```
4. **One customer's lines and floor are right** (step 4). Pass: `--- facts_lines(<jid>): <n>` with lines the owner confirms, the section between `<!-- facts:begin -->` and `<!-- facts:end -->`, and `--- stage_floor: won` or `active` as the owner expects. (K211, K214)
5. **The daemon ran it** (after step 6). Pass: `facts-now 指令: {'dry_run': True}`, then a `facts: dry-run, 不写 facts.json、不排开档、不抬 stage` line, then `facts: people <n> · paid <n> · 对不上 <n>/<n>/<n> · 排队开档 <n>` from the real run, and no `排班 facts 失败`. (K219)
   ```sh
   grep -e "facts-now 指令" -e "facts: " -e "排班 facts" "$LIVE/wa_live.log" | tail -5
   ```
6. **`facts.json` keeps its contract** (after step 6). Pass: today's `written_at`, the four mismatch lists, `-rw-------`. (K216)
   ```sh
   "$PY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["written_at"], sorted(d["mismatch"]))' "$LIVE/facts.json"
   stat -f %Sp "$LIVE/facts.json"
   ```
7. **Queue and lifts are logged** (after step 6). Pass: zero or more `dossier: 排队 <name> (开新档: 事实表, 付过钱有对话没档)` lines, never more than `dossier.nightly_cap` from one run; `facts: 付款地板抬了 <n> 行` when a board row was lifted. The owner agrees with each queued name. (K220, K221)
   ```sh
   grep -e "事实表, 付过钱有对话没档" -e "付款地板抬了" -e "排队开档到上限" "$LIVE/wa_live.log" | tail -10
   ```
8. **The floor is one function.** Pass: one line, in `daemon/wa_facts.py`. (K222)
   ```sh
   grep -rn "def stage_floor" "$KIT/daemon" "$KIT/skills"
   ```

After the first night: the digest's health block names the source with a ✓ and the time (station O6), and `facts.json` carries that night's `written_at`.

## Record

Under `### O1 facts source` in `<home>/INSTALL-PROGRESS.md`:

- `asked:` the source kind and label (a local path, or "published link on `<host>`", never the full link), which standard fields were mapped, `phone_default_cc`, the floor mapping, `facts.at`;
- `did:` the dry run's `people=` line (counts only) and the four mismatch counts;
- `did:` the customer checked in step 4, by jid, and the floor printed;
- `verify:` the daemon's `facts: people` line and `written_at`;
- the registry block added;
- `did:` how many note runs the real run started, and `dossier.nightly_cap` at that moment;
- `ruled: default 10`, one decision note: where the payment record lives and what `won` and `active` mean (it supersedes the note station C5 landed).

Tick `- [x] O1 facts source (<implementation>)` only when every check passes. Next: the next `- [ ]` line under `## Stations`.
