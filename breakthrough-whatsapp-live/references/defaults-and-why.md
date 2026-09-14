# Defaults and why

Every default in this kit was ruled once, by the owner of the running system it was generalized from, usually right after something went wrong or right after a measurement. This page writes each ruling as "we default to X, because Y, change it in Z", so that you can rule on it again for your own business with the reason in front of you.

Each section has four parts:

- **We default to**: the default in one sentence.
- **Why**: the reason in plain words, and the incident or measurement that taught it.
- **Change it here**: the config keys (in `live/config.json`; meanings and defaults in `references/config-reference.md`) or the file that holds it.
- **Re-rule it**: what the install wizard asks you, and at which station.

## How your ruling lands

The wizard reads each default to you at its station. Whatever you rule, kept as it is or changed, lands as **one decision note in your own Decisions room** (`paths.decisions_dir`, usually `02_Command-Base/Decisions/`), written through your command-base skill's "log a decision" route, so the check against your active decisions runs before the note lands. One default, one note. The note says what you ruled and why, in your words. The value itself lives in config or in the file named under "Change it here", never copied into the note, into this kit's `SKILL.md`, or into a code comment (default 14 says why).

A typical note, shaped by your own doctrine §8 (read the required keys from §8 when writing, not from this example):

```
---
cb: decision
date: <the day you ruled>
status: active
domain: "<your business tag>"
lane: build
project: "[[_<your WhatsApp Live project>-Brief]]"
---

# WhatsApp Live pushes only errors, plus one morning digest

**Decided:** ...
**Why:** ...
**Alternatives:** ...
**Consequences:** the values live in live/config.json (notify.mode, digest.at, digest.push).
```

When you later change a ruled value, the new note names the old one in `supersedes:` and the old note flips to `superseded`.

| # | Default | Station |
|---|---|---|
| 1 | Read only the local database, and never send | C0, C1 |
| 2 | The gate: every one-to-one chat passes, groups follow your whitelist | C2 |
| 3 | Sonnet for triage, and tokens instead of dollars | C2 |
| 4 | Speed and judgment quality over token savings | C2 |
| 5 | The board stays on your tailnet | C3 |
| 6 | Pushes only for errors, plus the morning digest | O4, O6 |
| 7 | Other people's chats never leave the judgment | C2 |
| 8 | One vocabulary, and stage only moves forward | C2 |
| 9 | Automatic person notes only for business contacts at talking or beyond | C4 |
| 10 | The payment record sets the stage floor | C5, O1 |
| 11 | One machine, backed up nightly | O5, O10 |
| 12 | The intake buffer is a command-base mode | O7 |
| 13 | A second message source is pulled nightly | C5, O2 |
| 14 | Skills point; rulings live in your Decisions | O11 |

## 1 · Read only the local database, and never send

**We default to** reading the database WhatsApp Desktop already keeps on your always-on Mac, read only, judging each burst of inbound messages once, and putting a draft on the board for you to send yourself. Nothing in this kit can send a WhatsApp message.

**Why** Reading the official app's own local database opens no new connection, so there is no ban risk to manage, and it sees the replies you send from your phone, which a webhook route never can. The other routes were checked and rejected: the Business and Cloud APIs do not serve a personal number; unofficial linked-device libraries have documented bans; automating WhatsApp Web in a browser has been banned repeatedly. Sending stays with a person because a wrong sentence costs the other side and cannot be taken back.

The problem this solves was measured before anything was built: hundreds of one-to-one chats had stopped at the other person's last message, and only a handful of them were still marked unread. The owner had seen them and not followed up. So the system reads accurately and hands over a draft; it does not reply for you.

Three smaller rules ride with this one:

