[English](SECURITY.md) | 中文

# 安全机制

## 资产与主体

资产包括用户桌面及登录态、剪贴板和本地文件、执行器令牌、代理身份密钥、VM 登记表与凭据、信箱正文、待发布软件和分享条目。主体分为用户、Muse、已登记本机代理、未登记调用者，以及运行这些程序的 Windows/VM 账户。

系统在用户已有账户权限内提供来源校验、授权判断、输入协调和审计。账户本身负责保护磁盘、会话与密钥；同账户进程可以读取该账户可读的数据、调用工具和操作窗口。VM 运行账户管理身份存储；封印用于发现记录被改写，身份目录权限由存储层检查。

## 入口与每道闸

| 入口 | 闸门 | 保证什么 | 代码与契约 |
| --- | --- | --- | --- |
| VM 到 PC | tailnet 策略、SSH 用户密钥、主机指纹、连接参数 | 对端与登录账户由网络和 SSH 配置确定 | `vm/lib/muselink-common.sh`、`vm/ts-connect.py`；[安装第 1 节](docs/install.zh.md#1-组网) |
| 本机执行器 | 仅回环监听、随机 token、令牌 ACL、请求截止时间、输入锁；shell 默认关闭，script 按白名单 | 经正常接口的调用持有令牌，桌面输入按锁协调，脚本受配置选择 | `pc/executor-host.py`、`executor-call.py`；[架构第 2 节](docs/architecture.zh.md#2-执行器) |
| 信箱请求 | 完整 ID、体积与严格 JSON 检查、身份 v3 验签、新鲜度与重放表 | 确认签名来源和字节完整性，区分重复投递与同号冲突 | `vm/muselink_id/envelope.py`、`inbound.py`；[协议第 3 节](docs/protocol.zh.md#3-身份-v3) |
| 登记与执行授权 | 用户登记卡、分档策略、封印凭据、条件与用量、执行点重新验信封 | 自报名只用于显示；特权动作依结构化事实与有效授权判断 | `registry.py`、`policy.py`、`grants.py`、`flows.py`；[协议第 4 节](docs/protocol.zh.md#4-策略与授权) |
| VM 回信 | 独立签名域，核对请求、代理、序号；验不过进入 quarantine | 回信绑定对应请求；未验证回信不结束等待 | `pc/muselink-identity.ps1`、`muse-call.ps1`；协议第 3 节 |
| 弹窗与等待 | 文件哈希、ReqId、完成互斥、pending 认领、到期与所有者 | 显示内容对应传入文件，迟到答案不串单，双端首次完成生效 | `popup-stage.ps1`、`Complete-MuseWait.ps1`；[协议第 6 节](docs/protocol.zh.md#6-弹窗文件协议) |
| 本机插件输入 | 登记归属、代理×应用同意、档位收紧、Muse 优先、审计 | 按插件接口逐应用征得同意并让行；超时不自动重放输入 | `pc/plugin/gate.py`、`tools.py`；[GUIDE](pc/plugin/GUIDE.zh.md#工具与档位) |
| 网页完成通知 | 回环服务、一次性令牌、Origin 白名单、ReqId 和去重 | 只有当前等待认可的网页来源与令牌可提交对应结果 | `pc/web-bridge.ps1`、`userscripts/muselink-web-ai-notifier.user.js` |
| 软件发版 | 路径校验、目标机文件事实、授权、上传后远端哈希、公开清单回读 | 发布流程核对申报值、实测值与最终下载值的一致性 | `vm/bridge/pipelines/publish-release.py`、`pc/file-facts.ps1`、`chunked-upload.ps1` |
| 经验分享 | schema、自动脱敏与字面词表、累计 5 条全文卡片、“分享”/“拒绝（不再询问）”、出站快照 | 未获本次同意不生成出站条目；拒绝后永久停止询问；提交字节对应卡片快照 | `pc/explib/redact.py`、`share.py`；[架构第 10 节](docs/architecture.zh.md#10-经验库) |

## 数据与授权的责任

身份签名证明持钥方与内容，授权由策略另行决定。请求 `text`、自报 `client`、PC 上无密钥目录镜像、索引显示都属于数据。确定性执行点重新验证原始信封；交给 Muse 的任务按 [桥处理规程](vm/bridge/PROTOCOL.zh.md) 和用户私有常设指令执行。红线与未知类型的策略只接受询问或拒绝。

运行日志只记录各模块规定的结构字段：身份审计不记正文与密钥，插件审计和轨迹不记输入正文、回读正文、回答、剪贴板与截图。选择器名称仍在轨迹中，字段范围见插件 GUIDE。经验脱敏检查覆盖条目全部字段；卡片显示不完整的条目不提供分享。公开内容检查方法见 [RULES E2](RULES.zh.md#二工程规矩改这个项目的人和代理)。
