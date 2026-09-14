# Config reference

Every key the kit's code reads from `live/config.json`, grouped by section, plus the keys computed at load, the reader config and `live/seeds.json`. This page was built by reading the code, not the design; when a module and this page disagree, the module is right. Why each default is what it is lives in `references/defaults-and-why.md`; how the five tool connections work lives in `references/interfaces.md`.

## How loading works

- **Where.** `--config`, else the environment variable `WA_LIVE_CONFIG`, else `~/WhatsApp-Live/live/config.json`. `wa_setup.py init` writes the file (0600) from `daemon/templates/config.example.json`, dropping its `_help` object, with the tools and whisper model it finds.
- **One loader.** Every module, the daemon, the command line tools and the evening job call `wa_common.load_config`, which fills every default the same way. A missing or `null` key gets its default; an object or a list of the wrong type is replaced by its default with a load warning; a scalar of the wrong type is kept as written (`wa_setup.py check-config` reports it). `wa_common.py --print-defaults` prints every default the loader fills (the keys marked ◇ below are filled where they are read instead).
- **Placeholders** in string values: `<home>` (`paths.home`), `<live>` (`paths.live_dir`), `<payload>` (the installed kit), `<vault>` (only when `paths.vault` is set), and a leading `~`. They are never expanded inside owner text: `vocab`, `group_policies`, `my_names`, `owner_label`, `owner_marker`, `my_phone`, `ignored_jids`. `<auto>` in `bin.*` and `doorbell.wal_path` is looked up at load; a tool that is not found becomes `""`.
- **Vault paths.** When `paths.vault` is set, the loader resolves a relative `paths.people_dir`, `paths.doctrine`, `paths.person_playbook`, `paths.filing_log`, `paths.decisions_dir`, `paths.intake_buffer` and `intake.buffer` against it, and fills an empty `paths.doctrine` with `99_Meta/structure-doctrine.md` and an empty `paths.filing_log` with `99_Meta/filing-log.md` inside the vault. Every module therefore reads the same absolute path.
- **Checking.** `wa_setup.py check-config` reports unknown keys, wrong types and values, vocabulary keys that do not exist, and enabled stations missing their paths. Keys starting with `_` are skipped, so `"_note"` entries are safe. It accepts a facts source, second source or calendar destination the kit does not ship when your own implementation of it sits in `<live>/adapters/` (`references/interfaces.md`, "Where a new implementation lives").

## How to read the tables

**Default** is written as `wa_common.py --print-defaults` prints it (JSON). **Station** is the wizard station the key belongs to: the one that asks for the value or sets it, or, for a tuning key no station asks about, the station whose feature it tunes (that station leaves its default alone unless the owner asks): C0 fit · C1 reader · C2 daemon · C3 board · C4 person notes · C5 your tools · O1 facts source · O2 second message source · O3 calendar · O4 push outlet · O5 nightly backup · O6 morning digest · O7 intake · O8 voice loop · O9 summary backfill · O10 standby machine · O11 observation and acceptance; `none` means no station touches it.

**Restart** says what it takes for a change to land:

| Value | Meaning |
|---|---|
| `restart` | The daemon reads it from the config it loaded at start: `launchctl kickstart -k gui/$(id -u)/<launchd.label>`. Command line tools see the change at once. |
| `sync` | Also copied into the reader config: run `wa_setup.py sync-reader-config` (a daemon start does the same, and each run then picks it up). |
| `reinstall` | Written into a launchd plist: `wa_setup.py render-launchd` then `install-launchd` (add `--intake` for the evening job), which also restarts that job. |
| `no` | Read fresh wherever it is used (a command line tool, the evening job, `check-config`). |

◇ marks a key that `config.example.json` lists but `wa_common.DEFAULTS` does not. `init` writes it into a new config, `check-config` knows it and the wizard can read its `_help` line; a config without the key gets the default applied where the key is read, and `wa_common.py --print-defaults` does not print it.

## Owner and identity

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `version` | `1` | integer | Config format version; `check-config` reports any other value. Nothing else reads it. | C1 | no |
| `my_phone` | `""` | digits with country code, no plus sign | Mention detection for groups set to `mention` (the text holds `@<my_phone>` or `@<your WhatsApp LID>`); the reader's own number (guessed from group ids, with a warning, when empty). | C1 | restart, sync |
| `my_names` | `[]` | list of strings | Mention detection: names in Latin letters match whole words only, names in other scripts match anywhere in the text. Also the reader's stop words, and your name when `owner_label` is empty. | C1 | restart, sync |
| `owner_label` | `""` | string | What prompts, person notes, intake entries and the board call you. Empty: the first of `my_names`, else a generic word for the owner. | C1 | restart, sync |
| `owner_marker` | `""` | 2 to 6 ASCII letters | The tag in `🗣 <marker> <date>` marks and `<!-- <marker> -->` lock segments in person notes, and your tag in the second source prompt. Empty: the initials of `owner_label` (the first two letters of a one word label), else `ME`. | C4 | restart |

## The gate

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `direct_policy` | `"all"` | `all` or `skip` | `all`: every inbound one to one message can start a run. `skip`: one to one chats never start one by themselves (board buttons and test runs still work). Summary backfill follows it while `backfill.respect_gate` is true. | C2 | restart |
| `group_policies` | `{}` | object, group jid to entry | Per group: `all` (every message), `mention` (only messages that mention you) or `skip`. An entry is `{"policy", "name", "in30", "defaulted"}` or a bare policy string. Only `policy` is read by the gate; `name` (the group name), `in30` (its 30 day message count) and `defaulted` (true when you never chose it) are notes for you and the wizard. A policy word the gate does not know acts as `skip`, with a warning. | C2 | restart |
| `unlisted_group_policy` | `"skip"` | `all`, `mention` or `skip` | Policy of every group not in `group_policies`, including groups created later. | C2 | restart |
| `ignored_jids` | `[]` | list of jids | Logged as `skip:ignored`; never run, never read into a conversation pack, never backfilled. | C2 | restart, sync |

