# Station C4 · Person notes

A person note is one note per person in the owner's vault: written by a model from that person's messages and records, checked by `dossier_core` before it is placed, backed up before it replaces anything, and kept fresh with short recent lines. This station needs a second-brain vault. Without one, or when the owner does not want notes, it is skipped with the reason logged, and nothing else in the kit breaks.

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live      # the folder holding SKILL.md, install/ and daemon/
HOME_DIR="<paths.home from the progress file header>"
LIVE="$HOME_DIR/live"
PY=/usr/bin/python3
LABEL="<launchd.label from the progress file header>"
ILABEL="<launchd.intake_label from the progress file header>"
PORT="<board.port from the progress file header>"
SKILLS="$(dirname "$KIT")"                           # the skills folder the kit sits in
VAULT="<vault from the progress file header>"
D="$KIT/skills/breakthrough-person-dossier/scripts"
```

Claude Code keeps no shell variables between commands: start each command with the lines it needs.

## Ask

One question at a time. Read `references/defaults-and-why.md`, default 9, before question 1, and `references/vault-proposals.md`, proposals a and b, before question 2.

1. **Do you want person notes?** Read default 9, and say the trade out loud: an automatic note puts AI-written text into the vault without a per-note approval; the checks, three backups and a filing-log line stand in for one. Notes run on `dossier.model` (opus unless C2 ruled otherwise), the heaviest runs in the kit, at most `dossier.nightly_cap` a night. A no: log it, tick C4 as skipped, go to C5.
2. **Where do people live in your vault?** Show the owner where they already file individuals, with the counts (proposal b, steps 1 and 2), offer the three choices with their costs, and recommend one in one sentence.
3. **What is a person to you?** The roles interview (proposal a): how you make money, who pays you, who works with you without money changing hands, who you pay, who brings you people, who you learn from, who works inside your own companies. About ten values, each a relationship rather than a job title, each with a one sentence meaning. Job, company and "candidate" belong in `roles_remark`.
4. **Your marker** (`owner_marker`): two to six letters, usually your initials. Every place a note records something you said is tagged with it, and your own lines are locked with it.
5. **Should notes open by themselves**, and for which worlds, from which stage (default `talking`)? Or only when you press "Start note" on the board?
6. **Do you want to update a note by saying so?** "Update <name>'s note: ..." in any session folds your words into that note (proposal d, patch 1, added to your command base). And may the notes you already have for people you talk to on WhatsApp get a `wa_jid` or `wa_phone` key, so the daemon recognizes them (proposal b, move 7)?
7. **Who should the first note be about?** A business contact with a real conversation in the last weeks.

## Do

### 1 · Read the vault before proposing

Follow `references/vault-proposals.md`, "Rules for the session applying these": read the owner's doctrine (sections 0, 1, 2 and 8, and the revision log), `Home.md`, and the door of every room this station touches. Where the doctrine says something other than the paths on that page, the doctrine wins.

### 2 · The person family and the People room, as one amendment

Carry proposal a, with proposal b's section 1 and section 2 lines inside it, through `breakthrough-vault-guardian`, the vault guardian skill that ships with my-second-brain ("add a new type of note"): one list of files, one yes, then the guardian's own proof that the frontmatter guard still reads the law. Fill the block with the owner's roles and their meanings, and its `stage` list with the keys of `vocab.stages`. Amending by hand is legal with the same discipline. Then the People room's door and its `Home.md` line (proposal b, move 5), and a decision note for where people live.

### 3 · Existing notes

Only on the owner's yes: add `wa_jid` or `wa_phone` to the notes of people they talk to on WhatsApp (proposal b, move 7). ⛔ Never move existing notes on your own (move 6); a move is offered once, with its cost.

### 4 · Link the person dossier skill

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; SKILLS="$(dirname "$KIT")"
ls -la "$SKILLS/breakthrough-person-dossier" 2>/dev/null || ln -s "$KIT/skills/breakthrough-person-dossier" "$SKILLS/breakthrough-person-dossier"
```

