---
type: method
lane: build
status: active
confirmed_by_owner: false
tags: []
---
<!-- seed: breakthrough-whatsapp-live playbooks/whatsapp-live-triage/standby-machine-runbook.md v1.0.1 -->

# 常驻机躺了怎么办 (WhatsApp Live 冷启动程序)

> 我们默认 daemon 只在一台常驻 Mac 上跑, 不做双机热备, 每晚把状态备份到你指定的夹。理由: 两台各跑一份, 会对同一封讯息判两次、写两份状态、推两次, 而且两台的 WhatsApp 本机库本来就不同步; 单点的风险用「每晚一份备份 + 一份照着按的冷启动程序」来付, 比双机同步便宜得多 (`references/defaults-and-why.md` 有同一条与在哪改)。这页只管一件事: **常驻机真的躺了, 怎么在备援机上把分诊接过来, 再怎么交回去。**
>
> 一句话版: 先确认真死 → 备援机装同一套 → 拉昨晚备份放回去 → ⛔ 绝不两台同跑。

⛔ **同一时间只准一个 session 动 daemon**, 两台机加起来也是 (改码、重启、重排都算): 重启会切断等不完的 run (`wa_setup.py restart` 最多等 plist 的 ExitTimeOut, 默认 90 秒), 两个 session 各自重启会互相踩。

## 先填好这几个值

下面的命令都用它们。尖括号那几个全换成你 config 里的值 (装机进度档的档头记着同样几个), 一个都不照抄 kit 的默认: 同一台机若装过第二份, 默认值指的是另一份。`WL` 写绝对路径, 引号里的 `~` 不会展开。

```bash
KIT="$HOME/.claude/skills/breakthrough-whatsapp-live"
WL="<config paths.home, 绝对路径>"      # config paths.home
L="$WL/live"                         # config paths.live_dir
LABEL="<config launchd.label>"       # config launchd.label
ILABEL="$LABEL.intake"               # config launchd.intake_label, 永远是 label 加 .intake
PORT="<config board.port>"           # config board.port
PRIMARY="<常驻机的 ssh 别名>"          # ~/.ssh/config 里给常驻机取的 Host 名
RL="<常驻机上的 live 夹, 相对常驻机的家目录>"   # 例: paths.home 是家目录下的 X 夹, 就写 X/live
B="<夜备去处的根>"                    # config backup.dest
```

文中 `references/…` `install/…` `daemon/…` 都指 `$KIT` 底下。

## 怎么知道它躺了

按可信度排:

1. **早上的日报没来 = 最可信的讯号。** 日报 (`digest.at`, 默认 08:00, 走你的推播出口) 连安静日也会发一句说 daemon 活着。早上没收到, 就是 daemon、推播出口、或整台常驻机三者之一躺了, 不用再猜「是不是刚好没事」。前提是 `digest.enabled` 与 `digest.push` 开着、`notify.mode` 不是 `off`。
2. **「WhatsApp 库太久没动」的告警** (`doorbell.stale_alert_hours`, 默认 6; `notify.mode` 是 `errors` 以上就会推)。这条只证明 daemon 还活着, 但 WhatsApp Desktop 没在写库: 多半是 WhatsApp Desktop 掉线或没开, 不是常驻机死了。
3. **「主循环已经 X 小时没动」的告警** (卡住超过 `doorbell.stale_alert_hours` 的一半就推, `notify.mode` 是 `errors` 以上)。daemon 进程还在, 只是主循环卡住了, 常驻机没死: 走第 1 步的 1g。
4. **板子打不开。** ⚠️ 这条最弱: 手机没开 Tailscale、tailnet 掉了、`tailscale serve` 没挂, 都会一样打不开。

## 第 1 步: 先确认真死 (不是 WhatsApp Desktop 掉线, 也不是 Tailscale 断)

在备援机上, 由外往内查; 哪一层通了就停在那一层修, ⛔ 不要一上来就去装备援机。

```bash
# 1a. tailnet 里看得到常驻机吗
tailscale status
# 1b. ssh 进得去吗
ssh "$PRIMARY" 'uptime; date'
```

