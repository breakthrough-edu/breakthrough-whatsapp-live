# Defaults and why

Every default in this kit has a reason. This page writes each one as "we default to X, because Y, change it in Z", so that you can rule on it again for your own business with the reason in front of you.

Each section has four parts:

- **We default to**: the default in one sentence.
- **Why**: the reason in plain words, and what goes wrong without it.
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
| 15 | An edited message is read again for 30 minutes | C2 |
| 16 | The board writes to your vault only when you switch it on | O7 |

## 1 · Read only the local database, and never send

**We default to** reading the database WhatsApp Desktop already keeps on your always-on Mac, read only, judging each burst of inbound messages once, and putting a draft on the board for you to send yourself. Nothing in this kit can send a WhatsApp message.

**Why** Reading the official app's own local database opens no new connection, so there is no ban risk to manage, and it sees the replies you send from your phone, which a webhook route never can. The other routes carry known costs: the Business and Cloud APIs do not serve a personal number; unofficial linked-device libraries have documented bans; automating WhatsApp Web in a browser has been banned repeatedly. Sending stays with a person because a wrong sentence costs the other side and cannot be taken back.

The usual problem is not unseen messages but seen ones that never got an answer: a chat read on the phone loses its unread mark, so it stops at the other person's last message and nothing reminds you. So the system reads accurately and hands over a draft; it does not reply for you.

Three smaller rules ride with this one:

- **The reach is your own number.** Watching whether a team did its work is a people question, not something to read out of other phones.
- **Missing media is reported, never skipped.** An unread image can change the meaning of the text after it: the text after an unread image may answer something only the image said. Knowing a gap is there is fine; missing it silently misreads the chat.
- **One-to-one drafts get a button, groups get a copy button.** The one-to-one button opens WhatsApp prefilled by phone number, so it cannot land in the wrong chat. No link form opens a group (the known group link forms either fail or open a forward-to picker), and picking a group through search is luck when two group names start with the same words.

If you also work from a second Mac, manual reads (the `breakthrough-whatsapp-read` skill) go to the always-on Mac's database over ssh, and stop with a plain message when that Mac cannot be reached. Each Mac keeps its own copy of the database; a laptop copy can be days behind without saying so, and a silent fallback answers from stale data.

**Change it here** There is no key that turns sending on, and none will be added. How the doorbell reads: `doorbell.enabled`, `doorbell.poll_seconds`, `doorbell.lookback`, `doorbell.max_message_age_hours`, `doorbell.stale_alert_hours`. The draft rules (a draft is a proposal; anything only you can supply is left in `[brackets]`; no money, dates or commitments promised on your behalf) are in your owner copy of the brain prompt, `live/prompts/brain-prompt.md`. The ssh rule for a second Mac is guidance in `skills/breakthrough-whatsapp-read/SKILL.md`; it has no config key.

**Re-rule it** Station C0 reads this default before anything is installed and asks one question: will you press send yourself, every time? A no ends the install there, because this kit is not built for auto-reply. A yes lands as the note. Station C1 asks how many Macs you work from: with one, the note says reads use the local copy; with two, it names the always-on Mac and the stop-when-unreachable rule.

## 2 · The gate: every one-to-one chat passes, groups follow your whitelist

**We default to** passing every inbound one-to-one message to the brain, sorting groups into three policies you tick yourself (`all`, `mention`, `skip`), dropping status updates, broadcasts and ignored chats, and treating any group you have not listed as `skip`.

**Why** One-to-one messages are nearly always for you personally. Groups are high volume and mostly chatter, and group traffic is usually concentrated in a few chatty groups; your own C0 measure shows the split. Which group deserves every message read is your judgment, so you tick and the machine follows the list. Letting the model decide which group matters would guess wrong, and it would pay for a run on every message before guessing. The whitelist keeps one source of truth: a list kept in two places (a page you tick and the config file) drifts the first time someone edits only one side.

**Change it here** `direct_policy` (`all` or `skip`), `group_policies` (per group jid: `policy`, `name`), `unlisted_group_policy` (`skip`), `ignored_jids`, and `my_names` with `my_phone` for mention detection. `backfill.respect_gate` (true) keeps summary backfill behind the same list. Group changes need a daemon restart: `wa_setup.py restart --config <live>/config.json` (`<live>` is `paths.live_dir`).

