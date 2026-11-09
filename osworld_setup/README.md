# 在本地虚拟机评测 WorkSwarm（OSWorld 1.0）

这套脚本在 Windows 宿主机运行 OSWorld 驱动，并在 OSWorld 的 Ubuntu 虚拟机内运行完整的 WorkSwarm 后端和 Agent。宿主机通过 HTTP 创建与网页端相同的 `agent` 模式、`web` 通道会话，把 OSWorld 指令交给 Agent；无需打开网页前端。WorkSwarm 的工具操作虚拟机桌面，OSWorld 在同一虚拟机上执行任务设置和 `evaluate()`。

| 脚本 | 用途 | 运行频率 |
| --- | --- | --- |
| `prepare_vm.py` | 同步源码、安装 WorkSwarm、写入模型配置并创建基线快照 | 每套基线一次 |
| `probe_vm.py` | 可选的记事本任务检查；导出轨迹后验证 `~/hello.txt` | 按需 |
| `run_local.py` | 从基线快照运行一个 OSWorld case，并评分、导出文件 | 每个 case 一次 |
| `run_serial.py` | 依次调用 `run_local.py`，统计每个 case 的完整墙钟时延 | 每批一次 |
| `review_video.py` | 根据原始录像和会话事件生成复核视频；由前两个运行脚本调用 | 无需单独运行 |

## 下载仓库与准备宿主机

