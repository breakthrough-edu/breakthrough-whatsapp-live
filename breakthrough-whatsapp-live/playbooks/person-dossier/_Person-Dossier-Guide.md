---
type: guide
guide_family: playbook
updated: 2026-09-14
---
<!-- seed: breakthrough-whatsapp-live playbooks/person-dossier/_Person-Dossier-Guide.md v1.0.0 -->

# Person dossier

把一个真人的对话与记录蒸馏成他在你 People 房 (config `paths.people_dir`) 的那份档: 谁跑都一样, 机器 (WhatsApp Live 的档案 run)、任何 session、你自己。

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

正文在 [[person-dossier-playbook]], 由 WhatsApp Live kit 的种子装进这个夹 (装的那天你点了头)。⚠️ 它同时是码的规格: 档案 run 每次现读这一份 (config `paths.person_playbook`), 节名从它「### 骨架」那个栅栏现读, 所以改它就是改机器的行为。骨架六句:

- **什么时候写**: 聊到问价、问日期、问怎么买以上 (stage 到 `dossier.auto_open_min_stage`), 而且对话的世界列在 `dossier.auto_open_worlds` 里 (默认空 = 只在你叫时开) 的人, 自动开档; 有档的人只在夜里重蒸 (当天来往 `dossier.nightly_min_msgs` 封以上或阶段变了, 一晚最多 `dossier.nightly_cap` 份); 你叫就马上跑; 你讲的话融进对应的节, 只动相关的格, 不整份重写 (见最后一句)。
- **怎么想**: 只写他讲的 (🗣 带时间)、看到的 (👁)、标了根据的推论 (🤔); 空格不补, ⬜ 就是下次要问的; 别人的对话不进他的档; 你写的一字不动, 验收要验内容, 不只验链接。
- **怎么写**: 骨架承接 customer avatar 常用的四段问法, 但规矩反过来 (写一个人、空格留着、推的要标); 「The moves」那节就是喂给档案 run 模型的规格。
- **stage 只提议**: daemon 裁决, 只准往前, 成交以上只认付款记录。
- **第 6 节「怎么跟他讲话」是给下一封讯息用的**: 六格从他的讯息数出来, 加一句「下一封怎么开口」; 分诊脑写草稿照它, 板上显示那句。
- **你讲, 它更新**: 对任何 session 说「更新 <名字> 的档: …」, 那个 session 自己做, 不经 daemon: `dossier.py prepare` 带上你的原话 → 熟读整份档, 把话融进对应的节 (每处标 `🗣 <marker> <日期>`, ⬜ 被答了就改成事实) + 手记那节 (`## <marker> 手记`) 留一行原话 → `verify` → `place`。命令的参数看 companion skill `breakthrough-person-dossier` 的 SKILL.md; 触发句住你 Command Base skill 的 update-person 模式 (装 C4 那站时提议加进去)。以后重写认得这些标记与手记行, 掉一处整份退回。`<marker>` 是你的缩写 (config `owner_marker`)。

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
  For this folder that is the nightly batch: the notes the daemon places on its own are
  indexed by the filing-log (config paths.filing_log; the People room itself when the
  vault keeps no log), and a session registers one row per batch it ran.
-->
