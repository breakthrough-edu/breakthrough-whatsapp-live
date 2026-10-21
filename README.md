# breakthrough-whatsapp-live

**Every WhatsApp message on your own number, judged the moment it lands, with a draft waiting on a private board on your phone. You still press send.**

A Claude Code skill package that turns an always-on Mac into a live triage desk for your personal WhatsApp. WhatsApp Desktop already keeps a local database of your chats on that Mac. A small resident daemon watches it, a rule gate decides which new messages are worth a model run, and a headless Claude Code run reads the conversation, sorts it (which part of your life or business, how far along it is, how urgent, whether the ball is in your court) and writes a draft reply in the conversation's own language. A board served from the same Mac, reachable from your phone through your own Tailscale network, shows all of it.

It is not a one-click installer, because no two owners' businesses, vaults and tools are alike. The package is a wizard your own Claude Code session reads and follows: it interviews you, measures your real traffic, installs one station at a time and proves each one works before moving on, and proposes every change to your second-brain vault instead of making it. Around the core you pick only what you want: person notes in your vault, a facts source for who paid, a calendar button, alerts, a nightly backup, a morning digest, an intake buffer, and a few more.

## What you see on the board

- **Everything that moved recently.** Conversations sorted into Reply, Waiting, FYI, Ignore and Done, plus every other chat active in the last three days, judged or not, with a "Judge now" button.
- **Where each conversation stands.** Three chips (world, stage, priority), a one-line situation, what is at stake for you, a short timeline, and the brain's running memory of the chat.
- **The draft.** Edit it in place. On a one-to-one chat the green button opens WhatsApp with your edited text already in the box; on a group it copies the text.
- **The conversation itself.** Text, photos, videos that play on an iPhone, and voice notes with a play button and a transcript made on your own Mac. Documents people send (Word, Excel, PowerPoint and the like) are converted to text on the Mac, so the brain reads them too.
- **Search.** Find any message or file name across every chat on the Mac, and open the conversation around the one you found.
- **Today, per project (optional).** A brief that proposes, project by project, what today's conversations mean for your work, as cards you answer OK or No; it is never pushed (it replaces the old evening intake push, now retired). By default it writes nothing into your vault; switched on at station O7, a write button puts a card you pressed OK on into its task file, by program, with a backup and an undo.
- **Your hand on it.** Change the world or the stage (the machine only ever moves a stage forward), mark a conversation done (D on a keyboard, a swipe on a phone), hand the conversation to a deeper Claude Code session, start or rewrite a person note, add a proposed meeting to your calendar.
- **Proof it is alive.** A footer with today's runs and tokens, and how long ago WhatsApp last wrote its database. If Claude cannot be called (a login that expired, a usage limit reached), a banner says so and what to do; the waiting messages stay queued, and your phone gets one alert, not one per message.

The board speaks English or Chinese. Drafts follow each conversation's language.

## Requirements, honestly

- **An always-on Mac** signed in to **WhatsApp Desktop** (linked to your phone and left open) and to **Claude Code**. It must not sleep: while it is off or asleep, nothing is judged.
- **macOS only. There is no Windows path.** WhatsApp Desktop for Windows encrypts its database with a key bound to that machine, and the only tools that open it are forensic ones.
- **Tailscale** on the Mac and on your phone, so the board reaches the phone without being on the internet.
- **Full Disk Access**, switched on by you in System Settings, for the node binary that runs the daemon and for your terminal app. The wizard explains why before it asks.
- Homebrew, node, ffmpeg, whisperkit-cli and a local whisper model of about 1.5 GB. The wizard installs them with your yes.
- A second-brain vault (the kind `my-second-brain` builds) for person notes and the intake buffer. Without one, the core still works.

## Usage, honestly

Every burst of messages that passes the gate becomes one Claude Code run, and a run is heavier than it looks: most of it is Claude Code's own system prompt and tool definitions, tens of thousands of tokens before your conversation is counted, so a few dozen runs a day reach millions of tokens. The board's footer shows your own figure. Person notes run on a heavier model, and the optional one-off summary backfill reads a month of history at once.

- **A Claude subscription is the practical way to run it.** On a subscription, the dollar figure each run reports is not billed; the limit you will meet is the usage window, so the board shows tokens, not dollars. On API billing, every run is real money.
- **That is why the gate exists.** One-to-one messages pass, because they are nearly always for you. Groups pass only when you tick them, for every message or only when you are mentioned, and a group you never ticked is skipped. Quick messages from one person become one run. Before you tick anything, the wizard measures three days of your own traffic, so you see what the gate will let through.
- A heavy account can start with a narrow gate and widen it after watching a week of real runs.

## It never sends