- **The reach is your own number.** Watching whether a team did its work is a people question, not something to read out of other phones.
- **Missing media is reported, never skipped.** Once, a poster in a group went unread, and the message that followed it looked like an emotion when it was really a clash with the date the poster announced. Knowing a gap is there is fine; missing it silently misreads the chat.
- **One-to-one drafts get a button, groups get a copy button.** The one-to-one button opens WhatsApp prefilled by phone number, so it cannot land in the wrong chat. No link form opens a group (one form tried opened a forward-to picker instead), and picking a group through search is luck when two group names start with the same words.

If you also work from a second Mac, manual reads (the `breakthrough-whatsapp-read` skill) go to the always-on Mac's database over ssh, and stop with a plain message when that Mac cannot be reached. Each Mac keeps its own copy of the database; a laptop copy can be days behind without saying so, and a silent fallback answers from stale data.

**Change it here** There is no key that turns sending on, and none will be added. How the doorbell reads: `doorbell.enabled`, `doorbell.poll_seconds`, `doorbell.lookback`, `doorbell.max_message_age_hours`, `doorbell.stale_alert_hours`. The draft rules (a draft is a proposal; anything only you can supply is left in `[brackets]`; no money, dates or commitments promised on your behalf) are in your owner copy of the brain prompt, `live/prompts/brain-prompt.md`. The ssh rule for a second Mac is guidance in `skills/breakthrough-whatsapp-read/SKILL.md`; it has no config key.

**Re-rule it** Station C0 reads this default before anything is installed and asks one question: will you press send yourself, every time? A no ends the install there, because this kit is not built for auto-reply. A yes lands as the note. Station C1 asks how many Macs you work from: with one, the note says reads use the local copy; with two, it names the always-on Mac and the stop-when-unreachable rule.

## 2 · The gate: every one-to-one chat passes, groups follow your whitelist

**We default to** passing every inbound one-to-one message to the brain, sorting groups into three policies you tick yourself (`all`, `mention`, `skip`), dropping status updates, broadcasts and ignored chats, and treating any group you have not listed as `skip`.

**Why** One-to-one messages are nearly always for you personally. Groups are high volume and mostly chatter: in one three-day measurement, groups sent about four hundred messages and two chatty groups made up two thirds of them. Which group deserves every message read is your judgment, so you tick and the machine follows the list. Letting the model decide which group matters would guess wrong, and it would pay for a run on every message before guessing. The whitelist keeps one source of truth: a list kept in two places (a page you tick and the config file) drifts the first time someone edits only one side.

**Change it here** `direct_policy` (`all` or `skip`), `group_policies` (per group jid: `policy`, `name`), `unlisted_group_policy` (`skip`), `ignored_jids`, and `my_names` with `my_phone` for mention detection. `backfill.respect_gate` (true) keeps summary backfill behind the same list. Group changes need a daemon restart: `launchctl kickstart -k gui/$(id -u)/<launchd.label>`.

**Re-rule it** Station C2 shows your own three-day traffic from `wa_setup.py measure` (group names and counts only, never message text) and asks you to tick each group. It also asks where the whitelist's source of truth lives (this config, or a page you tick that is read back into it). The note names that place and the three policies; it does not list your group names.

## 3 · Sonnet for triage, and tokens instead of dollars

**We default to** running every triage judgment on sonnet (`claude-sonnet-5`), writing person notes on opus (`claude-opus-5`), and showing only tokens and seconds on the board, in pushes and in logs, never a dollar amount.

**Why** On a subscription login, the `total_cost_usd` that `claude -p` reports is what the API would have charged; nothing is billed. Shown on the board, that number changed the owner's decisions as if money were being spent. The real limit on a subscription is the usage window, and that follows tokens, so tokens are the reading that should inform a decision. A day of about forty triage runs read about two million tokens, and the daemon was paused that night to rethink; the token count, not a dollar figure, was the reading that told the real story.

Triage runs dozens of times a day and sonnet judges well enough. Person notes are rare and need depth, so they get opus. There is no separate summary worker: the triage brain rewrites each conversation's rolling summary on every run, so it already is the distiller, and a second resident process would only read the same chat again.

