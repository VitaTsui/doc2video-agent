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

**不要直接 `cargo run`。** 两种错法，症状完全不同：

* 不带 `custom-protocol` 时 Tauri 走开发模式去加载 `devUrl`（localhost:5273），
  那个端口上没东西，窗口**全白**。
* 带上 `custom-protocol` 时前端产物是在**编译期**嵌进二进制的。所以
  `pnpm --dir web build` 之后直接跑旧二进制，窗口里是**上一次编译时**的界面——
  改了半天看不到任何变化，而且看起来像是改坏了。改完前端必须重新
  `cargo build`，或者干脆用 `pnpm dev` / `pnpm build`。

### 怎么看界面

窗口截不了图（macOS 要录屏权限），所以界面问题只能靠肉眼描述来回猜——除非
在普通浏览器里打开它。`src/devMock.ts` 就是干这个的：开发模式下、且检测不到
Tauri 外壳时，桩掉 `invoke` 和几个健康检查请求，于是 `pnpm --dir web dev` 起来
的页面能正常渲染，可以用任何浏览器（或 Playwright）直接看。

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

## 自更新

壳自己更新，运行时不跟着走——壳十几兆，运行时四百兆。

启动时静默查一次，有新版就在对话里说一句；设置页里能手动查、手动更新。
不自动装：装意味着重启，而后端是壳的子进程，正在渲染的话那几分钟就没了。
所以正在生成时更新按钮是禁用的。

安全性全在签名上：应用只认 `tauri.conf.json` 里那一个公钥签出来的包，
GitHub Release 被换掉也装不进去。CI 用 `TAURI_SIGNING_PRIVATE_KEY` 签，
签完由 `scripts/updater_manifest.py` 汇成 `latest.json` 挂到 Release 上。
没签名的平台不会进清单——列进去只会让应用下载一个注定被拒的包，用户看到
的是「更新失败」，而正确的表现是什么都不该发生。

发版前仓库要配两个 secret：

| Secret | 内容 |
| --- | --- |
| `TAURI_SIGNING_PRIVATE_KEY` | `pnpm exec tauri signer generate` 生成的私钥文件**整段内容** |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 生成时设的密码；没设就留空 |

私钥丢了就再也签不出能被现有安装接受的包，只能改公钥重新发一版，
已装的用户得手动重装。

## 运行时分两半

| | 大小 | 什么时候变 | 发在哪 |
| --- | --- | --- | --- |
| base | ~400MB / 两万多个文件 | 依赖、Node、Python、音色、字体变了才变 | `runtime-base-<摘要>` 这个 tag |
| app | ~0.2MB / 一百多个文件 | 每次发版 | 该版本自己的 Release |

**base 的版本号是算出来的，不是填的**：`scripts/build_runtime.py` 把声明的
依赖、`renderer/pnpm-lock.yaml`、Node 和 Python 的版本、音色、字体地址一起
做哈希。手工维护迟早漏一次，而漏的那次结果是「新的 app 装进了没有它新依赖
的树里」——在第一次渲染时才炸，不是在安装时。壳里嵌的那份摘要
（`base_version.txt`）和脚本算出来的必须一致，有测试盯着。

CI 里 base 摘要没变就整段跳过：不装解释器、不解锁 lockfile、不下浏览器，
一次普通发版几十秒完事，用户也只下 0.2MB。

装的时候两半分开下载校验解压。base 是「解到旁边、最后整目录换过去」，
所以中途死掉不影响已有的；app 是直接盖在活的树上——它只是那棵树里的
一百多个文件，其余四百兆必须原地不动。

## 还没做

- Intel macOS 构建：`macos-13` 的 runner 排不到队。
- 代码签名与公证：macOS 会弹 Gatekeeper，Windows 会被 SmartScreen 拦。
  卡在账号和钱上，不卡在代码上。