**Re-rule it** Station C2 shows your own three-day traffic from `wa_setup.py measure` (group names and counts only, never message text) and asks you to tick each group. Your ticks are written into `group_policies` in your config, the one place the whitelist lives. The note names the three policies; it does not list your group names.

## 3 · Sonnet for triage, and tokens instead of dollars

**We default to** running every triage judgment on sonnet (`claude-sonnet-5`), writing person notes on opus (`claude-opus-5`), and showing only tokens and seconds on the board, in pushes and in logs, never a dollar amount.

**Why** On a subscription login, the `total_cost_usd` that `claude -p` reports is what the API would have charged; nothing is billed. Shown on the board, that number pushes decisions as if money were being spent. The real limit on a subscription is the usage window, and that follows tokens, so tokens are the reading that should inform a decision: a few dozen triage runs a day can reach millions of tokens, and that is the figure to watch.

Triage runs dozens of times a day and sonnet judges well enough. Person notes are rare and need depth, so they get opus. There is no separate summary worker: the triage brain rewrites each conversation's rolling summary on every run, so it already is the distiller, and a second resident process would only read the same chat again.

**Change it here** `brain.model`, `dossier.model`, `board.show_usd` (false). The state database keeps each run's `cost_usd` either way; only the display is switched. What a run's tokens are made of is in `references/pitfalls.md`, pit 2.

**Re-rule it** Station C2 runs `claude auth status` with you. On a subscription, the wizard reads this default as it is. If you pay per API call the dollars are real money, and the wizard asks whether you want them on the board. It also asks whether your plan can carry opus for person notes, or whether a lighter model should write them. One note holds both answers.

## 4 · Speed and judgment quality over token savings

**We default to** starting a run once a conversation has been quiet for 45 seconds, or 180 seconds after the first message of a burst at the latest, one run per conversation at a time, two runs at once overall, and not trimming material or runs just to save tokens.

**Why** Several quick messages from one person should become one run, so the daemon waits for a pause; someone who never pauses would keep the draft from ever appearing, so there is a hard limit too. A longer wait saves only the bursts whose gaps fall between the old and the new limit. On typical chat traffic most gaps between messages are much shorter or much longer than that, so every draft waits longer while few runs are saved. Most of the tokens in a run are Claude Code's own system prompt and tool definitions, a fixed cost per run, so shrinking what the kit sends saves little. The only big savers, fewer runs or less material, hurt speed or judgment directly. Cutting runs is the gate's job (default 2), not the debounce's.

**Change it here** `debounce.quiet_seconds` (45), `debounce.max_wait_seconds` (180), `debounce.global_concurrency` (2). How much a run reads: `brain.context_days` (7), `brain.group_context_days` (3), `brain.warm_days` (2), `brain.ground_every_days` (7). Limits: `brain.run_timeout_seconds` (600), `brain.retries` (1), `brain.max_turns` (12). One run per conversation at a time is structural and has no key.

**Re-rule it** Station C2 explains the trade above (a longer wait delays every draft and saves only the bursts whose gaps fall in between; `wa_setup.py measure` counts your inbound messages per chat and does not measure the gaps), shows it next to your own three-day volume, and asks whether you want faster drafts or fewer runs. Changing the numbers later is a proposal your session makes and you rule on; the new values go into config and the ruling into a note that supersedes this one.

## 5 · The board stays on your tailnet

**We default to** serving the board from the daemon on `127.0.0.1` only, reaching it from your phone through `tailscale serve` inside your own tailnet, never hosting it on any cloud; listing every recently active conversation, judged or not; in light mode, with one layout ruler.

**Why** Everything on the board is private messages other people sent you, so privacy has to come from the structure: the data never leaves the machine, and there is nothing to deploy and nothing to pay. Any cloud host, even a locked one, is one more place to leak. Any hosted option adds a place for private messages to leak.

Unjudged conversations are listed too, so one screen shows everything that moved recently and a judgment can be started from any row. The layout has one ruler, the CSS variables at the top of `board.html`: a second, separately written layout spec drifts the first time only one side is changed. The board has no login; the tailnet is its only lock (`references/privacy.md`).

