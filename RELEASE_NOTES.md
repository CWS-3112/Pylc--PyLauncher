# PyLauncher v1.0.0

《我的世界》Java 版第三方启动器，Python + PySide6 编写，支持 Windows / macOS / Linux。

## 本次发布内容

**游戏与版本**
- 版本清单下载（官方源 / BMCLAPI 镜像，源故障自动切换），支持正式版、快照、Beta、Alpha
- 本地版本扫描、隔离目录、完整性校验；Forge / Fabric / NeoForge 安装
- 按版本 JSON 的 `javaVersion` 判定所需 Java，版本不足时给出明确提示

**内容管理**
- Mod 管理：扫描、启停、导入、删除、批量切换
- **内置 Modrinth 在线模组库**：搜索并一键下载安装，无需打开网页
- **材质包与光影**：按实例扫描 `resourcepacks` / `shaderpacks`，启停、导入、在线安装
- **服务器选项**：列表持久化 + Server List Ping（MOTD / 人数 / 延迟 / 版本）

**联机**
- **局域网联机**：按 Mojang 组播协议（224.0.2.60:4445）自动发现已开放的世界
- **跨网联机**：联机码 + 内置 TCP 中继（带口令鉴权，最多 8 客户端）

**账户**
- 离线账户、微软设备码登录、**第三方外置登录（Yggdrasil 皮肤站）**

**安全与稳定**
- 软件防火墙：出站域名白名单、强制 HTTPS、SSRF 拦截、下载体积与路径约束
- 解压防护：zip slip、符号链接、压缩包炸弹
- 分页懒加载与渲染去重，首屏 ~116ms；连续切换不涨内存、退出不崩

## 使用方式

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
python main.py
```

其它入口：

```bash
python main.py --selftest            # 核心自检（120 项）
python tests/smoke_ui.py             # 界面冒烟
python tests/stress_sim.py 1200      # 随机模拟压测
```

## 升级注意点

- **需要 Java**：本机需安装 Java（1.20.5+ 需 Java 17，现代快照需 Java 21+，部分整合包需 Java 25）。
  启动器只做检测与提示，不代为下载。
- **微软登录需自建 Azure Client ID**：启动器不内置任何第三方 Client ID，请在「设置 → 微软登录」填写。
- **首次运行会自动创建桌面快捷方式**（幂等，不重复）；可用环境变量 `PYLAUNCHER_NO_AUTOSHORTCUT=1` 关闭。
- **配置文件位置变更**：配置默认写入 `<launcher_home>`，可用 `--launcher-home` 指定便携目录。
  旧的 `launch.wrapper`（包装命令）配置已下线，加载时会自动清理。
- **升级前请备份**：`config.json`、`accounts.json` 与游戏实例目录（尤其存档）。

## 已知限制

- 不自动下载 Java
- Forge 安装依赖官方 Installer，1.12.2 及更早版本在部分环境需图形界面
- 跨网中继需房主在路由器做端口映射；Mod 无在线依赖解析

## 许可与声明

MIT 许可证（见 [LICENSE](LICENSE)）。软件按“原样”提供，详见 [DISCLAIMER.md](DISCLAIMER.md)。
安全问题请看 [SECURITY.md](SECURITY.md)，贡献请看 [CONTRIBUTING.md](CONTRIBUTING.md)。
