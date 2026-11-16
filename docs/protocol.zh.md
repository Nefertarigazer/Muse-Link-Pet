[English](protocol.en.md) | 中文

# 协议

## 1 信箱文件布局

文件是事实源，写入采用临时文件加原子替换。PC 根目录由站点配置确定；目录用途如下。

| PC 子目录 | 内容与写入者 |
| --- | --- |
| `inbox/`、`sent/` | 客户端投递的新请求及自留副本 |
| `claimed/` | 邮差已取走的请求 |
| `pending/`、`rejected/` | 等登记确认、被拒收的请求，由邮差移动 |
| `outbox/` | Muse 已投递、客户端未读的回信 |
| `archive/`、`ack/` | 客户端读过的回信和逐封 `<req_id>.<seq>.ack` |
| `done/` | 已读终局对应的请求 |
| `directory/` | 无密钥的代理与凭据镜像，只供查询 |
| `quarantine/` | 客户端验不过的回信 |
| `index/` | 可重建的 SQLite 缓存及单写者锁 |

VM 根为 `$ML_BRIDGE_HOME`：`inbox/working/processed/rejected` 是处理阶段，`replies/` 保留回信台账，`alerts/` 是告警；`envelopes/<id>.json` 保存原始信封，`verdicts/<id>.json` 保存裁决，`pending/<id>.json` 保存待确认登记。原信封与裁决不混进工作 inbox。实现：`pc/muse-call.ps1`、`vm/hooks/bridge-postman.sh`、`vm/muselink_id/inbound.py`、`vm/bridge/muse-bridge-reply.sh`。

## 2 请求与回信

无签名请求使用 `req_id`、`created_at`、`from`、`type`、`text`、对象 `payload`，以及 `reply_protocol`。`text` 是请求数据；结构参数放 payload。身份信封的内层字段见第 3 节。

| 类型 | 含义 |
| --- | --- |
| `ping` | 通道往返确认 |
| `message` | 传递消息 |
| `question` | 提问 |
| `task` | 请求办理任务，具体动作仍按授权判断 |
| `publish-release` | 由常驻服务交给发版流水线 |

发版 payload：`version`、`package_path`、`sha256`、`package_size`、`notes`；可选 `setup_path`、`setup_sha256`、`setup_size`、`test`。事实由执行点在目标机器测量。`pc/muse-call.ps1` 提供对应参数；流水线在 `vm/bridge/pipelines/publish-release.py`。