Then, as a separate command (as in C1 step 1):

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; SKILLS="$(dirname "$KIT")"
ls -L "$SKILLS/breakthrough-person-dossier/"
```

Writes: one link into the payload. The check lists `SKILL.md`, `scripts` and `assets`. The same check-first rule as C1 step 1: an entry already at that name is left alone and described to the owner.

### 5 · The person-dossier playbook in the vault

Required while person notes are on: the note run reads its skeleton on every run. When C1 landed it, confirm the path. Otherwise land it now on the owner's yes, exactly as C1 step 5 does (`wa_setup.py copy-seed --seed playbooks/person-dossier --to <its vault folder> --confirmed-by-owner`, then proposal e, moves 3 to 7). Its skeleton writes section names in Chinese on purpose, because the checks parse them; a section renamed in the playbook must be renamed in `person.section_roles` in the same breath.

### 6 · Paths and the marker in config

With the config edit ritual (`install/00-start-here.md`), set:

- `paths.vault`, `paths.people_dir` (the room from question 2), `paths.person_playbook` (the vault copy of `person-dossier-playbook.md`), `paths.decisions_dir`; each may be absolute or relative to the vault root
- `paths.doctrine` and `paths.filing_log` only when the vault keeps them somewhere else: left empty, the loader uses `<vault>/99_Meta/structure-doctrine.md` and `<vault>/99_Meta/filing-log.md`, the my-second-brain places
- `owner_marker`
- `person.roles_with_stage`: the owner's roles whose people carry a stage (the default `lead`, `member`, `client` fits only an owner whose roles use those words; it must share at least one value with the doctrine's roles, or every note run refuses)
- leave `dossier.enabled` at `false` for now

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"; PY=/usr/bin/python3
"$PY" "$KIT/daemon/wa_setup.py" check-config --config "$LIVE/config.json"
"$PY" "$KIT/daemon/wa_setup.py" sync-reader-config --config "$LIVE/config.json"
"$PY" "$KIT/skills/breakthrough-person-dossier/scripts/dossier_core.py" --check-live --config "$LIVE/config.json"
```