进得去 = 常驻机没死, 是上面的东西躺了。继续在 ssh 里查:

```bash
# 1c. daemon 在不在 launchd 里, 有没有 pid
ssh "$PRIMARY" "launchctl print gui/\$(id -u)/$LABEL | egrep 'state|pid|last exit'"
# 1d. WhatsApp Desktop 开着吗, 库最近什么时候有动
ssh "$PRIMARY" 'pgrep -x WhatsApp; stat -f "%Sm  %N" "$HOME/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite-wal"'
# 1e. daemon 自己的 log 尾巴
ssh "$PRIMARY" "tail -20 $RL/wa_live.log"
# 1f. 停电后会不会自己开机 (回 autorestart 1 = 会)
ssh "$PRIMARY" 'pmset -g' | grep -i autorestart
# 1g. daemon 的主循环还在转吗 (回 ok 开头 = 在转; 回 stalled 开头 = HTTP 503, 进程在但主循环卡住了)
ssh "$PRIMARY" "curl -s -m 5 http://127.0.0.1:$PORT/health"
```

判读:

| 看到什么 | 意思 | 做什么 |
|---|---|---|
| 1a 有、1b 进得去、1c 有 pid、1d 库几分钟内有动、1g 回 ok | 全活, 只是板子或日报那条出口坏了 | 修出口: 在常驻机上看 `tailscale serve status`; 日报看 `wa_live.log` 里 digest 那几行, 再丢一个 `notify-test.json` 指令档验推播出口 |
| 1c 有 pid, 但 1g 回 stalled | 进程还在, 主循环卡住了, 多半有子进程在等一个没人按的权限弹窗 | 到常驻机屏幕前 (或用屏幕共享) 看有没有没按的弹窗, 按掉; 再照下面「1c 说没在跑」那一行的命令重启 |
| 1c 有 pid, 但 1d 库几小时没动 | WhatsApp Desktop 掉线或没开 | 到常驻机屏幕前 (或用屏幕共享) 开 WhatsApp Desktop 重新连; 这不是本页的案子 |
| 1c 说没在跑, 或 pid 一直换 (不断重生) | daemon 自己挂了, 机器没死 | `ssh "$PRIMARY" "/usr/bin/python3 \$HOME/.claude/skills/breakthrough-whatsapp-live/daemon/wa_setup.py restart --config \$HOME/$RL/config.json"`, 最后一行要是 `proof: one daemon, …`; 不是 0 就照它印的那句先处理 (例如板子的端口被别的程式占着, 它一个讯号都不送就退出)。再看 1e; 起不来就读 [[whatsapp-live-triage-playbook]] 第 8、9 条与 `references/troubleshooting.md`, 还是不用本页 |
| 1a 看不到常驻机或它显示离线, 或 1b 连不上, 而备援机自己的 Tailscale 是通的 (看得到别的节点) | **常驻机真躺了** (停电、死机、硬体) | **往下走第 2 步** |
| 1a 连备援机自己都不在 tailnet | 是备援机这边的 Tailscale 断了 | 先修备援机的 Tailscale, 再从 1a 重来 |

⚠️ 确认真死之前先问一句「多久能救回来」: 常驻机只是重开机要十分钟, 就等它, 别装备援机。第 2 步的代价 (见下面的 ⚠️) 不是十分钟的事。

## 第 2 步: 备援机装同一套 (先不启动)

前提, 缺一个就装不起来:

- **node 有 Full Disk Access**: plist 里写的那一支 node (`command -v node` 找) 要在备援机的系统设定里授权, 授给终端机没有用。缺了, 子进程 ffmpeg、whisperkit-cli、claude 全卡在授权弹窗: 零输出、零 CPU、越等越久 ([[whatsapp-live-triage-playbook]] 第 9 条)。
- **工具**: `/usr/bin/python3` · `ffmpeg` · `whisperkit-cli` · whisper 模型副本在 `~/.cache/whisper-models/` (config `brain.whisper_model`; ⛔ 模型不放 Documents、Desktop、Downloads)。
- **Claude Code 在画面 session 登入**。⛔ ssh 里跑 `claude -p` 会假报 `Not logged in`, 那是测法的问题, 不是没登入。
- **同一个 tailnet** (板子要用)。
- **kit 与两支 companion skill**: 装跟常驻机同一版的 kit (`npx skills add -g breakthrough-edu/breakthrough-whatsapp-live`)。常驻机躺了就查不到它跑哪一版, 所以平时每次装或更新 kit, 就在登记簿常驻机那一行记一笔 `shasum -a 256` 算出的 `daemon/wa_live.py` 指纹, 躺的那天才对得上。再照 `install/C1-reader.md` 与 `install/C4-person-notes.md` 把 `breakthrough-whatsapp-read` 与 `breakthrough-person-dossier` 连进 `~/.claude/skills/`。daemon 读对话要前者, 档案 run 与验档要后者; 少了后者, daemon 照样判断, 但人物档一份都放不进去。
- **你在 C5 选的接口要的登入**: 事实来源 (`facts.source`)、第二讯息来源 (`second_source.source`)、日历去处 (`calendar.destination`)、推播出口 (`notify.outlet`) 里, 凡是靠命令行工具或帐号登入的实作, 备援机上都要用同一个帐号、在画面 session 里登入一次 (每个实作要什么登入, 写在 `references/interfaces.md`)。推播出口是 `command` 的, 那支命令在备援机上也要在、也要跑得通。
- **vault 两台是同一份**: 你的同步工具在备援机上跑着, People 房 (`paths.people_dir`) 与这个 playbook 夹都已经同步到了; 否则备援机会对着旧档写。
- **备援机的 WhatsApp Desktop 要 link 同一个号。** ⚠️ **这一步等于多一台 linked device, 代价写清楚**: 一个号最多四台 linked device, 备援机会占掉一台; 备援机本机的 `ChatStorage.sqlite` 是从零同步的 (只拉最近的历史, 群的老讯息不一定全到), 所以阅读包比常驻机薄, 语音也要重转 (转写快取在常驻机的 `~/WhatsApp-Live/transcripts-cache/`); 而且备援机一 link, 手机上会多一台 device, 常驻机那台若还在 (它只是没电) 也仍占着一格。这是可回头的 (之后在手机的 Linked devices 里把备援机踢掉), 但不是零成本。
- ⭐ **link 完, 等历史同步停下来再往下走。** 同步期间 WhatsApp Desktop 会把旧讯息一批批写进库, 编号都是新的; daemon 若这时起来, 会把它们当新讯息。kit 的年龄闸 (`doorbell.max_message_age_hours`, 默认 48) 会把更早的记成 `skip:too_old` 不跑, 但最近这段时间的仍会被判一次。看 WAL 档的修改时间不再几秒一跳、对话清单看起来齐了, 再装。

装法 (每个子命令的参数以 `wa_setup.py <子命令> --help` 为准):

```bash
npx skills add -g breakthrough-edu/breakthrough-whatsapp-live
shasum -a 256 "$KIT/daemon/wa_live.py"          # 跟登记簿里常驻机那一行记的值比, 一样才是同一版码
/usr/bin/python3 "$KIT/daemon/wa_setup.py" init --config "$L/config.json"      # 建家目录与权限, 照这台的路径生成 claude-settings
/usr/bin/python3 "$KIT/daemon/wa_setup.py" doctor --config "$L/config.json"    # 前提体检, 只出计数, 不出讯息内容
ls -L "$HOME/.claude/skills/breakthrough-whatsapp-read/SKILL.md" "$HOME/.claude/skills/breakthrough-person-dossier/SKILL.md"
# ⛔ 先不要 install-launchd: live/ 夹还没放回备份
```

**晚间进料口那支现在就决定。** 常驻机若开着晚间推播 (`intake.enabled` 与 `intake.evening_push` 都是 true), 它是另一支 launchd (`$ILABEL`), 每天 `intake.evening_at` 推一则。决定接手期间备援机要不要也跑它: 它只推给你自己, 不跑的代价是接手期间每晚少一则整理提醒; 要跑, 第 3 步上线时照 `install/O7-intake.md` 装它那支, 第 4 步交回时一起停。选哪个, 写进 `$WL/INSTALL-PROGRESS.md` 或登记簿里备援机那一行。

