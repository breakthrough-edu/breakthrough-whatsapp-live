---
name: breakthrough-person-dossier
description: "Write, rewrite, check or update a person note (person dossier, profile, 人物档, 真人档) in the owner's vault from their WhatsApp one-to-one history, with the same checks the WhatsApp Live daemon uses. Use when the owner asks to open or rewrite someone's note (\"write X's dossier\", \"rewrite X's note\", \"帮 X 开档\", \"重写 X 的档\"), or tells something about a person to fold into their note (\"update <name>'s note: ...\", \"更新 <名字> 的档: …\"). Follows the owner's person-dossier playbook in their vault and places a note only after every check passes. NOT a customer avatar (a group of people), NOT live triage or reply drafts (the WhatsApp Live board), NOT reading WhatsApp to answer a question (breakthrough-whatsapp-read). Never sends a message."
---

# breakthrough-person-dossier

A thin router. What a note says is ruled by the owner's playbook and its frontmatter by the owner's doctrine; the scripts do the mechanical part, and every check lives in `scripts/dossier_core.py`, the same module the daemon imports.

## Read first

| What | Where |
|---|---|
| How to think about and write a note (the source of truth) | the owner's vault copy of the playbook, config `paths.person_playbook`. Open its folder door `_Person-Dossier-Guide.md` first: settle open rows, do the work, register one row |
| Legal frontmatter: keys, the closed roles list, stages | the owner's doctrine, config `paths.doctrine`, family `person` |
| Paths, owner label and marker, section names the checks rely on | the live config (`$WA_LIVE_CONFIG`, default `~/WhatsApp-Live/live/config.json`): `paths.*`, `owner_label`, `owner_marker`, `person.section_roles` |
| What the checks will read right now | `/usr/bin/python3 $SKILL/scripts/dossier_core.py --check-live --config <live config>` |

`$SKILL` is this skill's folder: `~/.claude/skills/breakthrough-person-dossier` for a global install, `<project>/.claude/skills/breakthrough-person-dossier` for one installed into a project. `<live config>` is `<paths.home>/live/config.json` of the install this note belongs to (its progress file header names the home). Pass `--config <live config>` to every command: without it the scripts fall back to `$WA_LIVE_CONFIG`, then to the default install's config, which on a Mac with two installs is the other one.

## Write or rewrite a note

1. `/usr/bin/python3 $SKILL/scripts/dossier.py prepare --config <live config> --name "<note file name without .md>" --reason "<why now>"`
   Add `--jid <wa_jid>` for a person with no note yet. It pulls 30 days of the one-to-one chat (`--days` to change), the vault candidates, the board's judgment and the facts section into a draft folder, and prints that folder.
2. In this session, read `prompt.md` from that folder whole and write the complete note into `candidate.md` in the same folder with the Write tool. The prompt carries the playbook; follow it rather than improvising.
3. `/usr/bin/python3 $SKILL/scripts/dossier.py place --config <live config> --name "<name>" --candidate <draft>/candidate.md --reason "<why>"`
   Runs every check plus the quote check, re-reads the note on disk, backs it up, writes it and appends one filing-log line. `verify` with the same arguments checks without writing (`--existing <file>` rehearses the next rewrite). A low quote hit rate refuses: run it again with `--show-quotes` to see which quotes were not found (they are other people's words, so they print only when asked), then add `--force` only if they are real.
4. Register one row in the door's Recent runs. The scripts never do this.

## Update a note with what the owner said

For "update <name>'s note: ...": run step 1 with the owner's words verbatim, `prepare --config <live config> --name "<name>" --owner-input "<their words>"`. The prompt then opens with those words and three rules: they are the highest source, only the related cells change (each tagged `🗣 <marker> <date>`), and a ⬜ they answer becomes a fact. The owner notes section keeps one verbatim line. Then steps 2 to 4. Any later rewrite that loses an owner line or tag is refused.

To undo an update, copy the newest file in `<live>/dossier-backups/<name>/` back over the note and add a filing-log line saying so.

## Hard lines

- ⛔ Never sends a message to anyone. Only `place` writes into the vault, and only after the checks pass.
- ⛔ A `✗ FAIL` names the broken rule: fix the candidate, never the check. A rule that is wrong changes in the owner's playbook or doctrine, with the owner's yes.
- "person.section_roles names sections the playbook skeleton does not have" means a section was renamed in one place only: make the config and the playbook say the same name.