**Change it here** `brain.model`, `dossier.model`, `board.show_usd` (false). The state database keeps each run's `cost_usd` either way; only the display is switched. What a run's tokens are made of is in `references/pitfalls.md`, pit 2.

**Re-rule it** Station C2 runs `claude auth status` with you. On a subscription, the wizard reads this default as it is. If you pay per API call the dollars are real money, and the wizard asks whether you want them on the board. It also asks whether your plan can carry opus for person notes, or whether a lighter model should write them. One note holds both answers.

## 4 · Speed and judgment quality over token savings

**We default to** starting a run once a conversation has been quiet for 45 seconds, or 180 seconds after the first message of a burst at the latest, one run per conversation at a time, two runs at once overall, and not trimming material or runs just to save tokens.

**Why** Several quick messages from one person should become one run, so the daemon waits for a pause; someone who never pauses would keep the draft from ever appearing, so there is a hard limit too. Widening the wait to 120 and 300 seconds was measured against real traffic: of 57 gaps between consecutive messages, only 6 fell between 45 and 120 seconds, so it would have saved about a tenth of the runs while every single message waited 75 seconds longer. Most of the tokens in a run are Claude Code's own system prompt and tool definitions, a fixed cost per run, so shrinking what the kit sends saves little. The only big savers, fewer runs or less material, hurt speed or judgment directly. Cutting runs is the gate's job (default 2), not the debounce's.

**Change it here** `debounce.quiet_seconds` (45), `debounce.max_wait_seconds` (180), `debounce.global_concurrency` (2). How much a run reads: `brain.context_days` (7), `brain.group_context_days` (3), `brain.warm_days` (2), `brain.ground_every_days` (7). Limits: `brain.run_timeout_seconds` (600), `brain.retries` (1), `brain.max_turns` (12). One run per conversation at a time is structural and has no key.

**Re-rule it** Station C2 reads you the measurement above (`wa_setup.py measure` counts your inbound messages per chat; it does not measure the gaps between them) next to your own three-day volume, and asks whether you want faster drafts or fewer runs. Changing the numbers later is a proposal your session makes and you rule on; the new values go into config and the ruling into a note that supersedes this one.

## 5 · The board stays on your tailnet

**We default to** serving the board from the daemon on `127.0.0.1` only, reaching it from your phone through `tailscale serve` inside your own tailnet, never hosting it on any cloud; listing every recently active conversation, judged or not; in light mode, with one layout ruler.

**Why** Everything on the board is private messages other people sent you, so privacy has to come from the structure: the data never leaves the machine, and there is nothing to deploy and nothing to pay. Any cloud host, even a locked one, is one more place to leak. Hosting options were checked and rejected: a static site built from a private repository is still public unless you pay for an enterprise plan; a hosting platform can lock the site but adds a cloud and a storage service; a spreadsheet-style database used as the board adds a table and puts conversation content into that vendor.

Unjudged conversations are listed too, so one screen shows everything that moved recently and a judgment can be started from any row. The layout has one ruler, the CSS variables at the top of `board.html`: when the board was redesigned and a separately written layout spec was not updated, the two rulers disagreed at once. The board has no login; the tailnet is its only lock (`references/privacy.md`).

**Change it here** `board.enabled`, `board.bind` (`127.0.0.1`; the wizard never changes it), `board.port` (8787, change it only on a clash), `board.others_days` (3), `board.others_limit` (120), `board.lang` (`zh` or `en`). The tailnet command is `tailscale serve --bg --http=80 http://127.0.0.1:<board.port>`, as `wa_setup.py tailscale-hint` prints it (station C3). The hint reads `tailscale serve status` first, read only: when tailnet port 80 already serves something else, such as a second install's board, it warns and prints the command on the first free port from 8443 up, so it never replaces another handler. The layout ruler lives in `daemon/board.html`, which is part of the payload: `npx skills update` replaces it, so a layout you change is a change you carry yourself.