**Change it here** `board.enabled`, `board.bind` (`127.0.0.1`; the wizard never changes it), `board.port` (8787, change it only on a clash), `board.others_days` (3), `board.others_limit` (120), `board.lang` (`zh` or `en`). The tailnet command is `tailscale serve --bg --http=80 http://127.0.0.1:<board.port>`, as `wa_setup.py tailscale-hint` prints it (station C3). The hint reads `tailscale serve status` first, read only: when tailnet port 80 already serves something else, such as a second install's board, it warns and prints the command on the first free port from 8443 up, so it never replaces another handler. The layout ruler lives in `daemon/board.html`, which is part of the payload: `npx skills update` replaces it, so a layout you change is a change you carry yourself.

**Re-rule it** Station C3 reads this default and asks who else is in your tailnet. If it holds only your own devices, the default stands. If other people share it, the wizard says plainly that they can open the board, and you rule on it: restrict the Mac with Tailscale access rules, or accept it. Dark mode or another layout is a new ruling of your own.

## 6 · Pushes only for errors, plus the morning digest

**We default to** pushing to your phone only when something breaks (a run failed, the tool cage broke, the WhatsApp database has not changed for hours), sending one digest every morning as the only scheduled push, and never dropping a message silently.

**Why** Pushing every judgment turns the phone into noise: the owner stops reading, and the one real error drowns. Pushing nothing leaves silence ambiguous: a quiet day, or a dead daemon? The morning digest makes silence trustworthy, because on a quiet day it still says the day was quiet and the daemon is alive.

Every run has a timeout and one retry, and a failed run still leaves a row and sends one push (at most one per conversation per half hour): a run that fails silently looks exactly like a message nobody sent. When Claude itself cannot be called (an expired login or a reached usage limit), the circuit breaker holds every new run and keeps the messages queued; its opening and its closing are the only extra pushes, so a dead brain is one alert instead of a failed run per message. Pushes get a channel of their own, because alerts sharing a chat with another bot cannot be told apart. A new Telegram bot cannot message you until you press Start in it once; before that every send fails with "chat not found".

**Change it here** `notify.outlet` (`none`, `telegram`, `command`), `notify.mode` (`off`, `errors`, `needs_me`, `all`), `notify.telegram_file`, `notify.command`, `digest.enabled` (true), `digest.at` (08:00), `digest.push` (true), `doorbell.stale_alert_hours` (6), `brain.retries` (1), `brain.run_timeout_seconds` (600), `brain.gate_probe_seconds` (900, how often the open breaker probes). The board's Today brief (default 12) is never pushed, so it leaves this default as it is.

**Re-rule it** Station O4 asks where alerts should go and reads this default; O6 asks when the digest should arrive. You can push every judgment for the first week to see real volume (`notify.mode: all`) and then switch to `errors`; that choice, with the day to switch back, goes in the same note.

## 7 · Other people's chats never leave the judgment

**We default to** letting what people wrote in other conversations shape a judgment, and never letting it into a draft, a handoff pack, the voice examples, or any index someone can search.

**Why** The system reads private messages people sent you. Borrowing context from other chats helps you reply accurately, but the moment someone's words flow into a draft to another person, into a searchable index, or out of their own conversation, the system has done something with their words that they never agreed to. So:

- the brain sees other conversations only as one-line summaries, never their text;
- the vault notes it is pointed at come from a short list of rooms (project briefs, SOPs, decisions, the tool registry);
- the handoff pack carries only the conversation it belongs to;
- the voice loop stores only what you actually sent, never the other side, never a name;
- every headless run uses an empty working folder outside the vault, with session persistence off, so no transcript is left behind for a session-history index to pick up.

Without the no-persistence flag every headless run leaves a full transcript on disk. And a tool cage limits which tools exist, not which paths they read, so Read deny rules cover secrets too. For the same reason every headless run starts with Claude memory switched off: otherwise your own auto-memory index and any `CLAUDE.md` above the working folder load into every run (`references/pitfalls.md`, pit 14).

**Change it here** `brain.cwd` (`<home>/brain-cwd`, outside the vault and outside Documents); the rooms the brain may be pointed at, in `live/topic-map.json`; the rule text in `live/prompts/brain-prompt.md` (keep it when you edit the prompt); `voice.enabled` and `voice.examples`; the Read deny rules in `live/claude-settings.json`, rendered by `wa_setup.py init`. `--no-session-persistence` is set in code and has no key. What still lands in your vault (quotes in person notes and intake entries) and how to switch each writer off: `references/privacy.md`.

**Re-rule it** Station C2 reads the five parts above and asks you to confirm or narrow them, then writes `topic-map.json` with you against your own rooms. Widening the room list later means re-ruling this default first.

