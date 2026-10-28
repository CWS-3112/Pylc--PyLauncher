# 贡献指南

感谢你愿意参与 PyLauncher 的改进。

## 开发约定

- 版本号的唯一来源是 `launcher/core/constants.py` 的 `LAUNCHER_VERSION`，
不要在其它位置重复声明版本。
- 核心逻辑放在 `launcher/core/`（不依赖 Qt），界面放在 `launcher/ui/`；
核心层新增能力时请同步补充 `--selftest` 用例。
- 任何耗时操作（网络、子进程、磁盘扫描）都必须放到 `launcher/ui/workers.py`
 的 `Worker` 里，并支持 `should_cancel`（**禁止使用 `QThread.terminate()`**）。
- 跨线程访问 Qt 控件必须走信号，不能在子线程里直接操作控件。

## 提交信息

使用 Conventional Commits：

```
feat(versions): 支持按 javaVersion 判定所需 Java
fix(ui): 修复标签页切换后内容不可见
chore(release): bump version to 1.0.0
```

常用类型：`feat` / `fix` / `docs` / `refactor` / `perf` / `test` / `chore`。

## 提交前自检

```bash
python -m compileall launcher main.py tests   # 语法
python main.py --selftest                     # 核心自检
python tests/smoke_ui.py                      # 界面冒烟
python tests/smoke_shortcut.py                # 快捷方式
python tests/smoke_runtime.py                 # 运行时健壮性
python tests/stress_sim.py 1200               # 随机模拟压测
```

新增或修改涉及路径、压缩包、网络、线程的代码时，**必须**跑 `stress_sim.py`。

## 许可

提交你的改动即表示同意按本仓库的 [MIT 许可证](LICENSE) 发布，
并已阅读 [免责声明](DISCLAIMER.md)。安全问题请看 [SECURITY.md](SECURITY.md)。