停用名 `publish-voicebridge` 映射为 `publish-release`。PC 入口先归一再签名；VM 的 `RETIRED_TYPES` / `current_type()`、策略入口和排队请求读取处兼容旧输入。原始签名文本保持原字节，业务处理使用归一类型。新请求只发当前名称；删除兼容的条件见 [RULES E9](../RULES.zh.md#二工程规矩改这个项目的人和代理)。

回信字段：`req_id`、递增 `seq`、布尔 `final`、`status`、`replied_at`、`from:"muse"`、`reply_protocol`、`text`。`seq` 从 1 起，每封在 VM 台账独立保存。显式 final 优先；没有 final 时 `info` 为中间回信，`ok/error/needs-user` 为终局。客户端输出中间回信后继续等待终局。

v1 没有版本信封，回信名 `<req_id>.reply.json`；请求声明 reply_protocol ≥ 2 时，PC 使用 `<req_id>.<seq:03>.reply.json`。v2 引入编号与终局语义；v3 保留编号并添加身份签名。旧版读入仍兼容，登记代理不能退回无签名请求。

`muse-call` 默认等待 1800 秒，`-NoWait` 只投递，`-Check <id>` 续取。`bridge-reply-status.sh <id>` 按台账与 PC 位置报告 `FINAL_READ`、`FINAL_DELIVERED_UNREAD`、`INTERIM_ONLY`、`FINAL_MISSING_ON_PC`、`NOT_SENT` 或 `PC_PROBE_FAILED`。等待纪律见 [RULES O9](../RULES.zh.md#一操作规矩操作电脑的代理)。

## 3 身份 v3

请求外层恰有五个键：

```json
{"v":3,"req_id":"agent_20261113_120000_ab12cd","agent_id":"agent","signed":"<内层 JSON 文本>","auth":{"alg":"HMAC-SHA256","key_id":"agent#1","sig":"<base64>"}}
```

内层必填 `req_id/agent_id/ts/type/text/payload/reply_protocol`；reply_protocol 为 3，ts 为整数 epoch 秒。可选 `created_at/grant_id/thread_id/reply_to/client`；client 只含字符串 `display_name/product/model/host/session_id`，各不超过 200 字。自报信息用于显示。未知字段、重复 JSON 键和非有限数拒收；读取 UTF-8，容忍 BOM。

签名为 `base64(HMAC-SHA256(key, UTF-8(domain + signed)))`，标准 base64 带填充。密钥随机 32 字节，签名 32 字节。请求域 `muselink-req-v3\n`，回信域 `muselink-reply-v3\n`，记录封印域 `muselink-seal-v1\n`。验签使用 signed 原文，不重序列化，因而两侧无需约定 JSON 空格和键顺序。向量：`spec/test-vectors/identity.json`。

校验顺序由 `envelope.py` 与 `inbound.py` 实现：

| 顺序 | 检查 | 错误码 |
| --- | --- | --- |
| 1 | 文件体积、UTF-8 JSON 对象 | `oversize/unparsable` |
| 2 | 外层键和类型、文件名等于 req_id | `bad_outer/id_mismatch` |
| 3 | legacy、登记、签名分支 | `legacy_disabled/downgrade/unsigned` |
| 4 | 算法、key_id 前缀、严格 base64 和长度 | `bad_alg/bad_key_id/bad_encoding` |
| 5 | 登记状态、封印、当前密钥 | `unknown_agent/agent_revoked/registry_tampered/unknown_key/retired_key` |
| 6 | 常数时间比较 MAC | `bad_signature` |
| 7 | 解析内层、字段类型、内外 ID 一致 | `bad_inner/id_mismatch` |
| 8 | 新鲜度 | `stale/future` |
| 9 | seen 表按 ID 与摘要去重 | 同摘要 `duplicate`，不同摘要 `conflict` |

对已登记主体，疑似冒名的错误（`bad_alg`、`bad_key_id`、`bad_encoding`、`unknown_key`、`retired_key`、`bad_signature`、`registry_tampered`）写告警、不回信；已验证请求的内层、新鲜度或冲突错误回签名 error。duplicate 不再次投递。登记限流错误为 `enroll_rate_limited`，内部失败为 `internal`。完整错误集合在 `vm/muselink_id/__init__.py`。

登记是唯一 auth=null 的 v3 请求，内层 type 为 enroll，经用户卡确认后才发密钥。未获批准时代理没有钥匙，VM 只能发未签名回信；`muse-call -Enroll` 把本单本序号、非信封格式、终局 error 的未签名回信视为“未获批准”结束等待，该回信仍留在 quarantine，不带来任何权限。已撤销的代理再登记时出换钥卡，卡上写明已撤销；用户同意后换新钥并恢复为 active，原有凭据保持撤销。普通无版本请求按 legacy_unsigned 的 transition/reject 处理，from 若已登记则拒绝降级。

v3 回信外层为 `v/req_id/seq/agent_id/signed/auth`；内层为 `req_id/seq/agent_id/final/status/replied_at/from/reply_protocol/text`。客户端检查算法、key_id、MAC、键集合与预期 ID/序号；失败显示 UNVERIFIED、移入 quarantine，不算终局。实现：`pc/muselink-identity.ps1`。

身份存储在 `$MUSELINK_ID_HOME`（默认 `~/.config/muselink/identity/`）：`agents.json`、`grants.json` 含封印记录；`seal.key`；`state.sqlite` 保存 seen、用量、预留、吊销；`audit.jsonl` 保存结构审计。POSIX 目录 700、文件 600，账户或权限不合要求拒绝运行。PC 身份文件 `<工具目录>/identities/<agent_id>.json` 为 `{v:1,agent_id,key_id,key,enrolled_at}`，经文件传输并限制 ACL；密钥不进信箱或命令行。镜像没有密钥且不用于授权判断。

## 4 策略与授权

策略由站点配置生成，`policy.py` 校验全部键；未知键或非法取值使加载失败。类型先归一再归类。类为 conversation、task、privileged、red_line；代理档为 whitelist、ask-first、legacy。

| 类 | whitelist | ask-first | legacy |
| --- | --- | --- | --- |
| conversation | allow | allow | allow |
| task | allow | ask | allow |
| privileged | grant_or_ask | grant_or_ask | ask |
| red_line | ask | ask | ask |

表为模板策略；实际安装以已生成策略为准。红线包括 payment、delete-data、credential-change、third-party-message、network-change；加载器只接受 ask/reject，未知类型也只接受 ask/reject。裁决名为 `allow/allow_with_grant/delay/ask_user/reject`。执行点重新验证保留的原信封，不能仅凭 verdict 或请求里的 auth 标记放行。

凭据字段包括 `grant_id/agent_id/action/conditions/not_before/expires_at/max_uses/mode/auto_match/note/status/seal` 及提案、批准记录。只给 privileged 动作发凭据，条件全部为 AND；运算为 `equals/iequals/prefix/in/min/max/path_under`，缺事实不匹配，布尔不作数值。Windows path_under 按分隔符边界，只折叠 ASCII 大小写，拒绝相对路径、UNC/设备路径、上级跳转、数据流和含糊尾字符。事实来自执行点测量。

凭据通过 `muselink-id.sh grants propose` 提案并出结构化卡创建。正常 CLI 不提供直接激活通道。用量按凭据×req_id 去重；同号同动作同事实可重试，不同动作或事实报 `request_reused`。delay 先预留，执行前再确认时效、吊销和代理状态；取消释放用量，预留到期报 `reservation_expired`。JSON 状态与数据库吊销任一生效即阻止使用；吊销代理同时吊销其凭据。代码：`grants.py/flows.py/registry.py`。

询问卡只有明确允许选项放行，关闭、超时或卡片未出现不放行。迟延卡成功出现后，期限内可立即放行或取消，超时按 delay 机制放行；卡位忙、PC 不通不启动迟延放行。登记卡为“拒绝 / 先问我 / 白名单”，自报文字清控制字符、限长并标注来源。登记逐单处理，owned 卡可恢复等待；卡位忙不消耗尝试次数。可选同类免问由 grant_templates 生成结构化凭据。

默认数值与格式集中第 8 节。命令退出码：0 成功或放行，2 用法错误，10 需问用户，11 迟延，20 拒绝，30 内部错误。常设授权的具体内容读取用户私有文件，通用处理见 `vm/bridge/PROTOCOL.zh.md`。

## 5 线程

请求的 thread_id 决定归组，reply_to 指向被回复的 req_id；v3 两者在 signed 内，无签名请求在顶层。线程 ID 不合法时索引按缺省处理，客户端则拒绝发送。默认键 `<agent>/<topic>`：agent 取身份 agent_id 或旧 from，publish-* 的 topic 为 release，其余 chat；回信跟请求。

消息身份为 req_id，回信为 req_id#seq（v1 无序号记 0）。多目录副本只算一条，请求正文优先 sent。请求状态按文件计算：inbox=delivered，claimed/pending=claimed，中间回信=acked，终局= replied 或 needs_user，rejected=拒绝。done 证明终局已读，即使回信已移除；final_read 也可由 archive 终局证明。线程有待用户项则 waiting_user，否则有未终局项则进行中，其余完结。

索引只读文件、不验签；archive 回信标 read，outbox 标 unread_unverified，quarantine 标 rejected_by_client。索引不是授权依据。代码：`pc/mlindex/model.py/sources.py/indexer.py`；窗口机制见架构第 7 节。

## 6 弹窗文件协议

工具目录中的 `popup-text.txt/mode.txt/options.txt/expected-hash.txt` 是 staging 输入，mode 为 info/choice/input；`popup-expires-in.txt` 为可选相对到期秒数。stage 核对文件 SHA-256 后才准备卡片。`popup-reqid.txt` 与 `popup-pending.txt` 绑定本单 ReqId；`popup-armed.json` 给桌宠提供 `{req_id,mode,summary,at}`，与本机任务等待区分。

完成写 `popup-result.txt`、`popup-kind.txt`，最后写 `popup-done.txt`：首行时间戳、第二行 `REQ:<id>`。文本按 UTF-8；完成文本无 BOM。完成器校验 ReqId 并在互斥内“先到先得”；已有本单 done 不重写，退出 3。主等待与兜底通过 pending 原子认领互斥读取。结果输出包含 STATUS、RESULT、KIND 等行，解析按标签分界，后面的 DELIVERED 状态不是答案正文。

kind 区分 info/choice/typed/input、pet_done/pet_typed、expired、web_ai_done、任务结果等来源。通用桌宠完成不会被授权器误读为一个明确授权选项。双端展示响应见架构第 8 节。代码：`Complete-MuseWait.ps1/wait-popup.ps1/check-popup.ps1`、`vm/popup-send.sh/popup-wait.sh`。

VM inflight 标记在状态目录中，按字段解析而不 source；包含请求、时刻、模式、摘要和可选 owner。owned 卡由所有者续等，兜底不代收其答案。普通卡主路未交付时由兜底认领或报告到期。

## 7 钩子契约

每 tick 一个终止决策，每次唤醒一单或一个告警。平台适配器声明 `ML_HOOK_ADAPTER_CONTRACT=2`；有平台 runtime 时调用 silent/wake/log，否则输出 `SILENT: ...` / `WAKE: ...`。`HATCH_HOOK_DRY_RUN=1` 或 `MUSELINK_HOOK_DRY_RUN=1` 只报告，不写状态、不认领、不移动、不重拉、不清理。

| 检测器 → 平台 hook | 唤醒原因 | payload |
| --- | --- | --- |
| bridge-postman → codex-bridge-watch | codex_message | req_id、local_path、type、created_at、text 前 400 字、payload 或 parse_error；身份扩展 agent_id/auth/decision/reason/verdict_path |
| popup-fallback → popup-wake-fallback | popup_recovered | req_id/mode/summary/kind/done_at/result |
| 同上 | popup_expired | req_id/mode/summary |
| bridge-alert-watch → bridge-alert-watch | bridge_alert | alert_id/path/text 前 600 字/written_at/more/attempt/max_attempts/ack |
| bridge-selfheal（独立部署时） | selfheal_failed | log |

邮差无信、PC 不通、拉取失败、坏名、路由失败、拒收、重复、待登记、流水线接管时静默；弹窗无标记、坏标记、宽限期、已消费、仍在等待、owned、探测失败时静默；告警首次基线、无新增、等待确认、扫描失败时静默。告警须确认：处理者向用户报告后执行载荷里的 ack 命令（`vm/alert-ack.sh <alert_id>`，`--list` 列未确认项），之后才算完成；未确认的告警每 600 秒重新唤醒，共最多 3 次，最后一次再等 600 秒仍未确认则记为完成并在钩子日志写 gave_up，告警文件不动。状态在 `$ML_STATE_DIR/bridge-alert-watch.seen` 与 `.pending.json`，两个脚本共用一把锁；`MUSELINK_ALERT_RETRY_S`、`MUSELINK_ALERT_MAX_WAKES` 可改间隔与次数。唤醒字段兼容既有平台 prompt，新字段只能追加。包装器、检测器和适配函数在 `vm/hooks/`，安装对应见安装第 5 节。

## 8 协议常量

| 项目 | 值与唯一代码出处 |
| --- | --- |
| agent_id | `^[a-z][a-z0-9-]{1,31}$`，保留名见 `muselink_id/__init__.py` |
| key_id / grant_id | `<agent_id>#<1..6 位正整数>` / `g-<12 位十六进制>`，同上 |
| req_id | `^[A-Za-z0-9][A-Za-z0-9_-]{2,95}$`，两侧完整匹配 |
| thread_id | `^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$`，`mlindex/model.py` |
| 信封与新鲜度默认值 | 1 MiB；最大年龄 604800 秒，未来偏移 600 秒；seen 保留 691200 秒，`policy.py` |
| 授权默认上限 | 凭据 30 天 / 20 次；登记每小时 3 次；登记及询问卡 1800 秒，模板 policy |
| 迟延窗口 | 默认 120 秒，加载器允许 10–120；预留额外保留 300 秒，`policy.py/grants.py` |
| 执行器 kind | ping/info/phase/ad/sem/uia/seq/script/shell，`executor-host.py` |
| 截图门 | 超过 400 KB 转 JPEG，质量 80 到 50，`muse-shot.ps1` |

站点字段是安装配置，协议字段和文件名是两侧共享契约；变更时同步实现、向量与契约测试。工程流程见 RULES。