`sync-reader-config` rewrites `<home>/config.json` with the People room, the only place the vault lookup trusts a weak name match. `--check-live` writes nothing: it reports what the checks will read (the roles with their meanings, the stage list, the playbook's sections, the People room, the filing log) and ends with one `check-live:` line. `check-config` proves the config is well formed; only `--check-live` proves the note checks agree with the owner's doctrine and playbook, so both must pass.

### 7 · The update-person mode

On the owner's yes, adapt proposal d, patch 1, into the owner's command-base skill: the router row, the words in its description, and `<command base>/references/update-person.md` written from the outline with absolute paths. It is their file in their words; never copy over it, and leave every block carrying a revision marker untouched. Patches 2 and 3 belong to O7.

### 8 · The registry

Under the daemon's entry in the registry (C2 step 5): the person note writer block from proposal f, including its approval line with the trade the owner ruled on in question 1.

### 9 · Turn it on

With the config edit ritual: `dossier.enabled` `true`, `dossier.auto_open_worlds` (the world keys from question 5, or `[]` for button only), `dossier.auto_open_min_stage`. Then restart:

```sh
KIT=~/.claude/skills/breakthrough-whatsapp-live; PY=/usr/bin/python3
HOME_DIR="<paths.home from the progress file header>"; LIVE="$HOME_DIR/live"
"$PY" "$KIT/daemon/wa_setup.py" restart --config "$LIVE/config.json"
```

Runs in flight are killed and their bursts run again. A start after `dossier.nightly_at` marks tonight's batch as done for today, so a daytime restart never rewrites notes in the afternoon (`references/pitfalls.md`, pit 3).

### 10 · The first note

On the board, open the first person's conversation and press "Start note" ("Rewrite note" when they already have one). The daemon queues one note run: it reads 30 days of the chat, the person's facts lines and the playbook, writes the note, runs every check, backs up what was on disk and places the note. One opus run, a few minutes.

Writes: the note in the People room; a backup under `live/dossier-backups/<note>/` when a note was replaced; one filing-log line; `live/runs/dossier-<time>-<jid>/`; a row in the `dossier_jobs` table. The job's reason, and the `reason:` in the filing-log line, say `板上要求开档` (a note started from the board) for "Start note" and `板上要求重写` (a rewrite asked on the board) for "Rewrite note".

Then draft one row for the person-dossier door's Recent runs (the daemon never writes it) and add it on the owner's yes.

## Verify

Run each probe with the station's opening block in front of it, at the point its title names. Every check must pass.

1. **The law still reads** (proposal a, proof 1; after step 2). Pass: exit 0, and `person` listed with its closed `roles` and `stage` lists.
   ```sh
   /usr/bin/env python3 ~/.claude/skills/my-second-brain/scripts/doctrine_schema.py "$VAULT"; echo "exit $?"
   ```
2. **The kit reads the same law** (proof 2; after step 6). Pass: the owner's roles with their meanings, the stage list, the playbook's required sections, `people folder:` with a count, and the last line `check-live: ok`. The `playbook:` line must not say `PAYLOAD SEED`.
   ```sh
   "$PY" "$D/dossier_core.py" --check-live --config "$LIVE/config.json"
   ```
3. **The vault is still clean** (after step 5). Pass: no finding points at the People room, its door, the new template or the playbook folders.
   ```sh
   python3 ~/.claude/skills/my-second-brain/scripts/checkup.py "$VAULT" --json
   ```
4. **The skill is linked** (after step 4). Pass: `SKILL.md`, `assets` and `scripts` listed, then `---`.
   ```sh
   ls -L "$SKILLS/breakthrough-person-dossier/"
   head -1 "$SKILLS/breakthrough-person-dossier/SKILL.md"
   ```
5. **The checks run on this Mac** (after step 4). Pass: `dossier_core selftest: <n>/<n> ok` and `dossier.py selftest: <n>/<n> ok`, the two numbers of each equal.
   ```sh
   "$PY" "$D/dossier_core.py" --selftest | tail -1
   "$PY" "$D/dossier.py" selftest --config "$LIVE/config.json" | tail -1
   ```
6. **A note is never replaced without being read** (after step 6; `references/pitfalls.md`, pit 9). Pass: the probe there prints `refused:` and then `note intact`. It writes only inside a new temporary folder.
7. **The first note run finished** (after step 10). Pass: `ok` `1`, an empty error, the note's path and a count of open questions.
   ```sh
   sqlite3 -readonly "$LIVE/state.sqlite" "select id, ok, error, note_path, gaps from dossier_jobs order by id desc limit 1"
   ```
8. **The note is on disk, logged, and passes the checks when read back** (after step 10). Pass: today's date; a filing-log line naming the note, with `reason: 板上要求开档` (or `板上要求重写` when step 10 rewrote an existing note); `✓ checks passed` and the quote count line (`<n> quotes checked, <n> found`) with exit 0, and the note's modification time unchanged by `verify` (it writes nothing). `verify` prints the count only; never add `--show-quotes` here, which prints every quote, other people's words, into this session.
   ```sh
   N="<the note_path from check 7>"
   grep -m1 "^dossier_updated:" "$N"
   tail -1 "<paths.filing_log>"
   stat -f %m "$N"; "$PY" "$D/dossier.py" verify --config "$LIVE/config.json" --name "$(basename "$N" .md)" --candidate "$N"; echo "exit $?"; stat -f %m "$N"
   ```
9. **The board shows the note** (after step 10). Pass: `1` or more, and the row shows the note's name, date and open questions.
   ```sh
   curl -sS "http://127.0.0.1:$PORT/api/board" | "$PY" -c 'import json,sys; print(sum(1 for r in json.load(sys.stdin)["rows"] if r.get("dossier")))'
   ```
10. **The frontmatter guard judges person notes by the law** (proposal a, proof 3, proven with a probe; after step 2). The first person note (step 10) is written by the daemon's Python, which no hook sees, so this probe is the guard's proof, not that note. First check the guard is mounted on this vault: the grep must name `<vault>` among its paths.
    ```sh
    grep -n MSB_VAULT_PATHS ~/.claude/hooks/my-second-brain-fm-guard.sh
    ```
    When it names only another vault, the guard ignores this one and the probe below would pass on anything: log `skipped: guard registered for another vault` (installing the guard for this vault is my-second-brain's own job, on the owner's yes). When the owner has no frontmatter guard installed, log `skipped: no guard`. Otherwise pass: `exit 0` for a legal person note, then `exit 2` for the same note with a role that is not on the list. The guard only judges the payload it is handed; nothing is written. Use `printf '%s'`, never `echo`: zsh's `echo` turns the `\n` escapes into real line breaks, the guard cannot parse its input, and it lets everything through.
    ```sh
    G=~/.claude/hooks/my-second-brain-fm-guard.sh
    printf '%s' '{"tool_name":"Write","tool_input":{"file_path":"<people_dir>/Guard-Probe.md","content":"---\ntype: person\nroles: [<a role from the list>]\n---\n"}}' | "$G"; echo "exit $?"
    printf '%s' '{"tool_name":"Write","tool_input":{"file_path":"<people_dir>/Guard-Probe.md","content":"---\ntype: person\nroles: [not-a-role]\n---\n"}}' | "$G"; echo "exit $?"
    ```
11. **Automatic notes stay in business worlds** (check again after a few days of running). Pass: notes opened by the daemon itself only in the worlds listed in `dossier.auto_open_worlds`; notes the owner started or rewrote from the board (reason `板上要求开档` or `板上要求重写`) may be anywhere.
    ```sh
    sqlite3 -readonly "$LIVE/state.sqlite" "select c.world, j.reason, count(*) from dossier_jobs j join conversations c on c.jid = j.jid group by 1, 2"
    ```

## Record

Under `### C4 person notes` in `<home>/INSTALL-PROGRESS.md`:

- the answers: notes yes or no, the People room chosen and why, the roles (values only), the marker, the automatic worlds and stage or button only, patch 1 and the id keys yes or no;
- the amendment: the list of files it touched, the guardian's proof result;
- the guard probe: the guard mounted on this vault and its two exit codes, or the `skipped:` reason;
- the paths written into config, and `person.roles_with_stage` when set;
- the `--check-live` last line;
- the first note: its job id, note path, open questions, and the Recent runs row;
- the registry block added;
- `ruled: default 9`, plus the decision note for the People room.

Tick `- [x] C4 person notes` only when every check passes, or with `skipped: <reason>` logged. Next: `install/C5-your-tools.md`.
