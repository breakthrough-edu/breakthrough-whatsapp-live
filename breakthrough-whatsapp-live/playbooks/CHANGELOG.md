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

### 1.0.1 · 2026-10-21

- The launchd line of the summary adds the restart rule: restart the daemon with
  `wa_setup.py restart`, not `launchctl kickstart -k`, which can leave a frozen python
  holding the board port.
- "How we do this work" now says only that the kit's seed put the playbook in this folder.
  Nothing in the method changed.
- The carve-out in the closing comment gives daily content as an example of a
  high-frequency line.

### 1.0.0 · 2026-09-14

- First public seed of the triage folder's door: the four beats in the door's own words,
  a four-line summary of the playbook, a pointer to the standby-machine runbook,
  "What we have learned" empty, "Recent runs" with the header row only.
- The summary names the v1 judgment contract (world, stage, priority, needs_me).

## playbooks/whatsapp-live-triage/whatsapp-live-triage-playbook.md

### 1.1.0 · 2026-10-21

- New weigh 13: "the brain cannot be called" (an expired login or a reached usage limit) is
  not a failed run of that conversation, and there is nothing to fix in the conversation.
  The circuit breaker holds the queue, probes every `brain.gate_probe_seconds`, and pushes
  once when it opens and once when it closes; the fix is logging in once on the always-on
  Mac (or renewing the setup token) or waiting for the limit to reset. Nothing else in the
  method changed.
- Example stories elsewhere in the playbook ("When to run it", weighs 6, 7, 8, 9 and 10, and
  the closing list) were rewritten as generic reasons. Nothing in the method changed.

### 1.0.1 · 2026-10-21

- Weigh 9 adds what to do when a frozen child also stalls the main loop: `/health` answers
  503 (`stalled …`), the daemon pushes one error, and the restart is `wa_setup.py restart`.
  `launchctl kickstart -k` stops only the node parent, so a frozen python can be orphaned
  and keep the board port.
- Move 9's list of what errors mode pushes adds the stalled main loop alert.
- Move 10: stopping the dossier run is `dossier.enabled: false` plus `wa_setup.py restart`
  (was a kickstart).
- Reasons are written as generic mechanisms; the checks and fixes are unchanged. Weigh 2
  points at your own `measure` numbers for the traffic and run count to
  expect.
- Weigh 8 (c) points at the canary in `references/pitfalls.md` section 10 so you can confirm
  the Read deny rule on your own Mac.
- The closing section is renamed "不建议的路, 与原因"; the list itself is unchanged.

### 1.0.0 · 2026-09-14

- First public seed: every tunable is named by its config key and every ruling is written as "we default to X,
  because Y" with a pointer to `references/defaults-and-why.md`.
- Weigh 4 uses the v1 contract (world, stage, priority, needs_me, draft, reason,
  confidence, situation, stake, refs, summary, timeline, plus captures and calendar_hint),
  replacing the retired tag and flow.
- Weigh 8 adds the read rule for the tool cage (`Read(//<absolute path>)` deny entries).
- Weigh 9 covers the Full Disk Access pit (stub python, tccd, sample and pgrep).
- New weigh 11: why the doorbell looks back, the three ways old messages replay, and the
  kit's guard for each. New weigh 12: report what could not be read; reach is the owner's
  own number only.
- Move 9: the observation week may push every judgment; after the first week the default
  is errors only plus the morning digest.
- Move 10 lists the three residents of this kit, including the six cells of the dossier run.

## playbooks/whatsapp-live-triage/standby-machine-runbook.md

### 1.0.1 · 2026-10-21

- Restart steps use `wa_setup.py restart` instead of `launchctl kickstart -k`, which can
  leave a frozen python holding the board port; the remote line runs it over ssh on the
  always-on Mac with that machine's own paths.
- Step 1 adds check 1g (`/health` on the always-on Mac) and a row for "process up, main loop
  stalled" (`stalled`, HTTP 503): look for an unanswered permission dialog on that Mac's
  screen, click it, then restart. The first row ("all alive") now also needs 1g to answer
  ok, because a stalled daemon can still have a pid.
- "How to tell it is down" lists the new stalled main loop alert.
- Step 4 stops the daemon only with `wa_setup.py uninstall-launchd` (it stops the python
  first, then boots out, disables and removes the plist); the manual `launchctl bootout`
  fallback is gone, since it stops only the node parent. The handback stops the always-on
  Mac's daemon the same way over ssh and starts it again with `install-launchd`, instead of
  a raw `bootout` and `bootstrap`.

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

### 1.0.1 · 2026-10-21

- The pits line adds the restart rule for a resident with a child process: not
  `kickstart -k`, stop the child first, then prove one process holds the port.
- The carve-out in the closing comment gives daily content as an example of a
  high-frequency line.

### 1.0.0 · 2026-09-14

- First public seed of the resident-workers folder's door: four beats, summary of the six
  cells, the approval card test and the scheduling pits, "What we have learned" empty,
  "Recent runs" with the header row only.

## playbooks/resident-workers/resident-workers-playbook.md

### 1.0.2 · 2026-10-21

- Example stories were rewritten as generic reasons. Nothing in the method changed.

### 1.0.1 · 2026-10-21

- New pit in section 4: restarting a launchd job that has a child process. `kickstart -k`
  stops the top process only; a frozen child can be orphaned and keep its port. Stop the
  child first (TERM, then KILL after the plist's `ExitTimeOut`), let launchd start a fresh
  one, then prove one process holds the port. WhatsApp Live does this with
  `wa_setup.py restart`.
- Restart steps elsewhere say "restart" instead of naming `kickstart`.
- Reasons are written as generic failure patterns (what happens, why, how to guard), with
  no single incident told step by step; the checks and fixes are unchanged.

### 1.0.0 · 2026-09-14

- First public seed: each hole is described as a generic failure pattern (a stop that does
  not survive a login, a watcher that can write one input twice, a bot that looks idle
  while hung, a card that can write a newer draft).
- Covers four pits: exit 0 is not proof, the allowlist that only adds, a scheduled job's
  first start, and launchd children without Full Disk Access.
- Adds a worked example of the approval card test applied to this kit's dossier run.

## playbooks/person-dossier/_Person-Dossier-Guide.md

### 1.0.1 · 2026-10-21

- The carve-out in the closing comment gives daily content as an example of a
  high-frequency line. Nothing else changed.

### 1.0.0 · 2026-09-14

- First public seed of the person-dossier folder's door: four beats, six-line summary
  keyed to the `dossier.*` config keys, "What we have learned" empty, "Recent runs" with the
  header row only, and the high-frequency carve-out spelled out for the nightly batch.

## playbooks/person-dossier/person-dossier-playbook.md

### 1.0.2 · 2026-10-21

- Example stories were rewritten as generic reasons. Nothing in the method changed.

### 1.0.1 · 2026-10-21

- The reasons behind three rules are written as generic mechanisms (a full rewrite can
  merge or drop a hand-written section while every link check passes; a missing section is
  a common reason a new note is refused), and one example quote is made generic. These
  rewrites change no rule, section name or skeleton line.
- Rule 11 says what the verifier now does with the system record section: one that differs
  from the machine's block only in the pull date on its source line is replaced with the
  machine's current block; any other difference is still refused.

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