## 第 3 步: 拉昨晚备份, 放回 live/

备份在 `$B` (每晚 `backup.at` 写一个 `<YYYY-MM-DD>` 夹, 默认 03:30, 留 `backup.keep` 份, 默认 7)。备援机上要看得到那个夹: 云端同步夹要客户端在跑, 外接碟要接上。

```bash
ls "$B"
LATEST="$B/$(ls "$B" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort | tail -1)"
ls "$LATEST"                                      # 先看里面有什么, 再搬
cp "$LATEST/state.sqlite" "$LATEST/config.json" "$L/"
for f in "$LATEST"/*; do          # 摘要表用 summaries.* 认: 同一行出现 cp 与 .md 档名, 第二大脑的档头闸会整条拒绝
  case "$(basename "$f")" in
    summaries.* | facts.json | voice-pairs.jsonl | second-source-whitelist.json | topic-map.json) cp "$f" "$L/" ;;
  esac
done
for d in prompts digest dossier-backups adapters; do
  [ -d "$LATEST/$d" ] && cp -R "$LATEST/$d" "$L/"
done
chmod 600 "$L/state.sqlite" "$L/config.json"
```

放回去之后四件事, 漏一件 daemon 会「活着但瞎」:

1. **推播出口的秘密要重建。** 备份刻意不带 `telegram.json`, 而且 `config.json` 副本里任何名字像秘密的值 (key 名含 token、secret、password、key) 都被抹成 `<redacted>`。推播出口是 `telegram` 的: 在 `$L/telegram.json` 写 `{"bot_token": "<你那只 bot 的 token>", "chat_id": "<你的 chat id>"}` 再 `chmod 600` (token 在 BotFather 里拿, 同一只 bot 不用新开; 你若还有别的 bot, 别抄错)。写完 `grep -c redacted "$L/config.json"` 应该回 0; 不是 0, 就逐个把被抹掉的值补回来。
2. **路径换成这台的。** `config.json` 是常驻机那份, 里面的绝对路径 (`bin` 与 `paths` 两段) 可能跟这台不同 (使用者名、node 装在哪)。跑 `check-config` 与 `doctor`, 报哪个改哪个, 再跑 `sync-reader-config` 重生读取器工作夹的 config:
   ```bash
   /usr/bin/python3 "$KIT/daemon/wa_setup.py" check-config --config "$L/config.json"
   /usr/bin/python3 "$KIT/daemon/wa_setup.py" sync-reader-config --config "$L/config.json"
   ```
3. **水位与进讯纪录要重定。⚠️ 这一步最容易出事。** `state.sqlite` 里的水位 (`meta.last_pk`) 与 `inbound_log` 每一行的编号, 都是常驻机那份 `ChatStorage.sqlite` 的 `Z_PK`, 跟备援机本机库的编号无关:
   - 水位比备援机的库大: 永远等不到新讯息。kit 发现水位高过库里最大的编号会自己重定并推一则错误, 但别靠它。
   - 水位比备援机的库小: 一口气把中间的讯息全当新的跑。
   - **只删水位也不够**: 门铃每次扫库都往回看 `doorbell.lookback` 个编号 (默认 300), 用 `inbound_log` 判断哪些处理过。那张表装的是常驻机的编号, 所以往回看的那几百封会被当成没处理过, 重跑一轮 (年龄闸挡得住 `doorbell.max_message_age_hours` 以前的, 挡不住最近的); 更糟的是, 备援机的新讯息编号一旦撞上表里常驻机的旧编号, 会被当成处理过而**静默跳过**。
   - 所以三样一起清, 让 daemon 当成全新安装: kit 在 `inbound_log` 是空的时候第一次扫库, 会把往回看的那几百封记成 `skip:before_install` 不跑, 水位定在备援机库里当下最大的编号 (从现在开始, 不补旧讯息)。`pending` 里是备份那一刻还没跑的 burst, 常驻机早就跑过了, 一起清。
   ```bash
   sqlite3 "$L/state.sqlite" "DELETE FROM meta WHERE key='last_pk'; DELETE FROM inbound_log; DELETE FROM pending;"
   sqlite3 -readonly "$L/state.sqlite" "SELECT (SELECT COUNT(*) FROM meta WHERE key='last_pk'), (SELECT COUNT(*) FROM inbound_log), (SELECT COUNT(*) FROM pending);"   # 要印 0|0|0
   ```
   代价 (清掉的纪录在备份夹里还有, 没有丢): 进讯纪录从接手那一刻重新算起, 所以接手后第一份日报的进讯数只算接手之后的; 当晚档案夜批的「当天来往封数」也只算接手之后的, 那一晚重写的档会比平常少; 语气回路判「草稿有没有被你抢先回了」, 看不到接手之前的出站纪录。判断、摘要、时间线、草稿住在 `conversations` 表, 不受影响。
