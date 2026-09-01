# 奇谷米单机抢票器（Gugugaga）

奇谷米 App 的非官方单机终端工具。程序运行在本机，以账号为单位保存独立的设备指纹和登录态，支持商品查询、SKU/数量选择、实名购买人与收货地址管理、定时抢购、可选 DeepSeek 自动答题、代理切换、支付链接和本地/手机通知。

本项目是个人逆向研究代码，接口依赖奇谷米 App 4.9.1 的行为，服务端或 App 更新后可能失效。请只使用自己的账号和合法授权的数据，并自行确认平台规则、购票规则及支付责任。

## 发布内容

仓库只发布单机 Python 客户端及其运行说明：

- `account.py`、`client.py`、`crypto.py`、`goods.py`、`grabber.py` 等单机版源码；
- `requirements.txt`：核心 Python 依赖；
- `signer.py`：为旧脚本保留的兼容签名辅助函数；
- `.gitignore`：阻止本地凭据、账号环境、答题材料、缓存和构建产物进入仓库。

平台服务端、Go 节点、APK、jadx 工具、缓存和本地数据不属于本单机版发布内容。

## 环境要求

- Python 3.9 或更新版本；
- 能访问奇谷米接口；
- 一个可以接收短信的奇谷米账号；
- 如使用自动答题，需要单独可用的 DeepSeek API Key 和对应答题材料；
- Windows 的 TTS 需要 `pywin32`，音频播放可选 `playsound3`。不使用这些通知方式时无需安装。

## 安装与启动

当前代码按 `qigumi_grabber` 包名组织，克隆时请保留这个目录名，并从它的父目录启动模块。

### macOS / Linux

```bash
git clone https://github.com/miaowuawa/Gugugaga.git qigumi_grabber
cd qigumi_grabber
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cd ..
python -m qigumi_grabber
```

### Windows PowerShell

```powershell
git clone https://github.com/miaowuawa/Gugugaga.git qigumi_grabber
cd qigumi_grabber
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cd ..
python -m qigumi_grabber
```

如果系统默认的 `python` 不是目标版本，请把命令中的 `python` 替换成 `python3`、`py` 或 Python 的完整路径。

程序会进入终端菜单。创建任务时会尝试打开新的终端窗口显示抢购进度；如果当前系统没有 `x-terminal-emulator` 或终端自动启动失败，程序会打印一条可手动执行的命令，复制该命令即可继续运行任务。

## 第一次使用

1. 启动程序后进入“账号管理”。
2. 选择“注册/登录（短信验证码）”，填写手机号、区号和模拟设备类型，发送并输入短信验证码。
3. 回到账号管理检查登录状态；需要时可以维护实名购买人和收货地址。
4. 进入“新建抢票任务”，粘贴商品分享链接或输入 `goods_id`。
5. 选择 SKU、购买数量，并按商品类型填写场馆、场次、门店、核销手机号、地址或实名购买人。
6. 设置刷新延迟、下单延迟、最大重试次数和开抢时间。留空开抢时间表示立即开始。
7. 商品需要答题时，选择开售前或开售后答题，输入 DeepSeek Key，并从本地 `txt` 文件读取材料或直接粘贴材料。
8. 确认代理、支付宝支付页、提示音和 Server酱³ 通知设置，最后确认启动。
9. 抢购成功后程序会显示订单号和支付链接；如金额大于 0，请按平台要求及时完成支付。

## 设置说明

主菜单的“设置”可维护：

- DeepSeek API Key、Base URL 和模型；
- 默认刷新/下单延迟与最大重试次数；
- 默认支付方式；
- 巨量代理提取链接；
- Server酱³ SendKey。

这些内容会写入本地 `config.json`。配置文件包含密钥，已被 `.gitignore` 排除，绝不能提交或发给他人。也可以通过环境变量 `QIGUMI_GRABBER_CONFIG` 指定一个单独的配置文件路径。

自动答题会把你选择的材料发送到配置的模型服务，请不要放入不应外传的个人信息、证件信息或未获授权的内容。代理和 Server酱³ 都是可选功能，不配置时任务可以直连并关闭手机通知。

如需 Windows TTS 或音频提示，可在虚拟环境中额外安装：

```bash
python -m pip install pywin32 playsound3
```

## 本地数据与安全

账号登录后，程序会生成 `accounts/` 下的账号环境 JSON，里面可能包含手机号、设备标识、用户信息和登录 Token；不要上传、截图或共享这些文件。程序还可能在系统临时目录生成任务参数 JSON，其中可能包含答题材料和 API Key，任务结束后请按需清理。

以下本地内容明确不会提交：

- `accounts/`、`config*.json`、`.env*`、密钥/证书文件；
- `lq.txt`、`lq_ids.txt` 等用户答题材料或 ID 列表；
- `__pycache__/`、日志、临时文件和 macOS 元数据；
- `Publish.apk`、`jadx-1.5.6/`；
- `grab_platform/`、`grabber_node_go/` 等平台/节点代码。

提交前可以检查待提交清单：

```bash
git status --short
git diff --cached --stat
git diff --cached --name-only
```

如果某个密钥曾经被误提交，不能只删除当前文件，还应立即撤销/轮换该密钥，并从 Git 历史中清理。

## 常见问题

### `ModuleNotFoundError: No module named 'qigumi_grabber'`

请确认克隆目录名是 `qigumi_grabber`，并在该目录的父目录运行 `python -m qigumi_grabber`，不要在包目录内部运行同一条模块命令。

### `Crypto` 或 `rich` 导入失败

确认虚拟环境已激活，然后重新执行：

```bash
python -m pip install -r qigumi_grabber/requirements.txt
```

### 登录或商品查询失败

先检查网络、手机号/区号、短信验证码和账号登录状态。奇谷米接口、设备风控和商品状态都可能变化；网络异常时不要反复提交敏感信息。

### 自动抢购窗口没有打开

使用程序输出的“可手动运行”命令启动 `grab_window.py`。在 macOS/Linux 上需要一个可调用的终端程序；在 Windows 上建议使用 PowerShell 或 Windows Terminal，并确认 Python 虚拟环境路径正确。

### 不使用 DeepSeek 可以吗？

可以。无需答题的商品不需要配置 DeepSeek；需要答题的商品如果不提供有效 Key 和材料，任务会在答题阶段失败。

## 验证源码

不发起登录、下单或外部 API 请求的基础检查：

```bash
python -m compileall -q qigumi_grabber
```

真实登录、商品查询、DeepSeek、代理和通知测试会访问外部服务，请在确认账号、网络和平台规则后再进行。
