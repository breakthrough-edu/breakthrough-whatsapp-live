# Template changelog

Every seed in this folder carries a stamp `<!-- seed: breakthrough-whatsapp-live daemon/templates/<file> v<version> -->` (JSON seeds carry it in `"_seed"`). The owner's copies live in `<live>/prompts/` and `<live>/topic-map.json` and keep the stamp of the version they were copied from. After `npx skills update`, `install/update.md` reads this file to tell the owner what changed between their copy's version and the new seed, and merges section by section with the owner's yes. The maintainer bumps a seed's version and adds an entry here whenever its text changes.

## v1.0.0 (first public release)

- `brain-prompt.md`: the triage brain's rules and JSON contract (world, stage, priority, needs_me, draft, reason, confidence, situation, timeline, stake, summary, refs, captures, calendar_hint), with the owner's vocabulary, business context and window sizes filled from config on every run. Examples use an invented business.
- `backfill-prompt.md`: the one time summary backfill, ten conversations per batch, summaries only.
- `second-source-prompt.md`: the nightly second message source pull (recent lines for person notes and intake captures), quotes must be found in the source text.
- `topic-map.example.json`: the shape of topic rules that point the brain at vault notes; invented rules only.
- `claude-settings.json` (rendered, not an owner copy): hooks off, read only tools, the tool deny list, and Read deny rules for secrets, state, the WhatsApp databases and the reader snapshots.
- `config.example.json` (rendered at `init`): every key with its default and a `_help` line.
