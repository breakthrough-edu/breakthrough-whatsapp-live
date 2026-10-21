# Vault proposals

WhatsApp Live writes into your second-brain vault in a few places, and your vault's own law decides whether and where. This page holds every change the kit needs your vault to make, written as proposals: your own Claude Code session reads them, adapts them to your vault, shows you each one whole, and applies it only on your yes. Nothing here is ever copied over a file you already have.

## Rules for the session applying these

- **Read before proposing.** Read the owner's `99_Meta/structure-doctrine.md` (sections 0, 1, 2 and 8, and the revision log), `02_Command-Base/Home.md`, and the door (`_*-Guide.md`) of every room you will touch. The paths on this page are the my-second-brain defaults; wherever the owner's doctrine says something else, the doctrine wins.
- **One proposal, one list, one yes.** Show the list of files the proposal touches with the exact change to each, and ask for one yes for the list. If the owner changes a line, change the list and show it again.
- **A change to section 8 is an amendment.** Carry proposal a through the vault guardian skill that ships with my-second-brain (its name ends in `-vault-guardian`; it answers to "add a new type of note"), which moves the template, `Home.md`, the doors and the revision log with it and then proves the frontmatter guard still reads the law. By hand is legal too, with the same discipline.
- **Write notes with the Write tool**, never through the shell, so the frontmatter guard can judge each one.
- **Log every filing**: one line in `99_Meta/filing-log.md`.
- **Fill in real values.** Placeholders below (`<vault>`, `<people_dir>`, `<home>`, `<live>`, `<KIT>`, `<label>`, `<intake_label>`, `<port>`) come from `live/config.json` and the install. Files in a vault carry absolute paths, never shell variables.
- **Record the outcome** of each proposal (applied, changed, declined, and why) in `<home>/INSTALL-PROGRESS.md`. Declining is legal; each section says what stops working without it.

| Proposal | Needed by | Without it |
|---|---|---|
| a · the `person` family in section 8 | Station C4 | The frontmatter guard blocks every person note, and the note checks refuse to run |
| b · where the People room goes | Station C4 | `paths.people_dir` has nowhere to point |
| c · the intake buffer note | Station O7 | Intake stays off |
| d · three command-base patches | Stations C4 and O7 | Placing intake entries and dictating note updates means running commands by hand |
| e · the three playbook folders | Station C0 offers them; C4 requires person-dossier | Code falls back to the payload seeds with a warning on every read, and nothing accumulates in Recent runs |
| f · registry entries | Stations C2, C4, O7 | Resident jobs nobody can stop correctly or prove healthy |

## a · The `person` family in section 8

**Why it is needed.** The doctrine my-second-brain ships has no `person` family. Its personal wing `People/` room is for people who are neither family nor business, and clients and vendors are entity notes in the business wing. This kit writes one note per person, whoever they are to you, and its code reads the family at run time: `dossier_core.person_schema` takes the required and optional keys, the closed `roles` and `stage` lists, and one meaning per role from the comment lines. So this is a new family, not a key added to an existing one.

**The roles interview (station C4).** Roles are what a person is to you, and the list is closed so the code can test it. Ask how the owner makes money, who pays them, who works with them without money changing hands, who they pay, who brings them people, who they learn from, and who works inside their own companies. Aim for about ten values, each a relationship rather than a job title. Job, company and "candidate" go in `roles_remark`.

**The block**, placed in section 8's `families:` directly after the last `entity` subtype and before `record:`:

```yaml
  person:     {required: [type, roles],
               optional: [roles_remark, stage, stage_since, last_contact, since,
                          wa_jid, wa_phone, <source_id_keys>, dossier_updated],
               multi: [roles],
               roles: [<the owner's closed list>],
               stage: [<the keys of vocab.stages>]}
                                          # <people_dir>: EVERY person this vault keeps a note on,
                                          # one person one note, whoever they are to the owner.
                                          # `roles:` REQUIRED, CLOSED, MULTI: what this person is to the
                                          # owner, as many values as apply. Anything that is not a
                                          # relationship (job, company, "candidate") goes in
                                          # `roles_remark`. The person note run reads the lines below,
                                          # one per role, the meaning after the value:
                                          #   <role>       <one sentence meaning>
                                          # `stage:` the one lifecycle key, the same keys as the
                                          # WhatsApp Live board. Only people whose roles include the
                                          # roles named in config person.roles_with_stage carry it.
                                          # The machine moves it forward only; moving it back is the
                                          # owner's hand. `won` and beyond are written only against a
                                          # payment record, never from what somebody said.
                                          # `stage_since:` the day stage was last set.
                                          # `since:` the day the relationship began, YYYY-MM-DD, never
                                          # changed once written. `last_contact:` the day of the latest
                                          # exchange. `dossier_updated:` the day the body was last
                                          # rewritten whole (the note run writes it).
                                          # `wa_jid:` WhatsApp's stable id (the @lid form preferred, the
                                          # phone form legal), unquoted. `wa_phone:` digits with country
                                          # code, no plus sign, unquoted.
                                          # <source_id_keys>: one key per second message source, the
                                          # id that source gives this person (for example lark_id).
```

**How the comment lines must look**, because `dossier_core.parse_person_family` reads them:

- They follow directly after the line that closes the family's brace, with no blank line in between.
- Each role line is `#`, then the same run of spaces on every role line (two or more; three is the convention), the role value exactly as in the list, spaces, one sentence.
- A line that continues a meaning has more spaces after `#` than the role line plus the role word.
- `<source_id_keys>` is empty when the owner has no second message source. If the shipped doctrine gives `renew_by` to entities and a person note may carry a deadline, add `renew_by` to `optional` too.

**An invented example**, from a small baking school that also takes catering orders (twelve relationships; your own list will differ):

```yaml
  person:     {required: [type, roles],
               optional: [roles_remark, stage, stage_since, last_contact, since,
                          wa_jid, wa_phone, lark_id, dossier_updated],
               multi: [roles],
               roles: [lead, student, client, partner, collaborator, supplier, referrer,
                       instructor, venue, network, team, mentor],
               stage: [new, talking, offered, won, active, done, parked]}
                                          #   lead         asked about a class or an order, has not bought
                                          #   student      took one of the school's classes
                                          #   client       orders catering (not a class)
                                          #   partner      shares money or invests together with the owner
                                          #   collaborator works together with the owner, no money shared
                                          #   supplier     the owner pays them for ingredients, equipment or work
                                          #   referrer     brings students or orders to the owner
                                          #   instructor   teaches or assists in the school's classes
                                          #   venue        hosts a class, or an event the school caters
                                          #   network      pure network: information, introductions, a friend
                                          #   team         works inside the owner's own business
                                          #   mentor       the owner learned from them
```

(In the real family the explanatory comment lines of the block above sit around these role lines, in any order; they are left out here to keep the example short. Explanatory lines use one space after `#`, so the reader never mistakes them for a role. The example parses with `dossier_core.parse_person_family`.)

**Config that must agree with the block**, in the same breath:

- `vocab.stages` keys equal the `stage` list. Renaming a stage later changes both, through one re-ruling (`references/defaults-and-why.md`, default 8).
- `person.roles_with_stage` (default `lead`, `member`, `client`) names the roles that carry a stage (for the example above, `lead`, `student` and `client`). It must share at least one value with the `roles` list; when it shares none, every note run refuses with a message naming both lists, rather than silently dropping every stage.
- `second_source.id_key` equals the id key declared here (the Lark implementation's default is `lark_id`).
- `paths.doctrine` points at this doctrine (left empty, the loader uses `<vault>/99_Meta/structure-doctrine.md`).

**The whole amendment, as the list the owner says yes to:**

```
99_Meta/structure-doctrine.md      section 8: the person block, filled in, verbatim
                                   section 1: the People room line (proposal b)
                                   section 2: two precedent rows (below)
                                   revision log: one line (below)
99_Meta/Templates/Person.md        new (below)
<people_dir>/_People-Guide.md      the door, changed or new (proposal b)
02_Command-Base/Home.md            the room's line, only when the room is new
```

Section 2 precedent rows:

| Case | Home |
|---|---|
| Facts about one person, whoever they are to the owner | `<people_dir>`, one note per person, `type: person` |
| A client or vendor that is an organization (a company, a household account) | Stays an entity note in its business wing room; the people in it get person notes that link to it |

Revision log line:

```
- **<date>**: new family `person` (shape: a new family). One note per person, whoever they are to the owner, in `<people_dir>`; `roles` closed and multi, `stage` the same keys as the WhatsApp Live board, `won` and beyond only against a payment record. Why: <the owner's words>. Also touched: sections 1 and 2, `99_Meta/Templates/Person.md`, `<people_dir>/_People-Guide.md`, `02_Command-Base/Home.md`.
```

`99_Meta/Templates/Person.md`:

```
---
type: person
roles: [<one of the owner's roles>]
---

# <Full name>

<!--
One person, one note, whoever they are to the owner. File name: the person's name, written the way section 5 of the doctrine names files.
The sections of a person note are the skeleton in the person-dossier playbook (paths.person_playbook). They are not copied here: the note checks read them from the playbook, and a copy would drift.
Every optional key and what it means: section 8, the person family.
-->
```

**Proof, after the edit:**

1. The law still reads: `/usr/bin/env python3 "<my-second-brain payload>/scripts/doctrine_schema.py" "<vault>"` exits 0 and lists `person` with both closed lists.
2. The kit reads the same thing: `$PY "$KIT/skills/breakthrough-person-dossier/scripts/dossier_core.py" --check-live --config "$LIVE/config.json"` reports the owner's roles with their meanings, the stage list and the playbook's sections, with no error.
3. The frontmatter guard judges person notes by the new family. The first real note at station C4 is written by the daemon's Python, which no PreToolUse hook sees, so that note proves nothing about the guard: the proof is a probe. First check the guard mounts on this vault at all: `grep -n MSB_VAULT_PATHS ~/.claude/hooks/my-second-brain-fm-guard.sh` must name `<vault>`; a guard registered for another vault passes everything, so log `skipped: guard registered for another vault` (or reinstall the guard for this vault through my-second-brain), and with no guard installed log `skipped: no guard`. Then pipe it a Write of a template-shaped note (`type: person`, one role from the list) into `<people_dir>`, and the same with a role outside the list: the first must pass and the second be refused (station C4, Verify 10 has the command). The checker skips templates, so this probe is the only test that the template and the family agree.

## b · Where the People room goes

**Decide it from the owner's doctrine, in this order:**

1. Read sections 1 and 2 and `Home.md`, and find every place the owner already files a person: the personal wing's `People/` (friends, mentors, the doctor), `Family/` (relatives), the business wing's entity rooms (`Clients/`, `Vendors/`, `Employees/`), or a room of their own.
2. `ls` each of those rooms and count the notes about one individual, as opposed to an organization.
3. Offer the choices with their costs, recommend one in one sentence, and let the owner rule.

| Choice | Fits when | Costs |
|---|---|---|
| **One People room above both wings**, `02_Command-Base/People/` (recommended) | People cross the wings: a friend becomes a client, a client becomes a partner | A section 1 line (the command base gains a room) and the section 2 rows; the personal wing's `People/` door changes, or that room retires later by the owner's choice; entity rooms keep organizations |
| **A People room in the business wing**, `01_Assets/People/` | The owner wants the kit to touch only the business wing | A note opened from the board for a personal contact lands in the business wing; `Family/` and the personal `People/` stay as they are |
| **The personal wing's existing `People/`, widened to everyone** | Few business contacts, and the owner rarely separates | Breaks the shipped filing test ("a client, vendor or employee files in the business wing"), so it needs the same amendment anyway |

4. The ruling lands as the section 1 and section 2 lines inside proposal a's amendment (one amendment, one yes) and as a decision note; `paths.people_dir` gets the absolute path.
5. **The door.** When the room already exists, read its `_People-Guide.md` in full and change only the sentences the new rule contradicts (usually the filing test), then add three short lines: every person is one note here, whoever they are, with `roles`; `stage` follows the board and `won` needs a payment record; how notes are written lives in the person-dossier playbook folder. Keep the owner's own observations. When the room is new, write a short door in the owner's voice carrying those same lines, and add the room to `Home.md`.
6. **Never move existing notes on your own.** Offer the move once, with its cost: each moved note changes its `type` and loses keys its old family had (such as `status`), every inbound link is rewritten as section 3 requires, and the old template retires. More than a handful of linked notes is a migration and gets its own sitting.
7. **Existing notes need an id to be recognized.** The daemon matches a chat to a note by `wa_jid` or `wa_phone`. With the owner's yes, add one of those keys to the notes of people they talk to on WhatsApp. A note without either is treated as missing; a new note run for that person then refuses to overwrite the same file name and fails loudly instead of replacing it (`references/pitfalls.md`, pit 9).

## c · The intake buffer note

**Where.** `intake.buffer` (the same file as `paths.intake_buffer`; set one of them), default `<vault>/99_Meta/intake-buffer.md`, created with the owner's yes. The folder must already exist: the kit never creates a missing folder for it. The note has **no front matter**: like `capture-buffer.md` it is a machine's notepad, and the shipped doctrine's rule is that a file without `type:` is a notepad the guard never mounts.

**Shape**, top to bottom:

1. A few lines of rules in the owner's language (optional; the daemon never reads them).
2. **The open zone**: entries, each appended by the daemon just above the placed heading.
3. **The placed heading**, alone on its line.
4. **The placed zone**: entries the intake mode moved here after placing them, until compile clears it.

**One entry:**

```
### <YYYY-MM-DD> · [<source>] · <who> · <what to do, one line>
- <quote mark> <MM-DD HH:MM> <the quote, up to 200 characters>
- <owner_label> <side marker>: <promised, not delivered / due date / to place>
- <guess marker>: (<world> · <kind>) <suggested place>
- <dig marker>: `dossier.py prepare --name <note name>`
- <open status line>
```

`<source>` is `wa` for WhatsApp, or the second source's name. `<who>` is `[[<note name>]]` when the person has a note, otherwise their name followed by the no-note marker. The markers are written in the owner's language, chosen by `board.lang`, and must be exact, because the counting and placing commands match them character for character:

| Marker | `board.lang: en` | `board.lang: zh` |
|---|---|---|
| Placed heading | `## Placed` | `## 已归` |
| Open status line | `- status: open` | `- 状态: open` |
| Placed status line | `- status: placed → <where>` | `- 状态: 已归 → <where>` |
| Owner side | `<owner_label> side` | `<owner_label> 这边` |
| Guess, dig deeper | `machine guess`, `dig deeper` | `机器猜`, `深挖` |
| No note | `<name> (no note)` | `<name> (无档)` |
| Quote | `"<quote>"` | `「<quote>」` |

Reading accepts both languages in one file, so switching `board.lang` later breaks nothing. The literal strings live in `wa_intake.MARKS`; if a later version changes them, that table is the source, not this page.

**The quote line** (the second line of every entry) opens with one of five marks. The mark says what the program found when it looked the quote up in the run's own message pack (`intake.verify_quotes`), so the owner can tell a person's real words from the brain's summary of them:

| how / src | zh | en |
|---|---|---|
| resolved, src text | `- 🗣 MM-DD HH:MM「stored text」` | `- 🗣 MM-DD HH:MM "stored text"` |
| resolved, src transcript | `- 🎙 MM-DD HH:MM 语音转字「…」` | `- 🎙 MM-DD HH:MM voice transcript "…"` |
| resolved, src doc | `- 📄 MM-DD HH:MM 文件内文「…」` | `- 📄 MM-DD HH:MM document text "…"` |
| none | `- ≈ MM-DD HH:MM 大意, 非逐字「brain's words」` | `- ≈ MM-DD HH:MM gist, not verbatim "brain's words"` |
| not checked (verify off, or the pack could not be read, or the check raised) | `- ≈ MM-DD HH:MM 未核对「brain's words」` | `- ≈ MM-DD HH:MM not checked "brain's words"` |

With 🗣, 🎙 and 📄 the words between the quote marks are the stored characters of the message, copied by the program, and the time is that message's own time; the brain's spelling never reaches the line. A quote the program found inside one message, as a few pieces of one message in order (joined with ` … `), as two to four messages in a row from the same sender (joined with ` ⏎ `), or as a close match within one message, is marked found. With ≈ the words are the brain's, and the time is the one the brain gave. ⛔ No path writes 🗣 for words the program did not find: a gist stays a gist.

**A correction entry.** When the person edits a message after a run judged it (the edit watch, `edit_watch.*`), the next run reads the new version, and an entry whose words the edit changed is corrected by **appending** a new entry; the older entry is left byte for byte as it was, because what the machine wrote is part of the record and the owner may already have placed it. Two shapes:

```
### <YYYY-MM-DD> · [wa] · <who> · ✏️ 对方改了讯息 · <what to do, one line>
- 🗣 <MM-DD HH:MM>「<the new words>」
- 改之前: 「<the old words>」 (同一封讯息; 照旧版写的条目以这条为准)
...
```

That is a capture the brain wrote from the edited message (en: `✏️ they edited the message · ` in the title, and `- before the edit: "<the old words>" (same message; this entry replaces any entry written from the old version)`). When the brain wrote no new capture but an older open or placed entry of the same person quoted words the edit removed, the program writes the correction itself: the title `✏️ 对方改了讯息 · 这行标题是照旧版写的, 以下面的原话为准: <the older title>` (en `✏️ they edited the message · this title was written from the old version, trust the quote below: <the older title>`), a 🗣 line with the message's current words, `- 改之前: 「<the older quote>」 (旧条目还开着)` or `(旧条目状态: <its status>)` (en `- before the edit: "…" (the older entry is still open)` or `(older entry status: …)`), the owner line `旧条目的日期与期限照新原话重看` (en `recheck the older entry's date and deadline against the new words`), and the guess line `(<world> · 更正) 不是新的事, 归位时跟旧条目并成一件` (en `(<world> · correction) not a new item; merge it with the older entry when placing`). Correction entries do not count against the brain's two entries per run.

**The legend line.** The rules at the top of the note are the place to say what the marks mean, once, in the owner's language. For an English buffer:

```
Marks: 🗣 the person's own words, found in the message · 🎙 found in a voice transcript · 📄 found in a document's text · ≈ gist, not verbatim (or not checked) · ✏️ the person edited the message; this entry replaces the older one.
```

For a Chinese buffer: `标记: 🗣 讯息里找得到的原话 · 🎙 语音转字里找到的 · 📄 文件内文里找到的 · ≈ 大意, 非逐字 (或未核对) · ✏️ 对方改了讯息, 以这条为准。`

**An invented example** (`board.lang: en`, owner label Sam):

```
Things someone asked of me, or I promised, caught from my chats. The daemon only appends above
"## Placed" and never changes a status. I place entries by saying "intake" in a session.
Marks: 🗣 the person's own words, found in the message · 🎙 found in a voice transcript · 📄 found in a document's text · ≈ gist, not verbatim (or not checked) · ✏️ the person edited the message; this entry replaces the older one.

### 2031-03-04 · [wa] · [[Dana-Okafor]] · Send the revised garden quote before Friday
- 🗣 03-03 17:42 "Can you send the revised quote before Friday? We decide on Monday."
- Sam side: due 03-07
- machine guess: (Work · dated request) task → Garden-Redesign
- dig deeper: `dossier.py prepare --name Dana-Okafor`
- status: open

### 2031-03-04 · [wa] · Theo Brandvold (no note) · Site visit at the shop, Thursday 10:00
- ≈ 03-04 09:15 gist, not verbatim "Thursday at ten at the shop is fine"
- Sam side: to place
- machine guess: (Work · time and place) calendar 03-06 10:00
- dig deeper: `dossier.py prepare --name Theo Brandvold`
- status: open

## Placed

### 2031-03-02 · [wa] · [[Dana-Okafor]] · Deposit arrived, confirm the start date
- 🗣 03-02 11:03 "Deposit sent this morning"
- Sam side: to place
- machine guess: (Work · money) task → Garden-Redesign
- dig deeper: `dossier.py prepare --name Dana-Okafor`
- status: placed → task [[Confirm-Garden-Start-Date]]
```

**Who changes what.** The daemon (and the second source, when connected) only appends, and only while `intake.enabled` is true. Only the intake mode (proposal d, patch 2) moves an entry and rewrites its status line. Compile clears the placed zone. Open entries are never cleared by anything but a placement.

**Proof:** `$PY "$KIT/daemon/wa_intake.py" --config "$LIVE/config.json" --count-open` prints the same number as a manual count of open status lines above the placed heading, and `--list-open` numbers them from 1, each with its `key:` line; `--list-placed` lists the placed zone with the same keys, so an entry keeps its key when it is placed.

## d · Three command-base patches

The owner's command-base skill (in a my-second-brain vault, the `<slug>-command-base` skill folder kept inside the vault's `99_Meta`, with its `SKILL.md` and `references/`) is their file, generated for them and written in their words. These patches are instructions for the owner's session to adapt into that file, never a file to copy over it.

Before patching:

- Read the whole skill first, and its `references/` folder.
- Trigger words go in two places: the mode router row, and the skill's `description`. A row whose words are missing from the description is a row the model never reaches.
- Leave every block that carries a revision marker (`boot-gate-rev`, `doorbell-rev`) untouched. my-second-brain rewrites those blocks during its own updates, and anything inside them would be lost.
- After any my-second-brain update, check the three patches are still there: `grep -n "update-person\|intake" <the skill's SKILL.md>`.

### Patch 1 · "update <name>'s note: ..."

**What it does.** The owner says a few sentences about one person, and the session that heard them folds those sentences into that person's note, section by section, after reading the whole note. Only the cells the words touch change; the note is verified and backed up before it is placed. No daemon command, no second model run.

**Description.** Add the owner's phrasings, for example "update <name>'s note", "add to <name>'s note", and the same in the owner's other languages.

**Router row:**

| Trigger | Action |
|---|---|
| "update <name>'s note: ..." / "add to <name>'s note: ..." | Read `references/update-person.md` and follow it. The session that heard the words does the update itself. |

**`<command base>/references/update-person.md`** (inside the owner's command-base skill folder, not this kit), written by the owner's session from this outline, with absolute paths:

1. **Entrance.** The phrases above, or any sentence that hands over a fact about someone who has a note in `<people_dir>`. A passing mention of a person is a capture, not an update; when unsure, ask one question.
2. **Find the note.** `ls <people_dir>` and match the name. Two candidates: ask which. No note: say so and stop; opening a note is the person-dossier flow, not this mode.
3. **Build the packet.** `/usr/bin/python3 <KIT>/skills/breakthrough-person-dossier/scripts/dossier.py prepare --config <live>/config.json --name <note name> --owner-input '<the words>'`, with the owner's words passed through unchanged: not paraphrased, not translated, not trimmed (words containing a single quote go in through a quoted heredoc). It prints a folder holding the prompt, the conversation pack and the facts; the owner's words open the prompt as the highest source.
4. **Read in full.** The owner's words, the whole note (the real file, end to end), the pack wherever the words touch it, the facts.
5. **Write the candidate** in that folder: the note's full current text, then edit only the cells the words touch.
   Each place written carries `🗣 <owner_marker> <YYYY-MM-DD>`.
   The owner is the highest source: where the pack disagrees, keep the owner's version and write both sides.
   A question in the open questions section that the words answer becomes a fact in its cell and leaves the list.
   One line of the raw words goes into the owner notes section (its heading is the playbook skeleton's; config `person.section_roles.owner_notes`), under the `<!-- <owner_marker> -->` lock.
   Front matter: `dossier_updated` becomes today; `stage` only moves forward, and `won` and beyond need a payment record; `roles` may gain a value, never lose one; `wa_jid` and `since` never change.
   Nothing else is rewritten, and the machine sections stay byte for byte.
6. **Check the diff** between the note and the candidate. Every changed hunk must trace back to the owner's words; undo any that does not.
7. **Verify.** `dossier.py verify --config <live>/config.json --name <note name> --candidate <candidate>`. Fix what it names and verify again; after three failed rounds, stop and tell the owner the folder and the last error.
8. **Place.** Re-read the note first; if it changed since step 4, apply the edits onto the new text and verify again. Then `dossier.py place --config <live>/config.json --name <note name> --candidate <candidate> --reason "<owner_label> <YYYY-MM-DD> dictated update: <a few words>"`. It verifies again, keeps three backups and writes the filing-log line.
9. **Register and reply.** One row in the person-dossier door's Recent runs (several updates to one person on one day share a row), then a one or two line reply: which sections changed, any conflict written, any stage the checks capped.
10. **Taking it back**, only when the owner says so: write the newest file from `<live>/dossier-backups/<note name>/` over the note with the Read and Write tools (the frontmatter guard refuses a shell `cp` onto a note) and add one filing-log line. Recent lines appended since then are lost; say so.

### Patch 2 · "intake"

**What it does.** Lists the open intake entries, proposes one place for each, writes only on the owner's yes, and marks each entry placed.

**Description.** Add "intake" and the owner's own word for placing captured items.

**Router row:**

| Trigger | Action |
|---|---|
| "intake" | Read `references/intake.md` and follow it: list the open entries, one proposal each, write only on a yes, mark each placed. |

**Compile row.** Append one sentence to the owner's existing compile row: "If the intake buffer's placed zone holds entries, add one journal line (intake placed N: a to tasks, b to the calendar, c dropped) and clear the placed zone; never touch open entries."

**`<command base>/references/intake.md`** (the owner's command-base skill folder again), from this outline:

1. **The file.** `<intake.buffer>`, no front matter, open entries above the placed heading; five kinds only (a request with a date, something the owner promised and has not delivered, an appointment, money, a decision the other side stated); the entry shape is proposal c.
2. **List.** `$PY <KIT>/daemon/wa_intake.py --config <live>/config.json --list-open` prints the open entries numbered from 1, each followed by a line `    key: <12 hex>`, the entry's key (`--list-open --json` gives full keys; `--list-placed [--json]` lists the placed zone, read only). Zero open: say so in one line and stop.
   **Read the quote mark of each entry** before proposing anything. 🗣, 🎙 and 📄: the words are the person's own (from the message, its voice transcript, or a document's text), and a date, amount or place in them can be used as written. ≈ `gist, not verbatim` (`大意, 非逐字`): the words are the brain's summary; check a date, amount or place against the chat before it goes into a task or the calendar. ≈ `not checked` (`未核对`): nobody looked the words up; treat them as a gist.
   **A ✏️ entry is a correction**: the person edited a message after an earlier entry was written from it. The ✏️ entry replaces the older one: place the ✏️ entry, and merge the older entry into the same place or drop it (its `before the edit` line names the old words). When the older entry was already placed, update what it became (a task's date, a calendar event) to the new words, on the owner's yes.
3. **Propose, one line per entry**, in file order, taking the entry's machine guess as a starting point, never as the answer:
   **task**: a `cb: task` in a project's `Tasks/` folder with every key section 8 requires; when no project fits, propose opening one first.
   **calendar**: only when `calendar.destination` is not `none`, created through the calendar destination with title, date and time (ask for a missing piece rather than guessing). `wa_calendar.create_event(cfg, title, date, time, duration_min, place, note, idempotency_key=<the entry heading>)` returns `ok` and a `url`; for `ics` the file is `<live>/calendar/<key>.ics` and opening it imports the event. The board's calendar button does the same.
   **decision**: through the "log a decision" row, with its active-set check first.
   **project or method**: name the brief or playbook folder it belongs to, and stop there.
   **just a note**: one dated line in `capture-buffer.md`.
   **drop**: nothing written; say why in a few words.
4. **Write only on a yes**, entry by entry. A numbered answer ("1 task, 2 drop, 3 calendar Friday 3pm") is a yes for each number.
5. **Mark placed** right after each write, by key: `wa_intake.py --config <live>/config.json --mark-placed-key KEY --to "<where it went>"`, where KEY is a full key or a unique prefix of at least 8 hex. It prints the same confirmation as `--mark-placed` (`placed → <where>` and the moved title). It is the safe way to place several entries in a row: numbers shift after every move and when a correction or a new capture lands while the owner decides, keys do not. Refusals: `key <p> matches <n> entries (run --list-open again)` (use more of the key), `entry <k> is already placed → <where>` (an earlier mark did it: say so and go on), `entry <k> (number <n>) is not open, it cannot be placed`, `no entry with key <k> (run --list-open again)`. `--mark-placed <n>` still works; with numbers, mark from the highest number down, or list again between marks.
6. **Done.** One line: "placed N, still open M".

Rules that do not bend: never place anything at boot; never edit an entry's text or its machine guess (what the machine thought is part of the record); open entries are never cleared by compile, and a pile of them is information for the owner, not a mess to tidy.

When the evening push is on (`intake.evening_push`), set `intake.rules_file` to this `<command base>/references/intake.md`, so the evening sort suggests the same places the mode offers.

### Patch 3 · the morning intake line

**Where.** In the skill's morning session start, as a sub-step directly under the compile backfill doorbell (step 4 in the shipped skill). Not inside the maintenance doorbell block, and not inside any block with a revision marker.

**Text to adapt:**

> **4b. Intake doorbell.** Count the open intake entries: `/usr/bin/python3 <KIT>/daemon/wa_intake.py --config <live>/config.json --count-open`. More than zero: one line in the brief, "N intake entries waiting; say intake to place them." Never place anything at boot: report the count, once. A missing buffer or a failing command: skip silently; a doorbell never blocks the brief.

It belongs to the morning entry only, like the other doorbells in that list.

## e · The three playbook folders

**What they are.** The payload carries three seed folders, each a door plus a playbook: `playbooks/whatsapp-live-triage/` (`_WhatsApp-Live-Triage-Guide.md`, `whatsapp-live-triage-playbook.md`, `standby-machine-runbook.md`), `playbooks/resident-workers/` (`_Resident-Workers-Guide.md`, `resident-workers-playbook.md`) and `playbooks/person-dossier/` (`_Person-Dossier-Guide.md`, `person-dossier-playbook.md`). Every file carries a seed stamp comment right after its front matter.

**Where they go.** `<business wing>/04_Methodology/Playbooks/<the same folder name>/`, one folder per kind of work with its door, as doctrine section 9 lays out.

**Why the owner's yes, and why at that moment.** `04_Methodology` is earned, never captured: only the owner's yes lands anything there. A playbook the owner adopts out loud is legal on day one (doctrine section 7), and `confirmed_by_owner: true` means the owner who was in the room said yes at the moment the note was written. The seeds therefore ship with `confirmed_by_owner: false`, and only the install session that heard the yes flips it, through the copy command's `--confirmed-by-owner`.

**The moves, one folder at a time:**

1. Show the owner the door's "How we do this work" summary and the playbook's "When to run it" and "What to weigh" headings. Say what the folder is for in one sentence. Ask for a yes for this folder.
2. On the yes, copy the folder into the vault: `$PY "$KIT/daemon/wa_setup.py" copy-seed --config "$LIVE/config.json" --seed playbooks/<folder> --to "<the vault folder>" --confirmed-by-owner` (station C1, step 5; `--dry-run` first). It never overwrites a file, creates missing folders 0755 and files 0644 like the rest of the vault, keeps the seed stamp comment in every file, writes `confirmed_by_owner: true` wherever a file's frontmatter says `false` and today's date into the door's `updated`, and records in `live/seeds.json` the hash of what landed. Keep `lane` unless the owner's lane ladder puts that kind of work in another lane (a change made after the copy is an owner edit).
3. Leave the door's "What we have learned" and "Recent runs" empty, except for one row in the whatsapp-live-triage door's Recent runs registering this install: its address is `<home>/INSTALL-PROGRESS.md`, and the bet columns hold the owner's expectation, or stay blank when they have none.
4. Add each new folder to `Home.md`, and log one filing-log line per folder.
5. Point config at the vault copy: `paths.person_playbook` is the vault's `person-dossier-playbook.md`.
6. Run `$PY "$KIT/daemon/wa_setup.py" seed-status --config "$LIVE/config.json"`: each copy shows `unchanged` with its stamp version equal to the payload's (`copy v<x>, payload v<x>`). The whatsapp-live-triage door reads `edited by owner` once move 3's row is in, the person-dossier door once C4 adds its Recent runs row, and so does any file whose `lane` was changed; all are expected.
7. If the owner keeps an auto-memory index, add one pointer line per folder, naming the folder (its door), never the playbook file inside it.

**Declined.** The payload seeds stay in use. The code warns on every read (a log warning and a line in the digest's health block), Recent runs never accumulate, and `npx skills update` replaces those files. Write one auto-memory line pointing at `~/.claude/skills/breakthrough-whatsapp-live/playbooks/`, and say those three facts out loud. The person-dossier folder is not optional while `dossier.enabled` is true: the note run reads its skeleton every time.

**Later updates.** `install/update.md` shows the owner the seed changes between the stamped version and the new one (`playbooks/CHANGELOG.md`) and merges them section by section, on the owner's yes. It never overwrites the owner's copy, and never touches a door's "What we have learned" or "Recent runs".

## f · Registry entries

**Where.** In the owner's machine registry note if they keep one (your machine registry note: one note listing every resident program on every machine); otherwise in `<home>/INSTALLED.md`. Each resident job gets its own entry, and a capability added to the daemon later gets a short addition under the daemon's entry.

**When.** At install, by the session that installs, in the same act as the install: a job installed without its entry is how a machine ends up running something nobody can stop (`references/pitfalls.md`, pit 6). The six cells come from the resident-workers playbook (read · write · limits · stop · proof · rollback); a cell nobody can fill is a hole to fix before the job runs. **Stop** must hold across a reboot. **Proof** must be something read back from outside, never an exit code. Write the Mac's own name in each entry, never "this machine": a registry that syncs to other machines turns "this" into a false statement there. Write the cells in the owner's language.

### The daemon (`<label>`)

```
Mac: <the Mac's name> · plist ~/Library/LaunchAgents/<label>.plist · node <bin.node> runs <home>/launchd/run.mjs, which runs python with <KIT>/daemon/wa_live.py
code: wa_live.py sha256 <shasum -a 256 of it> · VERSION <x> · recorded <date> (refreshed after every update)
read: WhatsApp Desktop's database and media, read only, checked every doorbell.poll_seconds · <live>/config.json · the vault notes topic-map.json points at, and person notes of people named in a chat · <live>/facts.json · the reader's working copies under <home>/runs-work/
write: <home>/ (runs-work, transcripts-cache, doc-text-cache, prewarm) · <live>/ (state.sqlite, summaries.md, runs/, digest/, logs, calendar/) · the vault: recent lines in existing person notes, intake buffer entries when intake.enabled is true, filing-log lines (the person note writer below adds whole notes) · pushes through notify.outlet · never WhatsApp, never a message sent
limits: debounce 45 s quiet, 180 s at most · 2 runs at once, 1 per conversation · 600 s per run, 1 retry, 12 turns · tools Read, Glob, Grep with the cage check · Read denied on secrets · board on 127.0.0.1:<port> only
stop: wa_setup.py uninstall-launchd --config <live>/config.json boots the job out, then stops any python of this install left running, disables it and removes its plist, so it stays stopped after a reboot (install-launchd brings it back); a raw launchctl bootout is not a stop, it can leave a frozen python holding the board port · remove the board's tailscale serve rule · leaves no half state: bursts in progress run again at the next start, and a person note job cut off is marked failed at start
proof: curl -s http://127.0.0.1:<port>/health answers 200 with a body starting ok (503 stalled means the main loop is frozen) · after a restart, wa_setup.py restart ends with proof: one daemon · launchctl print gui/$(id -u)/<label> shows state = running · a test message produces a new row in the runs table and a row on the board · the morning digest arrives (silence is not proof)
rollback: wa_setup.py uninstall-launchd, then remove <home> (install/uninstall.md) · a person note restored from <live>/dossier-backups/<note>/ · an intake entry removed by hand
```

**Only when the daemon runs on a setup token** (`install/C2-daemon.md`, step 12), the daemon's entry carries a seventh cell, and its `stop` and `rollback` cells gain one clause each:

```
expires: the setup token in <home>/secrets/claude-bot.env, made <date>, renew by <renewal date, about a year later> · renew: claude setup-token in the owner's own terminal, the new token written into that file with a text editor, then probe-claude.json · never the token itself in this note
stop (add): set bin.claude back to its previous value (recorded at install, usually <auto>), then wa_setup.py render-launchd and follow its next: line (install-launchd, or restart when nothing is to reinstall) to leave the token unused
rollback (add): bin.claude back to its previous value, render-launchd and follow its next: line, delete <home>/secrets/claude-bot.env and <home>/bin/claude-bot, revoke the token from the Claude account
```

### The evening intake job (`<intake_label>`), installed only when `intake.evening_push` is true

```
Mac: <the Mac's name> · plist ~/Library/LaunchAgents/<intake_label>.plist · node runs <home>/launchd/intake-evening.mjs once a day at intake.evening_at
read: the intake buffer · <live>/config.json (intake, notify, brain) · <live>/claude-settings.json · intake.rules_file when set
write: nothing in the vault · one push through notify.outlet · its own log line
limits: once a day · no model call and no push when nothing is open, when intake.enabled or intake.evening_push is false, or when notify is off · one model call, read only tools, 4 turns, 300 s · a list of at most 12 entries
stop: set intake.evening_push to false (the job reads config and exits); to remove it, launchctl bootout gui/$(id -u)/<intake_label> and move the plist out of ~/Library/LaunchAgents/ · RunAtLoad is false, so a restart never fires it by itself
proof: with one open test entry, launchctl kickstart -k gui/$(id -u)/<intake_label> adds a line to <live>/intake-evening.log and delivers the list to the phone · with none open, the log says why nothing was sent and no push arrives · the buffer's md5 is unchanged either way
rollback: nothing to roll back in the vault · remove the plist and <home>/launchd/intake-evening.mjs
```

### The person note writer (inside the daemon; no launchd entry of its own), written under the daemon's entry

```
runs: nightly at dossier.nightly_at, on the board's open and rewrite buttons, and for paying customers queued by the facts job
read: this person's messages over dossier.pack_days · their facts lines · the current note · the person-dossier playbook skeleton (paths.person_playbook) · the doctrine's person family (paths.doctrine)
write: one whole person note in <people_dir>, only after dossier_core.finalize passes · a backup of what was on disk to <live>/dossier-backups/<note>/ first (dossier.backups_keep, 3) · one line in paths.filing_log · its run folder under <live>/runs/ · never a note it did not read: a new note run refuses to replace an existing file
limits: dossier.nightly_cap notes per night (8) · one run at a time · the same person at most once per dossier.min_gap_hours (6), failures included · dossier.run_timeout_seconds (1200) and dossier.max_turns (24) · model dossier.model
stop: dossier.enabled false, then wa_setup.py restart --config <live>/config.json; prove that recent lines stopped too (references/privacy.md), and stop the daemon to stop every vault write
proof: the filing-log line · the backup file · dossier_updated is today in the note · sqlite3 -readonly <live>/state.sqlite "select id, ok, error, note_path from dossier_jobs order by id desc limit 3" shows ok = 1
rollback: copy the newest file from <live>/dossier-backups/<note>/ over the note and add one filing-log line; recent lines appended since then are lost
approval: this writer puts AI-written text into the vault without a per-note approval card; the checks, three backups and the filing-log line stand in for one. The owner ruled on that trade at station C4.
```

### Additions when an optional station connects

Under the daemon's entry, one short block per station, as it connects: what it now reads, what it now writes, the key that switches that block off alone, and its proof.

| Station | Reads | Writes | Switch | Proof |
|---|---|---|---|---|
| O1 facts | The facts source | `<live>/facts.json`, stage raises on the board, note queue | `facts.enabled` | `facts.json` `written_at` is today |
| O2 second source | Yesterday's whitelisted chats | Recent lines, intake entries, `<live>/second-source-last.json` | `second_source.enabled` | `second-source-last.json` shows `ok` |
| O3 calendar | nothing | Events at `calendar.destination` | `calendar.destination: none` | The board's button returns a link, and pressing it twice makes one event |
| O4 push outlet | `<live>/telegram.json` or `notify.command` | Pushes | `notify.mode: off` | `notify-test.json` reaches the phone |
| O5 backup | `<live>/` | `<backup.dest>/<date>/` | `backup.enabled` | `<live>/backup-last.json` shows `"ok": true` |
| O6 digest | The state database, `<live>/` | `<live>/digest/`, one push | `digest.enabled`, `digest.push` | The digest arrives, even on a quiet day |
| O7 intake | The brain's captures | The intake buffer | `intake.enabled` | `wa_intake.py --count-open` rises after a run with a capture |
| O9 backfill | Chats the gate passes | Summaries in the state database | Only runs when started | `wa_backfill.py --list` shows no skipped groups |