## 8 · One vocabulary, and stage only moves forward

**We default to** tagging each conversation with a world (which part of your life or which business, set by the first judgment and then kept), a stage (`new`, `talking`, `offered`, `won`, `active`, `done`, plus `parked`) that the machine may only move forward, and a priority (`now`, `soon`, `low`) judged fresh on every run and dropped to `low` when you reply. Only you move a stage back, on the board.

**Why** Whose turn it is is computed (whether the last message is yours), so it is not a judged field. Many work chats are with people already being served rather than moving through a sale, so the board shows stage words where they help most (`new`, `talking`, `offered`, `won`, `parked`).

Stage only moves forward because a model reading a stretch of small talk easily moves `won` back to `talking`; moving back is a human judgment, so the machine's attempt is kept out and written into the reason instead. A conversation sitting at an early stage with no messages for 21 days becomes `parked` (21 by default; set it to your own sales rhythm at C2). Personal and other worlds carry no stage. A person who is both a customer and a partner gets one stage, for whatever matters most in that conversation now; who someone is to you belongs in the person note's `roles`, not in the stage.

The group-name date rule (a group whose name carries a date is marked `done` once that date passes) ships off, because a name such as "24/7" reads as a date, and a group made in December for a January event would be marked done straight away, and the brain may never move `done` back.

**Change it here** `vocab.worlds` (each with `key`, `label`, `label_en`, `business`), `vocab.stages`, `vocab.park_from`, `vocab.priorities`, `vocab.business_context`, `stage_rules.parked_after_days` (21; 0 turns it off), `stage_rules.group_date_done` (false). The brain prompt receives the vocabulary through placeholders on every run, so there is no second copy to edit. The `stage` list in your doctrine's `person` family must equal the `vocab.stages` keys (`references/vault-proposals.md`, proposal a).

**Re-rule it** Station C2 interviews you: which worlds your life and businesses split into, what to call each stage in your language, what each business sells and how people pay (that paragraph becomes `vocab.business_context`), and whether 21 days fits your sales rhythm. One note holds the vocabulary you ruled.

## 9 · Automatic person notes only for business contacts at talking or beyond

**We default to** opening a person note automatically only for someone in a business world whose stage has reached `talking` and who has no note yet; family and friends never get one automatically (a note that already exists is still kept up to date); one note run at a time, and the same person at most once every 6 hours.

**Why** The bar for a person note is "this person is worth remembering". In a business world, reaching `talking` means the relationship is really moving. A note about family or friends is your own judgment, not something a model should draft.

Someone who has paid is always worth a note, so when a facts source is connected, paying customers who have a chat but no note are queued without waiting for `talking`, at most `dossier.nightly_cap` each time the facts job runs (the first run after connecting the source queues them at once, station O1). Without that rule, paying customers who never reached `talking` in chat would have no note at all. A paying customer with no messages in the last 30 days is listed in the digest instead of queued: the model cannot even write how to open the next message, and the note comes out an empty shell.

The 6-hour gap counts failed runs too. If failures did not count, a note that keeps being rejected would be queued again on every new message from that person, each time an expensive run and a failure push.

**Change it here** `dossier.enabled` (false until station C4 passes), `dossier.auto_open_worlds` (empty until the wizard fills it with your business worlds), `dossier.auto_open_min_stage` (`talking`), `dossier.min_gap_hours` (6), `dossier.nightly_at` (02:30), `dossier.nightly_min_msgs` (5), `dossier.nightly_cap` (8), `dossier.stale_days` (14), `dossier.pack_days` (30), `dossier.model`, `facts.recent_days` (30), `paths.people_dir`. The board's open and rewrite buttons work whenever you press them. How a note is written, checked and placed is the person-dossier playbook in your vault (`paths.person_playbook`).

**Re-rule it** Station C4 asks which worlds count as business, whether paying customers should be queued (only with a facts source, O1), and whether notes should open automatically at all or only when you press the button. The wizard also says plainly that an automatic note puts AI-written text into your vault without a per-note approval, guarded instead by the verifier, three backups and a filing-log line, and asks whether that trade is acceptable to you. One note.

## 10 · The payment record sets the stage floor

**We default to** letting your payment records, never the conversation, decide `won` and beyond: paid and attended (or served) is at least `active`; paid and not yet attended is at least `won`; someone who attended through another person's payment is at least `active`; without a payment record, `won`, `active` and `done` are capped at `offered` unless the note already held them. Someone saying "paid" in a chat does not count. The floor is one function, `wa_facts.stage_floor`, used by the brain, the facts job and the note verifier.