## `debounce`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `debounce.quiet_seconds` | `45` | number | A burst starts once its chat has been quiet this long. | C2 | restart |
| `debounce.max_wait_seconds` | `180` | number | A burst starts at the latest this long after its first message, even while messages keep coming. | C2 | restart |
| `debounce.global_concurrency` | `2` | integer | Brain runs allowed at the same time (one per conversation always). | C2 | restart |

## `doorbell`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `doorbell.enabled` | `true` | boolean | `false`: messages are still logged (as `skip:doorbell_off`) and your replies still mark conversations, but no run starts by itself; the board, its buttons, command files and scheduled jobs keep working. | C2 | restart |
| `doorbell.wal_path` | `"<auto>"` | path | The file whose changes wake a scan; `<auto>` is `ChatStorage.sqlite-wal` in WhatsApp Desktop's group container (or under `WA_LIVE_WA_ROOT`). Its age drives the stale alert. | C1 | restart |
| `doorbell.poll_seconds` | `2` | number | How often the main loop checks that file. | C2 | restart |
| `doorbell.stale_alert_hours` | `6` | number | An error push, at most one every 6 hours, when the database has not changed for this long; also shown on the board. | O4 | restart |
| `doorbell.full_rescan_seconds` | `60` | number | Query the database at least this often, even when the file looks unchanged. | C2 | restart |
| `doorbell.lookback` | `300` | integer | Message ids behind the watermark each scan checks for messages written late. On a fresh state database they are logged `skip:before_install`, never judged. | C2 | restart |
| `doorbell.max_message_age_hours` | `48` | number | Inbound older than this is logged `skip:too_old` instead of judged (a relinked WhatsApp Desktop can flood old messages under new ids). `0` turns the check off. | C2 | restart |

## `brain`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `brain.model` | `"claude-sonnet-5"` | model name | Model of every triage run, and of the second source's nightly call, summary backfill, `probe-claude` and the evening intake pre-sort. | C2 | restart |
| `brain.cwd` | `"<home>/brain-cwd"` | path | Empty working folder of every headless `claude` run. Keep it outside Documents, Desktop and Downloads; the daemon creates it. | C1 | restart |
| `brain.context_days` | `7` | integer | Days of one to one history in a full (grounding) read; also the window of a `prewarm` command file without a number. | C2 | restart |
| `brain.group_context_days` | `3` | integer | Days of group history in a full read. | C2 | restart |
| `brain.warm_days` | `2` | integer | When a chat already has a summary, a run reads the days since its last judgment, at least this many and at most the full window. | C2 | restart |
| `brain.ground_every_days` | `7` | number | A full read is forced when the chat's last grounding is older than this. | C2 | restart |
| `brain.run_timeout_seconds` | `600` | integer | A `claude` run slower than this is killed and counted as failed; backfill batches get at least 900. | C2 | restart |
| `brain.retries` | `1` | integer | Extra attempts after a failed triage run. | C2 | restart |
| `brain.sweep_timeout_seconds` | `1500` | integer | Time limit of the reader building a conversation pack, voice transcription included (triage, person notes, backfill). | C2 | restart |
| `brain.max_turns` | `12` | integer | Turn limit of a triage run. | C2 | restart |
| `brain.whisper_model` | `"~/.cache/whisper-models/openai_whisper-large-v3-v20240930"` | path | Whisper model folder, handed to the reader as `WA_WHISPER_MODEL` when the folder exists. Keep it under `~/.cache` (a model under Documents stalls under launchd). `init` fills it when it finds one model folder under `~/.cache/whisper-models`. | C1 | restart, sync |
| `brain.whisper_language` | `""` | string | Transcription language hint such as `zh` or `en`; empty lets whisper detect. | C1 | sync |
| `brain.transcribe` | `true` | boolean | `false`: no new transcription anywhere; transcripts already in the cache still show. | C1 | restart, sync |

## `bin`

Every `bin` value is resolved at load. `<auto>`, a bare name, or a configured path that no longer exists is searched in `PATH`, `/opt/homebrew/bin`, `/usr/local/bin`, `~/.npm-global/bin`, `~/.local/bin`, `~/.claude/local`, `~/.bun/bin`, `~/.volta/bin`, nvm installs (newest first), then the system folders.

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `bin.claude` | `"<auto>"` | path | The `claude` command line for every headless run. | C1 | restart |
| `bin.python` | `"/usr/bin/python3"` | path | The Python the plist starts the daemon with (its scripts run on the same one); command line tools use it for the reader. macOS system Python 3.9 is the tested one. | C1 | reinstall |
| `bin.node` | `"<auto>"` | path | The node binary launchd starts (`run.mjs`); this exact binary needs Full Disk Access. Also runs `lark-cli` when that is a node script. | C1 | reinstall |
| `bin.ffmpeg` | `"<auto>"` | path | Converts voice notes before transcription (reader). | C1 | sync |
| `bin.whisperkit` | `"<auto>"` | path | `whisperkit-cli`, the local transcription engine (reader). | C1 | sync |
| `bin.lark_cli` | `"<auto>"` | path | `lark-cli`, needed only by a Lark implementation. | O1, O2, O3 | restart |
| `bin.path_env` | `"<auto>"` | PATH string | PATH of the daemon under launchd and of every child it starts. `<auto>` is the folders of the tools above plus the system folders. | C1 | reinstall |