There is no sending code in this kit, and no setting adds one. It reads WhatsApp Desktop's database read only and opens no connection to WhatsApp. It uses no unofficial WhatsApp library and no browser automation, the routes that get numbers banned. The board's button opens WhatsApp with the draft prefilled; the last tap is always yours.

## Privacy in brief

- The WhatsApp database never leaves your Mac, and the kit never backs it up.
- What a run reads (the conversation, a few related vault notes) travels to Anthropic inside that Claude Code run, under the account Claude Code is logged in with. Headless runs leave no session transcript behind.
- The board listens only on your Mac and reaches your phone through your tailnet. It has no login, so your tailnet is its only lock.
- Alerts leave through the outlet you choose; a Telegram bot passes through Telegram's servers. The nightly backup, when you turn it on, is an unencrypted copy in the folder you pick.
- Person notes and intake entries put other people's words into your vault, and each writer has its own off switch.

Every flow, with who receives it and how to switch it off: [references/privacy.md](breakthrough-whatsapp-live/references/privacy.md).

## Install

```bash
npx skills add -g breakthrough-edu/breakthrough-whatsapp-live
```

Then open Claude Code on the always-on Mac and say:

> set up WhatsApp Live

The wizard keeps its place in `~/WhatsApp-Live/INSTALL-PROGRESS.md`, so an install can pause after any station and continue in a new session. Afterwards, in any session: "is my WhatsApp Live running", "why didn't this message show on my board", "uninstall WhatsApp Live". To update, run `npx skills update breakthrough-whatsapp-live -g -y`, then say "update WhatsApp Live": your prompts, topic map and playbooks are never overwritten, and newer seed text is merged into them section by section, with your yes.

## The stations at a glance

| Station | What happens | What you have at the end |
|---|---|---|
| C0 Fit and prerequisites | Four fit questions, the Mac, Full Disk Access, three days of your traffic measured, the playbook offer | A readiness report and your real numbers |
| C1 Reader | Voice tools and model, the working folder and config, the first read | A reading pack of your last three days, voice transcribed |
| C2 Daemon | Your gate and vocabulary, the launchd install, the first judgment by hand | A daemon judging new messages on its own |
| C3 Board | The board on your Mac, then on your phone through Tailscale | Every conversation in your pocket |
| C4 Person notes | The person note family and People room in your vault, the first note | One person note, written and checked |
| C5 Your tools | An interview about the tools you already use | The optional stations you chose, in order |
| O1 to O11 | Facts source, second message source, calendar, alerts, nightly backup, morning digest, intake buffer, voice loop, summary backfill, standby Mac, observation and acceptance | Only what you picked |

## A first step for heavy accounts (optional)

Not sure how much traffic you have, or whether a machine judging it would help? Run the export-based [breakthrough-whatsapp-triage](https://github.com/breakthrough-edu/breakthrough-whatsapp-triage) for three days first:

```bash
npx skills add breakthrough-edu/breakthrough-whatsapp-triage
```

It builds a one-page board on demand from a snapshot of your history, with nothing resident, and shows you on your own chats what "what needs me today" looks like, and how much there is of it, before you hand the job to a daemon.

## What it is not

- Not an auto-reply bot, not bulk messaging, not a way to read another person's number or to watch a team.
- Not the WhatsApp Business API or Cloud API, and not built on an unofficial WhatsApp library.
- Not `breakthrough-whatsapp-triage`, which builds a board on demand from an exported history, on any computer.
- Not a hosted service: nothing runs anywhere but your Mac and the Claude Code runs it starts.
- Not a set of fixed opinions: every default comes with its reason, and the wizard asks you to rule on it for your own business.

## Repo layout

The payload lives in [`breakthrough-whatsapp-live/`](breakthrough-whatsapp-live/); that nesting is what `npx skills` installs as one unit.

```
breakthrough-whatsapp-live/
  SKILL.md        the router: set up, update, operate, diagnose, uninstall
  install/        the wizard: 00-start-here, stations C0 to C5 and O1 to O11, update, uninstall
  references/     config reference, the five interfaces, defaults and why, pitfalls, vault proposals,
                  feature checklist, privacy, operating, troubleshooting
  daemon/         wa_live.py and its modules, wa_setup.py, adapters/, board.html, templates/
  playbooks/      seeds for three playbook folders, copied into your vault only with your yes
  skills/         breakthrough-whatsapp-read and breakthrough-person-dossier, linked into your skills folder
```

The Python runs on the macOS system Python (3.9) with the standard library only; the one optional exception is the write-to-vault button of station O7, which needs PyYAML for the system Python, installed on your yes. node is the launchd parent.

## License

MIT, see [LICENSE](LICENSE).

This kit directs an AI agent running on your machine to read your own messages and to modify your own files. Review what it proposes before approving it, keep backups of anything you care about, and note that everything here is provided as is, without warranty of any kind.

Made by Breakthrough EDU.
