# 桌面版

Tauri 2 的壳 + 后端进程 + React 界面。壳里没有业务逻辑：流水线在 Python 里，
界面走的是后端那套普通 HTTP API——和 MCP 客户端、curl 脚本用的是同一套接口，
没有只有桌面版才知道的私有通道。

## 跑起来

```bash
cd desktop && pnpm install
pnpm dev      # 开发：自动拉起 Vite 和壳，界面热更新
pnpm build    # 出安装包
```

**不要直接 `cargo run`**：不带 `custom-protocol` 特性时 Tauri 走开发模式、
去加载 `devUrl`（localhost:5273），而那个端口上没有东西，窗口就是全白的。
要绕过 Tauri CLI 的话得用 `cargo run --features custom-protocol`，那样加载的
是已经构建好的 `web/dist`。

后端怎么找：优先用应用数据目录里下载好的运行时，没有就回落到源码仓库
（`uv run --project <repo> doc2video`）。所以开发时改 Python，重启壳就生效。

## 壳负责的四件事

| | 为什么必须由壳来做 |
| --- | --- |
| 挑端口 | 默认 8400 对服务器合适，对桌面应用不合适——开第二个实例或别的程序占用就起不来。绑 `:0` 让系统给一个。 |
| 发令牌 | 后端每条路由都要 Bearer token。每次启动随机一个、只走环境变量，没有东西可泄漏也不用轮换。 |
| 指定可写目录 | `storage_dir` 默认是相对当前工作目录的 `./storage`，装成应用之后那是系统随便给的目录。必须显式指定。 |
| 收尸 | 壳被强杀时不会走析构，后端会活下来占着端口和 CPU。所以 pid 落盘，下次启动先清。 |

## 密钥

存在系统钥匙串（macOS Keychain / Windows 凭据管理器 / Linux Secret Service），
不落配置文件，也不回读给界面——设置页只显示「已配置」。改 key 会重启后端：
后端的配置在进程生命周期内是冻结的（`get_settings` 是 `lru_cache`），重启是
唯一诚实的生效方式。

## 还没做

- 运行时包的下载与校验（M7）：现在只能跑在有源码和 uv 的机器上。
- Windows / Linux 构建（M7）：语音在非 macOS 上还是静音，字幕字体也还没补。
- 自更新（M8）。