4. **`runs/` `variants/` `commands/` 与 log 不在备份里**, 那是刻意的 (跑过的 run 夹不值得保); daemon 起来会自己建空夹。备份里的 `seeds.json` 与 `acceptance.json` 不放回: 这台的 `seeds.json` 是第 2 步 `init` 照这台的路径记的, 盖掉会让 `seed-status` 把这台重新生成的 `claude-settings.json` 看成被改过。`adapters/` 是你自己写的接口实作 (有才有), 要放回来, 不然 `facts.source` 这类设定找不到它。

然后上线:

```bash
/usr/bin/python3 "$KIT/daemon/wa_setup.py" install-launchd --config "$L/config.json"
tail -f "$L/wa_live.log"                                     # 要看到首跑定水位那一行; Ctrl+C 离开
/usr/bin/python3 "$KIT/daemon/wa_setup.py" tailscale-hint --config "$L/config.json"   # 印出这台该跑的 serve 指令 (它先读 serve status, 端口被别的服务占着就换一个), 你自己跑它印的那行; 备援机的主机名不同, 板子网址也不同, 用 tailscale serve status 抄下来
echo '{}' > "$L/commands/notify-test.json"                   # 手机该收到一句, 证明推播出口重建对了
echo '{}' > "$L/commands/probe-claude.json"                  # log 里 probe 几秒内结束, 证明 launchd 底下的 claude 通
sqlite3 -readonly "$L/state.sqlite" "SELECT decision, COUNT(*) FROM inbound_log GROUP BY 1;"   # 首跑之后只该有 skip:before_install, 加上之后真的新进来的
```

晚间进料口那支若决定要跑, 这时照 `install/O7-intake.md` 装上。

## 第 4 步: ⛔ 绝不两台同跑

两台同时跑同一个号会: 双开档 (两边都往 People 房写同一个人)、双推播、水位互踩 (两边的 `state.sqlite` 各记各的, 合不回来)。所以 **常驻机一活, 第一个动作是把备援机那份停掉**, 不是先看常驻机好不好:

```bash
# 在备援机上, 常驻机一出现在 tailnet 就先做
/usr/bin/python3 "$KIT/daemon/wa_setup.py" uninstall-launchd --config "$L/config.json"
/usr/bin/python3 "$KIT/daemon/wa_setup.py" tailscale-hint --config "$L/config.json"   # 跑它 Remove 那一行 (上线时用的是哪个端口, 就关哪个); 板子已经在 tailnet 上时它只印端口
launchctl print "gui/$(id -u)/$LABEL"              # 要回找不到这个 service
ls "$HOME/Library/LaunchAgents/$LABEL.plist"       # 要回 No such file
```

⭐ **停 = bootout 加上 plist 离开 `~/Library/LaunchAgents/`。** 只 bootout 的话, 备援机下一次登入或重开机, launchd 会把 LaunchAgents 里的 plist 全部载回, 你就在不知情下两台同跑。`uninstall-launchd` 先停 python (TERM, 过了 ExitTimeOut 还活着就 KILL), 再 bootout、disable、删掉 LaunchAgents 里那份 plist; render 出来的那份留在 `$WL/launchd/`, 之后 `install-launchd` 就从那里装回。上面 `ls` 若还看得到 plist, 就重跑 `uninstall-launchd` 看它印的 `ERROR` 行, ⛔ 不改用手动 `launchctl bootout`: 它只停 node 家长, 卡住的 python 会变成孤儿继续占着端口。晚间进料口那支 (`$ILABEL`) 若也装了, 用同一行加 `--intake` 停。