**Re-rule it** Station C3 reads this default and asks who else is in your tailnet. If it holds only your own devices, the default stands. If other people share it, the wizard says plainly that they can open the board, and you rule on it: restrict the Mac with Tailscale access rules, or accept it. Dark mode or another layout is a new ruling of your own.

## 6 · Pushes only for errors, plus the morning digest

**We default to** pushing to your phone only when something breaks (a run failed, the tool cage broke, the WhatsApp database has not changed for hours), sending one digest every morning as the only scheduled push, and never dropping a message silently.

**Why** Pushing every judgment turns the phone into noise: the owner stops reading, and the one real error drowns. Pushing nothing leaves silence ambiguous: a quiet day, or a dead daemon? The morning digest makes silence trustworthy, because on a quiet day it still says the day was quiet and the daemon is alive.

Every run has a timeout and one retry, and a failed run still leaves a row and sends one push (at most one per conversation per half hour). That rule came from a voice message that vanished without a trace in an earlier assistant. When these pushes once shared a chat with another bot, nobody could tell which system was shouting, so pushes get their own channel. A new Telegram bot cannot message you until you press Start in it once; before that every send fails with "chat not found".

**Change it here** `notify.outlet` (`none`, `telegram`, `command`), `notify.mode` (`off`, `errors`, `needs_me`, `all`), `notify.telegram_file`, `notify.command`, `digest.enabled` (true), `digest.at` (08:00), `digest.push` (true), `doorbell.stale_alert_hours` (6), `brain.retries` (1), `brain.run_timeout_seconds` (600). The evening intake list is a second scheduled push and is off by default (`intake.evening_push`); turning it on re-rules this default as well (default 12).

**Re-rule it** Station O4 asks where alerts should go and reads this default; O6 asks when the digest should arrive. Some owners want every judgment pushed for the first week to see real volume (`notify.mode: all`) and then switch to `errors`; that choice, with the day to switch back, goes in the same note.

## 7 · Other people's chats never leave the judgment

**We default to** letting what people wrote in other conversations shape a judgment, and never letting it into a draft, a handoff pack, the voice examples, or any index someone can search.

**Why** The system reads private messages people sent you. Borrowing context from other chats helps you reply accurately, but the moment someone's words flow into a draft to another person, into a searchable index, or out of their own conversation, the system has done something with their words that they never agreed to. So:

- the brain sees other conversations only as one-line summaries, never their text;
- the vault notes it is pointed at come from a short list of rooms (project briefs, SOPs, decisions, the tool registry);
- the handoff pack carries only the conversation it belongs to;
- the voice loop stores only what you actually sent, never the other side, never a name;
- every headless run uses an empty working folder outside the vault, with session persistence off, so no transcript is left behind for a session-history index to pick up.

That last rule was learned the hard way: early headless runs made before the no-persistence flag was added left full transcripts on disk. An audit later found that the tool cage limited which tools the brain had, not which paths they could read, so Read deny rules for secrets were added as well.

**Change it here** `brain.cwd` (`<home>/brain-cwd`, outside the vault and outside Documents); the rooms the brain may be pointed at, in `live/topic-map.json`; the rule text in `live/prompts/brain-prompt.md` (keep it when you edit the prompt); `voice.enabled` and `voice.examples`; the Read deny rules in `live/claude-settings.json`, rendered by `wa_setup.py init`. `--no-session-persistence` is set in code and has no key. What still lands in your vault (quotes in person notes and intake entries) and how to switch each writer off: `references/privacy.md`.

**Re-rule it** Station C2 reads the five parts above and asks you to confirm or narrow them, then writes `topic-map.json` with you against your own rooms. Widening the room list later means re-ruling this default first.

## 8 · One vocabulary, and stage only moves forward

**We default to** tagging each conversation with a world (which part of your life or which business, set by the first judgment and then kept), a stage (`new`, `talking`, `offered`, `won`, `active`, `done`, plus `parked`) that the machine may only move forward, and a priority (`now`, `soon`, `low`) judged fresh on every run and dropped to `low` when you reply. Only you move a stage back, on the board.

