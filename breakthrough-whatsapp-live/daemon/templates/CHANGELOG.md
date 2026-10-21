# Template changelog

Every seed in this folder carries a stamp `<!-- seed: breakthrough-whatsapp-live daemon/templates/<file> v<version> -->` (JSON seeds carry it in `"_seed"`). The owner's copies live in `<live>/prompts/` and `<live>/topic-map.json` and keep the stamp of the version they were copied from. After `npx skills update`, `install/update.md` reads this file to tell the owner what changed between their copy's version and the new seed, and merges section by section with the owner's yes. The maintainer bumps a seed's version and adds an entry here whenever its text changes.

## v1.2.0

- `brain-prompt.md` 1.2.0, three changes. (a) Under input 1 (the burst): a burst line ending ✏️ is a message the person edited after an earlier run judged it, and the old version follows; the current version wins, every field written from the old version is corrected, and `reason` opens with a short 「对方改了讯息: …」 (a typo fix that keeps the meaning keeps the judgment and still says so). (b) Under `captures`: `quote` is copied from one message as one unbroken span, never joined across messages and never shortened with an ellipsis, because the program now checks every quote against the run's own pack and marks what it cannot find as a gist (≈) instead of a quote (🗣); and the one exception to "fewer is better": an edited message whose date, amount, place or meaning changed gets a new capture with the new words. (c) The `stake` ending is now 「怎么开口: …」 (it was 「怎么跟他讲: …」); the person note section 怎么跟他讲话 keeps its name. An owner copy without (a) and (b) still runs, but an edited message is judged without the rule for it, and quotes the brain joins or shortens land as ≈ entries.
- `claude-settings.json` (rendered, not an owner copy): one new Read deny rule, `<home>/transcripts-cache/**` (the voice transcripts of every chat; the brain loses nothing, because the transcripts of the run's own conversation reach it inside the pack), and the `_comment` names it. `seed-status` shows `template newer` on an install rendered from the earlier template; `install/update.md`, step 6, renders it again and keeps the owner's own rules.
- `config.example.json` (rendered at `init` only): three new keys with their `_help` lines, `edit_watch.enabled` (true), `edit_watch.minutes` (30) and `intake.verify_quotes` (true). An existing config gets the defaults when it loads; nothing to merge.

## v1.1.0

- `brain-prompt.md` 1.1.0: one sentence under input 3 (the conversation pack). Lines marked 📄 under an attachment are text the reader converted from that document on the Mac: quote them as the document's content, never as words the person typed. Nothing else changed; an owner copy without it still runs, but may quote a document as if the sender had written it.
- `claude-settings.json` (rendered, not an owner copy): two new Read deny rules, `<home>/secrets/**` (the optional setup token and the deny probe's marker) and `<home>/doc-text-cache/**` (text of documents from every chat). `seed-status` shows `template newer` on an install rendered from the earlier template; `install/update.md`, step 6, renders it again and keeps the owner's own rules.
- `config.example.json` (rendered at `init` only): two new keys with their `_help` lines, `brain.gate_probe_seconds` (900) and `reader.read_documents` (true). An existing config gets both defaults when it loads; nothing to merge.

## v1.0.0 (first public release)

- `brain-prompt.md`: the triage brain's rules and JSON contract (world, stage, priority, needs_me, draft, reason, confidence, situation, timeline, stake, summary, refs, captures, calendar_hint), with the owner's vocabulary, business context and window sizes filled from config on every run. Examples use an invented business.
- `backfill-prompt.md`: the one time summary backfill, ten conversations per batch, summaries only.
- `second-source-prompt.md`: the nightly second message source pull (recent lines for person notes and intake captures), quotes must be found in the source text.
- `topic-map.example.json`: the shape of topic rules that point the brain at vault notes; invented rules only.
- `claude-settings.json` (rendered, not an owner copy): hooks off, read only tools, the tool deny list, and Read deny rules for secrets, state, the WhatsApp databases and the reader snapshots.
- `config.example.json` (rendered at `init`): every key with its default and a `_help` line.