**Why** Stage is a business fact, and whether someone paid should not be guessed from their tone: "I've paid" in a chat is not money received. Computed in two places, the rule can judge one person `active` in one part and `won` in another while both pass their tests, so the floor lives in one function.

A person's own payment outranks a payment someone else made for them. A payment table without phone numbers matches nobody until it is joined to a roster that has them, so a facts source needs a phone on every person or a link to a table that has one. The digest lists three mismatches (paid with no chat, a phone that cannot be parsed, a payer with a chat and no note) and deliberately not "the note says active but no payment is on record": long relationships and partners are not in payment tables, and that list would be all false alarms.

**Change it here** `facts.enabled`, `facts.source` (`none`, `csv`, `lark`), `facts.at` (03:00), `facts.floor` (`paid`, `paid_attended`, `via_attended` each mapped to a stage key), `facts.phone_default_cc`, `facts.phone_valid`, `facts.csv.path` and `facts.csv.columns` (which columns mean name, phone, paid, date, product, attended), `facts.lark.people_tables` and `facts.lark.payment_tables`. A business that records no attendance only ever gets the `won` floor. Other record systems implement the facts contract in `references/interfaces.md`.

**Re-rule it** Station C5 asks where you record who paid and who was served; O1 connects it and asks what `won` and `active` mean in your business. With no payment system at all, the note says so, and stages beyond `offered` are then set by your hand only. One note.

## 11 · One machine, backed up nightly

**We default to** running the daemon on one always-on Mac only, never on two at once, backing its state up every night to a folder you choose and keeping 7 copies, keeping secrets out of the backup, and letting only one session touch the daemon at a time.

**Why** Two machines running for the same number would judge each message twice, keep two states that cannot be merged, and push twice, and the two local WhatsApp databases do not sync with each other anyway. The risk of a single machine is paid for with a nightly backup and a written cold-start runbook, which is far cheaper than keeping two machines in sync. The backup stays out of the vault: the state database and people's raw words do not belong in a second brain or in its note sync. One session at a time, because a restart kills the runs in flight, and two sessions restarting the daemon step on each other. A backup that copies a fixed list silently misses files added later (`references/pitfalls.md`, pit 12), so every new state file joins the copy list or the skip list in the same change.

**Change it here** `backup.enabled` (false until O5), `backup.dest` (a folder in iCloud Drive, a Google Drive folder, an external disk), `backup.require_process` (the sync client that must be running; empty skips the check), `backup.at` (03:30), `backup.keep` (7). What is copied and what never is: `wa_backup.run_backup`. The cold start: your vault copy of `standby-machine-runbook.md` in the whatsapp-live-triage playbook folder (station O10).

**Re-rule it** Station O5 asks where backups should go and reads this default, including that the kit does not encrypt the backup and that a cloud folder puts the daemon's state in that provider's hands (`references/privacy.md`). Station O10 asks whether you keep a standby Mac. One note, which also carries the one-session rule.

## 12 · The intake buffer is a command-base mode

**We default to** having the brain catch at most two things per run that you need to act on, of five kinds only (a request with a date, something you promised and have not delivered, an appointment, money, a decision the other side stated), appending them to one intake buffer note in your vault, and placing them only in a session with you when you say "intake", entry by entry, on your yes. That is the catching half. The proactive half is the board's Today brief (`today.enabled`, off until you say yes at O7): per project, what today's conversations and open entries mean, with cards proposing a task update, a new task, a project seed or a drop, each answered with OK or No on the board. It is recomputed at most every `today.every_minutes` (30) and only when its inputs changed, and it is never pushed. There is no separate skill and no evening push.

**Why** What a model catches from messages includes mistakes and duplicates. Written straight into tasks or a calendar, it would dirty your command base, and nobody goes back to delete those later. The buffer splits catching from placing: the machine makes sure nothing is lost, and you put each item where it belongs. Fewer is better, and in groups only things said to you count. Placing is a mode of your command-base skill rather than a skill of its own because a second skill would compete with the command base for the same trigger words. The daemon only ever appends; only the intake mode changes an entry's status.