## `paths`

Write paths absolute, with `~`, as `<vault>/...`, or, for the vault keys named under "How loading works", relative to the vault root: the loader resolves those against `paths.vault` for every module. Any other path (`paths.home`, `paths.live_dir`, `backup.dest`, `facts.csv.path` and the rest) is never resolved against the vault; write it absolute or with `~`.

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `paths.home` | `"~/WhatsApp-Live"` | path | The kit's working folder: reader snapshots, transcript cache, `brain-cwd`, launchd copies, `live/`. When the key is empty and the config sits in a folder named `live`, that folder's parent is used. Must stay outside Documents, Desktop, Downloads and cloud folders. `init --home` writes it. | C0 (chosen), C1 (written) | reinstall |
| `paths.live_dir` | `"<home>/live"` | path | The daemon folder: config, state database, logs, runs, commands, prompts. When the key is empty, the folder holding the loaded config is used. | C1 | reinstall |
| `paths.vault` | `""` | path | Your vault root: topic map files, the reader's vault lookup, person notes, and the `<vault>` placeholder. Empty: no vault features. | C1 | restart, sync |
| `paths.people_dir` | `""` | path | Folder of person notes, used to match chats to notes (facts, second source, board, note runs, intake) and by the reader's vault lookup. A missing folder is an error, never an empty index. | C4 | restart, sync |
| `paths.doctrine` | `""` | path | Your vault's structure law, read for the `person` family: keys, closed `roles` and `stage` lists, role meanings. Empty with `paths.vault` set: the loader fills `<vault>/99_Meta/structure-doctrine.md`. Empty without a vault: nothing, and `check-config` reports it while `dossier.enabled` is true. | C4 | restart |
| `paths.person_playbook` | `""` | path | Your vault copy of the person dossier playbook; its skeleton lists the sections every note must have. Missing: the payload seed is used, with a WARNING. | C4 | restart |
| `paths.intake_buffer` | `""` | path | Same file as `intake.buffer`; set one of them. When both are set and differ, `intake.buffer` wins with a load warning. | O7 | restart |
| `paths.filing_log` | `""` | path | Your vault's filing log: one line per person note placement, appended (the file is created when its folder exists). Empty with `paths.vault` set: the loader fills `<vault>/99_Meta/filing-log.md`. Empty without a vault: no line, and `check-config` warns while `dossier.enabled` is true. | C4 | restart |
| `paths.decisions_dir` | `""` | path | Your Decisions folder. The reader config lists it as a person folder for vault lookups; the wizard logs rulings there. | C4 | sync |
| `paths.reader_dir` | `"<payload>"` | path | Where `wa_sweep.py` and `wa_context.py` are found: that scripts folder, its parent, or a payload root holding `skills/*-whatsapp-read/scripts`. | none | restart |
| `paths.dossier_dir` | `"<payload>"` | path | Where `dossier_core.py` is found, the same way (`skills/*-person-dossier/scripts`). | none | restart |

## `vocab`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `vocab.worlds` | three entries, below | list of entries | Which part of your life, or which business, a conversation belongs to. Set by the first judgment and then kept; a key the brain invents falls back to `other` (else the last non business world). | C2 | restart |
| `vocab.stages` | seven entries, below | list of entries | Stages of a business conversation in forward order. The brain only moves forward; only you move a stage back, on the board. Must equal the `stage` list of your doctrine's `person` family. | C2 | restart |
| `vocab.park_from` | `["new", "talking", "offered"]` | list of stage keys | Stages from which a conversation may move to the side stage. The last one is also where `stage_rules.parked_after_days` applies. | C2 | restart |
| `vocab.priorities` | three entries, below | list of entries | How urgent the next move is, most urgent first, judged fresh on every run. The last one is used when you reply; the digest orders open conversations by this list. | C2 | restart |
| `vocab.business_context` | `""` | string | One paragraph: what each business world sells, price points, how people pay. It fills `{BUSINESS_CONTEXT}` in the brain prompt; empty tells the brain to be less sure about business worlds. | C2 | restart |

Entry fields:

| Field | In | Meaning |
|---|---|---|
| `key` | all | The stored value: lowercase letters, digits and underscore |
| `label` | all | Label on the board when `board.lang` is `zh`, and in the brain's tables |
| `label_en` | all | Label on the board when `board.lang` is `en`; defaults to `label` |
| `business` | worlds | `true`: the world carries a stage, the payment floor, automatic person notes and the business context |
| `side` | stages | `true` marks the side stage; without it, the stage keyed `parked` is the side stage |
| `criteria` | all | Optional rule text the brain reads for this value instead of the built in hint; `{owner}` becomes `owner_label` |

`check-config` accepts `side` and `criteria`, and the judgment rules, the time rule and the person note writer take the stage marked `side: true` as the side stage, else the stage keyed `parked`. Two places still go by the key: while `stage_rules.parked_after_days` is above 0, `check-config` reports an error when no stage is keyed `parked`, and the board draws only the stage keyed `parked` as the gray side stage. So keep the key `parked` for the side stage. A bare string entry loads (label and key the same) but `check-config` reports it. An empty list falls back to the defaults:

