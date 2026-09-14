---
type: guide
guide_family: playbook
updated: 2026-09-14
---
<!-- seed: breakthrough-whatsapp-live playbooks/whatsapp-live-triage/_WhatsApp-Live-Triage-Guide.md v1.0.0 -->

# WhatsApp Live Triage

给一个 personal WhatsApp 号装实时分诊: 讯息一进来自己苏醒、判断、出草稿、上手机开得到的板, 人按最后那一下 send。

## Read this before working, in this order

1. **Settle what is open.** Read "Recent runs" below. Any row whose date has passed with
   nothing in "What happened" gets looked up where the answer lives (the messages, the
   orders, the sign-ups), drafted, and put to the owner as one question. ⛔ A row with a
   blank date carried no bet: it is an index entry, it is never chased, and it is not
   something to settle.
2. **Revise, if a sentence just came up for the third time.** In "So". The owner's yes
   first, then "How we do this work" changes.
3. **Do the work**, with the section above brought up to date a minute ago.
4. **Register what came out**, in this same session: one row, ⭐ its address in the
   first column, then what the owner expects and when that counts if they have an
   expectation. "Nothing in particular" is a real answer and the row still goes in.

## How we do this work

正文在 [[whatsapp-live-triage-playbook]]: 决策点、坑与动手的顺序, 从一套跑着的安装口述下来, 由 WhatsApp Live kit 的种子装进这个夹 (装的那天你点了头)。骨架四句:

- **读官方 client 写在本机的数据库, 不接 API, 不用非官方库**: 唯一零封号面的路, 代价是绑一台常驻 Mac。
- **闸是设计核心, 不是门铃**: 一对一全过、群照你自己勾的白名单、每条对话各自 debounce; 你自己回了就是 replied 信号。
- **脑一个 run 做完四件事** (读、分类、分流、草稿), 吐 JSON 契约 (`world` · `stage` · `priority` · `needs_me`, 加草稿与理由); 板走 Tailscale 内网不上云; 草稿可改, 按钮带最新的字; 永不自动发送。
- **launchd 底下的坑先读**: 会等 GUI 的 hook 让 claude 永不返回; python 的子进程不继承 Full Disk Access, 用 node 当家长; 库里的时间戳从 UTC 起算。

常驻机躺了 (早上日报没来是最可信的讯号) 照 [[standby-machine-runbook]] 按: 先确认真死 → 备援机装同一套 → 拉昨晚备份放回去 → ⛔ 绝不两台同跑。

## What we have learned

- (还没有: 「So」那栏同一句出现第三次, 才在这里落一行)

## Recent runs

| What came out | What I expect | When it counts | What happened | So |
|---|---|---|---|---|

<!--
Filename is `_<Name>-Guide.md`, one per playbook folder, and it is mandatory from the day
  the folder is made (doctrine §9.1). At most one `_`-prefixed file per folder.
⭐ This door is written to be handed over. A contractor, an assistant or another AI should
  be able to work from this folder with no skill installed, which is why the four beats are
  written out above instead of living in a skill.
The two halves age differently. "How we do this work" goes stale and gets revised; the rows
  below are things that already happened and are never rewritten to stay correct.
⭐ "What came out" is an ADDRESS, and it is the point of this table: a wikilink or path
  when the thing lives in the vault, a URL or a plain-words location when it does not.
  Three months later "what have I made with this, and where is it" is one glance.
⛔ Every row lands with the owner's yes, drafted by whoever is in the session (§4 law 4 is
  not carved out for this table).
"When it counts" is the owner's own number: a post answers in about a week, a proposal in
  about a month, a course in about a quarter. Ask; the maturing time belongs to the line of
  work and nothing else knows it. ⛔ Blank on a row that carried no bet, and blank is what
  tells beat 1 to leave that row alone forever.
⭐ Third time the same sentence appears in "So" = propose revising "How we do this work".
  That is the whole trigger mechanism, and it is a person noticing a repeat on purpose.
⚠️ This file is read every time the folder is opened, so it must not be allowed to grow
  without limit: keep "What we have learned" to a few permanent lines and let old rows sink
  to the bottom of "Recent runs".
⭐ EVERY output made with this playbook gets a row, whether or not the owner had an
  expectation about it. This is that playbook's own index plus its track record, and it is
  one of the two content-mapping exceptions doctrine §4 names (Home stays the only
  vault-wide directory; this indexes one folder's own outputs and nothing else).
⛔ ONE carve-out, from iron law 1: a line that ships at high frequency (daily content is
  the case that exists) does not get a row per output. Put ONE pointer line here naming
  where those rows really live, and keep individual rows only for the outputs that carried
  a bet. High-frequency rows never enter the vault, and a door is still the vault.
-->