**Why** An earlier vocabulary tagged whose turn it was and what kind of message it was. The two overlapped, and the board still could not show where a conversation stood in the business. The replacement came from a census of 124 conversations over 30 days: of 90 work conversations, 57 were already in service and only 12 were really moving through a sale, so the board shows stage words where they help most (`new`, `talking`, `offered`, `won`, `parked`). Whose turn it is became a computed fact: whether the last message is yours.

Stage only moves forward because a model reading a stretch of small talk easily moves `won` back to `talking`; moving back is a human judgment, so the machine's attempt is kept out and written into the reason instead. A conversation sitting at an early stage with no messages for 21 days becomes `parked` (14 was proposed; the owner's own sales rhythm said 21). Personal and other worlds carry no stage. A person who is both a customer and a partner gets one stage, for whatever matters most in that conversation now; who someone is to you belongs in the person note's `roles`, not in the stage.

The group-name date rule (a group whose name carries a date is marked `done` once that date passes) ships off. An audit found that it would read "24/7" in a group name as a date, and would mark a group made in December for a January event as done straight away, and the brain may never move `done` back.

**Change it here** `vocab.worlds` (each with `key`, `label`, `label_en`, `business`), `vocab.stages`, `vocab.park_from`, `vocab.priorities`, `vocab.business_context`, `stage_rules.parked_after_days` (21; 0 turns it off), `stage_rules.group_date_done` (false). The brain prompt receives the vocabulary through placeholders on every run, so there is no second copy to edit. The `stage` list in your doctrine's `person` family must equal the `vocab.stages` keys (`references/vault-proposals.md`, proposal a).

**Re-rule it** Station C2 interviews you: which worlds your life and businesses split into, what to call each stage in your language, what each business sells and how people pay (that paragraph becomes `vocab.business_context`), and whether 21 days fits your sales rhythm. One note holds the vocabulary you ruled.

## 9 · Automatic person notes only for business contacts at talking or beyond

**We default to** opening a person note automatically only for someone in a business world whose stage has reached `talking` and who has no note yet; family and friends never get one automatically (a note that already exists is still kept up to date); one note run at a time, and the same person at most once every 6 hours.

**Why** The bar for a person note is "this person is worth remembering". In a business world, reaching `talking` means the relationship is really moving. A note about family or friends is your own judgment, not something a model should draft.

Someone who has paid is always worth a note, so when a facts source is connected, paying customers who have a chat but no note are queued without waiting for `talking`, at most `dossier.nightly_cap` each time the facts job runs (the first run after connecting the source queues them at once, station O1). Before that rule, notes opened only when the owner pressed a button, and about ten paying customers turned out to have no note at all. A paying customer with no messages in the last 30 days is listed in the digest instead of queued: the model cannot even write how to open the next message, and the note comes out an empty shell.

The 6-hour gap counts failed runs too. A note that kept being rejected was queued again on every new message from that person, burning an expensive run and a failure push each time.

**Change it here** `dossier.enabled` (false until station C4 passes), `dossier.auto_open_worlds` (empty until the wizard fills it with your business worlds), `dossier.auto_open_min_stage` (`talking`), `dossier.min_gap_hours` (6), `dossier.nightly_at` (02:30), `dossier.nightly_min_msgs` (5), `dossier.nightly_cap` (8), `dossier.stale_days` (14), `dossier.pack_days` (30), `dossier.model`, `facts.recent_days` (30), `paths.people_dir`. The board's open and rewrite buttons work whenever you press them. How a note is written, checked and placed is the person-dossier playbook in your vault (`paths.person_playbook`).

**Re-rule it** Station C4 asks which worlds count as business, whether paying customers should be queued (only with a facts source, O1), and whether notes should open automatically at all or only when you press the button. The wizard also says plainly that an automatic note puts AI-written text into your vault without a per-note approval, guarded instead by the verifier, three backups and a filing-log line, and asks whether that trade is acceptable to you. One note.

