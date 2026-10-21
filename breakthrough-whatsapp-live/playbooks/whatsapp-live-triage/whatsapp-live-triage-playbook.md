---
type: playbook
lane: build
status: forming
confirmed_by_owner: false
references: []
tags: []
---
<!-- seed: breakthrough-whatsapp-live playbooks/whatsapp-live-triage/whatsapp-live-triage-playbook.md v1.1.0 -->

# Playbook: 给 personal WhatsApp 装一个实时分诊 daemon

写给「想给自己的号码装一套实时分诊」的人和他的 AI: 这份不是设定值, 是决策点和坑。照它走能避开已知的坑, 剩下的按自己的机器填。kit 的码已经照这些坑做好; 这份留着的是判断, 以后你改它、加东西、出事时读。

怎么读这份:

- kit 装在 `~/.claude/skills/breakthrough-whatsapp-live/`, 文中 `references/…` `install/…` `daemon/…` 都指那里。
- 写成 `doorbell.lookback` 这样的是 config 键, 在你的 `live/config.json` (默认 `~/WhatsApp-Live/live/config.json`); 每个键的默认值与意思在 `references/config-reference.md`。
- 文中「我们默认 X, 因为 Y」的每一条, `references/defaults-and-why.md` 都有同一条并写着在哪改; 你改了哪一条, 照你 Command Base skill 的「log a decision」在 Decisions 房记一份。

## When to run it

- 你的生意跑在**自己的 personal WhatsApp 号**上, 一天几十到几百封 inbound, 病不是「没看到」是「看了没接上」。
- 你**不要**接 WhatsApp Business / Cloud API (不是 personal number, 而且 Meta 2026 年初关了通用型 AI 助理那道门), 也**不要**用 Baileys / whatsmeow / WAHA 那类 linked-device 库 (公开报告里, 封号多半出在那一路)。
- 你有: 一台**常驻**的 Mac (会合盖的笔电不算) + 上面登入着 WhatsApp Desktop + Claude Code 登入 + Tailscale (手机开板子用)。⛔ Windows 没有路: 这整套读的是 macOS 版 WhatsApp 写在本机的 SQLite。
- 你接受**永不自动发送**: 系统出草稿, 人按 send。这不是保守, 是这条路唯一没有封号面的原因。我们默认这样, 而且 kit 里没有打开自动发送的开关, 也不要做一个。

## What to weigh

**1 · 唯一零封号面的路是读官方 client 自己写的数据库, 别的都在赌。** WhatsApp Desktop (Mac) 把每封讯息写进 `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite`, 语音图片在旁边的 `Message/Media/`。你不接任何新连线, 就没有多一台 device 的指纹。代价是绑一台醒着的 Mac; 有常驻机, 这个代价几乎不存在。那个库的表与栏位是 app 私有格式, WhatsApp 升级可能会漂: 读不到某个栏位就停下来讲清楚, 不要跑出一份空的阅读包。

**2 · 门铃便宜, 闸才是设计核心。** 活跃的号一天可能有几百封 inbound, 多半是群闲聊。「一封一个 run」就是一天几百个 headless session, 钱和噪音都受不了。闸分三档: 一对一全过 (`direct_policy`); 群按白名单三选一 (全部过 / 提到你才过 / 不过闸, 写在 `group_policies`), 没列的群默认不过 (`unlisted_group_policy`); 再加每条对话各自的 debounce (同一个人连发五行算一个 burst: 静默 `debounce.quiet_seconds` 秒起跑, 默认 45; 从第一封起算 `debounce.max_wait_seconds` 秒强制起跑, 默认 180)。⭐ **白名单让你自己勾**, 别让 AI 替你猜哪个群值得每句都读; 正本只准一份。勾完之后一天跑几个 run, 主要看你设成「全部过」的群有多吵; C0 的 `measure` 给你自己的数字。「提到你」认的是 `@你的号码` 与 `my_names` 里的称呼, 回覆你的讯息不算提到; 装完拿一个设了「提到你才过」的群实测一次再信它。