下面的命令适用于 Windows x86-64 和 PowerShell。先安装 Git、Python 3.12、[VirtualBox](https://www.virtualbox.org/) 和 [FFmpeg](https://ffmpeg.org/download.html)，并确认 `git`、`py`、`VBoxManage`、`ffmpeg`、`ffprobe` 可运行。VirtualBox 需允许创建 Ubuntu 虚拟机及主机与虚拟机之间的网络连接。准备足够的磁盘空间保存官方虚拟机、快照、Python 依赖和每个 case 的视频/轨迹。已存在两个仓库或 OSWorld 虚拟环境时，跳过对应的克隆和创建步骤。

```powershell
New-Item -ItemType Directory -Force C:\Users\didan\Documents\ChatGPT\jiuwen | Out-Null
Set-Location C:\Users\didan\Documents\ChatGPT\jiuwen
git clone https://github.com/openJiuwen-ai/jiuwenswarm.git jiuwenswarm
git clone https://github.com/xlang-ai/OSWorld.git OSWorld

py -3.12 -m venv .\OSWorld\.venv
$python = '.\OSWorld\.venv\Scripts\python.exe'
& $python -m pip install --upgrade pip
& $python -m pip install -r .\OSWorld\requirements.txt

git -C .\OSWorld rev-parse HEAD
VBoxManage --version
ffmpeg -version
ffprobe -version
```

这里的 `osworld_setup/` 是当前工作区的评测适配代码，尚不能假定上游 WorkSwarm 克隆已包含它。如果在另一个目录克隆仓库，需要把当前工作区的 `jiuwenswarm/osworld_setup/` 整个目录复制到新仓库的同名位置，再运行下文命令。`prepare_vm.py` 会把指定本地 WorkSwarm 仓库的源码同步进虚拟机，因此评测的是该目录当时的代码。请记录两个仓库的 commit；当前工作区的 OSWorld commit 是 `8903ebeb0efdf74331bd7dc37ed91ccbbff22317`。

首次准备时可以**不传 `--vm-path`**。OSWorld 的 VirtualBox manager 会从其配置的官方 Ubuntu 镜像地址下载、解压并导入虚拟机，之后 `prepare_vm.py` 才在里面安装 WorkSwarm 和创建快照。该下载/导入只在没有可复用虚拟机时发生，可能耗时较长；若已导入官方 VM，则向 `prepare_vm.py` 传现有 `.vbox` 文件的 `--vm-path`。仓库中的 case JSON 随 `git clone OSWorld` 一起下载，不需要逐个下载。官方虚拟机的 Ubuntu 账户是 `user` / `password`；脚本默认用该密码处理首次启动的服务恢复。

## 环境依赖

- Windows 宿主机、PowerShell、Git、Python 3.12，以及已安装的 VirtualBox 和 `VBoxManage`。脚本会查找 `PATH` 和常见安装路径。当前流程在 VirtualBox 上验证过；代码也接受 VMware，但这里的命令采用 VirtualBox。当前 OSWorld `pyproject.toml` 要求 Python ≥3.12；其 README 中较早的最低版本说明是 ≥3.10，这里按更严格的项目元数据准备。
- 已检出的 [OSWorld 1.0 仓库](https://github.com/xlang-ai/osworld)，按其说明准备宿主机 Python 环境、官方 Ubuntu 虚拟机和 VirtualBox 网络。宿主机须能访问虚拟机的 OSWorld HTTP 服务（端口 `5000`）；WorkSwarm 后端使用端口 `8766`。运行时不要同时让其他程序占用同一台虚拟机。
- 宿主机能运行 `ffmpeg` 和 `ffprobe`（放入 `PATH`）。Ubuntu 虚拟机内也需要 `ffmpeg`，用于录制桌面。复核视频依赖宿主机 `ffmpeg`/`ffprobe`。
- 可访问 WorkSwarm 模型 API。首次准备还需下载 Linux `uv`、Python 3.11 和 WorkSwarm 的 Python 依赖。
- 本地 WorkSwarm 仓库。`prepare_vm.py` 默认打包 Git 已跟踪的源码；如需包含未跟踪但未被忽略的源码，显式加 `--include-untracked`。不要把密钥文件加入源码包。

以下命令以当前工作区为例，从 `C:\Users\didan\Documents\ChatGPT\jiuwen` 执行：

```powershell
Set-Location C:\Users\didan\Documents\ChatGPT\jiuwen
$python = '.\OSWorld\.venv\Scripts\python.exe'
& $python -c "import desktop_env, requests; print('OSWorld imports OK')"
VBoxManage --version
ffmpeg -version
ffprobe -version
```

`$python` 应指向已安装 OSWorld 依赖的 Python。当前 `osworld_vm.json` 已指向 `OSWorld\virtualbox_vm_data\Ubuntu0\Ubuntu0.vbox` 中的 `workswarm_debug_clean` 快照；如果直接使用这套已准备好的环境，从“运行一个 case”开始。

## 一次性准备：`prepare_vm.py`

脚本实际执行的步骤如下：

1. 校验 OSWorld/WorkSwarm 仓库、虚拟机路径、API key 环境变量，以及目标状态文件尚不存在。
2. 将当前 WorkSwarm 源码打包；启动 OSWorld 官方虚拟机，优先恢复已有的 `network_ready` 快照，然后将源码上传到 `/home/user/workswarm`。
3. 在虚拟机内安装 `uv`、Python 3.11，并执行 `uv sync --python 3.11 --no-default-groups --extra cua`；初始化 `/home/user/.jiuwenswarm` 工作区。
4. 写入 `/home/user/.jiuwenswarm/config/.env` 和 `config.yaml`：配置模型、启用 HTTP 后端、CUA Agent、轨迹诊断，以及 Agent/Code 的 debug trace 和 dump。
5. 创建含安装结果及模型配置的快照，写出宿主机状态 JSON。状态文件记录虚拟机路径、快照名、模型名和源码哈希；不记录 API key。

例如，先从本地 `env.txt` 读密钥到当前 PowerShell 进程的环境变量，再准备一套**新**基线：

```powershell
Set-Location C:\Users\didan\Documents\ChatGPT\jiuwen
$python = '.\OSWorld\.venv\Scripts\python.exe'
$env:MODEL_API_KEY = & $python -c "from dotenv import dotenv_values; print(dotenv_values('env.txt')['MODEL_API_KEY'])"

& $python .\jiuwenswarm\osworld_setup\prepare_vm.py `
  --osworld-repo .\OSWorld `
  --workswarm-repo .\jiuwenswarm `
  --vm-provider virtualbox `
  --state-file .\osworld_vm_new.json `
  --snapshot-name workswarm_new `
  --model 'deepseek-v4.1-flash' `
  --api-base 'https://api.openlux.ai/v1' `
  --api-key-env MODEL_API_KEY

Remove-Item Env:\MODEL_API_KEY
```

上例的读取方式需要宿主机 Python 安装 `python-dotenv`；也可以用其他方式预先设置 `$env:MODEL_API_KEY`。不要把密钥值直接放在命令行 `--api-key`：它会留在 shell 历史或进程参数中。**当前 `osworld_vm.json` 已经准备过，不能用相同状态文件再次执行准备脚本。** 新建基线要使用新的状态文件和快照名。快照内部包含模型密钥，不要把虚拟机镜像或快照公开分享。

上述命令省略 `--vm-path`，用于首次自动下载/导入官方虚拟机。如果官方 VM 已存在，可在命令中补上 `--vm-path .\OSWorld\virtualbox_vm_data\Ubuntu0\Ubuntu0.vbox`（先确认实际路径）。在全新工作区中也可将状态文件命名为 `osworld_vm.json`、快照命名为 `workswarm_ready`；当前工作区为了避免覆盖已有基线，示例采用新名称。

### 快照中由 `prepare_vm.py` 显式写入的内容

| 虚拟机内位置 | 显式操作 | 作用 |
| --- | --- | --- |
| `/home/user/workswarm/` | 上传指定本地 WorkSwarm 仓库源码；安装 `uv`、Python 3.11 和 `uv sync --python 3.11 --no-default-groups --extra cua` 的依赖 | 固定评测使用的 WorkSwarm 代码和运行环境 |
| `/home/user/.jiuwenswarm/` | 运行 `jiuwenswarm.init_workspace` 初始化工作区 | 从随源码提供的模板生成用户配置及工作区目录 |
| `/home/user/.jiuwenswarm/config/.env` | 写入 `MODEL_NAME=<--model>`、`API_BASE=<--api-base>`、`API_KEY=<--api-key-env 对应的值>`、`MODEL_PROVIDER=<--model-provider，默认 OpenAI>`、`ENDPOINT_PROFILE=<--endpoint-profile，默认 openai>` | 模型连接参数；文件权限设为 `600` |
| 同一个 `.env` | 写入 `AGENT_HTTP_ENABLED=true`、`AGENT_HTTP_HOST=0.0.0.0`、`AGENT_HTTP_PORT=8766` | 让宿主机通过 HTTP 向虚拟机内的 WorkSwarm 后端发送任务 |
| `/home/user/.jiuwenswarm/config/config.yaml` | 将 `trajectory_ui.enabled=true`、`trajectory_ui.diagnosis_enabled=true`、`react.subagents.cua_agent.enabled=true` | 记录轨迹、显示轨迹诊断入口并启用 CUA 子 Agent |
| 同一个 `config.yaml` | 将 `debug_trace.agent.enabled=true`、`debug_trace.agent.dump_enabled=true`、`debug_trace.code.enabled=true`、`debug_trace.code.dump_enabled=true` | 默认生成 Agent/Code debug dump |

`prepare_vm.py` **没有显式修改** `agent_observability.enabled`、`agent_observability.exporter`、`team_observability.exporter`、`debug_trace.*.otel_enabled` 或 `trajectory_ui.db_path`。这些字段来自初始化时的 WorkSwarm 配置模板；当前模板的 Agent/Team exporter 为 `file`。网页“实验功能 → 轨迹诊断”的开关对应上表中的两个 `trajectory_ui` 字段，开启后 Agent 可观测性会按运行时逻辑启用，但这不等同于准备脚本手动设置了 exporter。准备脚本也不会在快照中创建本次 OSWorld 的任务文件或 `hello.txt`。

完成安装后，`prepare_vm.py` 创建由 `--snapshot-name` 指定的 VirtualBox 快照，同时在宿主机写 `--state-file`（例如 `C:\Users\didan\Documents\ChatGPT\jiuwen\osworld_vm.json`），记录 OSWorld 路径、虚拟机路径、快照名、模型名和源码哈希，**不含 API key**。快照含虚拟机内的 API key；评测每次恢复它，因此快照之后在虚拟机内临时改动的配置会在下一次运行前消失。当前状态文件指向 `workswarm_debug_clean`，其名称和附加状态字段可能来自后续基线维护，不能仅凭该文件认定都是 `prepare_vm.py` 原始生成的设置。

## 可选检查：`probe_vm.py`

```powershell
& $python .\jiuwenswarm\osworld_setup\probe_vm.py `
  --state-file .\osworld_vm.json `
  --output .\osworld_probe_manual `
  --timeout 1200
```

它先恢复基线并确认 `/home/user/hello.txt` 不存在，再向 WorkSwarm 发送“打开记事本，输入 hello，保存到~/下面，文件名为 hello.txt。”。会话结束后先导出原始 WorkSwarm trace 和录像，随后检查文件内容是否为 `hello`，并写入 `probe.json`。退出时再次恢复基线，所以下一次不会继承 `hello.txt`。

## 运行一个 OSWorld case：`run_local.py`

```powershell
Set-Location C:\Users\didan\Documents\ChatGPT\jiuwen
$python = '.\OSWorld\.venv\Scripts\python.exe'
& $python .\jiuwenswarm\osworld_setup\run_local.py `
  --state-file .\osworld_vm.json `
  --output .\osworld_run_manual `
  --domain libreoffice_calc `
  --example-id 357ef137-7eeb-4c80-a3bb-0951f26a8aff `
  --timeout 2400 `
  --virtualbox-nat
```

当前工作区的 `Ubuntu0` 第一块网卡原为桥接 Wi-Fi。2026-11-09 实测其 DNS 无法解析 `api.openlux.ai`，WorkSwarm 的模型调用报 `APIConnectionError`；切换 NAT 后虚拟机可解析域名并完成 HTTPS 连接。`--virtualbox-nat` 会在每次恢复快照后、开机前将第一块网卡临时切为 NAT，第二块宿主机专用网卡保持不变；运行结束恢复原快照及其网卡设置。该选项仅适用于 VirtualBox，不修改快照中的 WorkSwarm 或模型配置。其他环境若桥接网络正常，可以省略。

`--state-file` 指向准备阶段保存的状态；`--domain` 和 `--example-id` 指向 `OSWorld/evaluation_examples/examples/<domain>/<id>.json`；`--output` 是本次结果的根目录。再次运行建议换新的输出目录，避免混入上次文件。`--timeout` 是 Agent 聊天阶段的秒数上限，**不是**包括启动虚拟机、任务设置、导出录像、生成复核视频和评分的总时限。

单次运行按顺序恢复配置好的快照、启动虚拟机、调用 OSWorld 的任务设置、启动 WorkSwarm 后端和桌面录像、通过后端 HTTP 创建 `agent` 模式会话并发送该 case 的原始 `instruction`。会话结束后停止录像并生成复核视频，再导出 WorkSwarm history、轨迹数据库、debug dump 和 OTel 每日 JSONL，最后调用 OSWorld `evaluate()` 并写入 `result.json`。脚本退出时会再次恢复快照。即使聊天失败，脚本也会尽量导出已经产生的 trace 和录像；此时没有成功评分文件。

OTel `file` exporter 按天把不同 session 的 span 写入虚拟机 `/home/user/.jiuwenswarm/.trace/traces-YYYY-MM-DD.jsonl`。导出器异步写盘，脚本等待一次批处理窗口后复制所有非空的每日文件到 case 目录，保留原文件名；因此 JSONL 可能包含同一天其他 session 的 span，不能直接当作单 session 文件。未找到或复制失败会写入 `trace-export-errors.json`，成功运行会将其视为导出失败。该路径使用默认 `traces_dir`；若修改 `agent_observability.traces_dir` 或 `team_observability.traces_dir`，需同步修改导出脚本。

网页前端没有参与运行。桌面录像展示虚拟机桌面；会话提示词、后端事件和工具轨迹分别保存在 `request.json`、`stream.sse`、`history.jsonl`、`debug-trace.txt` 和 `trajectory.sqlite3`，`review.mp4` 在桌面画面旁附上事件字幕。它不是 WorkSwarm CLI 终端的录屏。

## 串行运行及墙钟时延：`run_serial.py`

默认运行 `evaluation_examples/test_all.json` 中的**全部 368 个样本**，严格按清单中 domain 和 ID 的顺序逐个运行。每个样本独立恢复同一基线快照，失败后默认继续下一例：

```powershell
& $python .\jiuwenswarm\osworld_setup\run_serial.py `
  --state-file .\osworld_vm.json `
  --output .\osworld_all_20261109 `
  --timeout 2400 `
  --virtualbox-nat
```

`--manifest evaluation_examples/test_nogdrive.json` 可改跑该清单的全部 360 例；这不是默认的完整清单。默认 `--count 0` 表示全部，`--count N` 表示按清单顺序取前 N 例。运行前可生成完整计划并核对顺序，不启动虚拟机：

```powershell
& $python .\jiuwenswarm\osworld_setup\run_serial.py `
  --state-file .\osworld_vm.json `
  --output .\osworld_all_plan `
  --plan-only
```

只测一个指定 case 的完整墙钟时延，也可以用串行脚本包装单次运行：

```powershell
& $python .\jiuwenswarm\osworld_setup\run_serial.py `
  --state-file .\osworld_vm.json `
  --output .\osworld_timing_one `
  --case libreoffice_calc/357ef137-7eeb-4c80-a3bb-0951f26a8aff `
  --timeout 2400 `
  --virtualbox-nat
```

如需随机抽样 3 例，可同时指定 `--count` 和 `--seed`；脚本将抽中的样本按 `plan.json` 所列顺序串行执行：

```powershell
& $python .\jiuwenswarm\osworld_setup\run_serial.py `
  --state-file .\osworld_vm.json `
  --output .\osworld_serial_20261108 `
  --count 3 `
  --seed 20261108 `
  --timeout 2400 `
  --virtualbox-nat
```

没有 `--seed` 时，`--count 3` 表示清单的前三例。只预览随机抽样而不启动虚拟机：

```powershell
& $python .\jiuwenswarm\osworld_setup\run_serial.py `
  --state-file .\osworld_vm.json `
  --output .\osworld_plan_preview `
  --count 3 --seed 20261108 --plan-only
```

指定两个 case，按命令列出的顺序串行运行：

```powershell
& $python .\jiuwenswarm\osworld_setup\run_serial.py `
  --state-file .\osworld_vm.json `
  --output .\osworld_serial_fixed `
  --case libreoffice_calc/357ef137-7eeb-4c80-a3bb-0951f26a8aff `
  --case os/5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57 `
  --timeout 2400 --stop-on-error --virtualbox-nat
```

串行脚本使用相同的 Python 逐次启动 `run_local.py`，每个 case 都在开始和结束时恢复基线。`serial_results.jsonl` 记录逐 case 的开始/结束 UTC 时间、完整墙钟秒数、进程退出码和分数；`summary.json` 记录总时长、平均每 case 时长及成功/失败数量。墙钟时长覆盖 `run_local.py` 整个子进程，包括虚拟机启动和快照恢复、Agent 执行、文件导出及评分。默认某个 case 失败后继续下一个；`--stop-on-error` 在第一个失败处停止。输出根目录必须为空。

完整清单包含依赖外部网站、账户或凭据的任务；能否完成及得分取决于 OSWorld 任务本身的配置和网络。脚本会尝试清单中的每一例，并在 `summary.json` 中区分已完成和失败的数量。大量视频和轨迹会占用磁盘；长时间运行期间不要让其他任务同时操作该虚拟机。

## 保存的文件结构

```text
osworld_serial_20261108/
├── plan.json                         # OSWorld revision、manifest、seed、case 顺序
├── serial_results.jsonl              # 每个 case 的完整墙钟时延和结果
├── summary.json                      # 串行汇总；执行完后写入
└── <domain>/<example-id>/
    ├── driver.log                    # 串行脚本捕获的 run_local 控制台输出
    ├── initial.png                   # OSWorld 任务设置后的初始截图
    ├── session.json                  # WorkSwarm 会话创建响应
    ├── request.json                  # 发送给 Agent 的原始任务指令及参数
    ├── stream.sse                    # 后端流式事件原文
    ├── history.jsonl                 # WorkSwarm 会话历史
    ├── metadata.json                 # WorkSwarm 会话元数据
    ├── trajectory.sqlite3            # WorkSwarm 原始轨迹 span 数据库
    ├── debug-trace.txt               # WorkSwarm Agent debug dump
    ├── traces-YYYY-MM-DD.jsonl        # OTel file exporter 原始按日 span 文件；可能有多个日期
    ├── recording.json                # 录像开始时间和标识
    ├── desktop.mp4                   # 虚拟机桌面原始录像
    ├── review.ass                    # 复核视频字幕
    ├── review.mp4                    # 桌面加事件侧栏的复核视频
    └── result.json                   # OSWorld evaluate() 分数和会话信息
```

### 导出文件的来源与宿主机路径

下表中的 `<case>` 是宿主机的 `<--output>/<domain>/<example-id>/`。例如上面的单例命令对应 `C:\Users\didan\Documents\ChatGPT\jiuwen\osworld_run_manual\libreoffice_calc\357ef137-7eeb-4c80-a3bb-0951f26a8aff\`。`<session_id>` 来自 `<case>/session.json`；`<hash>` 为该 ID 的 SHA-256。虚拟机路径均为 Ubuntu 的 `/home/user/...`，**不是**宿主机 WorkSwarm 仓库下的 `.jiuwenswarm` 目录。

| 宿主机文件 | 虚拟机来源或生成过程 | 内容 |
| --- | --- | --- |
| `<case>/initial.png` | OSWorld `env.reset(task_config=example)` 返回的 `screenshot` 字节；并非复制虚拟机内现成图片文件 | 完成该 case 的环境设置后、Agent 开始前的桌面截图 |
| `<case>/session.json` | 宿主机请求虚拟机 WorkSwarm `POST /api/v1/sessions` 后保存响应 | 会话 ID 等创建结果 |
| `<case>/request.json` | 宿主机从 `OSWorld/evaluation_examples/examples/<domain>/<id>.json` 的 `instruction` 构造 | 发给 Agent 的原始提示词和会话参数 |
| `<case>/stream.sse` | 宿主机保存 `POST /api/v1/chat/completions` 的流式 HTTP 响应 | Agent 执行过程的服务端事件原文 |
| `<case>/history.jsonl` | `/home/user/.jiuwenswarm/agent/sessions/<session_id>/history.jsonl` | WorkSwarm 的该会话历史事件 |
| `<case>/metadata.json` | 同一虚拟机会话目录的 `metadata.json` | WorkSwarm 会话元数据 |
| `<case>/trajectory.sqlite3` | `/home/user/.jiuwenswarm/.trace/sessions/<hash 前两位>/<hash>.sqlite3` 经 SQLite `backup()` 备份到虚拟机 `/tmp/workswarm-trace.sqlite3` 后复制 | 该 session 的轨迹 span 数据库；不是整个 `.trace` 目录 |
| `<case>/debug-trace.txt` | `/home/user/.jiuwenswarm/.agent/traces/dump-agent-<session_id>.txt` | Agent debug dump；不来自 `.trace` 目录 |
| `<case>/traces-YYYY-MM-DD.jsonl` | `/home/user/.jiuwenswarm/.trace/traces-YYYY-MM-DD.jsonl`；按日逐个复制，可能有多个日期 | OTel file exporter 的原始 span 日志；同一天可能混有其他 session |
| `<case>/recording.json` | 宿主机在启动虚拟机内的 `ffmpeg` 时新建 | 录像 ID、启动请求与就绪时间，用于复核视频对齐 |
| `<case>/desktop.mp4` | 虚拟机 `ffmpeg` 录制 `:0.0` 到 `/tmp/workswarm-<recording_id>.mp4`，结束后复制 | Agent 运行期间的 Ubuntu 桌面录像，不是逐次模型截图 |
| `<case>/review.ass` | 宿主机根据 `request.json`、`stream.sse`、`recording.json` 新建 | 复核视频的提示词及事件字幕 |
| `<case>/review.mp4` | 宿主机用 `ffmpeg` 合成 `desktop.mp4` 和 `review.ass` | 桌面画面加事件侧栏的视频 |
| `<case>/result.json` | 宿主机调用 OSWorld `env.evaluate()` 后新建 | OSWorld 分数、会话 ID、case ID 和快照名；只在成功评分后出现 |
| `<case>/trace-export-errors.json` | 宿主机仅在轨迹文件缺失或复制失败时新建 | 各导出失败的具体原因；正常导出时不存在 |

`run_serial.py` 还会在 `<--output>/plan.json` 写清单、顺序、OSWorld revision 和抽样种子，在 `<--output>/serial_results.jsonl`、`summary.json` 写每例及汇总时延，并为每例生成 `<case>/driver.log`（`run_local.py` 的控制台输出）。这些都是宿主机新生成的文件，不是从虚拟机复制的。已有结果目录里的 `selection.json` 或 `artifacts.json` 如由人工实验额外创建，不属于当前 `run_local.py` 的标准导出清单。

直接运行 `run_local.py` 时，文件放在 `<output>/<domain>/<example-id>/`，没有 `plan.json`、`driver.log` 和串行汇总。`probe_vm.py` 直接把类似的会话/录像/轨迹文件及 `baseline.json`、`probe.json` 放在它的 `--output` 目录。发生异常时文件可能不齐；导出 trace 不完整时还会有 `trace-export-errors.json`，具体原因也见控制台输出或串行运行的 `driver.log`。`desktop.mp4` 使用 H.264、`yuv420p` 编码，供 Windows 播放器读取。

当前工作区曾使用上述 `libreoffice_calc` case 完成一次评测，分数为 `1.0`，结果保存在 `osworld_run_one/libreoffice_calc/357ef137-7eeb-4c80-a3bb-0951f26a8aff/`。2026-11-09 又使用 `--virtualbox-nat` 跑通随机抽中的 `vs_code/4e60007a-f5be-4bfc-9723-c39affa0a6d3`，分数为 `1.0`，文件位于 `osworld_run_random_20261109_vs_code_4e60007a_nat/vs_code/4e60007a-f5be-4bfc-9723-c39affa0a6d3/`；该次结果包含 `traces-2026-11-09.jsonl` 和 `trajectory.sqlite3`，两者各有 27 条该 session 的 span。这些事实只说明所列 case 和当前基线曾跑通；新的模型配置、源码或快照应重新验证。
