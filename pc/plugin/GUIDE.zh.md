[English](GUIDE.en.md) | 中文

# 本机代理指南

运行时从工具目录读取本文件。MCP 入口 `python -m plugin.mcp_server --agent <已登记ID>`，CLI 入口 `python -m plugin <工具> --agent <ID> --args-file <UTF-8 JSON>`；两者共用校验、同意闸、归属与审计。中文参数用 args-file，避免 Windows PowerShell 5.1 的 stdin 编码转换。操作规矩集中 [RULES](../../RULES.zh.md)，跨应用方法见 [操作指南](../../docs/operating-guide.zh.md)。

## 工具与档位

| 档位 | 工具 | 参数摘要 |
| --- | --- | --- |
| read | observe | app；可选 mode=skeleton/digest/full、surface |
| read | resolve、verify | app、query；verify 可选 should=present/absent、surface |
| read | uia_locate、uia_read_text、uia_focused | app；可选 query |
| read | shot、ping | shot 的 mode=window/screen，window 需 app；ping 空对象 |
| input | act | app、query；可选 action/key/nth/surface/expect |
| input | uia_focus、uia_invoke | app；可选 query |
| input | seq | steps；timeout 默认 150 秒 |
| ask | ask_user | text；mode=input/choice/info；choice 带 1–4 个 options；可选 expires_in |
| ask | notify_user | text；可选 expires_in，静音且不等回答 |
| read | exp_lookup | app；可选 goal/ui/tags，最多三条 |
| local | exp_record、exp_feedback | 起草/确认一条经验；反馈 id、worked |

语义 query 支持 name/exact/role/path_contains/path_excludes；UIA query 支持 name/exact/controlType/classContains。expect 支持 query/should/text。seq 接收语义、UIA、sleep、act_if/unless 及 alternatives；每个引用应用都需要同意。步骤级 job/token/deadline 和原始 shell/ad/script 不通过插件校验。

`--agent` 对应工具目录 identities 下的登记文件。未登记只列 read；登记用于本机归属，签名信箱身份由另一入口处理。input 首次按代理×应用询问“允许这次 / 30 分钟内免问 / 拒绝”，只有前两项授权；关闭、到期、桌宠通用勾不等于允许。policy 可把工具禁用或收紧为 input，不能把输入降为只读。默认 consent_seconds=1800、muse_priority_seconds=15。

最近窗口内有非插件工作时，input 返回 busy 和 retry_after_s；插件没有排队服务，最终输入仍由执行器锁与用户让行协调。jobs 记为 `plugin:<agent>:<tool>`。档位与同意代码在 gate.py/tools.py，相关规矩 O8。

## 结果纪律

每个工具返回 `{status,verb,...}`，ok/resolved 归 ok，fail 保留错误码，unknown 保留不确定性与 effect/verification。超时、断线、送达不明确后的处理见 RULES O3：先 observe/verify，再决定下一步。

CLI 退出：0 成功，1 失败或卡到期/被替代，4 unknown，5 busy，10 拒绝或未登记。MCP 的 content[0].text 放 JSON，非成功设置 isError。截图数据只交调用者，不写 audit/trace。MCP initialize 协商代码在 mcp_server.py，当前支持版本集合以该文件为准。

audit.jsonl 记录时间、代理、工具、应用、选择器、闸门、状态、耗时；trace/<agent>.jsonl 保留最近 50 调用的结构查询与状态。二者不记录输入正文、期望正文、回读、剪贴板、卡文、选项、答案或截图；选择器名称会保留。日志与本机状态的保存规则见 RULES E3。

## 提问礼仪

按 RULES O4/O8 先查可查的信息，再用一张卡问当前缺项。ask_user 默认 600 秒，可选 10–3600 秒；返回 kind/result/req_id。卡位忙返回 busy；Muse 可替代本机卡，原调用报告 preempted。notify_user 为不响铃、立即返回的信息卡，有有界子进程回收槽位，不用于批准动作。

浏览器控制按 RULES O12：先查 Muse 官方浏览器扩展，未装时用 `ask_user` choice 卡问能否添加，未答、关闭、到期不算同意，`notify_user` 不能代替询问；步骤见操作指南第 3 节 Chrome。

## 联系 Muse

在工具目录执行 muse-call.ps1 或 muse-call.cmd；长文本写 UTF-8 文件：

```text
muse-call.cmd -As <agent_id> -Enroll -DisplayName <显示名> -Product <产品> -Model <模型>
muse-call.cmd -As <agent_id> -Type question -TextFile <正文文件>
muse-call.cmd -As <agent_id> -Type task -TextFile <正文文件> -NoWait
muse-call.cmd -Check <req_id>
muse-call.cmd -ListPendingReplies
muse-call.cmd -As <agent_id> -ListGrants
```

登记须等用户真实选择，收到身份文件后请求才签名。一次请求保留 req_id；心跳 WAITING_FOR_MUSE 表示仍在等，超时 MUSE_CALL_TIMEOUT 后用 Check 续取。`-NoWait` 只改变调用等待，不取消请求；读取中间回信后还需等终局。`-Thread` / `-ReplyTo` 归组，`-Grant` 出示凭据。发版后的核验、清单修正或确认回执，请带上 `-Thread <agent>/release` 或 `-ReplyTo <发布单req_id>`，避免另外拆出一条新对话。具体字段、发版参数、身份验证和错误码见 [协议](../../docs/protocol.zh.md)，通用等待约定见 RULES O9。

## 什么时候记经验

调用顺序与草稿确认步骤见 [操作指南第 5 节](../../docs/operating-guide.zh.md#5-什么时候记经验怎么记)。本机轨迹能起草结构方法，正文缺项由代理明确补充；local 档不调执行器、不弹输入同意卡，只允许已登记代理写本机库。方法不自动执行，分享另走逐条静音同意卡。机制见架构第 10 节，规矩 O1/E11。

## 安装

`plugin/install.ps1 -Agent <id> [-Client codex|kiro|claude]` 预览这个代理所在客户端的配置（Codex TOML、Kiro JSON 或 Claude Code 的 ~/.claude.json 与命令）及已有配置处理；一份客户端配置只放一个 muselink 项，即一个代理，ID 即客户端名时可省 -Client。`-Apply` 合并用户配置文件，保留其他项与已有 muselink 项（属于别的代理时注明），内容不变不写备份。Codex 配置里其他根下带反斜杠的表名（如 projects 路径）照原样保留。Codex、Kiro 有自己的目录而缺配置文件时新建，其余缺文件跳过；-Force 明确备份重写；-ConfigHome 供临时目录验证。它优先用信箱 venv，再查本机 Python；查不到报错。

Codex 项包含 tool_timeout_sec=3620，其他客户端调用预算也需覆盖请求的卡片寿命。客户端自己的工具批准与 MuseLink 同意卡各自生效，安装器保留客户端审批策略。安装总流程和已装跳过依据见安装第 6 节；开发测试按 RULES E1。
