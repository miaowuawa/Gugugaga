# 奇谷米自动下单工具（Gugugaga）

奇谷米 App 的非官方单机终端工具。程序运行在本机，以账号为单位保存独立的设备指纹和登录态，支持商品查询、SKU/数量选择、实名购买人与收货地址管理、定时抢购、可选 DeepSeek 自动答题、代理切换、支付链接和本地/手机通知。

本项目是个人研究代码，接口依赖奇谷米 App 4.9.1 的行为，服务端或 App 更新后可能失效。请只使用自己的账号和合法授权的数据，并自行确认平台规则、购票规则及支付责任。

本程序旨在方便个人用户省去繁琐的答题步骤，快速自动化完成下单。
本程序不存在任何提高并发，未授权获取数据，对系统发起渗透测试/网络攻击/高并发请求的行为。
本程序只能进行自动化票务下单，不能进行任何内容发布删除，账号信息（除票务必须外）修改行为。
如侵犯您的合法权益，请联系miaowuawa【和谐】g mail.com（和谐换成@），我会在收到邮件的第一时间进行下架处理。

请注意合理使用，在本项目上添加违规功能，非法使用的后果由使用者本人自行承担。

## 环境要求

- Python 3.9 或更新版本；
- 能访问奇谷米；
- 一个奇谷米账号；
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

## Windows EXE 打包

在 **Windows** 上打开命令提示符，进入项目根目录后运行：

```bat
build_windows.bat
```

脚本会安装打包所需依赖，并生成单文件控制台程序
`dist\\QigumiGrabber.exe`。该程序保留终端界面；启动抢票时会自动打开一个
新的控制台窗口显示进度。`accounts/`、`task_configs/` 和 `config.json` 会创建
在 exe 同级目录，升级 exe 时请保留这些目录和文件。

也可在 GitHub Actions 的 **Build Windows executable** 工作流中手动运行构建，
随后从该次运行的 Artifacts 下载 `QigumiGrabber-windows`。PyInstaller 不能从
macOS/Linux 可靠地交叉编译 Windows exe，因此 Windows 本机构建或该工作流是
生成 Windows 版本的推荐方式。

程序会进入终端菜单。创建任务时会尝试打开新的终端窗口显示抢购进度；macOS 使用系统 Terminal，Linux 使用 `x-terminal-emulator`。如果终端自动启动失败，程序会打印一条可手动执行的命令，复制该命令即可继续运行任务。

## 第一次使用

1. 启动程序后进入“账号管理”。
2. 选择“注册/登录（短信验证码）”，填写手机号、区号和模拟设备类型，发送并输入短信验证码。
3. 回到账号管理检查登录状态；需要时可以维护实名购买人和收货地址。
4. 进入“任务配置”→“新建任务配置”，无需选择账号，直接粘贴商品分享链接或输入 `goods_id`。
5. 选择 SKU、购买数量，并按商品类型填写所有账号共用的场馆、场次或门店。程序会自动读取商品详情中的开售时间；需要时可手动覆盖。
6. 商品需要答题时，选择开售前或开售后答题，并设置本地答题材料 `txt` 文件的位置。DeepSeek Key 只需在“设置”中保存一次。
7. 设置代理、支付宝支付页和抢到提醒后，保存任务配置文件。
8. 之后启动任务先选择配置文件和账号，再单独选择该账号的地址、实名购买人及核销手机号；系统会先根据该账号判断是否真的需要答题，只有需要时才读取材料。
9. Server酱³ 是全局通知：设置全局 SendKey 后，任一成功任务都会自动推送。抢购成功后程序也会显示订单号和支付链接；如金额大于 0，请按平台要求及时完成支付。

## 测试拉题

主菜单的“测试拉题（按商品 ID 直接拉取）”可在选择已登录账号后，输入商品分享链接或 `goods_id`，直接请求 `answerList` 并展示题干、选项和对应 ID。它不读取商品详情，也不检查商品当前的答题开关或答题时机；该功能只查看题目，绝不会提交答案或创建订单。

## 设置说明

主菜单的“设置”可维护：

- DeepSeek API Key、Base URL 和模型；
- 默认刷新/下单延迟、最大重试次数和答题完成时间（默认 6 秒）；
- 默认支付方式；
- 巨量代理提取链接；
- Server酱³ SendKey。

这些内容会写入本地 `config.json`。配置文件包含密钥，已被 `.gitignore` 排除，绝不能提交或发给他人。也可以通过环境变量 `QIGUMI_GRABBER_CONFIG` 指定一个单独的配置文件路径。

自动答题会把任务配置所指向的材料发送到配置的模型服务，请不要放入不应外传的个人信息、证件信息或未获授权的内容。代理和 Server酱³ 都是可选功能；未配置 Server酱³ 时不会发送手机通知。

如需 Windows TTS 或音频提示，可在虚拟环境中额外安装：

```bash
python -m pip install pywin32 playsound3
```

## 本地数据与安全

账号登录后，程序会生成 `accounts/` 下的账号环境 JSON，里面可能包含手机号、设备标识、用户信息和登录 Token；不要上传、截图或共享这些文件。任务配置保存在 `task_configs/`，其中包含商品、票档和答题材料路径，但不保存账号私有的地址、实名购买人或手机号；临时运行参数不再包含 DeepSeek Key 或答题材料正文。

以下本地内容明确不会提交：

- `accounts/`、`task_configs/`、`config*.json`、`.env*`、密钥/证书文件；
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

## 贡献

合理贡献都接受。请确保功能已通过测试，并考虑实际使用。
合理使用AI Agent，禁止AI拉屎式贡献（指没有实际作用，甚至极其离谱的贡献）

不接受以下类型：
1.对服务端进行渗透测试，网络攻击，高并发请求，破坏性请求
2.自动支付等涉及资金交易流程的功能
3.刷抢优惠，批量抢购，批量注册等黑灰产功能
4.获取未授权数据，无视平台限制发起请求

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=miaowuawa/Gugugaga&type=Date)](https://www.star-history.com/#miaowuawa/Gugugaga&Date)