## 10 · The payment record sets the stage floor

**We default to** letting your payment records, never the conversation, decide `won` and beyond: paid and attended (or served) is at least `active`; paid and not yet attended is at least `won`; someone who attended through another person's payment is at least `active`; without a payment record, `won`, `active` and `done` are capped at `offered` unless the note already held them. Someone saying "paid" in a chat does not count. The floor is one function, `wa_facts.stage_floor`, used by the brain, the facts job and the note verifier.

**Why** Stage is a business fact, and whether someone paid should not be guessed from their tone: "I've paid" in a chat is not money received. The rule used to be computed in two places. Each part's tests passed, and an audit that ran both paths against real data found the same person judged `active` by one and `won` by the other, while the written spec said a third thing. So the floor lives in one function.

A person's own payment outranks a payment someone else made for them. A payment table that carried no phone numbers could not be matched to anyone until a roster with phones was joined to it, so a facts source needs a phone on every person or a link to a table that has one. The digest lists three mismatches (paid with no chat, a phone that cannot be parsed, a payer with a chat and no note) and deliberately not "the note says active but no payment is on record": long relationships and partners are not in payment tables, and that list would be all false alarms.

**Change it here** `facts.enabled`, `facts.source` (`none`, `csv`, `lark`), `facts.at` (03:00), `facts.floor` (`paid`, `paid_attended`, `via_attended` each mapped to a stage key), `facts.phone_default_cc`, `facts.phone_valid`, `facts.csv.path` and `facts.csv.columns` (which columns mean name, phone, paid, date, product, attended), `facts.lark.people_tables` and `facts.lark.payment_tables`. A business that records no attendance only ever gets the `won` floor. Other record systems implement the facts contract in `references/interfaces.md`.

**Re-rule it** Station C5 asks where you record who paid and who was served; O1 connects it and asks what `won` and `active` mean in your business. With no payment system at all, the note says so, and stages beyond `offered` are then set by your hand only. One note.

## 11 · One machine, backed up nightly

**We default to** running the daemon on one always-on Mac only, never on two at once, backing its state up every night to a folder you choose and keeping 7 copies, keeping secrets out of the backup, and letting only one session touch the daemon at a time.

**Why** Two machines running for the same number would judge each message twice, keep two states that cannot be merged, and push twice, and the two local WhatsApp databases do not sync with each other anyway. The risk of a single machine is paid for with a nightly backup and a written cold-start runbook, which is far cheaper than keeping two machines in sync. The backup stays out of the vault: the state database and people's raw words do not belong in a second brain or in its note sync. One session at a time, because a restart kills the runs in flight, and two sessions restarting the daemon step on each other. The backup already missed a file once (`references/pitfalls.md`, pit 12), so every new state file joins the copy list or the skip list in the same change.

**Change it here** `backup.enabled` (false until O5), `backup.dest` (a folder in iCloud Drive, a Google Drive folder, an external disk), `backup.require_process` (the sync client that must be running; empty skips the check), `backup.at` (03:30), `backup.keep` (7). What is copied and what never is: `wa_backup.run_backup`. The cold start: your vault copy of `standby-machine-runbook.md` in the whatsapp-live-triage playbook folder (station O10).

**Re-rule it** Station O5 asks where backups should go and reads this default, including that the kit does not encrypt the backup and that a cloud folder puts the daemon's state in that provider's hands (`references/privacy.md`). Station O10 asks whether you keep a standby Mac. One note, which also carries the one-session rule.

## 12 · The intake buffer is a command-base mode

**We default to** having the brain catch at most two things per run that you need to act on, of five kinds only (a request with a date, something you promised and have not delivered, an appointment, money, a decision the other side stated), appending them to one intake buffer note in your vault, and placing them only in a session with you when you say "intake", entry by entry, on your yes. There is no separate skill, and no evening push unless you turn it on.