Every quote is looked up in the run's own message pack before the entry is written: what the program finds is marked 🗣 (🎙 for a voice transcript, 📄 for a document's text) and copied from the stored message, and what it cannot find is marked ≈ as a gist, in the brain's words. A machine never promotes a paraphrase to a quote, because the buffer is placed later by someone who was not reading the chat: a date or an amount on a line marked as the person's words is used as written, and a paraphrase that looks like a quote turns a model's slip into something the person seems to have said. The mark keeps the two apart, so a gist is checked against the chat before it goes into a task or the calendar, and a quote is not.

The brief is never pushed because a push is a second inbox: one more list to clear, read at whatever moment it arrives, away from the vault. The board is where the owner already looks, and the cards wait there until they are answered. The brief proposes and never places: an OK on a card is a note to your own session, and placing still happens in the intake mode, where the mode reads the board's cards under each entry as a starting point, not an answer. By default the daemon writes nothing into the vault for it; a card whose entries were placed retires itself, which changes only the daemon's own state. The optional write button that puts an OK'd card into its task file is default 16. Quoted words in the brief go through the same check as intake quotes: found, they are shown as stored; not found, they are shown as a gist. A stretch of the model's answer, in any field, that repeats one of your own replies (at least `today.quote_min_chars` CJK characters (Chinese, kana or Hangul) or 4 Latin words (6 letters or digits or more), ignoring case, spacing, punctuation (inside a word too), full-width forms, and the escapes, entities, percent codes and tags listed in `references/privacy.md`) is replaced with "(your reply, not copied)" before anything is stored; the run folder keeps the prompt and the model's raw answer, like every run folder. A source you take out of a project with "not this project" never comes back to that project.

The evening push that earlier releases offered is gone. Two proactive surfaces that disagree are worse than one: an evening list that suggests one place and a board card that proposes another leave the owner judging the machines instead of the work. An install that kept the evening job through an update keeps it working until it is removed (`install/update.md`).