⚠️ 常驻机停电前 daemon 是常驻的 (KeepAlive), 1f 那格若是 `autorestart 1`, 它会**自己开机、自己起来**。也就是说常驻机一有电, daemon 可能已经在跑了, 备援机那份多活一分钟就多一分钟双跑。所以备援机上要盯的是 `tailscale status` 里常驻机一出现就停, 而不是等常驻机上什么都对了才停。

**合回去, 二选一, 这里定一条: 备援机的 live/ 状态整份盖回常驻机, 常驻机自己那份放弃。** 理由: 备援机那份是从备份长出来的, 备份就是常驻机凌晨那一刻的状态, 所以备援机的 `state.sqlite` / `summaries.md` / `digest/` / `dossier-backups/` 是常驻机的超集 (多了接手期间的判断)。反过来 (常驻机的赢) 会把接手期间的所有判断与开档纪录丢掉。代价: 常驻机从凌晨备份到躺下之间那几个小时的判断, 两条路都救不回, 认了。

```bash
# 在备援机上, 确认备援机的 daemon 已经停了之后
ssh "$PRIMARY" "/usr/bin/python3 \$HOME/.claude/skills/breakthrough-whatsapp-live/daemon/wa_setup.py uninstall-launchd --config \$HOME/$RL/config.json"   # 常驻机那份也先停 (先停 python 再 bootout), 盖档时不准有人在写
ssh "$PRIMARY" "cp -R $RL $RL.bak-before-merge-\$(date +%Y%m%d)"
for f in state.sqlite summaries.md facts.json voice-pairs.jsonl second-source-whitelist.json; do
  [ -f "$L/$f" ] && scp "$L/$f" "$PRIMARY:$RL/"
done
scp -r "$L/digest" "$L/dossier-backups" "$PRIMARY:$RL/"
# ⛔ config.json、telegram.json、claude-settings.json、prompts/、topic-map.json、seeds.json、adapters/ 不盖: 常驻机自己那几份本来就对
ssh "$PRIMARY" "sqlite3 $RL/state.sqlite \"DELETE FROM meta WHERE key='last_pk'; DELETE FROM inbound_log; DELETE FROM pending;\""   # 编号是备援机的, 到常驻机一样要重定
ssh "$PRIMARY" "/usr/bin/python3 \$HOME/.claude/skills/breakthrough-whatsapp-live/daemon/wa_setup.py install-launchd --config \$HOME/$RL/config.json"   # 从常驻机自己 render 好的那份装回、enable、起跑
ssh "$PRIMARY" "tail -5 $RL/wa_live.log"
```

接手期间在备援机上改过的设定 (例如白名单、词表、`live/prompts/` 里的提示) 不会跟着盖回去, 要手动带回常驻机那份, 再用第 1 步判读表「1c 说没在跑」那一行的 `wa_setup.py restart` 命令重启一次。

人物档走你 vault 的同步, 两台本来就是同一份, 不用搬; 备援机接手期间开的档已经在 vault 里。

最后, 收尾两件: 手机 WhatsApp 的 Linked devices 里决定要不要把备援机踢掉 (留着 = 下次再躺不用重新 link, 代价是占一格 device, 而且备援机本机一直在同步一份聊天库); 在你的常驻物登记簿里备援机那一段登记一行「装过又停了, plist 搬到哪、live/ 留着或删了、晚间那支装了没」, 别让下一次的人以为备援机上什么都没有。

## 这页没写的

- 备份本身怎么跑、备什么、不备什么: `references/interfaces.md` 的「5 · Nightly backup destination」与 `install/O5-nightly-backup.md`。
- `claude-settings.json` 不在备份清单里: 第 2 步的 `init` 照备援机的路径重新生成 (内容关掉全部 hook, 并挡住读秘密档)。
- 常驻机本身怎么防躺: 系统设定里的「停电后自动开机」与 WhatsApp Desktop 加进登录项, 两个都是系统层的开关, 你自己按 (`install/O10-standby-machine.md`)。