```json
"vocab": {
  "worlds": [{"key": "personal", "label": "私人", "label_en": "Personal", "business": false},
             {"key": "work", "label": "工作", "label_en": "Work", "business": true},
             {"key": "other", "label": "其他", "label_en": "Other", "business": false}],
  "stages": [{"key": "new", "label": "新", "label_en": "New"},
             {"key": "talking", "label": "在谈", "label_en": "Talking"},
             {"key": "offered", "label": "已报价", "label_en": "Offered"},
             {"key": "won", "label": "已成交", "label_en": "Won"},
             {"key": "active", "label": "服务中", "label_en": "Active"},
             {"key": "done", "label": "结束", "label_en": "Done"},
             {"key": "parked", "label": "搁置", "label_en": "Parked"}],
  "park_from": ["new", "talking", "offered"],
  "priorities": [{"key": "now", "label": "现在", "label_en": "Now"},
                 {"key": "soon", "label": "这几天", "label_en": "Soon"},
                 {"key": "low", "label": "不急", "label_en": "Low"}],
  "business_context": ""
}
```

## `stage_rules`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `stage_rules.parked_after_days` | `21` | integer | Every 10 minutes, a conversation at the last stage of `vocab.park_from` (default `offered`) with no message either way and no stage change for this many days moves to the side stage, marked automatic, its previous stage kept. `0` (or a negative number) switches the rule off. | C2 | restart |
| `stage_rules.group_date_done` | `false` | boolean | `true`: a group in a business world whose name carries a day/month date more than a day past moves to `done` (or the last stage when there is no `done`). Why it ships off: `references/defaults-and-why.md`, default 8. | C2 | restart |

## `board`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `board.enabled` | `true` | boolean | Serve the board. | C3 | restart |
| `board.bind` | `"127.0.0.1"` | address | Address the board listens on. Keep a loopback address and reach the board through `tailscale serve`: it has no login. | C3 | restart |
| `board.port` | `8787` | integer | Board port on this Mac; `tailscale serve` points at it. Change it only on a clash, then change the serve command too. | C0 (chosen), C1 (written) | restart |
| `board.others_days` | `3` | integer | The Others list shows chats active in the last this many days. | C3 | restart |
| `board.others_limit` | `120` | integer | Maximum rows in the Others list. | C3 | restart |
| `board.lang` | `"zh"` | `zh` or `en` | Board labels, and the language of what the machine writes for you: digest, intake entries, facts lines, second source news lines. | C3 | restart |
| `board.show_usd` | `false` | boolean | Show each run's reported dollar figure on the board; tokens and seconds always show. | C2 | restart |

## `notify`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `notify.outlet` | `"none"` | `none`, `telegram` or `command` | Where pushes go (`references/interfaces.md`, 4). | O4 | restart |
| `notify.mode` | `"errors"` | `off`, `errors`, `needs_me` or `all` | Which kinds go out: `errors` sends failures and the digest, `needs_me` adds judgments that need you, `all` adds every judgment, `off` sends nothing. An unknown value counts as `errors`. | O4 (C2 starts at `errors`) | restart |
| `notify.telegram_file` | `"<live>/telegram.json"` | path | JSON file with `bot_token` and `chat_id`, kept 0600; its content is read at every push. | O4 | restart |
| `notify.command` | `""` | shell command | For outlet `command`: run with `/bin/sh -c`, the text on stdin ending in a newline (so pushes appended to a file land one per line), exit 0 means delivered. Placeholders such as `<home>` are filled in. | O4 | restart |

## `dossier`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `dossier.enabled` | `false` | boolean | Write and refresh person notes in the vault. `false` stops the whole block: no queue, no new notes, no rewrites, no recent lines. | C4 | restart |
| `dossier.model` | `"claude-opus-5"` | model name | Model of person note runs. | C2, C4 | restart |
| `dossier.auto_open_worlds` | `[]` | list of world keys | A one to one chat in one of these worlds, with no note yet, gets a note once its stage reaches `auto_open_min_stage`. | C4 | restart |
| `dossier.auto_open_min_stage` | `"talking"` | stage key | The stage from which a note opens automatically; the side stage counts as reached. A key not in the stage order means the second stage. | C4 | restart |
| `dossier.pack_days` | `30` | integer | Days of history read for a person note run. | C4 | restart |
| `dossier.nightly_at` | `"02:30"` | `HH:MM` | Local time of the nightly refresh. A daemon start after this time counts today as done. | C4 | restart |
| `dossier.nightly_min_msgs` | `5` | integer | A person with a note and at least this many messages in 24 hours (both ways, what they said in groups included) is refreshed that night; so is a person whose stage changed. | C4 | restart |
| `dossier.nightly_cap` | `8` | integer | At most this many notes queued a night; also caps the paying customers each facts run queues, and those note runs start at once (so the first `facts-now` at station O1 can start this many runs in the daytime). | C4 | restart |
| `dossier.stale_days` | `14` | integer | A note not rewritten for this long is refreshed when there was any message in that window. | C4 | restart |
| `dossier.min_gap_hours` | `6` | number | Minimum hours between two runs for the same person, failed runs included; a board press skips the gap. | C4 | restart |
| `dossier.recent_lines` | `12` | integer | Lines kept in a note's machine recent block, newest first. | C4 | restart |
| `dossier.max_turns` | `24` | integer | Turn limit of a person note run. | C4 | restart |
| `dossier.run_timeout_seconds` | `1200` | integer | Time limit of a person note run. | C4 | restart |
| `dossier.backups_keep` ◇ | `3` | integer | Copies per note kept in `<live>/dossier-backups/`; what is on disk is backed up before every placement. | C4 | restart |

## `person` ◇