**3 · 你自己回了就是信号。** 数据库也记 outbound (`ZISFROMME=1`)。看到某条对话有新 outbound, 就把它标 replied、取消还没跑的 burst。板上那行自动清, 不用人按「已处理」。这是本机数据库路线独有的, webhook 路线拿不到。

**4 · 脑一个 run 做完四件事, 吐 JSON 契约不吐 markdown。** 读、分类、分流、草稿, 不拆四个 session。喂进去: 这个 burst 原文 (语音先转字) + 这条对话最近几天的阅读包 + 第二大脑里关于这个人的候选档 (他的人物档若有「怎么跟他讲话」那节, 草稿照它写) + 上一次的判断 (`world` / `stage` / `priority` / 草稿 / 状况 / 时间线, 让它延续, 不是每次重来)。吐出: `{world, stage, priority, needs_me, draft, reason, confidence, situation, stake, refs, summary, timeline}`, 另有 `captures` (进料口) 与 `calendar_hint` (日历钮) 两个键给选配站用; 完整契约与一个虚构的例子在 `daemon/templates/brain-prompt.md`。`world` 是哪个世界 (词表在 `vocab.worlds`), `stage` 走到哪一步而且只准往前 (非生意的世界不填), `priority` 多急, `needs_me` 是「球在不在你手上」。⛔ 别信它会吐裸 JSON, 扫第一个成对大括号。`confidence` 的定义要写死: 是「`world` / `stage` / `priority` / `needs_me` 判对了」的把握, 不是草稿的好坏。

**5 · 群的对话包要短。** 活跃群拉太多天加全部语音, 脑一跑就容易撞超时。群只拉 `brain.group_context_days` 天 (默认 3), 再截到 40k 字左右; 一对一拉 `brain.context_days` 天 (默认 7)。

**6 · 板子不上云, 因为板上全是别人的私讯。** 任何云端托管都多一个私讯外泄的地方, 所以板子只在本机。最省也最私密的是 daemon 自己在本机开一个 port (`board.bind` 默认 `127.0.0.1`, `board.port` 默认 8787), 用 `tailscale serve` 挂到你的 tailnet, 手机开着 Tailscale 就能开, 数据一个 byte 不出这台机。⛔ 不准用 `tailscale funnel`, 那是公网。我们默认这样, 理由就是这一条。

**7 · 草稿是可改的, 按钮永远带最新的字。** 板上草稿是 textarea, 一边改 `whatsapp://send?phone=…&text=…` 一边跟着变, 停手就存回。`@lid` 那种没号码的联络人, 去 `LID.sqlite` / `ContactsV2.sqlite` 解出电话, 一样能跳。⛔ 群跳不了: WhatsApp 的网址格式开不了群, 有的写法会改开「转发给…」面板; 群只能复制。推播里的链接要用 `https://wa.me/<号码>`, 多数推播 app (例如 Telegram) 只把 http(s) 变成可点。

**8 · headless Claude 在 launchd 底下, 跟桌面上不一样的四件事。**

- (a) 全域 settings 里任何会等 GUI 的 hook (例如会播放声音或等待 GUI 回应的 hook) 永不返回, claude 答完就永远不出 result。无人值守的 run 用自己的 `--settings` 写 `disableAllHooks: true` (kit 的 `claude-settings.json` 就是这样), 或把那条 hook 从全域拿掉。
- (b) `--allowedTools` 读起来像「只准这些」, 实际是「这些也算进来」, 只加不减: 允许清单之外的工具也可能被载入; 要靠 deny 清单挡, 并印出实际工具清单核对。真正关门的是 `--disallowedTools` 加 settings 的 `deny`, 而黑名单只关得住叫得出名字的东西, 所以再加一道 cage check: 每个 run 把 CLI 自己回报的 `system/init` 工具清单跟预期比, 多出来的就推一则错误。⛔ 模型说「我没有那个工具」不算闸有效, 那可能只是它在复述你给它的 system prompt; 要拿执行环境自己的说法。
- (c) 工具笼子只挡工具种类, 不挡读哪里。外人传来的讯息原文会进提示, 一句话就可能叫脑去读推播钥匙、`config.json` 或别人的整份阅读包, 再写进草稿或摘要。所以 settings 的 `deny` 还要挡读秘密: 写成 `Read(//<绝对路径>)`, 两条斜线再接去掉开头斜线的绝对路径 (这种写法在 `claude -p --settings` 底下挡得住; `references/pitfalls.md` 第 10 条的金丝雀让你在自己机上验)。kit 的 `wa_setup.py init` 照你这台机的路径生成这几条。
- (d) ⛔ 不能在 `ssh` 里直接跑 headless claude 来测, 拿不到 keychain 会假报 `Not logged in`。验收走 launchd 那条活路: kit 的 daemon 认 `live/commands/` 里的指令档, 丢一个 `probe-claude.json` 进去就是这个用途。

