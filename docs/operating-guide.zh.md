[English](operating-guide.en.md) | 中文

# 电脑操作指南

## 1 先查经验库

以目标应用的实际进程名查询，例如小写 `notepad.exe`。本机代理用插件 `exp_lookup(app,goal,ui,tags)`；Muse 用 `vm/exp.sh lookup <app> [goal]`。命中包含方法、验证步骤、状态、来源与距上次核验天数，按当下界面选择。带 `tool-choice` 标签的条目记的是这个软件用哪种工具顺手（agent-desktop、原生 UIA、winappCli 等，含实测耗时与所需参数）：有就按它选路，不先用默认通道失败一次再换。调用参数见 [插件 GUIDE](../pc/plugin/GUIDE.zh.md)，状态机制见架构第 10 节；相关规矩 [O1](../RULES.zh.md#一操作规矩操作电脑的代理)。

## 2 通道选择顺序

浏览器任务先按 O12 与下文“Chrome”流程选择官方扩展通道；用户拒绝或无法添加时才走浏览器备选。桌面应用从语义观察、UIA 补位到 seq 组织动作，最后用当前截图定位：

```text
sem.sh observe <app> skeleton
sem.sh observe <app> digest
sem.sh resolve <app> <名称> --exact
uia.sh locate <app> <名称>
seq.sh <作业.json>
shot.sh window <app>
```

sem 的 resolved 表示可操作目标已定位；not_operable 表示有匹配但没有 ref，可转 UIA。UIA 以 name/controlType/class 查询，再按当前元素描述判断 focus、invoke 或只读文本。seq 将已明确的步骤合并，含条件守卫与后置验证；它是执行组织方式，不能替代目标识别。只有截图能表达目标时，从刚保存的原图取得物理像素与本机坐标映射，动作后再取证。机制见架构第 3 节，相关规矩 O2/O3。

## 3 标准动作

### 截图

Muse：`shot.sh window <应用名>` 或 `shot.sh screen`。读取末行本地图片路径及元数据，检查实际尺寸、格式、保存字节数；Windows 原始 PNG 留 shots 目录。窗口最小化时入口会还原截图并放回。图像传输压缩门见协议第 8 节。

### 取文本

先用当前应用可用的数据接口、DOM 或 UIA TextPattern。本机插件 `uia_read_text` 的返回即读取证据；网页在当前对话节点内定位内容，长生成结合生成状态、完成标记和新鲜截图。键盘复制时按第 4 节剪贴板安全门保存、聚焦、复制、读取和恢复。具体应用的选择器与方法查经验库，避免在指南另存同一份配方。

### Chrome

先核对目标浏览器、实际 Profile 与 Muse 官方浏览器控制扩展（Chrome 扩展）的安装状态。已装时核验官方连接并复用，不重复安装或询问添加。未装时先明确问：“可以为你的浏览器添加 Muse 官方浏览器控制扩展（Chrome 扩展）吗？”提供“同意添加 / 不添加，使用备选 / 无法添加，使用备选”的明确选择；一张卡等待真实回答，未答、关闭或到期不自动选择。

同意后从 Muse 提供的官方入口引导添加，按官方通道要求让用户确认目标标签；以可读取目标页面验证连接，然后核对账号、模型档位、输入框与对话区，再执行任务。官方扩展是浏览器控制首选。连接失败先查现状、如实报告，不将其解释为用户拒绝，也不自行切换通道。

只有用户拒绝或无法添加（例如浏览器不支持、组织策略阻止）时，说明原因，才使用包侧备用浏览器栈 agent-browser / Playwright 等已有接口；需要 CDP 时现查连接与权限，按工具要求批准目标标签。复用已验证连接，不另开浏览器或调试端口来跳过询问。安装检查与配置边界见安装第 0 节。

要新开指定 Profile 窗口时，把启动参数作为一个字符串传给 Start-Process，完整引用含空格的 profile 名：

```powershell
$chromeArgs = '--profile-directory="' + $profile + '" --new-window "' + $url + '"'
Start-Process -FilePath $chromeExe -ArgumentList $chromeArgs -WindowStyle Hidden
```

这里的三个变量均来自目标机器和本次任务；后台启动形态按 RULES O7。此启动示例不改变上述控制通道优先级。备选需要 CDP 时，DevToolsActivePort 文件可能残留，连接前结合当前进程与廉价探活确认；工具依赖按目标环境实际安装情况调用。

### 桌面应用

用 `executor.sh ad list-windows` 查实际窗口/app，再 observe。菜单 surface 或浮层出现后重新 resolve；有名同名装饰图时按 operable 候选判断。动作例：`sem.sh act <app> <名称> expand --exact`，再 `sem.sh verify <app> <菜单项> present`。UIA focus 返回 focusVerified 后，可接已验证的键盘通道；若控件只支持 SelectionItem，先聚焦再使用应用自己的激活键。

### 文件

优先按任务给定范围用文件 API 或本机索引查路径；未找到时检查查询范围和权限，再换第二个落点。文件事实用 `file-facts.ps1` 的参数文件接口，经白名单 script 调用；复杂路径和中文正文使用 UTF-8 JSON 文件。传输后在目标机计算大小与 SHA-256，进度通过有截止时间的短请求取回。工具不会替用户推断不存在的目录。

### 等用户与本机任务

交互请求用下面的标准弹窗入口；等待本机 pid/file/cmd/web 用 `start-wait.sh --kind ...`（参数以脚本头部为准）。发出后保留 ReqId，主路断开时先查等待状态和目标进程；续取 owned 卡用 popup-wait.sh。结果和退出码语义见协议第 6 节，相关规矩 O3/O8/O9。

### 告诉桌宠在做什么

在 PC 上干活时，每进入一个新阶段用 `phase.sh "<短语>"` 告诉桌宠，例如“正在整理周报”。宿主追加一条 phase 事件，桌宠在工作状态的气泡里显示最新一句，截到 60 字。短语会显示在屏幕上，不写密钥、账号或用户原文；不需要每步都发。

### 弹窗联系用户

准备 UTF-8 正文与可选的逐行选项文件：

```text
popup-send.sh --mode input --text-file <正文文件> --summary <一句话>
popup-send.sh --mode choice --text-file <正文文件> --options-file <选项文件>
popup-send.sh --mode info --text-file <正文文件> --no-wait
```

短问句可用 `popup-ask.sh <正文> [选项...]`。脚本完成传文件、stage、触发、等待；用户在弹窗或桌宠回答后收到本单结果。本机代理则使用插件 ask_user/notify_user。显示、到期与双端行为分别见架构第 4、8 节。

## 4 跨应用的通用方法

### 悬停才出现的控件

先找稳定的父区域或文本锚点，取得当前 bounds；准备一次截图与 fresh resolve。指针移到父区域后，在悬停仍生效的时间内重新扫描树和 menu surface；目标出现就按语义操作。新树仍无目标而新截图可见时，依本次截图完成几何定位。动作后检查子菜单、chip 或页面状态；窗口形态改变后重新定位。

### 回车发送

先识别应用的发送键与当前编辑器状态，准备全文、守卫和验证方法；采用已验证输入通道，结束时连续送出终止键。发后在用户消息节点或任务状态中查原文；结果未知时回读当前状态，区分完整未发、部分输入和已送达，再决定下一步。该规矩的依据与重试边界见 O3/O6。

### 剪贴板安全门

读取格式与可恢复快照、记录序列号；聚焦目标并核对前台；写入本次载荷，在粘贴前一刻回读来源；完成动作后检查目标正文。恢复时比较当前序列与本次写入的内容，确认没有覆盖用户后来复制的新数据，再在同一桌面会话恢复原内容。SSH 会话的 Set-Clipboard 与桌面应用可能属于不同会话。完整恢复不了的复杂格式按 O5 交给用户决定。

### 锚点定位

用用户刚发问题、唯一标题、区域名字或层级关系作为锚点；锚点自身也回读确认，避免命中编辑器残留。对话生成检查消息节点和生成中指示物；记录正文长度、节点是否在场、完成状态与采样时刻。坐标锚点从本次原图取得，DPI、滚动、窗口尺寸变化后重算，不迁移某台机器的偏移常量。

### 类比起步

先查本应用现有条目与同类应用已验证的界面结构，形成一个入口假设，再用一次当前树或截图确认。例如 Electron 编辑器常见附件入口、提及菜单、任务侧栏，可帮助选首个观察区域；应用自己的返回结果决定下一步，类比不充当执行证据。相关规矩 O2/O4。

## 5 什么时候记经验、怎么记

成功解决新情况后，为当前 app 记录一条；跨应用的方法留在第 4 节，规矩留 RULES。已有条目能覆盖时反馈并修订原方法，避免同义条目。

本机代理：`exp_record(app,goal,from_trace=true)` 从结构轨迹起草；检查草稿 method/verify，补足轨迹未保存的必要输入，调用 `exp_record(confirm=<id>,edits={...})`。Muse 没有本机插件轨迹，用 `exp.sh record <app> <goal> <method-file> <verify>` 明确提供方法，检查返回的草稿后用 `exp.sh confirm <id>` 转为有效；草稿不进查询，也不接受反馈。使用后用 `exp.sh feedback <id> worked|failed`。

工具选择单记一条：goal 写“定位或点击控件时选哪种工具”，tags 含 `tool-choice`，method 按先后列各工具的查法、实测耗时和必要参数（如 find 预算），verify 写唯一候选与动作后回读；同一软件只留一条，新测量修订原条。示例：`library/entries/kiro.exe/`。每条含应用、界面、目标、步骤、验证与日期；status 必须反映证据。条目 schema 与存储规则见架构第 10 节；CLI 使用 `python -m explib <命令> --args-file <UTF-8 JSON>`，drafts/confirm 可逐条审阅。公共条目作为方法参考，执行工具仍走当前授权和目标核验。分享由独立同意流程办理。