What it costs: one extra model call, at most every `today.every_minutes` minutes, only when its inputs changed (today's judgments, the open intake entries, a source you took out of a project, or your replies in conversations its cards cover); it reads a few vault files (the briefs and task lists of the projects in play, and at most `today.max_reads` files it opens itself).

**Change it here** `intake.enabled` (false until O7; false also stops the second source's captures), `intake.buffer` (the same file as `paths.intake_buffer`; set one of them), `intake.rules_file` (optional placement rules), `intake.verify_quotes` (true; false writes every quote as ≈ not checked). The brief: `today.enabled` (false), `today.model`, `today.every_minutes` (30), `today.manual_min_gap_seconds` (60), `today.max_reads` (6), `today.quote_min_chars` (6), `today.extra_rules` (your own judgment paragraph). `intake.evening_push` and `intake.evening_at` are retired; `check-config` warns while they are set. The buffer's format and the command-base patches: `references/vault-proposals.md`, proposals c and d.

**Re-rule it** Station O7 reads this default and asks whether the board should propose, per project, what today's conversations mean. One note.

## 13 · A second message source is pulled nightly

**We default to** reading a second message source (a work chat app, for example) once a night for the previous day, only from chats you whitelisted and only messages real people sent, and writing just two things: one line in the recent block of a person note that carries that source's id key, and captures into the intake buffer. Nothing from it appears on the board.

**Why** A work chat is where team and client projects happen, and most of what is said there already has someone on it; it rarely needs you to reply on the spot. What is worth keeping is what each person has been talking about and what was promised. Once a night is cheap and keeps the board from turning into a second copy of that app. If nobody else wrote that day, no model is called.

The model's output is checked against the source: a person id must really appear in the fetched messages, and a quote must be an exact piece of a real message, or it is dropped. A person without the id key is never matched by name, because shared names and nicknames would write into the wrong note. A live feed onto the board is not offered: work groups would flood it, and it would add another resident listener. A missing or unreadable whitelist is an error, because otherwise a missing file yields a silent zero that the digest would report as a successful pull.

**Change it here** `second_source.enabled`, `second_source.source` (`none`, `lark`), `second_source.at` (03:10), `second_source.lark.whitelist`; the prompt is your owner copy `live/prompts/second-source-prompt.md`; the id key on person notes (for example `lark_id`) is declared in your doctrine (`references/vault-proposals.md`, proposal a). Other sources implement the contract in `references/interfaces.md`.

**Re-rule it** Station C5 asks where else your conversations happen, and this default is always ruled there with one decision note, also when you have no second source: then the note says WhatsApp is the only place your messages come from, O2 is skipped, and nothing else lands. With a second source, O2 reads this default again while connecting it.

## 14 · Skills point; rulings live in your Decisions

**We default to** keeping this kit's skill files as pointers (where the source of truth is, the fewest commands to run things, and what only that skill knows), with every ruling in your own Decisions room, one ruling per note, values in config, pits in Lessons, and nothing appended to a `SKILL.md` at the end of a session.

**Why** The same thing written in two places goes stale in one of them, and nobody knows which. A skill file used as the design record grows fast and drifts: the layout changes and the file does not follow, a status line goes stale, two rules contradict. A list copied into code drifts the same way (a copied list of legal `roles` values rejects a correct note as soon as the doctrine changes), so code and prompts read values from their one home at run time (the doctrine's roles, the playbook's section names) instead of carrying copies.

**Change it here** The kit's own `SKILL.md` stays a pointer table. Rulings: `paths.decisions_dir`. Seeds versus your copies (`live/prompts/`, `live/topic-map.json`, the three playbook folders): `wa_setup.py seed-status` and `install/update.md`.

**Re-rule it** Station O11 checks with you that every default ruled during the install is a note in your Decisions room and that none is written into a skill. If your vault has no Decisions room, the wizard stops and proposes a home before going on.

## 15 · An edited message is read again for 30 minutes

**We default to** keeping each message that passed the gate on a watch list for 30 minutes and reading it again while it is there. When the person edited it, a burst still waiting for its run gets the new text; a burst already judged is queued again with the old and the new version side by side, so the brain corrects every field it wrote from the old one. An intake entry the edit changed is corrected by appending a new entry with the words before and after; the older entry is never rewritten.

**Why** WhatsApp lets a sender edit a message for about a quarter of an hour after sending it, and an edit is usually a correction that matters: a different date, another amount, the right address. A judgment and a draft made from the old words then answer something the person no longer says, and the intake entry carries a date that is no longer true. The window is longer than the edit allowance because a run may still be in flight when the edit lands, and the next pass has to see it after that run ends. Rereading costs one query on the local database per pass while anything is on the list, and a model run only when the text really changed. The older intake entry stays as it was because the owner may already have placed it, and what the machine wrote is part of the record; the correction names what it replaces instead.

**Change it here** `edit_watch.enabled` (true), `edit_watch.minutes` (30, 1 to 1440). Both need a daemon restart. The watch list sits in `<live>/state.sqlite` (`references/privacy.md`, "Quotes in your vault").

**Re-rule it** Station C2 reads this default with the debounce window. One note.

## 16 · The board writes to your vault only when you switch it on

**We default to** no write button: `today.write_mode` is `off`, the Today brief only proposes, and placing happens in your own session. Switched on at station O7, the board writes only what you pressed, by program, never by the model, with a backup and an undo: a card you pressed OK on gets a write button, and pressing it updates or creates one task file (or, for a drop, places the entries), rendered by the program from the card, checked against your doctrine's own schema, backed up first, read back after, and logged in your filing log. A seed card is never written: projects are opened in your session.

**Why** Off by default because a background program that writes into your notes must first be able to prove what it changed. Proving it takes three things, and the button exists only while all three can be done: it pins what you were shown (a write whose pin does not match the card you pressed is refused whole), it backs up what it touches (so an undo restores the exact file, and refuses when you edited it since), and it reads back what it wrote (a mismatch restores the backup before it answers). Without the doctrine schema reader or PyYAML it cannot check the file against your own law, so the button stays hidden and the page says why: a write that cannot be checked is a guess, and no file is written before every check has passed, and a failure after the write restores the files from the backup; an entry already placed keeps its placed line and gains a rollback line. The model never writes, because what lands in your notes must be exactly what you pressed OK on, and other people's words are taken out first, because a task file is a note you keep and sync, not a quote store. `queue` exists for owners who want the press on the phone and the write in a session they watch.

What it costs: no model call. PyYAML for the system Python, installed in your own session on your yes (the kit never installs it). A folder of backups under `<home>/vault-write-backups/` that the kit never cleans.

**Change it here** `today.write_mode` (`off`, `direct` or `queue`), `paths.schema_reader` (empty looks in the two default places). Both need a restart. The shape of every write: `references/vault-proposals.md`, proposal g.

**Re-rule it** Station O7 asks, as question 6, whether you want the write button, after question 5 turned the brief on. One note.