**9 · 最大的坑: launchd 直起的 python 有 Full Disk Access, 它的子进程没有。** `/usr/bin/python3` 是个 stub, 会 exec 成 Command Line Tools 的 `Python.app`, 它自己拿到的授权不传给子进程。每个子进程碰到受保护的路径 (Documents、别的 app 的 Group Containers) 都被 tccd 挂起, 等一个没有画面能答的授权弹窗: ffmpeg 开 WhatsApp 的语音档、whisperkit 开 model、claude 开受保护的夹, 全部卡在 `open()`, 零 CPU, 直到超时。症状是「零输出 + 零 CPU + 越等越久」, 不是报错。因为没有报错, 很容易当成被叫的那支程式自己的 bug, 到错的地方去找; 语音不上板, 看起来又像 daemon 漏讯息。判法: 第一刀 `sample <pid>` 看堆叠是不是停在 `__open` / `__openat_nocancel`, 第二刀 `pgrep -P <pid>` 看子进程; 停在 `__open` 就是 TCC, 别去改码猜。若有人在屏幕前点掉弹窗, 症状会暂时消失。修法: launchd 的程式换成有 Full Disk Access 的 node, 由它起 python (kit 装在 `~/WhatsApp-Live/launchd/run.mjs` 的那个十行的壳), 整棵树都通。系统设定里授权的必须是 plist 里写的那一支 node (`command -v node` 找; 官方安装包在 `/usr/local/bin/node`, Apple Silicon 的 Homebrew 在 `/opt/homebrew/bin/node`), 授给终端机没有用。三道保险留着, 因为它们各自独立成立: 语音档先由 python 复制到临时夹再给 ffmpeg; whisper model 副本住 `~/.cache/whisper-models/` (⛔ 不放 Documents、Desktop、Downloads); claude 不给任何 `--add-dir`, cwd 也不放 Documents。换 node 版本或路径之后, 先用 probe 复验一次。子进程这样卡住, 要是连主循环也被卡住, 板子的 `/health` 回 503 (`stalled …`), 卡太久 daemon 会推一则错误; 按掉弹窗之后用 `wa_setup.py restart` 重启。⛔ 不用 `launchctl kickstart -k`: 它只停 node 家长, 卡住的 python 跑不到自己的停止处理, 变成孤儿继续占着板子的端口, 新起来的那份绑不上, 板子显示的是旧进程或什么都没有。

**10 · 时间戳。** Core Data 的秒数从 UTC 的 2001-01-01 起算, 直接当本地时间用会差你所在时区的时差 (例如 UTC+1 就差 1 小时); 不转的话, 脑会把 burst 放错时段, 例如把上班时间进来的讯息看成半夜的。所有对外的时间都转本地。

**11 · 门铃往回看, 是为了不静默丢; 代价是重放, 要认得三个口。** 例如: 新对话的第一封, 编号 (`Z_PK`) 可能比同一秒的系统通知小, 却较晚写进库; 门铃先看到编号大的那封就把水位推过去, 小的那封从此永远在水位之下, 静默丢了。所以门铃每次扫库都往回看 `doorbell.lookback` 个编号 (默认 300), 用 `inbound_log` 判断哪些处理过。代价是有三个口会把旧讯息当新讯息跑, kit 各有一道护栏:

- **全新状态库第一次起跑**: `inbound_log` 是空的, 往回看的那几百封全都「没处理过」。kit 把它们记成 `skip:before_install`, 不跑。
- **WhatsApp Desktop 重新 link 或补同步**: 旧讯息拿到新编号, 一批批写进库。kit 的年龄闸把早于 `doorbell.max_message_age_hours` (默认 48) 的讯息记成 `skip:too_old`; 水位若高过库里最大的编号 (编号被重建过), daemon 自己重定水位并推一则错误。
- **把别台机的 `state.sqlite` 放回来** (备援机接手、交回常驻机): 水位与 `inbound_log` 里的编号都是别台库的, 两样都要清, 步骤在 [[standby-machine-runbook]] 第 3 步。

**12 · 读不到的要报出来; 射程只到你自己的号。** 语音转字是空的、图片开不了、影片只有缩图, 判断里明写读不到什么: 知道漏了不是问题, 静默漏掉才是。盯团队有没有做事不在范围内: 团队在群里报告的照样读得到, 那是副产品不是目标, 真要盯是共享进度工具的题。

**13 · 「脑叫不动」不是那条对话跑坏了。** run 失败有两种。一种是那条对话自己的问题 (阅读包太大、超时、模型吐了坏的 JSON), 修法在那条对话里。另一种是脑根本叫不动: Claude Code 的登入失效, 或用量额度到顶。这一种不管哪条对话都会一样失败, 每进一封就多一个失败的 run, 那条对话里也没有东西可修。kit 的断路器专管这一种: 连续两次失败都是「叫不动」就打开, 不起新的 run, 待判的讯息留在队列里一封不丢; 每 `brain.gate_probe_seconds` 秒 (默认 900) 用最小的呼叫探一次, 通了就关上, 排队的讯息照顺序接着判。开与关各推一则, 板上挂一条横幅, 隔天日报的健康那块记停了几分钟。修法是登入或等: 登入失效, 就在常驻机上登入 Claude Code 一次 (daemon 用 setup token 时, 换一个新的 token); 额度到顶, 就等它探到通。⛔ 别为了这种失败去改闸、改提示, 或重判那条对话。

## The moves

