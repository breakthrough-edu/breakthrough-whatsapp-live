---
kind: seed-changelog
scope: playbooks/
---

# Playbook seeds: changelog

Every seed file in this folder carries a stamp right after its frontmatter:
`<!-- seed: breakthrough-whatsapp-live playbooks/<folder>/<file> v<semver> -->`.
The owner's vault copy keeps the stamp it was copied with, so `wa_setup.py seed-status` can
compare it with the version here. When a seed's text changes, the maintainer bumps that
file's version and adds an entry below. `install/update.md` shows the owner the entries
between the two versions and merges section by section with the owner's yes.

⛔ An update never overwrites an owner copy, and never touches a door's "What we have
learned" or "Recent runs": those are the owner's history, not seed text.

This changelog is payload metadata. It is never copied into a vault.

## playbooks/whatsapp-live-triage/_WhatsApp-Live-Triage-Guide.md

### 1.0.0 · 2026-09-14

- First public seed of the triage folder's door: the four beats in the door's own words,
  a four-line summary of the playbook, a pointer to the standby-machine runbook,
  "What we have learned" empty, "Recent runs" with the header row only.
- The summary names the v1 judgment contract (world, stage, priority, needs_me).

## playbooks/whatsapp-live-triage/whatsapp-live-triage-playbook.md

### 1.0.0 · 2026-09-14

- First public seed, generalized from a running install: no people, businesses, machines,
  bots or vault paths of the original owner; every tunable is named by its config key and
  every ruling is written as "we default to X, because Y" with a pointer to
  `references/defaults-and-why.md`.
- Weigh 4 uses the v1 contract (world, stage, priority, needs_me, draft, reason,
  confidence, situation, stake, refs, summary, timeline, plus captures and calendar_hint),
  replacing the retired tag and flow.
- Weigh 8 adds the read rule for the tool cage (`Read(//<absolute path>)` deny entries).
- Weigh 9 folds in the Full Disk Access lesson (stub python, tccd, sample and pgrep).
- New weigh 11: why the doorbell looks back, the three ways old messages replay, and the
  kit's guard for each. New weigh 12: report what could not be read; reach is the owner's
  own number only.
- Move 9: the observation week may push every judgment; after the first week the default
  is errors only plus the morning digest.
- Move 10 lists the three residents of this kit, including the six cells of the dossier run.

## playbooks/whatsapp-live-triage/standby-machine-runbook.md

### 1.0.0 · 2026-09-14

- First public seed of the cold-start runbook (always-on Mac down, standby Mac takes over,
  handing back), with placeholders for the home folder, label, port, relative live folder, ssh
  alias and backup destination (no kit default is written as a value; the intake label is the
  label plus `.intake`).
- Step 2 prerequisites add the companion skills (`breakthrough-whatsapp-read`,
  `breakthrough-person-dossier`), the logins the interfaces chosen in C5 need, the synced
  vault, and waiting for WhatsApp Desktop's history sync before starting the daemon.
- The evening intake job is covered: decide at takeover, install with O7, stop at handback.
- Step 3 resets `meta.last_pk`, `inbound_log` and `pending` together, explains the lookback
  replay and the silent number collision a restored `inbound_log` causes, and relies on the
  kit's first-scan `skip:before_install` behavior; it also fixes paths with `check-config`
  and `sync-reader-config`, restores the owner's own `adapters/`, and leaves `seeds.json`
  and `acceptance.json` to the standby's own install.
- Step 4: stopping the standby copy means bootout plus the plist leaving
  `~/Library/LaunchAgents/`, so a reboot cannot bring back a second running copy.

## playbooks/resident-workers/_Resident-Workers-Guide.md

### 1.0.0 · 2026-09-14

- First public seed of the resident-workers folder's door: four beats, summary of the six
  cells, the approval card test and the scheduling pits, "What we have learned" empty,
  "Recent runs" with the header row only.

## playbooks/resident-workers/resident-workers-playbook.md

### 1.0.0 · 2026-09-14

- First public seed, generalized: the real cases stay as anonymous stories (a stop that did
  not survive a login, a watcher that booked one invoice twice, a bot that looked idle while
  hung, a card that wrote a newer draft).
- Folds in four lessons that the seed cannot link to: exit 0 is not proof, the allowlist
  that only adds, a scheduled job's first start, and launchd children without Full Disk
  Access.
- Adds a worked example of the approval card test applied to this kit's dossier run.

## playbooks/person-dossier/_Person-Dossier-Guide.md

### 1.0.0 · 2026-09-14

- First public seed of the person-dossier folder's door: four beats, six-line summary
  keyed to the `dossier.*` config keys, "What we have learned" empty, "Recent runs" with the
  header row only, and the high-frequency carve-out spelled out for the nightly batch.

## playbooks/person-dossier/person-dossier-playbook.md

### 1.0.0 · 2026-09-14

- First public seed of the dossier spec that the dossier code reads every run.
- The `### 骨架` fence keeps the Chinese section names the code parses; the owner's notes
  section is `## <marker> 手记` (the owner's initials from `owner_marker`), and the lock
  comment and spoken marks use the same `<marker>`.
- The system record section is `## 系统记录 (自动)`, fed by the configured facts source.
- One sentence explains that renaming a section must be mirrored in
  `person.section_roles`.
- Trigger thresholds point at `dossier.auto_open_worlds`, `dossier.auto_open_min_stage` and
  the nightly keys, each with its default.
- Landing describes the safe placement: re-read the target before writing, never overwrite
  a note the run did not start from.
