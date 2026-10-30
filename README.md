# Pycl (PyLauncher)

《我的世界》**Java 版**启动器，  
使用 **Python + PySide6** 编写，跨 Windows / macOS / Linux。当前版本 **1.0.0**。

代码按「核心层 / 界面层」严格分离，`launcher/core` 不依赖任何 GUI 组件，  
可以单独被命令行工具或测试脚本调用。

> **免责声明**：本项目为第三方开源启动器，按「原样」（AS IS）提供，与 Mojang Studios、
> Microsoft 及其关联公司无任何隶属、授权或合作关系。作者不对因使用或无法使用本项目
> 所导致的任何直接或间接损失承担责任，请自行评估风险并承担全部后果。详见
> [第 8 节「免责声明」](#8-免责声明) 与 [DISCLAIMER.md](DISCLAIMER.md)。

---

## 1. 功能清单

| 模块      | 能力                                                                                                                    |
| ------- | --------------------------------------------------------------------------------------------------------------------- |
| 版本管理    | 拉取官方/镜像版本清单；下载并安装任意正式版、快照版、Beta、Alpha；校验与修复缺失文件；删除版本                                                                  |
| Java 环境 | 自动扫描注册表、`JAVA_HOME`、`PATH`、各发行版默认目录；真实执行 `java -version` 解析版本号与位数；按版本需求（1.17→16、1.18→17、1.20.5→21）择优；支持手动指定           |
| 账户      | **离线登录**（按 `OfflinePlayer:` 规则生成确定性 UUID）；**微软正版登录**（完整设备码流程：Device Code → XBL → XSTS → Minecraft → 角色信息，支持刷新令牌）      |
| 加载器     | **Fabric** 由本启动器自行组装版本 JSON 并下载其库；**Forge** 调用官方 Installer（`--installClient`），失败时回退为解包安装                              |
| Mod 管理  | 扫描实例 `mods` 目录；解析 `fabric.mod.json` / `META-INF/mods.toml` / `mcmod.info`；启用 / 禁用（`.jar.disabled`）、导入、删除、批量切换；**内置 Modrinth 在线模组库，搜索后一键下载安装**   |
| 启动      | 完整实现 Mojang 参数规范：`inheritsFrom` 继承链合并、rules 求值、占位符替换、classpath 与 natives 拼装、内存/窗口/包装命令/自定义 JVM 与游戏参数；实时日志窗口、进程托管与强制结束 |
| 多版本隔离   | 每个版本可有独立实例目录（`isolates/<id>` 存放存档、Mod、配置、options.txt），也可共用公共目录；实例可单独指定 Java、内存、分辨率与参数                                 |
| 持久化     | 配置、实例、账户全部落盘为 JSON，写入即保存；文件损坏时自动备份并回退默认值                                                                              |
| 材质包/光影 | 按实例扫描 `resourcepacks` / `shaderpacks`，启用 / 禁用 / 删除 / 本地导入，同样支持从 Modrinth 在线安装                                                    |
| 服务器联机 | 服务器列表持久化 + Server List Ping（MOTD / 人数 / 延迟 / 版本）；**局域网组播发现**（224.0.2.60:4445）；**跨网联机**：联机码 + 内置 TCP 中继（带口令鉴权）          |
| 加载性能   | 分页懒加载（首屏只建首页，构造 846ms→13ms）、Java 探测后台化与缓存（设置页 681ms→17ms）、版本清单**缓存秒开 + 后台静默刷新**（15ms 出列表）、多源**并发**取最快响应、渲染去重（脏标记 + 0.5s 节流） |
| 稳定性     | 动画层可中断无残留（快照叠加层复用、控件不会被留在透明状态）；退出时统一收尾后台线程与 IO 线程池；分页懒加载 + 渲染节流，连续切换不涨内存 |
| 稳定性     | 动画层可中断无残留（快照叠加层复用、控件不会被留在透明状态）；退出时统一收尾后台线程与 IO 线程池；分页懒加载 + 渲染节流，连续切换不涨内存 |
| 加载兜底   | 所有列表都有「加载中」占位；版本列表失败时给出原因 + 「重试」按钮，有缓存时优先显示缓存并标注「离线缓存」，绝不出现无反馈白屏                     |
| 加载性能   | 分页懒加载（首屏只建首页，构造 846ms→13ms）、Java 探测后台化与缓存（设置页 681ms→17ms）、版本清单**缓存秒开 + 后台静默刷新**（15ms 出列表）、多源**并发**取最快响应、渲染去重（脏标记 + 0.5s 节流） |
| 稳定性     | 动画层可中断无残留（快照叠加层复用、控件不会被留在透明状态）；退出时统一收尾后台线程与 IO 线程池；分页懒加载 + 渲染节流，连续切换不涨内存 |
| 稳定性     | 动画层可中断无残留（快照叠加层复用、控件不会被留在透明状态）；退出时统一收尾后台线程与 IO 线程池；分页懒加载 + 渲染节流，连续切换不涨内存 |
| 加载兜底   | 所有列表都有「加载中」占位；版本列表失败时给出原因 + 「重试」按钮，有缓存时优先显示缓存并标注「离线缓存」，绝不出现无反馈白屏                     |
| 安全防火墙 | 出站域名白名单、强制 HTTPS、SSRF（内网地址）拦截；下载目标限制在游戏目录内；解压拦截 zip slip、符号链接与压缩包炸弹；单文件体积上限                          |
| 下载源     | 官方源与 BMCLAPI 镜像一键切换；**主源失败自动回退备用源**，安装前还会先做源健康检查并自动切到当前真能连通的那个（清单、版本 JSON、JAR、库、资源、Fabric meta 均已接入） |
| 桌面快捷方式 | 按运行平台生成 Windows `.lnk` / Linux `.desktop` / macOS `.app`；内置尺寸合规的多分辨率图标（`.ico` / `.icns` / hicolor PNG）；**首次运行自动创建且幂等不重复**，重复创建提示覆盖、失败分类报错、无权限自动降级为启动脚本 |
| 界面体系    | 统一设计令牌（配色 / 间距 / 圆角 / 字号 / 尺寸）+ 单一 QSS 主题；卡片化布局，表格与列表空状态占位，hover / 选中 / 禁用 / 加载四种交互态齐全，自适应常见分辨率                       |

---

## 2. 快速开始

```bash
pip install -r requirements.txt      # PySide6 + requests
python main.py                       # 启动图形界面
```

其它入口：

```bash
python main.py --selftest            # 核心自检，120 项断言，无需界面与网络
python main.py --game-dir D:/mc      # 指定游戏目录
python main.py --mirror official     # 使用官方源
python main.py --launcher-home D:/pl # 便携模式，配置写在指定目录
python main.py --no-gui              # 仅初始化核心
```

桌面快捷方式（也可在「设置 → 桌面快捷方式」里点按钮完成）：

```bash
python main.py --create-shortcut              # 在当前平台桌面创建
python main.py --create-shortcut --shortcut-dir D:/tmp/icons   # 指定输出目录（便于测试）
python main.py --create-shortcut --force      # 已存在时直接覆盖
```

测试：

```bash
python tests/smoke_ui.py             # 界面冒烟测试（5 个页面 + 主题/快捷方式卡片检查）
python tests/smoke_shortcut.py       # 快捷方式端到端测试（真实写盘并行读回校验）
python tests/smoke_runtime.py        # 运行时健壮性回归（线程取消 / Java 判定 / 下载 / 快捷方式幂等）
python tests/stress_sim.py 1200      # 沙箱随机模拟压测（1200 次迭代，19 类异常场景）
python tests/stress_sim.py 1200      # 沙箱随机模拟压测（1200 次迭代，19 类异常场景）
python tests/e2e_launch.py 1.20.4 15 # 真实下载安装并启动游戏 15 秒
python tests/screenshot.py           # 开发用：渲染各页面截图到 .shots/
```

---

## 3. 目录结构

```
mc-launcher/
├── main.py                      命令行入口（参数解析、自检、启动 GUI）
├── requirements.txt
├── launcher/
│   ├── core/                    —— 与界面无关的核心层
│   │   ├── constants.py         镜像源定义、启动器信息
│   │   ├── paths.py             目录布局（游戏目录 / 版本目录 / 隔离目录 / natives）
│   │   ├── config.py            全局配置 + 实例（Instance）模型与持久化
│   │   ├── utils.py             日志、SHA1、安全文件名、zip 解压（含 native 扁平化）
│   │   ├── http.py              带重试的会话、多线程下载器、校验与回退
│   │   ├── rules.py             version.json 的 rules 求值、Maven 坐标转换、natives 识别
│   │   ├── java.py              跨平台 Java 探测、版本解析、按需择优
│   │   ├── auth.py              离线账户 + 微软设备码登录全流程
│   │   ├── manifest.py          远程版本清单、本地版本扫描、继承链解析
│   │   ├── installer.py         下载任务收集、版本安装、完整性校验
│   │   ├── modloader.py         Fabric 组装 / Forge Installer 调用
│   │   ├── mods.py              Mod 扫描、元数据解析、启停与导入
│   │   ├── launcher_core.py     参数构建、占位符替换、进程管理
│   │   ├── netguard.py          软件防火墙：域名白名单 / HTTPS / 体积与路径约束
│   │   ├── modrinth.py          Modrinth 官方接口：搜索与下载模组 / 光影 / 材质包
│   │   ├── resources.py         材质包与光影管理（扫描、启停、导入、在线安装）
│   │   ├── servers.py           服务器列表与 Server List Ping
│   │   ├── netplay.py           局域网组播发现 + 联机码 + 跨网 TCP 中继
│   │   ├── shortcut.py          跨平台桌面快捷方式（lnk / desktop / app bundle + 降级脚本）
│   │   └── selftest.py          核心自检套件
│   └── ui/                      —— PySide6 界面层
│       ├── app.py               主窗口：导航、分页容器、状态栏、进程托管
│       ├── theme.py             设计令牌 + QSS 主题 + 字体/翻译安装
│       ├── icons.py             纯 Qt 绘制的应用 logo 与多尺寸图标资源生成
│       ├── shortcut_flow.py     快捷方式「界面 ↔ 核心」适配层（目标定位、名称、图标）
│       ├── workers.py           通用后台线程（进度 / 消息 / 结果 / 异常）
│       ├── widgets.py           Card / Badge / BusySpinner / LoadingButton / EmptyState / PathPicker / ProgressDialog / LogWindow
│       ├── helpers.py           打开目录等小工具
│       └── pages/               首页 / 版本 / 账户 / Mod / 材质与光影 / 服务器联机 / 设置 七个分页
└── tests/
    ├── smoke_ui.py              界面冒烟测试
    ├── smoke_shortcut.py        快捷方式端到端测试
    ├── smoke_runtime.py         运行时健壮性回归测试
    ├── screenshot.py            开发用截图脚本
    └── e2e_launch.py            端到端安装 + 启动测试
```

数据存放位置（默认在当前工作目录）：

```
<launcher_home>/config.json     全局配置与全部实例设置
<launcher_home>/accounts.json   账户数据库
<launcher_home>/logs/launcher.log  运行日志（超过 2MB 自动截断）
<launcher_home>/icons/          运行时生成的 SpinBox 箭头与快捷方式图标资源
<game_dir>/versions/<id>/       版本目录（json / jar / natives）
<game_dir>/libraries/           共享依赖库
<game_dir>/assets/              资源索引与资源对象
<game_dir>/isolates/<id>/       隔离实例的存档、Mod、设置
```

---

## 4. 关键实现说明

### 4.1 版本安装流程

1. 从版本清单取得版本 JSON（镜像优先，失败回退官方）；
2. 下载客户端 JAR（镜像 `/version/<id>/client`，失败回退 `piston-data`）；
3. 按 `rules` 过滤依赖库，展开 `inheritsFrom` 链，去重后并发下载，逐个校验 SHA1；
4. **识别并解压 natives**：
   - 1.19 起 LWJGL native 是**独立库条目**，classifier 写在 Maven 坐标里  
     （`org.lwjgl:lwjgl-glfw:3.3.2:natives-windows`），按架构精确匹配；
   - 1.18 及更早是主条目挂 `natives` 映射 + `downloads.classifiers`。
   两类都会解压到 `versions/<id>/natives`，并且因为 LWJGL 3 的 jar 内部是  
   `windows/x64/org/lwjgl/...` 这类层级结构，**除保留原结构外还会在根目录  
   额外放一份扁平副本**，确保 `System.loadLibrary` 一定找得到；
5. 下载 log4j 配置与资源索引，再并发拉取资源对象（哈希即路径与校验值）。

### 4.2 启动参数构建

`resolve_version()` 沿 `inheritsFrom` 链自底向上合并：主类取最近的非空值、  
`arguments.jvm/game` 按「祖父 → 父 → 子」顺序拼接（子版本覆盖父版本）、  
classpath 按「子 → 父」排列（保证子类库优先）、客户端 JAR 定位到链上真实存在的那个。  
1.12 及更早没有 `arguments` 的版本会从 `minecraftArguments` 拆分。

`build_command()` 依次追加：内存参数 → GC 与编码 → `-Djava.library.path` →  
版本自带 JVM 参数 → 用户额外 JVM 参数 → 主类 → 游戏参数 → 窗口/服务器/额外参数，  
最后替换全部 `${...}` 占位符。首页的「查看启动命令」按钮可以看到最终命令行。

### 4.3 多版本隔离与配置持久化

每个版本对应一条 `Instance` 记录。字段为空即「跟随全局设置」，  
因此既能一键全局统一，也能针对某个 1.7.10 Forge 单独指定 Java 8 和 2G 内存。  
`isolated=True` 时工作目录切到 `isolates/<id>/`，存档、Mod、截图互不干扰。

### 4.4 微软登录

需要你自己注册一个 Azure 应用（**本启动器不内置任何第三方 Client ID**）：

1. Azure 门户 → 应用注册 → 新注册 → 「任何 Microsoft 账户」；
2. 重定向 URI 填 `https://login.microsoftonline.com/common/oauth2/nativeclient`；
3. 「身份验证 → 高级设置」中启用「允许公共客户端流」；
4. 把应用程序（客户端）ID 填进「设置 → 微软登录」。

之后点击「使用微软账号登录」，按提示在浏览器输入设备码即可。

### 4.5 桌面快捷方式

核心逻辑在 `launcher/core/shortcut.py`，**不依赖任何 GUI**，界面只通过
`launcher/ui/shortcut_flow.py` 取「要指向什么」，平台细节对界面完全透明。

| 平台      | 产物                            | 写入方式                                                                    |
| ------- | ----------------------------- | ----------------------------------------------------------------------- |
| Windows | `<名称>.lnk`                    | `ctypes` 直接调 `IShellLinkW` + `IPersistFile`（不经过 PowerShell / 外部依赖）            |
| Linux   | `<名称>.desktop`                | XDG Desktop Entry，追加可执行位，并尝试 `gio set metadata::trusted true` 解除「未信任」提示 |
| macOS   | `<名称>.app`                    | 完整 bundle：`Contents/MacOS/` 外壳脚本 + `Resources/AppIcon.icns` + `Info.plist` |

桌面目录解析：Windows 走 `SHGetKnownFolderPath(FOLDERID_Desktop)`，
因此 OneDrive 重定向后的真实桌面也能命中；Linux 依次读 `~/.config/user-dirs.dirs`、
`~/Desktop`、`~/桌面`；都拿不到时引导用户另选目录。

图标由 `launcher/ui/icons.py` 用 `QPainter` 绘制同一枚 logo 后按需导出：

- Windows → 单个 `.ico`，内含 **16 / 24 / 32 / 48 / 64 / 128 / 256** 七档 PNG；
- macOS → `.icns`，含 `icp4/icp5/icp6/ic07/ic08/ic09` 六种 PNG 块（含 HiDPI）；
- Linux → `icons/hicolor/<size>x<size>/apps/` 标准 PNG 树 + `Desktop Entry` 里的绝对路径。

因此不依赖 Pillow / QtSvg，也不需要额外二进制资源文件。

健壮性处理：

| 情况          | 行为                                                                 |
| ----------- | ------------------------------------------------------------------ |
| 快捷方式已存在     | 抛 `EXISTS`，界面弹「已存在，是否覆盖？」并展示**旧快捷方式指向的目标**，确认后才重建                     |
| 桌面不可写 / 不存在 | 归为 `PERMISSION_DENIED` / `DESKTOP_NOT_FOUND`，提示用户**另选一个文件夹**创建      |
| COM 组件不可用   | 自动降级为桌面启动脚本（`.cmd` / `.command` / `.sh`），界面明确提示「已降级」而非假装成功         |
| 目标已被卸载      | `validate_spec()` 提前校验解释器与 `main.py` 是否存在，缺失时报 `TARGET_MISSING` 并给出修复建议 |

另外，快捷方式的「目标」里固定写入 `--launcher-home <绝对路径>`，
保证双击时无论工作目录在哪，读到的都是同一份配置；Windows 优先指向
`pythonw.exe` 以免弹出控制台窗口。若进程仍然崩溃，`main.py` 的顶层异常守卫
会把堆栈写进 `logs/crash.log` 并弹出错误框（无控制台时也能看见）。

### 4.6 界面设计系统

`launcher/ui/theme.py` 先定义**设计令牌**再生成样式，所有页面只准用令牌，杜绝散落硬编码：

```
COLORS     底色 / 卡片 / 主色 / 强调 / 危险 / 文本三级 / 边框 / 悬停 …
SPACE      xs=4 → xxxl=24 的等距间距刻度
RADIUS     sm=6 / md=10 / lg=14 / pill=999
FONT_SIZE  xs=11 → xxl=20 的字号阶梯
METRICS    窗口最小尺寸、侧栏宽度、控件高度 32/40/48、表格行高 36、表头 34
```

`QSS` 由令牌拼装，统一管控：窗口 / 卡片 / 侧栏导航 / 按钮五种变体
（primary、accent、danger、quiet、inTableCell）/ 输入框 / 表格 / 分组框 / 标签页 /
进度条 / 滚动条 / 状态栏。四种交互态全部显式定义：

- **hover**：按钮、导航项、标签页、表格行、下拉项、输入框、滚动条滑块均有反馈；
- **选中**：导航用 checked 态、表格行保留 `[selected:!active]` 使失焦时仍可辨认；
- **禁用**：每种按钮变体单独写了低对比 but 仍可读的配色，并在 `QPalette` 里统一 disabled 文本色；
- **加载**：`BusySpinner`（Qt 自绘旋转弧，QSS 无法做动画）用于状态栏、下载/安装进度框、
  微软登录等待，`LoadingButton` 在忙碌时切换文案并禁用点击，结束后自动还原原标题。

为适配常见分辨率，主窗口启动时按屏幕可用区计算初始尺寸并居中，
低于阈值时切到「小屏模式」（更小的窗口下界与控件间距），
配合 `QScrollArea` + 统一的 `EmptyState` 空状态（内容宽度上限 460px），
在 1366×768 到 4K 之间都不会出现挤压或截断。

---

## 5. 故障排查与已修复缺陷

| 现象 | 根因 | 现在的处理 |
| --- | --- | --- |
| 启动 / 运行时随机崩溃 | ① 取消下载或登录时调用了 `QThread.terminate()`，强杀正在跑 Python 字节码的线程；② `Worker` 对象（线程还在跑）被 Python 回收；③ 游戏日志/退出回调在读线程里直接操作 Qt 控件 | 全部改为**协作式取消**（`request_cancel` + 任务自查 `should_cancel`）；线程由 `start_worker()` 托管生命周期；跨线程一律走 Qt 信号回到主线程，游戏日志 decoding 也在子进程侧完成 |
| 点「启动游戏」没反应 / 闪退 | 所需的 Java 版本靠版本号字符串猜，`26.3` 这类现代命名与整合包被判成 Java 8，于是用 Java 21 去跑要求 Java 25 的游戏，0.5 秒就 `UnsupportedClassVersionError` | 改为**优先读取版本 JSON 的 `javaVersion.majorVersion`**；启动前若版本不足会明确告知「需要 Java N / 本机最高 M」并给出可执行建议 |
| 游戏日志是乱码 | Windows 上 Java 输出跟随系统 ANSI 代码页（GBK），代码里硬写成 UTF-8 | 二进制读取 + **按行智能解码**（UTF-8 优先、控制台编码兜底），中文路径与设备名均可正常显示 |
| 下载无法开始 / 全线失败 | 默认源 BMCLAPI 经常整体不可用（SSL EOF / 节点重定向失败） | 安装前做**源健康检查并自动切换到当前可用源**；每个下载任务额外挂一条**跨源备用地址**；失败会在进度框里列出具体文件与原因 |
| 下载中断残留 / 校验不通过 | 旧实现在异常时不清理 `.part`，也不校验实际长度 | 失败自动删除半成品；按 `Content-Length` 比对完整性；SHA1 不符即刻换源重试 |
| 老版本（1.7.10 等）必然报「必需文件下载失败」 | `lwjgl-platform` 这类依赖只有 `downloads.classifiers`、没有主构件，代码硬拼出的主 jar 地址一定 404 | 无主构件的依赖**不再生成主构件任务**（由其 native classifier 单独处理），classpath 也同步剔除这些不存在的条目 |
| 安装后桌面上没有图标 | 只有手动点按钮才会创建 | **首次运行自动创建**（可用 `shortcut.auto_create` 关闭或设环境变量 `PYLAUNCHER_NO_AUTOSHORTCUT=1` 禁用），且做了幂等保护：已存在不重建、创建过一次就不再打扰 |

排查入口：运行时详情看 `<launcher_home>/logs/launcher.log`；未处理异常的堆栈会写进
`logs/crash.log` 并弹出错误框（用 `pythonw.exe` 双击启动时没有控制台，这是唯一可见途径）。

---

## 6. 已知限制

- **Forge** 走官方 Installer，安装期间需要联网且耗时较长；1.12.2 及更早版本的  
  Installer 在部分环境下需要图形界面，此时会回退为解包安装（不执行预处理步骤）。
- 不支持自动下载 Java（仅检测与手动指定）。版本所需的 Java 本机没有时，  
  启动前会明确提示需要安装的版本号，但不会代为下载。
- 不支持 Mod 的在线搜索与下载（仅本地文件管理）。
- 快照 / 老版本的资源索引走官方端点，因为多数镜像未缓存这些文件。

---

## 7. 自检覆盖范围

`python main.py --selftest` 在临时目录里构造最小版本夹具（含 `inheritsFrom` 子版本  
与 1.12 风格 `minecraftArguments` 版本），断言 **86** 项，分十个部分：

**版本与安装**：规则求值、Maven 坐标、镜像回退、继承链合并顺序、  
classpath 与 JAR 定位、native 平台过滤、新老两代 natives 识别、native 解压扁平化。

**环境与账户**：离线 UUID 稳定性、配置往返与隔离目录、Java 版本字符串解析与本机探测。

**启动**：启动命令（内存 / 主类 / 占位符 / 窗口 / 额外参数 / 全屏）。

**桌面快捷方式**（第 `[9]` 节，纯逻辑、不写真实桌面）：平台判定、各平台文件名后缀与
标签、图标资源键、Windows / Desktop Entry 两套参数转义规则、命令行含解释器与
`--launcher-home`、`validate_spec` 正常与缺文件两种分支、桌面目录可解析、
不存在的快捷方式查询返回 `None`、配置默认值含 `shortcut` 段、降级脚本内容正确。

**启动健壮性与下载源**（第 `[10]` 节，回归用例）：Java 需求取自 `javaVersion.majorVersion`
与按版本号兜底两条路径、现代/传统版本命名的解析、Java 版本不足的提示文案、
版本扫描不会把 `usercache.json` 这类文件当成版本、下载任务自动获得跨源备用地址。

界面与运行时侧另有三个独立测试：

- `tests/smoke_ui.py`：逐个加载五个页面，校验窗口图标、主题生效、导航项、空状态、
  中文按钮文案、禁用态原因提示；
- `tests/smoke_shortcut.py`：真实写盘创建再读回校验目标/参数/工作目录/描述/图标，
  覆盖重复创建、覆盖、查找与删除、强制降级、无权限与目标缺失的错误分类；
- `tests/smoke_runtime.py`：运行时健壮性回归——源码里不再出现危险的线程强杀、
  协作式取消能让线程自然退出、游戏日志回调确实运行在主线程、Java 需求判定、
  源健康检查与真实小文件下载、无主构件的老依赖不再产生 404 任务、
  桌面快捷方式首次自动创建且幂等。

---

## 8. 免责声明

本项目（PyLauncher）是**第三方开源启动器**，按「原样」（AS IS）提供。

- **无授权与无担保**：作者对软件的适用性、稳定性、安全性、无侵权性不作任何明示或
  暗示的担保；软件可能包含缺陷，运行中可能出现崩溃、界面异常、下载失败或数据损坏。
- **责任豁免**：对于因使用或无法使用本项目而产生的任何直接、间接、偶然、特殊或
  后果性损失（包括但不限于游戏存档损坏、文件丢失、系统故障、账号异常、数据泄露、
  时间与商业损失），作者不承担责任。使用者应自行评估风险并承担全部后果。
- **与 Mojang / Microsoft 的关系**：本项目与 Mojang Studios、Microsoft、Xbox
  及其关联公司没有任何隶属、授权或合作关系。《我的世界》（Minecraft）为其商标与
  版权作品，本软件不分发、不修改、不内置任何游戏本体文件；用户须自行确保拥有
  正版授权并遵守相应的 EULA 与使用条款。
- **第三方内容与服务**：通过本软件访问的下载源、模组 / 光影 / 材质包平台、
  外置登录服务器以及联机中继，均不由本项目控制，其可用性、安全性与内容合规性
  由各自运营方负责；账号凭据（微软 Client ID、外置登录账号密码等）由使用者自行
  保管，因凭据泄露或误填服务器地址造成的后果由使用者承担。
- **使用者义务**：使用前请备份重要数据（存档、实例目录、`config.json`、
  `accounts.json`）；请勿将本软件用于违反所在地区法律法规或侵害他人权益的行为。

完整声明见 [DISCLAIMER.md](DISCLAIMER.md)；安全披露见 [SECURITY.md](SECURITY.md)；
本项目采用 MIT 许可证，见 [LICENSE](LICENSE)。
