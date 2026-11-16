[English](troubleshooting.en.md) | 中文

# 排错

每条按症状、原因、处理和相关规矩排列。代码位置在所链接的机制章节；实际目录和地址由目标机器查询。

## 组网与 SSH

| 症状 | 原因 | 处理 | 相关规矩 |
| --- | --- | --- | --- |
| Host key verification failed | 实际 HOME、SSH 配置或 known_hosts 与预期不一致，或指纹变化 | 用 ssh -v 查加载文件，可信渠道核对指纹；修正站点字段后生成配置 | O3、E4、E7 |
| banner 超时、PC offline | 本端代理路径、tailnet 或 PC 服务不在线 | 先查本端代理和 tailscale status，再以已授权第二落点验证；PC 检查 Tailscale 服务，已有 watchdog 回读运行结果 | O3、O9 |
| 服务中无代理环境、交互 shell 却通 | systemd/cron 环境没有交互代理变量 | 在目标 VM 确认可用 PROXY_FALLBACK，写入站点配置后生成；复查服务 Environment | E4、E7 |
| 长 SSH 结束，任务未返回 | 会话或本次调用预算结束，目标进程可能继续执行 | 查目标进程、任务日志与结果文件，改用脱离会话任务和短轮询，保留原请求 | O3、O7、O9 |

## 执行器与操作层

| 症状 | 原因 | 处理 | 相关规矩 |
| --- | --- | --- | --- |
| AUTH_FAILED | executor token 缺失、不匹配或客户端来源不对 | 检查目标工具目录令牌文件与 ACL，不把令牌复制到报告；核对桥与宿主属于同一安装 | O3、E3 |
| 不可达、退出 2 | 没有监听、任务未启动或不在交互会话 | 查任务动作、运行账户、监听及日志；按安装手册第 4 节重启验活 | O7、O10 |
| 新旧行为交替 | 多宿主或进程仍加载旧代码 | 用 restart-host 预览和受控重启，验零监听后单次启动，查版本指纹 | O10、E8 |
| snapshot、status 报 PERM_DENIED：`private file DACL grants an untrusted principal` | 状态目录的名单里多了 SYSTEM、Administrators、当前用户之外的主体。同机有 Codex 时，通常是提权沙箱初始化给用户目录下每个一级文件夹加的 `CodexSandboxUsers` 读权限（`%USERPROFILE%\.codex\.sandbox\sandbox.<日期>.log` 里有 `granting read ACE to ...`）。list-windows 这类不读状态的命令照常 | 用 `status` 的 `state_root` 定位目录。应急：`icacls <目录> /save <备份>` 后，只对目录本身 `icacls <目录> /remove:g <多出的组>`，不带 /T。根治：按安装第 4 节第 1 步把状态目录移到用户目录之外 | O10、E5 |
| snapshot、status 报 PERM_DENIED：`private file is owned by a foreign principal` | 状态文件的属主不是当前运行身份，常见于以提权身份创建或复制后改由普通权限运行 | 用 `install\fix-agent-desktop-owner.ps1` 先报告，再 `-Apply`；它只改属主、不改名单，执行前备份权限 | O10 |
| sem not_operable、NOT_OPERABLE | 找到的元素没有可操作 ref | 用 UIA 当前语义定位，检查焦点或 TextPattern；必要时新截图定位 | O2 |
| act AMBIGUOUS 或 UIA 多匹配 | 名称重叠、装饰元素、多个窗口 | 加 exact、role、surface、path 或明确序号，再看当前候选 | O2、O3 |
| 点了但没有预期效果 | 控件角色所需 Pattern 不同，窗口后台或元素已变 | 重新 observe/verify，菜单试 expand、开关试 toggle；根据当前证据决定动作 | O2、O3 |
| UWP 看不见内容、列表找不到屏外项 | 后台挂起撤树、frame 拥有根、列表虚拟化 | 前置并查实际窗口根，滚动到目标可见区域后重新定位 | O2 |
| busy、INPUT_LOCK_TIMEOUT、expired | Muse 优先、输入锁占用或请求已过截止时间 | 按 retry_after_s 等待，查状态后提交当前需要的新动作，不重放结果不明的输入 | O3、O8 |
| resolve、act、verify 在大窗口（如 Kiro）返回 TIMEOUT | agent-desktop 的 live find 要读完整个窗口才判定唯一，深层 Chromium 树在预算内读不完 | 先查该软件的工具选择经验（操作指南第 2 节）；find 预算默认 30 秒，`sem.sh --find-timeout-ms` 或插件 `find_timeout_ms` 可到 50 秒；或改用 `uia.sh locate`。不改用 `--first` 或截断深度，它们放弃唯一性证明。分段计时用 `tools/diagnose-sem.py`（开发树） | O1、O2 |

## 弹窗与等待

