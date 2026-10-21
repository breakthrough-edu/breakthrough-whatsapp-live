---
type: guide
guide_family: playbook
updated: 2026-10-21
---
<!-- seed: breakthrough-whatsapp-live playbooks/resident-workers/_Resident-Workers-Guide.md v1.0.1 -->

# Resident Workers

建或改一支你不在场也在跑、会动手的东西 (聊天 bot、launchd 常驻程序或 folder watcher、排程 job) 时, 怎么判断它的边界, 与哪些动作要你按了才做。

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

读 [[resident-workers-playbook]] (由 WhatsApp Live kit 的种子装进这个夹, 装的那天你点了头; 不限于 WhatsApp)。两章, 加一节坑:

- **六格** (读 · 写 · 上限 · 停 · 证明 · 回滚), 动手前写在你的常驻物登记簿那一行底下。写不出来的那一格就是洞。最常见的三种: 停了其实没停 (重开机自己复活) · 证明只有 exit 0 · 从机器外面看不出它还活着。
- **审批卡**, 任一条成立就上卡: 别人看得到 · 碰真钱 · 收不回来 · 写进去的值是 AI 生成的。卡上钉完整内容加版本, 按下后由程序照卡执行, 不回 AI 重新生成; 失败把草稿放回去。三十天里每张卡都原样按过, 就是判准太宽。
- **排程与 launchd 底下的坑**: 第一次启动先判今天该不该补跑 (默认不补); launchd 直起的 python, 子进程没有 Full Disk Access, 用 node 当家长; 重启有子进程的常驻物不用 `kickstart -k` (⛔ 卡住的子进程会变孤儿, 继续占着端口), 先停子进程, 再证明只有一个进程占着端口; 一个没实测过的闸是信念, 不是闸。

平台自己的水管 (某个聊天平台的命令行工具、使用者 id、卡片回调) 不在这里, 住各支自己的登记簿那一行或项目夹。

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
⛔ ONE carve-out, from iron law 1: a line that ships at high frequency (daily content, for
  example) does not get a row per output. Put ONE pointer line here naming
  where those rows really live, and keep individual rows only for the outputs that carried
  a bet. High-frequency rows never enter the vault, and a door is still the vault.
-->