1. **先量流量**: `wa_setup.py measure` 数三天 (一对一与群的 inbound 各多少、哪几个群占大头; 只出数字与群名)。没这个数字, 闸设不准。
2. **画流程图让你确认理解** (触发 → 读 → 分类 → 分流 → 草稿 → 板 → 人按 send; 自动发送不做), 你说「对」再动手。
3. **确认常驻机上 WhatsApp Desktop 已 link 而且库活着** (`stat` 看 WAL 档的修改时间是几分钟内; `wa_setup.py doctor` 一起查), 不要以为要重新 link。
4. **白名单给你勾** (一页清单、板上、或直接在对话里勾都行), 读回来落进 `group_policies`; 正本只准一份, 正本在哪写进你的决定。
5. **daemon 三层** (kit 已经照这样做好; 你改它时守住这几条): 门铃 (每 `doorbell.poll_seconds` 秒看一次 WAL 的修改时间, 以 read-only 查 `ZWAMESSAGE` 里 `Z_PK` 大于水位的行, 加上第 11 条的往回看; 水位持久化, 首跑定在当下, 不补旧讯息) → 闸 → 脑; 状态落 sqlite (conversations / runs / pending / inbound_log)。⛔ 绝不静默丢: 每封 inbound 在 `inbound_log` 留一行过或不过与为什么, 每个 run 成败都留行, 失败推给你。
6. **launchd 用 node 当家长起 python** (第 9 条), 脑的 cwd 不放 Documents (`brain.cwd`); 先用最小的 probe 验 claude 通不通 (丢 `probe-claude.json` 指令档, 几秒内回 result 才算通; 拖到被杀掉就是第 8、9 条的坑)。
7. **语音预热**: daemon 第一次跑之前, 先把窗内所有语音转进快取 (`prewarm-<天数>.json` 指令档), 用自己的工作夹跑, 不挡真实 run。⛔ 每个 run 各用自己的快照夹 (两个 sweep 共用快照档会互踩), 只共用语音快取; 同一时间只丢一个预热指令。
8. **板子**: daemon 自己 serve, 用 `tailscale serve --bg --http=80 http://127.0.0.1:<board.port>` 挂上 tailnet (要 https, 先在 Tailscale 管理页开 HTTPS 凭证再改挂法); 三栏 (列表 / 状况 + 时间线 + 草稿 / 最近对话), 手机单栏; 首屏数据内嵌在页面里 (内容拦截器挡了 fetch 也能看); 定时刷新只在内容变了才重画 (否则会把人推到最下面)。
9. **观察期一周, 再收**: 头一周板子天天看; 想让手机也收到每个判断, 这一周可以把 `notify.mode` 暂设 `all` (不用回的也推, 标「不用回」)。一周后看 runs 表的真实数量与花费, 再收白名单、再改词表, 然后回到默认的 `errors`: 只推出错 (run 失败、笼子破口、WhatsApp 库超过 `doorbell.stale_alert_hours` 小时没动、daemon 的主循环卡住), 外加每天早上一则日报。我们默认只推出错, 因为成功也推会把手机变成噪音, 你很快就不看了, 真出错那则反而被淹掉; 可是完全不推又分不清「没消息」是安静还是死了, 所以每天早上一则日报 (安静日也发一句说它活着), 把沉默变成可信的沉默。
10. **登记**: 常驻物六格 (读 · 写 · 上限 · 停 · 证明 · 回滚) 写进你的常驻物登记簿, 一支一行: vault 里有列出各台机常驻程序的登记档就写那里, 没有就写 `~/WhatsApp-Live/INSTALLED.md`; 写机器的名字, 不写「这台」。这套有三支要写:
    - daemon 本身。
    - 晚间进料口排程: 开了 `intake.evening_push` 才有, 它是另一支 launchd (`launchd.intake_label`)。
    - daemon 里的档案 run: 不是独立的 launchd 项, 却是这套唯一会整份改写 vault 档的东西。读 = 对话包 · 事实 · 现档 · person-dossier playbook 的骨架 · 宪法的 roles; 写 = 人物档整份 · filing-log 一行 · 备份夹留三版 · `live/runs/` 里的 run 夹; 上限 = 夜批每晚 `dossier.nightly_cap` 份、一次一个、同一人 `dossier.min_gap_hours` 小时一次、每 run `dossier.run_timeout_seconds` 秒; 停 = `dossier.enabled: false` 加 `wa_setup.py restart`, 然后看 People 房的档修改时间, 确认近况行那一路也停了, 没停就只能停 daemon; 证明 = 验档器过 + filing-log 那一行 + 备份在; 回滚 = 盖回备份, 中间追加的近况行会跟着丢, 要讲出来。

    你拍的规矩一条一份进 Decisions 房 (照 Command Base skill 的「log a decision」, 落档前先查现行的决定有没有冲突); ⛔ 不写进任何 skill 的 SKILL.md, skill 只当指针。我们默认这样, 因为 skill 每次 `npx skills update` 会被整个换掉, 而且往 SKILL.md 追加拍板会越长越大、越长越漂。项目 brief 加 handoff, 让下一场接得上。

## 不建议的路, 与原因

- WhatsApp Business / Cloud API (不是 personal number; 通用 AI 助理那道门已经关了)。
- Baileys / whatsmeow / WAHA / OpenClaw 那类 linked-device 库 (有公开的封号报告)。
- 浏览器自动化盯 WhatsApp Web (公开报告里有反复被封的例子)。
- iPhone 端通知监听 (Shortcuts 不认第三方讯息); macOS 通知中心数据库 (只有消掉的才留档, 拿不到群 jid)。
- 把板子部署到云端 (见第 6 条)。
- 自动回覆、群发或广播、替你按 send: 永不代发。
- 读别人的号、盯团队的号: 那是别人的隐私, 也是共享进度工具的题。