**Why** What a model catches from messages includes mistakes and duplicates. Written straight into tasks or a calendar, it would dirty your command base, and nobody goes back to delete those later. The buffer splits catching from placing: the machine makes sure nothing is lost, and you put each item where it belongs. Fewer is better, and in groups only things said to you count. Placing is a mode of your command-base skill rather than a skill of its own because a second skill would compete with the command base for the same trigger words. The daemon only ever appends; only the intake mode changes an entry's status.

**Change it here** `intake.enabled` (false until O7; false also stops the second source's captures and the evening push), `intake.buffer` (the same file as `paths.intake_buffer`; set one of them), `intake.evening_push` (false), `intake.evening_at` (20:00), `intake.rules_file` (optional placement rules the evening job reads). The evening job: `daemon/templates/intake-evening.mjs` with `intake-evening.plist`, installed only when the push is on. The buffer's format and the command-base patches: `references/vault-proposals.md`, proposals c and d.

**Re-rule it** Station O7 reads this default and asks whether you want the open list pushed in the evening. Saying yes also re-rules default 6, so the note says both. One note.

## 13 · A second message source is pulled nightly

**We default to** reading a second message source (a work chat app, for example) once a night for the previous day, only from chats you whitelisted and only messages real people sent, and writing just two things: one line in the recent block of a person note that carries that source's id key, and captures into the intake buffer. Nothing from it appears on the board.

**Why** A work chat is where team and client projects happen, and most of what is said there already has someone on it; it rarely needs you to reply on the spot. What is worth keeping is what each person has been talking about and what was promised. Once a night is cheap and keeps the board from turning into a second copy of that app. If nobody else wrote that day, no model is called.

The model's output is checked against the source: a person id must really appear in the fetched messages, and a quote must be an exact piece of a real message, or it is dropped. A person without the id key is never matched by name, because shared names and nicknames would write into the wrong note. A live feed onto the board was rejected: work groups would flood it, and it would add another resident listener. A missing or unreadable whitelist is an error, because it once produced a silent zero that the digest reported as a successful pull.

**Change it here** `second_source.enabled`, `second_source.source` (`none`, `lark`), `second_source.at` (03:10), `second_source.lark.whitelist`; the prompt is your owner copy `live/prompts/second-source-prompt.md`; the id key on person notes (for example `lark_id`) is declared in your doctrine (`references/vault-proposals.md`, proposal a). Other sources implement the contract in `references/interfaces.md`.

**Re-rule it** Station C5 asks where else your conversations happen, and this default is always ruled there with one decision note, also when you have no second source: then the note says WhatsApp is the only place your messages come from, O2 is skipped, and nothing else lands. With a second source, O2 reads this default again while connecting it.

## 14 · Skills point; rulings live in your Decisions

**We default to** keeping this kit's skill files as pointers (where the source of truth is, the fewest commands to run things, and what only that skill knows), with every ruling in your own Decisions room, one ruling per note, values in config, pits in Lessons, and nothing appended to a `SKILL.md` at the end of a session.

**Why** The same thing written in two places goes stale in one of them, and nobody knows which. A skill file that called itself the design record grew to about 50 KB and 35 rulings within days, and drifted in three places: the board's layout ruler changed and the file did not follow, its description's status line went out of date twice, and two rules in the person-note spec contradicted each other. In the same stretch a list of legal `roles` values was copied into code while the prompt only said how many there were, and a new note was rejected twice in a row. So code and prompts read values from their one home at run time (the doctrine's roles, the playbook's section names) instead of carrying copies.

**Change it here** The kit's own `SKILL.md` stays a pointer table. Rulings: `paths.decisions_dir`. Seeds versus your copies (`live/prompts/`, `live/topic-map.json`, the three playbook folders): `wa_setup.py seed-status` and `install/update.md`.

**Re-rule it** Station O11 checks with you that every default ruled during the install is a note in your Decisions room and that none is written into a skill. If your vault has no Decisions room, the wizard stops and proposes a home before going on.