| 症状 | 原因 | 处理 | 相关规矩 |
| --- | --- | --- | --- |
| STAGE_FAIL 或乱码 | staging 字节或哈希不一致，脚本编码不合约定 | 用 popup-send 文件通道重备输入，检查 UTF-8 和哈希清单，先查当前单所有权 | O8、E10 |
| 卡片出现却不能直接打字 | 默认不激活，键盘保护尚在 | 点击卡片取得焦点；需要改变默认时改站点 popup.activate_on_show 后生成 | E7 |
| REQ_MISMATCH、旧卡答案混入 | 结果属于另一 ReqId | 查 pending/armed/done 的请求号，不认领错号；确认旧流程结清后再发 | O8、O9 |
| 答了但主等待没回来 | 连接结束、pending 已消费、owned 等待需续取 | 查本单 done、owner 和 inflight；程序所有者用 popup-wait 续等，普通卡查兜底心跳 | O3、O8、O9 |
| 探针总说弹窗仍活 | 判活条件误匹配探针自身，或命令行里同样含 popup 的 check-popup、wait-popup、popup-stage | 检查 Get-MLPopupProcess 目标条件与自身排除；用目标不存在的场景验证 | O3 |
| 卡位 busy、登记迟迟不弹 | 另一单占位，或所有者保留可恢复卡 | 查当前所有者，完成原单后续等；卡位忙不算拒绝和重试消耗 | O8 |
| 登记点白名单却未完成 | 旧解析把状态行并入答案，或等待中断 | 核对包内 ui.py 分界解析与登记日志，升级后恢复已有请求；不从信箱手改授权 | O8、E8 |
| 桌宠报完成后输入卡没关 | 双端规则保留可输入卡接收补充文字 | 按架构第 8 节核对本单结果与补充消息路径 | O3、O8 |

## 信箱与发版

| 症状 | 原因 | 处理 | 相关规矩 |
| --- | --- | --- | --- |
| 只见已收到，没有最终结果 | 中间回信或调用等待结束 | 保留 req_id，用 muse-call -Check；VM 查 bridge-reply-status 的终局位置 | O9 |
| 称已读却未见终局 | 用 ACK 的位置推断了整单状态 | 按协议第 2 节的逐封与终局语义查 outbox/ack/archive/done | O3、O9 |
| UNVERIFIED、quarantine | 签名、密钥或请求/序号不符 | 检查对应安装身份和更新记录，保留原件供核验，继续等待有效终局 | O3、O8 |
| STATE:REJECTED/PENDING_ENROLL | 路由拒收或尚待登记卡 | 用 Check 看状态，VM 按 verdict.reason 与身份审计判断；按既有登记流程续取 | O8、O9 |
| Muse inbox 空但 PC 请求未处理 | PC 断网、邮差未取或文件尚在其他阶段 | 查 PC inbox/claimed/pending 与 VM 台账、hook 心跳，不重发相同请求 | O9 |
| 回信文件存在但命令无输出 | PowerShell 输出被捕获为函数返回 | 核对 muse-call 使用 Console.WriteLine 的打印路径；按 Check 续取 | O3、O9 |
| 服务在环境替换后消失 | systemd 单元需由保存的模板恢复 | 查自愈日志和工作区服务母本，按安装第 5 节恢复单元，回读运行状态 | E8、O7 |
| 桥服务每次启动即退出，日志为 `config not found` | 单元缺 `MUSELINK_HOME` / `MUSELINK_CONFIG`，服务按 User= 的 passwd 家目录找配置 | `systemctl cat muse-bridge` 核对这两行；按安装第 5 节用 install.sh 渲染的单元替换，回读 status | E8、O7 |
| 发版哈希、大小或版本不符 | 申报、构建文件或传输产物不一致 | 向请求方给实测与声明值，查 PC 原包、远端拼装、公开清单；纠正后由授权流程继续 | O3、O8 |
| SHELL_DISABLED | 旧动作仍调用已默认关闭的 shell | 截图、文件事实改走现有白名单 script，具体动作查经验；必要的旧兼容由用户决定配置 | O2、E7 |

## 窗口、经验库与环境

| 症状 | 原因 | 处理 | 相关规矩 |
| --- | --- | --- | --- |
| 信箱窗口空白 | WebView2 缺失、JS MIME 或前端资源不匹配 | 核对 runtime、包清单和 shell.py MIME 表；只在临时目录排查前端 | E1、E8 |
| 索引与文件不符 | 缓存未同步、移动期间跳过文件 | mlindex sync，必要时 rebuild，再 verify --sample N；原始信件不改 | O3 |
| 经验 lookup 无命中 | app 不同、条目 draft、路径/source 或 schema 不符 | 查规范化进程名、drafts、Store.skipped；按 schema 修条目再确认 | O1、E11 |
| 经验没有出分享卡 | 累计未满 5 条 valid 条目、单条卡过长、脱敏命中、超时冷却或已拒绝（never） | 查 share-blocked/share 状态；条件见架构第 10 节，不通过手改状态绕过同意 | O1、O8 |
| 零字节、哈希不符，像传输丢数据 | 目标暂存空间满或本端写失败 | 两端查可用空间，VM 查 df -h /tmp 与工作目录，确认文件写成再传 | O3、E5 |
| PS1 中文变问号或乱码 | PS5.1 无 BOM 脚本按 ANSI 读，stdin 可能经默认编码转换 | 以 UTF-8 读取核对原件，非 ASCII PS1 加 BOM，参数走 UTF-8 文件，CMD 保持 ASCII/CRLF | E10 |
| 任务注册后程序没起 | 动作参数、会话、权限或 launcher 位置不符 | 比较任务定义和命令行，用脚本绝对路径，启动后回读进程和监听 | O7、O10 |
| git 认证在 SSH 会话失败 | 凭据绑定桌面登录会话 | 在已授权桌面会话执行认证操作，通过结果文件回读；不在报告输出凭据 | O3、O7 |

编号对应 [RULES](../RULES.zh.md)。安装差异和恢复方法见安装第 8 节。