Read only by `skills/breakthrough-person-dossier/scripts/dossier_core.py` (the daemon's note runs and `dossier.py` share it). `dossier_core.py --check-live --config "$LIVE/config.json"` prints what the checks will use.

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `person.section_roles` ◇ | below | object | The headings and labels the note checks rely on, by role. Every non empty heading must exist in your playbook's skeleton, or the checks refuse; `""` switches that guard off on purpose. `<marker>` and `{OWNER}` are filled in. | C4 | restart |
| `person.roles_with_stage` ◇ | `["lead", "member", "client"]` | list of role values | People holding one of these roles carry a stage; everyone else never does. Must share a value with your doctrine's roles. | C4 | restart |
| `person.paid_stages` ◇ | `["won", "active", "done"]` | list of stage keys | Stages a note may hold only with a payment record (or when the old note already held them); otherwise the stage is capped just below. | C4 | restart |
| `person.house_punctuation` ◇ | `false` | boolean | `true`: before a note is placed, long dash characters, and double hyphens or short dashes between spaces, become commas. | C4 | restart |

`person.section_roles` defaults (the seed playbook's own headings; an owner who rewrites the playbook in another language sets these to match):

```json
{"gaps": "## ⬜ 下次见面要问的", "notes": "## 这份档的备注", "owner_notes": "## <marker> 手记",
 "recent": "## WhatsApp 近况", "facts": "## 系统记录", "next_message": "下一封怎么开口", "privacy_hint": "私事"}
```

| Role | Guard behind it |
|---|---|
| `gaps` | Heading whose numbered questions are counted as open gaps |
| `notes` | Heading whose items may never be lost in a rewrite |
| `owner_notes` | Heading of your own words: every line stays, word for word |
| `recent` | Heading of the machine recent block; its begin and end markers must be there |
| `facts` | Heading of the machine system records section |
| `next_message` | Label of the line the board shows as how to open the next message |
| `privacy_hint` | A word of the fixed privacy line at the end of a note; a note without it gets a warning |

## `facts`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `facts.enabled` | `false` | boolean | Run the nightly facts job. | O1 | restart |
| `facts.source` | `"none"` | `none`, `csv` or `lark` | Which implementation reads your records (`references/interfaces.md`, 1). | O1 | restart |
| `facts.at` | `"03:00"` | `HH:MM` | Local time of the facts job. A daemon start after this time counts today as done (`commands/facts-now.json` runs it at once). | O1 | restart |
| `facts.label` | `""` | string | Name of your records in the brain's lines, person notes and the digest, for example `Jobs sheet`. Empty: `CSV` or `Lark`. | O1 | restart |
| `facts.currency` | `""` | string | Currency of amounts that carry none, for example `USD`. | O1 | restart |
| `facts.phone_default_cc` | `""` | digits | Country code given to numbers written with a leading `0` (a leading `00` is dropped, a `+` number is kept as is). Empty: such numbers stay as written. | O1 | restart |
| `facts.phone_valid` | `[]` | list | Accepted number shapes, used only to list unparsable numbers (matching never depends on it): `"60:11-12"` (country code, then total digits), `"44"`, or `{"cc", "len", "min_len", "max_len", "label"}`. Empty: any 8 to 15 digits. | O1 | restart |
| `facts.recent_days` | `30` | integer | A paying customer with a chat but no content message (text, media, voice) in this many days is listed as silent in the digest instead of being queued for a note. | O1 | restart |
| `facts.floor` | `{"paid": "won", "paid_attended": "active", "via_attended": "active"}` | object of stage keys | The lowest stage for: own payment; own payment and attended; attended through someone else's purchase. A stage missing from `vocab.stages` means no floor for that case. | O1 | restart |
| `facts.csv.path` | `""` | path, https link, or a list of them | Source of `csv`; several files are concatenated. | O1 | restart |
| `facts.csv.columns` | `{}` | object, field to header | `phone` (required), `name`, `paid`, `amount`, `currency`, `method`, `date`, `product`, `attended`, `record_id`. | O1 | restart |
| `facts.csv.day_first` | `true` | boolean | `true` reads `03/04/2031` as 3 April, `false` as March 4. | O1 | restart |
| `facts.lark.people_tables` | `[]` | list of table entries | Lark tables listing people: `label`, `base`, `table`, `kind`, `fields` (`name`, `phone`, `date`, `product`, `role`, `via_link`, `attended`, `tag`, `flags`, `external_id`). | O1 | restart |
| `facts.lark.payment_tables` | `[]` | list of table entries | Lark tables listing payments: `label`, `base`, `table`, `currency`, `fields` (`date`, `amount`, `currency`, `method`, `product`, `participants`, `remark`, `external_id`, `name`, `person_link`, `phone`). | O1 | restart |

## `second_source`

The keys read through `wa_second_source.opt` (`whitelist`, `skip_chat_ids`, `system_name_patterns`, `world_by_chat`, `world_keywords`, `prompt_max_chars`, `map_roles`, `name_stopwords`) may sit under `second_source.<source>` or directly under `second_source`; the per source spelling wins. Because the defaults always fill `second_source.lark.whitelist`, a whitelist written directly under `second_source` is never used by `lark`.

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `second_source.enabled` | `false` | boolean | Run the nightly pull. | O2 | restart |
| `second_source.source` | `"none"` | `none` or `lark` | Which implementation pulls (`references/interfaces.md`, 2). | O2 | restart |
| `second_source.at` | `"03:10"` | `HH:MM` | Local time of the nightly pull, for yesterday. | O2 | restart |
| `second_source.label` ◇ | `""` | string | Name of the source in the prompt and in the digest. Empty: the source's own name (`Lark`). | O2 | restart |
| `second_source.id_key` ◇ | `""` | frontmatter key | Key on person notes holding this source's sender ids. Empty: the implementation's own (`lark_id`). | O2 | restart |
| `second_source.world_by_chat` ◇ | `{}` | object, chat id to world key | Fixed world guess for a chat; beats every other guess. | O2 | restart |
| `second_source.world_keywords` ◇ | `{}` | object, word to world key | World guess when a chat title holds the word and nothing stronger applies. | O2 | restart |
| `second_source.prompt_max_chars` ◇ | `60000` | integer | Size limit of the prompt's message area; over it, every chat keeps its newest messages in proportion to its size. | O2 | restart |
| `second_source.map_roles` ◇ | `[]` | list of role values | `--probe-members`: list notes with these roles for id mapping. Empty: notes that have no id key yet. | O2 | no |
| `second_source.name_stopwords` ◇ | `[]` | list of words | `--probe-members`: words ignored when matching names. | O2 | no |
| `second_source.lark.whitelist` | `"<live>/second-source-whitelist.json"` | path | The whitelist file (format in `references/interfaces.md`, 2), read at every pull. Missing, unreadable, or nothing to pull is an error. | O2 | restart |
| `second_source.lark.owner_ids` ◇ | `[]` | list of ids | Your own Lark ids. Empty: asked from `lark-cli auth status`. | O2 | restart |
| `second_source.lark.page_limit` ◇ | `50` | integer | Page size of each message list call. | O2 | restart |
| `second_source.lark.timeout_seconds` ◇ | `120` | integer | Time limit of one `lark-cli` call. | O2 | restart |
| `second_source.lark.skip_chat_ids` ◇ | `[]` | list of chat ids | Never pulled, whatever the whitelist says. | O2 | restart |
| `second_source.lark.system_name_patterns` ◇ | `["customer support", "lark assistant", "飞书助手", "飞书小助手", "小助手", "服务台", "helpdesk"]` | list of words | Chats whose names hold one of these (built in helper chats) are never pulled. `init` writes these seven words from the example; a config without the key (or with an empty list) uses the adapter's own `SYSTEM_NAME_PATTERNS`, the same seven. | O2 | restart |

## `calendar`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `calendar.destination` | `"none"` | `none`, `ics` or `lark` | What the board's "Add to calendar" creates (`references/interfaces.md`, 3); `none` hides the button. | O3 | restart |
| `calendar.timezone` ◇ | `""` | IANA zone name | Zone of timed events. Empty: `TZ` when it names a zone, else the zone macOS uses; a name that cannot be resolved falls back to the system offset. | O3 | restart |
| `calendar.lark.calendar_id` | `""` | string | The one Lark calendar events go into; empty refuses without calling `lark-cli`. `wa_calendar.py --probe` lists the ids. | O3 | restart |

## `backup`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `backup.enabled` | `false` | boolean | Run the nightly backup. | O5 | restart |
| `backup.dest` | `""` | folder path | Backup root: each night writes `<dest>/<YYYY-MM-DD>/`. Its parent folder must exist. | O5 | restart |
| `backup.require_process` | `""` | process name | A sync app that must be running before anything is written; empty skips the check. | O5 | restart |
| `backup.at` | `"03:30"` | `HH:MM` | Local time of the nightly backup. | O5 | restart |
| `backup.keep` | `7` | integer | Dated folders kept; `0` counts as 7. | O5 | restart |

## `digest`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `digest.enabled` | `true` | boolean | Build the morning digest; a quiet day still gets one. | O6 | restart |
| `digest.at` | `"08:00"` | `HH:MM` | Local time of the digest for yesterday. | O6 | restart |
| `digest.push` | `true` | boolean | Also push its short form (kind `digest`); `false` only writes `live/digest/<date>.md`. | O6 | restart |

## `intake`

The evening job loads the config fresh every evening (`wa_intake.py --evening-status`), so changes to `intake.*`, `notify.*` and `brain.model` reach it without a restart; the daemon's own captures follow the table.

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `intake.enabled` | `false` | boolean | Append things asked of you to the intake buffer. `false` stops every writer: the brain's captures, the second source's captures and the evening push. | O7 | restart |
| `intake.buffer` | `""` | path | The buffer note; same file as `paths.intake_buffer`, and this key wins when both are set. Its folder must exist. | O7 | restart |
| `intake.evening_push` | `false` | boolean | Push the open entries every evening with a suggested place each. `install-launchd --intake` refuses unless this and `intake.enabled` are true. | O7 | no |
| `intake.evening_at` | `"20:00"` | `HH:MM` | Time of the evening job, written into its plist. | O7 | reinstall |
| `intake.rules_file` | `""` | path | Optional note saying where each kind of entry usually goes; the pre-sort reads its first 6000 characters. Empty: built in places. | O7 | no |

## `voice`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `voice.enabled` | `true` | boolean | Pair the brain's drafts with what you actually sent, and show the brain pairs from the same world as writing examples. | O8 | restart |
| `voice.examples` | `12` | integer | Pairs shown to the brain per run. | O8 | restart |
| `voice.pairs` | `"<live>/voice-pairs.jsonl"` | path | File of closed pairs (0600); in the nightly backup. | O8 | restart |

## `backfill`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `backfill.days` | `30` | integer | Window of summary backfill (`commands/backfill-summaries.json`, `wa_backfill.py`). | O9 | restart |
| `backfill.min_msgs` | `10` | integer | Only chats with at least this many content messages in the window, both ways. | O9 | restart |
| `backfill.batch_size` | `10` | integer | Chats per model call. | O9 | restart |
| `backfill.respect_gate` | `true` | boolean | `true`: only chats the gate would let through are backfilled, and backfilled summaries of chats the gate blocks stay out of the summary table the brain reads. | O9 | restart |
| `backfill.max_turns` | `20` | integer | Turn limit of one backfill batch, its own instead of `brain.max_turns` (12): every image or file the model opens costs a turn. A batch that runs out fails with `error_max_turns` in its log line and `runs.error`, and its conversations stay candidates. | O9 | restart |
| `backfill.max_images` | `4` | integer | Image or file lines per batch that keep their local path, newest first, taking turns across the batch's chats; every other media line keeps its label and caption but shows `(路径略)`, so the model cannot open it. Fills `{MAX_IMAGES}` in the backfill prompt. `0` drops every path. | O9 | restart |

## `launchd`

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `launchd.label` | `"local.whatsapp-live"` | reverse domain label | LaunchAgent label of the daemon, and the name of its plist. Change it only for a second, separate install, and uninstall the old label first or two daemons run. | C0 (chosen), C1 (written); O11 for a test install | reinstall |
| `launchd.intake_label` | `"local.whatsapp-live.intake"` | reverse domain label | LaunchAgent label of the evening job; must differ from `launchd.label`. The wizard always writes `launchd.label` plus `.intake`. | C0 (chosen), C1 (written) | reinstall |

## `reader` ◇

Settings of the reader's vault lookup (`wa_context.py`) and of its database snapshots (`wa_sweep.py`). Neither script reads `live/config.json`: `wa_setup.py sync-reader-config` (and every daemon start) passes each key of this block into the reader config (below). Folders are relative to the vault root; an absolute folder inside the vault works too.

| Key | Default | Type | What it changes | Station | Restart |
|---|---|---|---|---|---|
| `reader.index_dirs` ◇ | `[]` | list of folders | When not empty, the vault lookup indexes only these folders. Empty: the whole vault. | none | sync |
| `reader.exclude_dirs` ◇ | `["98_Archive", "99_Meta", ".obsidian", ".trash"]` | list of folders | Folders the vault lookup never indexes; anything inside `node_modules` is always skipped. `init` writes these four from the example; a config without the key, or with an empty list, gets the reader's own default, the same four folders. | none | sync |
| `reader.low_priority_dirs` ◇ | `["00_Inbox", "01_Daily"]` | list of folders | Notes in these folders count 0.4 of their score in the vault lookup. `init` writes these two from the example; a config without the key, or with an empty list, gets the reader's own default, the same two folders. | none | sync |
| `reader.person_dirs` ◇ | `[]` | list of folders | Extra folders whose notes count as people for the vault lookup (their weak hits are kept when nothing scores well); `paths.people_dir` and `paths.decisions_dir` are always included. | none | sync |
| `reader.stop_words` ◇ | `[]` | list of words | Extra words never used as lookup keys, such as your company and brand names; added to the words of `my_names` and `owner_label`. | none | sync |
| `reader.keep_snapshot` ◇ | `false` | boolean | `true` keeps the snapshot copies of the WhatsApp databases after a read. Debugging only: each copy is a full copy of your chats. | none | sync |

## Keys computed at load

`wa_common.apply_defaults` adds these at every load; they are never written back to the file. Setting them in the file does nothing (the load overwrites them), and `check-config` reports the first four as unknown keys.

| Key | Value |
|---|---|
| `config_path` | Absolute path of the loaded file (`""` when defaults were applied without a file) |
| `live_dir` | The same as `paths.live_dir` |
| `state_db` | `<live>/state.sqlite` |
| `sweep_work_dir` | The same as `paths.home` |
| `_load_warnings` | What the load corrected: a value of the wrong kind replaced by its default, a bad `owner_marker` or `board.lang`, two different intake paths, a legacy spelling |

**Legacy spellings** still load: `"notify": "<command>"` (a string) is read as `notify.outlet: command` with that command, and a top level `notify_mode` becomes `notify.mode` when that is not set. `check-config` reports both; rewrite them.

**Environment variables.** `WA_LIVE_CONFIG`: the config path when `--config` is not given; the daemon sets it for its children. `WA_LIVE_WA_ROOT`: a folder holding WhatsApp Desktop's databases instead of the group container (selftests point it at fakes). `WA_WHISPER_MODEL`: set by the daemon from `brain.whisper_model`; the reader prefers it when the folder exists. The launchd scripts also accept `WA_LIVE_PYTHON` and `WA_LIVE_SCRIPT` (`run.mjs`) and `WA_LIVE_DAEMON_DIR` (`intake-evening.mjs`).

## The reader config (`<home>/config.json`)

The reader (`wa_sweep.py`, `wa_context.py`) does not read `live/config.json` (without `--config`, `wa_context.py` falls back to `$WA_LIVE_CONFIG` only when no `config.json` sits beside the pack's folder or in it). It reads `<home>/config.json` (0600), which `wa_setup.py sync-reader-config` generates from the live config and copies into every `<home>/runs-work/<key>/config.json` and `<home>/prewarm/config.json`. The daemon runs the same sync at start and merges the root file into a run's work folder before every read. Never edit it by hand: edit `live/config.json`, then sync. Keys already in a copy that the sync does not manage are left as they are.

| Key | Generated from | Read by | Meaning |
|---|---|---|---|
| `days` | not managed; set to `3` when absent | `wa_sweep.py` | Window when `--days` is not given (the daemon always gives it) |
| `ignored_jids` | `ignored_jids` | `wa_sweep.py` | Chats left out of every pack |
| `my_phone` | `my_phone` | `wa_sweep.py` | Your number, to tell your messages apart |
| `my_names` | `my_names`, trimmed | `wa_sweep.py`, `wa_context.py` | Your names; never used as lookup keys |
| `owner_label` | `owner_label` | `wa_sweep.py` | How the pack names you |
| `transcribe` | `brain.transcribe` | `wa_sweep.py` | Transcribe new voice notes |
| `whisper_model` | `brain.whisper_model`, expanded | `wa_sweep.py` | Model folder (`WA_WHISPER_MODEL` wins when its folder exists) |
| `whisper_language` | `brain.whisper_language` | `wa_sweep.py` | Language hint |
| `whisperkit_bin` | `bin.whisperkit`, resolved | `wa_sweep.py` | Transcription engine (`""` searches PATH and Homebrew) |
| `ffmpeg_bin` | `bin.ffmpeg`, resolved | `wa_sweep.py` | Audio converter (the same) |
| `vault` | `paths.vault` | `wa_context.py` | Vault to look people and matters up in |
| `people_dir` | `paths.people_dir` | `wa_context.py` | Added to the person folders |
| `person_dirs` | `paths.people_dir` and `paths.decisions_dir`, when set, plus `reader.person_dirs` | `wa_context.py` | Folders whose weak hits are kept when nothing scores well |
| `index_dirs` | `reader.index_dirs` | `wa_context.py` | When not empty, only these vault folders are indexed |
| `stop_words` | the words of `my_names` and `owner_label` (two letters or more, not numbers), plus `reader.stop_words` | `wa_context.py` | Words never used as lookup keys |
| `exclude_dirs` | `reader.exclude_dirs`, only when that key holds at least one folder | `wa_context.py` | Folders never indexed (absent: `98_Archive/`, `99_Meta/`, `.obsidian/`, `.trash/`) |
| `low_priority_dirs` | `reader.low_priority_dirs`, only when that key holds at least one folder | `wa_context.py` | Folders whose notes count 0.4 (absent: `00_Inbox/`, `01_Daily/`) |
| `keep_snapshot` | `reader.keep_snapshot`, only when that key is in the live config | `wa_sweep.py` | Keep the database snapshots after a read (absent: `false`) |
| `_generated` | `{"by", "from", "note"}` | nobody | Says the file is generated and from where |

`wa_context.py` also looks for any of its keys inside a `context` object of the reader config; the sync never writes one.

## `live/seeds.json`

The record of every seed copied to an owner location and every rendered file (`wa_setup.py init` and `copy-seed` add records, never change existing ones; `seed-status` reads it). Never edit it by hand.

```json
{"version": 1,
 "payload": "~/.claude/skills/breakthrough-whatsapp-live",
 "seeds": {"prompts/brain-prompt.md": {"seed": "daemon/templates/brain-prompt.md", "seed_version": "1.0.0",
                                       "sha256": "<hash of the bytes copied>", "copied_at": "2031-02-28T10:02:11"}},
 "rendered": {"claude-settings.json": {"template": "daemon/templates/claude-settings.json",
                                       "template_sha256": "<hash of the template>", "sha256": "<hash of the rendered file>",
                                       "rendered_at": "2031-02-28T10:02:11"}}}
```

| Key | Meaning |
|---|---|
| `version` | `1` |
| `payload` | The payload path at the last write (absolute in the real file) |
| `seeds` | One record per owner copy, keyed by its path relative to `live/` (absolute for copies outside it, such as playbooks in the vault) |
| `seeds.*.seed` | The seed's path inside the payload |
| `seeds.*.seed_version` | The version in the seed's stamp when copied (`""` when it had none) |
| `seeds.*.sha256` | Hash of the bytes that landed (with `copy-seed --confirmed-by-owner`, after `confirmed_by_owner` and `updated` were set in the frontmatter); a copy whose hash differs now reads "edited by owner" |
| `seeds.*.copied_at` | When it was copied |
| `rendered` | One record per rendered file, keyed like `seeds` |
| `rendered.*.template` | The template's path inside the payload |
| `rendered.*.template_sha256` | Hash of the template when rendered; a different template hash now reads "template newer" |
| `rendered.*.sha256` | Hash of the rendered file; a different hash now reads "edited by owner" |
| `rendered.*.rendered_at` | When it was rendered |

A copy's stamp version below the payload seed's reads "seed newer", which the update mode merges section by section with your yes (`install/update.md`). `seed-status` also lists the standard owner copies (the three prompts, the topic map, the person playbook) when they carry no record.

## Other files read as settings

| File | Holds | Read | Restart |
|---|---|---|---|
| `live/telegram.json` | `bot_token`, `chat_id` (0600) | At every push through `telegram` | no |
| `live/second-source-whitelist.json` | `chats` (`chat_id`, `name` or `title`, `kind`, `pull`, `world`), `rule`, `decided_at` | At every nightly pull | no |
| `live/topic-map.json` | `rules`: each `{"keys": [words], "files": [vault relative paths]}` | At every brain run: a burst or situation holding a key offers those files (up to 6 listed, the brain opens at most 2) | no |
| `live/acceptance.json` | `questions`: `{"YYYY-MM-DD": "text"}` | When the digest for the day before that date is built | no |
| `live/claude-settings.json` | Tool and Read deny rules for every headless run, rendered by `wa_setup.py init` | By `claude` at every run; the daemon compares its tool list at start | no |
| `live/adapters/<interface>_<name>.py` | Your own implementation of an interface: `facts_<name>.py`, `second_source_<name>.py` or `calendar_<name>.py` (`references/interfaces.md`) | At every use, imported fresh; a file with the name of a shipped implementation wins over it | no |

`second_source.world_by_chat`, `second_source.world_keywords`, `group_policies`, `facts.csv.columns` and `facts.floor` hold keys you choose (chat ids, words, jids, field names), so `check-config` never reports their inner keys as unknown.
