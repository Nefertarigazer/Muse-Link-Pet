[English](install.en.md) | 中文

# 安装

界面语言自动跟随用户的 Windows 显示语言，不需要配置。

## 0 前置条件

目标是 Windows 10/11 的日常登录桌面、Linux Muse 环境、SSH 客户端和 Python。Windows UI 模块使用 Python 3.10+、WebView2；VM 标准库模块使用 Python 3.8+。先在各自目标机器检查，已有安装沿用。位置规则按 [RULES E4–E6](../RULES.zh.md#二工程规矩改这个项目的人和代理)。

Windows 用以下命令列本地固定盘和可用空间，再选择工具目录；`$env:SystemDrive` 是系统盘，不预填盘符或路径：

```powershell
Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |
  Select-Object DeviceID,@{n='FreeGiB';e={[math]::Round($_.FreeSpace/1GB,2)}}
Get-Volume
Get-PSDrive -PSProvider FileSystem
$env:SystemDrive
```

| 组件 | 在目标 PC 检查，满足则跳过安装 | 未安装时 |
| --- | --- | --- |
| Python | `Get-Command python.exe -All`；`python -c "import sys; print(sys.executable); print(sys.version)"`，排除商店占位入口 | 运行 Python 安装程序，选非系统盘目录；重新检查实际解释器 |
| Node.js/npm | `Get-Command node.exe`；`node --version`；`npm.cmd --version` | Node 安装程序支持选目录时选非系统盘，不改全局 npm 设置 |
| agent-desktop | `Get-Command agent-desktop -All`；`npm.cmd root -g`；在查到的包根及工具目录下递归查 `agent-desktop*.exe`，执行实际 exe 的 `--help` | 按第 4 节局部安装，未查到 exe 不填写猜测位置 |
| OpenSSH 服务器 | 管理员 `Get-WindowsCapability -Online -Name OpenSSH.Server*`；`Get-Service sshd` | 仅 State 未 Installed 时 `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`，再设 sshd 自动并启动；Windows 组件允许系统盘 |
| Tailscale | `Get-Command tailscale.exe`；`tailscale status` | 运行 Tailscale 安装程序，支持选择目录时选非系统盘；登录和批准设备见第 1 节 |
| WebView2 | 检查 Edge WebView2 Runtime 安装项；启动窗口时后端也检查 runtime | 缺失时安装 Microsoft WebView2 Runtime；可选目录时按同一位置规则 |

PC 安装脚本不自动安装这些前置软件。未找到时先完成该项再继续，避免运行端采用未核验的内置回退。VM 检查 `python3 --version`、`command -v ssh scp bash systemctl`；安装缺项使用该环境的包管理方式，已有组件不重复装。

### 浏览器控制扩展（按需）

Muse 官方浏览器扩展不随包安装：需要控制浏览器时按 O12 先查是否已装，未装经用户同意后由用户添加，步骤见[操作指南第 3 节](operating-guide.zh.md#3-标准动作)。包侧备选浏览器栈（agent-browser / Playwright 等）只在用户拒绝或无法添加时使用，已有则不重装。没有选择控制通道的配置键；`pc.pet.session_browser/session_profile` 只用于桌宠打开会话。

## 1 组网

网络登录、设备批准与策略编辑由用户本人操作。Muse 每次引导一个操作，说明应看到的状态，等用户完成再继续；卡住时根据当下界面解释下一步，不替用户确认账号。

### Tailscale 配置完整参考（PC、Muse 端与可选扩展服务器）

1. **Windows 电脑端**：Muse 先检查 `Get-Command tailscale.exe -ErrorAction SilentlyContinue` 与 `tailscale status`。未装时在征得用户同意后引导下载安装（`https://tailscale.com/download/windows`），由用户在右下角托盘登录自己的账号，随后运行 `tailscale ip -4` 记下 PC 的 tailnet IPv4（`100.x.y.z`，填入 `site.pc_host`），并用 `tailscale status` 首行记下设备名（填入 `pc.tailscale.device_name`）。
2. **Muse Linux 环境（及按需接入的其他服务器）**：Muse 检查 `command -v tailscale` 与 `tailscale status`。未装时在征得用户同意后安装（标准 Linux 可用官方包或 `curl -fsSL https://tailscale.com/install.sh | sh`；若处于无 `/dev/net/tun` 的容器环境，则以 userspace 模式启动 `tailscaled --tun=userspace-networking` 并按需配置 `vm.USE_TUNNEL`），运行 `sudo tailscale up`（或 `tailscale up`），把登录授权链接发给用户在 `https://login.tailscale.com/admin/machines` 确认上线。在用户同意的情况下，也可以用同样方式把用户需要联动控制的其他服务器设备接入同一 tailnet。
3. **核对两台设备在线**：用户在 Tailscale 管理后台核对两端显示在线后，Muse 在两端运行 `tailscale status` 确认互通。
4. **Windows OpenSSH（`sshd`）与公钥**：在 PC 检查并启用 `sshd` 服务。Muse 在 VM 只读检查现有 SSH 密钥及公钥指纹，已有可用密钥直接沿用；用户把对应公钥写入目标 Windows 账户的 `authorized_keys`（普通账户为 `%USERPROFILE%\.ssh\authorized_keys`；管理员账户依 OpenSSH 配置写入 `C:\ProgramData\ssh\administrators_authorized_keys` 并收紧 ACL）。
5. **主机指纹核验（`known_hosts`）**：用户通过可信途径核对 PC 主机指纹，VM 建立 `known_hosts`。默认严格检查主机密钥（`HOST_KEY_CHECK=yes`），仅在首次建连且已核对指纹时临时使用 `accept-new`。
6. **Tailscale ACL 最小权限策略**：填好站点配置后运行 `python3 vm/site-config.py acl --site <站点文件>`，会生成仅允许 `tag:muse` 通过 `tcp:22`（SSH）访问 `tag:muselink-pc` 的策略片段：

   ```json
   {
     "tagOwners": { "tag:muse": ["autogroup:admin"], "tag:muselink-pc": ["autogroup:admin"] },
     "grants": [
       { "src": ["autogroup:member"], "dst": ["*"], "ip": ["*"] },
       { "src": ["tag:muse"], "dst": ["tag:muselink-pc"], "ip": ["tcp:22"] }
     ]
   }
   ```

   Muse 向用户解释这两个 tag、方向和端口后，由用户在 Tailscale **Access controls**（`https://login.tailscale.com/admin/acls`）贴入策略，并在 **Machines** 列表里给 Muse 设备打 `tag:muse`、给 PC 打 `tag:muselink-pc`（已有策略已包含时跳过替换）。
7. **SSH 连通实测**：VM 执行 `ssh <实查Windows账号>@<实查PC地址> whoami`，确认返回用户名与目标 Windows 账户一致。若 VM 运行环境需要经 userspace / CONNECT 代理（`vm.USE_TUNNEL=1`、`vm.TUNNEL_PORT=3130`），用配置后的 `vm/pc.sh whoami` 测试通过。

密钥文件和密码不经聊天明文传递。相关闸门见 [SECURITY](../SECURITY.zh.md)。

## 2 站点配置

### 2.1 填写与现场查值

从 `config/muselink.site.example.json` 复制站点文件。唯一站点文件放 PC `<工具目录>/muselink.site.json` 与 VM `~/.config/muselink/muselink.site.json`，两份内容一致。示例的地址值均为空，`_字段` 给出查询办法。

已有安装先读原运行文件：PC muselink.json→pc、plugin/policy.json→plugin_policy；VM muselink.env→vm、policy.json→policy、publish.env→publish。共用的 PC 地址、用户、工具目录、Python、信箱写进 site；上传信息写 publish，不再在 pc.upload 或 vm 重复定义。原设置不同不能凭一边的值覆盖另一边，先验证两侧实际调用并看预览差异。

| 地址类字段（均留空起步） | 在目标机器怎么查、怎么确认 |
| --- | --- |
| site.pc_host / pc_user | PC `tailscale ip -4` / `$env:USERNAME`；SSH whoami 核对实际登录账户 |
| site.pc_tool_dir | 已装查 executor-host 任务动作、运行进程命令行与目录；新装列第 0 节盘与空间，选非系统本地固定盘目录，格式 `<盘符>:\MuseLink` |
| site.pc_python | PC `Get-Command python.exe -All` 与 `python -c "import sys; print(sys.executable)"`；已有安装核对 executor.python 可执行 |
| site.pc_bridge_dir | 留空即 `<工具目录>\muse-bridge`（新装用这个）；已有安装读现行 bridge.root 并 `Test-Path`，不是默认位置时才填 |
| pc.executor.agent_desktop_exe | 按第 4 节查实际包和 exe，运行 --help 核验后填写 |
| pc.executor.agent_desktop_home | 已装：以 agent-desktop `status` 返回的 `state_root` 为准。新装：选非系统盘、用户目录之外的路径（格式 `<盘符>:\agent-desktop-home`），只建父目录，由 agent-desktop 自己创建该目录。宿主、截图脚本和属主修复脚本共用此值，详见第 4 节第 1 步 |
| pc.pet.session_url | 留空为 https://muse.ai/；指定会话时用户在目标机器浏览器打开它，从地址栏复制 https URL |
| pc.pet.session_browser / session_profile | 个人值，可留空。浏览器 exe 用 `Get-Command chrome.exe` 或开始菜单快捷方式目标查；档案在该档案里打开 chrome://version（Edge 为 edge://version）看“个人资料路径”最后一段 |
| pc.index.path | 读现行 index.path 或查已确认信箱下 index/threads.sqlite；新装未生成留空，由索引创建 |
| pc.tailscale.exe_dir / device_name | `(Get-Command tailscale.exe).Source` 后 Split-Path；`tailscale status` 核对设备名称 |
| pc.web_bridge.allowed_origins | 不是本机地址，是网页 AI 的公共来源；模板所示即代码默认值。要增减时按浏览器地址栏的 origin（协议 + 主机）填写 |
| pc.explib.public.index_url / repo | 在实际公共库托管处查 index.json HTTPS 地址和仓库标识；公共库未建立两者留空 |
| vm.SSH_KEY / SSH_CONFIG / KNOWN_HOSTS | VM `ls -la ~/.ssh`、`ssh -G <PC目标>` 查 identityfile、配置与 userknownhostsfile，验证文件并核对指纹 |
| vm.ML_STATE_DIR / ML_BRIDGE_HOME | VM 查现行 muselink.env、钩子包装脚本和 `systemctl cat muse-bridge`，验证目录；新装按所选 VM 布局确认后填写 |
| vm.PROXY_FALLBACK | VM 查运行环境提供的代理及服务环境，验证在 systemd/cron 的干净环境可用；不输出含凭据环境 |
| publish.RELEASE_SSH / RELEASE_SSH_KEY / RELEASE_KNOWN_HOSTS | VM 查更新目标 `ssh -G <目标>` 的 user/hostname/identityfile/userknownhostsfile，验证连通和文件；未配置则不启用发版 |
| publish.RELEASE_WEB_DIR / RELEASE_TEST_DIR / RELEASE_STAGE | 在实际更新服务器查看站点配置、暂存目录和测试目录，`pwd`、`test -d`、权限检查；仅创建并确认后的目录才填写 |
| publish.RELEASE_BASE_URL / RELEASE_LATEST_URL | 查实际站点路由与更新清单，HTTP 回读验证公开地址；未发布留空提示 |
| publish.RELEASE_ALLOWED_DIRS | PC `Resolve-Path` 查用户批准的出包目录，多个用分号分隔；未指定留空 |
| publish.UPLOAD_DEST_PC / UPLOAD_KEY_PC | PC `ssh -G <上传目标>` 查 user/hostname/identityfile，`Test-Path` 验证密钥文件；可相对工具目录，不猜文件名 |
| publish.RELEASE_ZIP_NAME / RELEASE_SETUP_NAME | 在目标项目实际构建产物和更新器配置中查发布文件名，可含 `{version}`；无安装包时 setup 名留空 |

账号、目录、服务器、URL 未安装或查不到时保持空值并提示待办。site 的三个必填项（`pc_host`、`pc_user`、`pc_tool_dir`）缺一项，安装器就拒绝；其余空字符串由生成器省略，读取端仍可能应用内置默认值，因此“留空”不证明运行端已禁用该功能。安装时确认实际路径再启用相关模块，不把回退值当现场检测结果。

其他配置按模板机制填写：`_` 为帮助键，会丢弃；密钥与令牌保留在独立文件，站点文件只填路径。`publish.enabled=false` 不生成 publish.env，也不删除已有文件。敏感词只填 pc.explib.redact.literals；协议常量见协议第 8 节。

### 2.2 生成、比较与替换

| 生成文件 | 输入 | 生成者 |
| --- | --- | --- |
| PC muselink.json | pc + site 中 Python/信箱 + publish 上传字段 | pc/install/deploy-pc.ps1、site-config.ps1 |
| PC plugin/policy.json | plugin_policy | 同上 |
| VM muselink.env | site + vm | vm/site-config.py，经 install.sh 调用 |
| VM policy.json | policy | 同上，使用身份模块校验 |
| VM publish.env | publish，启用时 | 同上 |
| tailnet 策略片段 | tailnet | site-config.py acl，只打印，由用户应用 |

缺失为 new；有效内容相同打印 skip；不同列键差异并停。PC `-ReplaceConfig`、VM `--replace-config` 才替换不同配置，旧件留备份。已有安装先预览到一致，再决定用户明确要变的值。工程约定见 RULES E7。

## 3 出包与校验

在开发 checkout 先提交，再执行：

```text
python tools/make-release.py <空的出包目录> <提交号>
```

发布物含 pc/、vm/、RELEASE.txt，两侧各有 RELEASE-MANIFEST.sha256 和站点模板，VM 另带常设授权模板。只从提交导出，不含工作树未提交状态；检查清单哈希与文件数，在复制前后各核验一次。

PC 对清单的每行分出 64 位哈希和相对路径，用 `Get-FileHash -LiteralPath ... -Algorithm SHA256` 比较；VM 在树内 `sha256sum -c RELEASE-MANIFEST.sha256`。验收记录写 RELEASE.txt 的提交号。迁移旧发版类型的换包窗口内先暂停发版，待 PC 客户端、VM 服务与钩子都切换后恢复。

## 4 PC 侧

以下 `$ToolDir`、`$SiteFile`、`<发布物>` 都是在目标 PC 查到或选定的值。部署脚本默认只预览；现有目录即使在系统盘也保留，新建系统盘目录被拒绝，需要明确 `-AllowSystemDrive`。系统盘没有其他盘时也要求该参数。

1. **agent-desktop，已装则跳过。** 查 PATH 启动入口、已有 npm 包根和工具目录：

   ```powershell
   Get-Command agent-desktop -All -ErrorAction SilentlyContinue
   npm.cmd root -g
   Get-ChildItem "$ToolDir\tools\agent-desktop" -Recurse -Filter agent-desktop*.exe -ErrorAction SilentlyContinue
   ```

   在实查包目录递归找 exe，执行 --help。已有的沿用并写 `pc.executor.agent_desktop_exe`。新装先完成下一步部署，让工具目录通过系统盘检查；再执行局部安装，查出 exe 后重跑配置生成。缓存也在工具目录，恢复原会话环境：

   ```powershell
   $oldNpmCache = $env:npm_config_cache
   try {
     $env:npm_config_cache = Join-Path $ToolDir 'tools\npm-cache'
     npm.cmd install --prefix "$ToolDir\tools\agent-desktop" agent-desktop
     if ($LASTEXITCODE -ne 0) { throw 'agent-desktop 安装失败' }
   } finally { $env:npm_config_cache = $oldNpmCache }
   Get-ChildItem "$ToolDir\tools\agent-desktop" -Recurse -Filter agent-desktop*.exe
   ```

   查不到 exe 就停在此项。该步骤不改 npm 全局设置。包位置和状态目录分别配置，安装时把确认过的状态目录填入唯一站点文件的 `pc.executor.agent_desktop_home`，由部署生成运行配置，宿主启动时设置 `AGENT_DESKTOP_HOME`。

   **状态目录放在用户目录之外。** agent-desktop 默认把状态放在 `%USERPROFILE%\.agent-desktop`。每次操作前它会自检这个目录：属主不是当前用户、或名单里有 SYSTEM、Administrators、当前用户之外的主体，就拒绝工作。同机的 Codex 提权沙箱每次初始化都会给用户目录下的每个一级文件夹加 `CodexSandboxUsers` 读权限（记录在 `%USERPROFILE%\.codex\.sandbox\sandbox.<日期>.log`），默认位置因此会反复失效。新装时选一个非系统盘、用户目录之外的路径（格式 `<盘符>:\agent-desktop-home`）填进 `pc.executor.agent_desktop_home`。这个目录**不要预先创建**：父目录存在即可，agent-desktop 首次写状态时自己建好，只含三个主体、不继承上级权限、属主为当前用户。宿主和截图脚本启动时据此设置 `AGENT_DESKTOP_HOME`，状态位置以 `status` 返回的 `state_root` 为准。

   路径优先级：修复脚本的 `-StateDir`，运行配置 `executor.agent_desktop_home`，进程、用户、系统三级环境变量，最后才是默认位置。用户级变量从注册表读取，计划任务没继承新变量时也能取到。单独在终端运行 agent-desktop 时也设同一变量。

   已有安装原位保留状态，换位置时：先停宿主；复制旧状态到新路径（跳过 `refstore.lock`），复制时不带权限；用目标运行身份执行一次 `status`，确认 `state_root` 和三方名单；切换并验证宿主后，旧目录改名保留。以提权身份复制或创建的文件属主是 Administrators，降为普通权限运行前先执行 `install\fix-agent-desktop-owner.ps1`（第 4 步）。

2. **部署代码和生成配置。** `powershell -File <发布物>\pc\install\deploy-pc.ps1 -SiteFile <站点文件>`；预览列磁盘、new、change、skip。确认后加 `-Apply`。代码按 SHA-256 跳过相同文件，变化文件先备份，复制后再验哈希；本机独有文件不删除。`-Force` 强制代码重拷，配置差异仍使用 ReplaceConfig。
3. **launcher。** `powershell -File <工具目录>\launcher\build-launcher.ps1 -OutDir <工具目录> -Preview` 看计划，去掉 Preview 编译。exe 存在且 `launcher.source.sha256` 等于当前 launcher.cs 哈希则跳过；csc 未找到报错。`-Force` 强制编译。
4. **任务定义。** `powershell -File <工具目录>\install\register-tasks.ps1 -ToolDir <工具目录>` 预览，需变更才加 `-Apply`。动作、参数、触发器、主体、运行级别、设置相同则 skip；不同列出旧→新。普通任务为交互 Limited；tailscale-watchdog 需管理员和 `-IncludeWatchdog`。已有 Highest 执行器会明确列为改 Limited；先按用户决定运行属主修复再执行变更。
5. **执行器。** 代码变化后用提权终端运行 restart-host.ps1，先预览再 `-Apply`；它处理旧实例并启动任务。新安装先注册任务，再启动一次。已有正确版本且恰好一个监听时跳过重启；用执行器 ping、日志 host_start 和监听证据验活。
6. **网页等待。** 按需在目标浏览器安装 `userscripts/muselink-web-ai-notifier.user.js`。检查已有脚本内容与站点来源；相同跳过，更新时替换；按第 2 节填写 origin。

依赖、launcher、venv、日志、信箱、索引、身份和经验数据留原安装目录。部署不覆盖本机 venv、launcher、信箱、身份、经验与运行状态；源码哈希记录由构建器维护。

## 5 VM 侧

1. 查现有服务 `systemctl cat muse-bridge`、钩子 script_path 与进程命令行，确定当前树和状态位置。已有安装原位升级；代码树先封存或备份，保留身份、信箱、SSH 和钩子状态。
2. 将校验过的 vm/ 树放目标目录；完整替换代码前确认没有进行中的登记或弹窗待交接，保留与状态有关的文件。身份目录不从代码包复制。
3. `bash <VM树>/install.sh --site <站点文件>` 预览，检查无 FAIL；再加 `--apply`。配置比较见第 2 节，三个 hook 包装脚本和 service.rendered 内容相同则 skip，不同才写；`--force` 强制这四个文件重写。脚本同时校验发布清单、清树内 pycache、设置可执行位，检查本机身份与桥目录。
4. 比较渲染服务与现有单元：`systemctl cat muse-bridge`，内容相同且服务运行则跳过安装。单元由 `bridge/render-unit.sh` 渲染，安装与自愈共用；其中 `MUSELINK_HOME`、`MUSELINK_CONFIG` 两行指向 install.sh 所用的家目录与 muselink.env，因为服务按 User= 的 passwd 家目录启动。缺失或变化按 install.sh 打印的 sudo install、daemon-reload、enable/restart 命令应用，回读 status 和服务日志。root 操作由脚本打印，不自动执行。
5. 平台 hook 每 60 秒：codex-bridge-watch→ml-bridge-postman.sh，popup-wake-fallback→ml-popup-fallback.sh，bridge-alert-watch→ml-bridge-alert-watch.sh。读取平台现有定义逐个比较 script_path、调度与唤醒字段，相同跳过；缺失注册，不同才修改。首次切换前以 dry run 看决策，然后一项一项启用。契约在协议第 7 节。
6. 常设指令已存在就保留；没有时 install.sh 发 warn，按 `standing-orders.example.md` 由用户填写后放 `~/.config/muselink/standing-orders.md`。部署者不替用户扩充授权。

旧命令转发在 vm/compat，完成旧调用方迁移和换包验收后删除；过渡排除条件在 tools/public-exclude.txt。

## 6 可选模块

| 模块 | 安装与“已装跳过”依据 |
| --- | --- |
| 信箱依赖 | 普通用户执行 mailbox/install.ps1，先预览再 `-Apply`，复用共享 venv 和相同依赖哈希；不创建快捷方式。升级删除桌面、开始菜单旧 MuseLink Mailbox.lnk 及匹配的 Startup 链接，结束本安装目录的旧独立信箱进程，清理对应计划任务、Run/RunOnce 自启动。`-AutoStart` 仅注册 pet 任务，register-tasks 的旧 mailbox 开关也映射到 pet；不启动 mailbox/tray.py。 |
| 桌宠 | 先完成信箱 venv；普通用户 pet/install.ps1 预览再 `-Apply -AutoStart`；确认音存在跳过生成，快捷方式和 pet 自启定义相同跳过。`-RequestIdentity` 是显式登记请求，按既有流程由用户决定。 |
| 本机插件 | plugin/install.ps1 -Agent <已登记ID> [-Client codex\|kiro\|claude] 预览再 `-Apply`；只写这个代理所在客户端的配置（ID 即客户端名时可省 -Client），保留其他项及已有 muselink 项，一致不备份；Codex、Kiro 已安装（有 .codex / .kiro 目录）而配置文件不存在时新建。临时配置测试用 -ConfigHome。客户端详情见 GUIDE。 |
| 经验库 | 代码随包，运行时自动读 experience/local 与 public。私有条目拷进 local/<app>/；公共库按实际托管填写配置后 sync。已有相同 JSON 不重复拷。每日分享任务用 register-tasks.ps1 -ExplibOnly，先预览再 Apply。 |
| 发版 | 只用于用户自己的更新站。现场填 publish 并启用，上传目标先在目标机器上验证连通后再填写；已有服务、任务和配置相同跳过。先跑 test:true，授权、远端目录、哈希与公开清单全部验证后再正常发版。 |

模块 installer 的 `-Force` 用于明确强制重做；不会替代身份同意。登记按代理分别执行 `muse-call.ps1 -As <id> -Enroll`；每个代理的卡完成后再下一单。全部登记后，可按用户决定在站点配置启用 legacy_unsigned=reject、bridge.require_identity=true，再生成运行文件。

## 7 验收清单

每项记录提交、命令、结果、日志或截图。失败修入提交、重出包，再从一致性开始；操作与授权按 RULES。

| 项 | 验什么 |
| --- | --- |
| 一致性 | PC/VM 清单全部相符，安装预览配置一致，进程命令行都来自工具目录与所选 VM 树 |
| 通道 | pc-master.sh ensure、executor.sh ping、ad list-windows 能往返 |
| 操作层 | sem observe/resolve、uia locate、一次经查验的 seq 作业、shot 截图 |
| 弹窗与总线 | 选择卡主路收到答案，no-wait 卡由兜底送达，短到期卡关闭并报 expired；本机任务等待走通，网页通道按需走通 |
| 桥 | 登记代理签名 ping 得到中间与终局回信，Check 可续取；测试发版由常驻服务单独处理，旧名兼容入口输出归一类型 |
| 钩子与自愈 | 临时测试告警被发现，事后清理；受控停止服务后观察自愈。对照工具属于私有验收材料，不随公共包安装 |
| 登记 | 模拟等待中断后可续卡，卡位忙不丢单；用户真实点一次 pet 登记，回信与密钥安装完成 |
| 信箱窗口 | 线程、发版卡、未归档回信正确；从 pet 打开、关闭、再打开复用同进程窗口；最小化在任务栏显示 pet.ico 并可点击还原，关闭隐藏至后台，未读角标位于 pet 信箱按钮，pet 退出后无子窗残留。 |
| 桌宠 | 登录启动、悬停按钮、隐藏与唤回、四种双端回复组合、一次确认音、入口打开信箱；正式形象按素材清单确认 |
| 插件 | observe，首个 act 同意卡，时段同意后同应用可继续，拒绝不执行；ask_user 回答可取 |
| 经验库 | record 草稿、审阅确认、lookup、实用后 feedback；Muse CLI 路径也走一次；用合成条目验证累计 5 条批量分享与拒绝后不再询问，不对真实个人条目测试投稿 |

## 8 升级已有安装

沿第 2–7 节同一流程：查现状、照搬现行站点值、预览、只应用缺项与差异。现有工具目录不搬家，包括系统盘上的旧目录；每次出包来自一个提交，当前进程加载的旧代码需按第 4 节单独重启。安装源码与生产运行的区别见 RULES E1/E8。

PC 部署备份目录含被覆盖代码、原配置、executor-host 任务 XML 与 manifest.json。回滚时先停止相关进程，按 manifest 还原备份文件，按备份 XML 恢复需要恢复的任务，站点文件也回到与旧运行文件相符的版本，再按旧发布物校验和重启验活。新添代码可按清单识别；不删除信箱、身份、令牌或经验数据。

VM 回滚恢复上一提交的完整代码树、原服务单元和 hook script_path；生成配置使用安装时的 .bak 时间戳备份。身份存储和信箱台账留原位，不用旧身份副本覆盖。回滚后复查通道、签名 ping、等待与服务状态，确认终局继续可取。
