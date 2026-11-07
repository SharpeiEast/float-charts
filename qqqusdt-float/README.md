# QQQUSDT Float

一个可以挂在桌面上的小窗口,实时显示蜡烛图。每个窗口左上角有一个**下拉菜单**选品种,目前三项:

- **QQQ · 币安 QQQUSDT**:币安 QQQUSDT 永续合约。顶行同时显示 Yahoo 的 QQQ 最新价(盘前、盘中、盘后、夜盘都取最新)和 QQQUSDT 最新价
- **币安 · ETH 永续**:币安 `ETHUSDT` 永续合约
- **币安 · BTC 永续**:币安 `BTCUSDT` 永续合约

支持 15 分钟 / 30 分钟 / 1 小时 / 4 小时 / 日(1d)/ 周(1w)/ 月(1M)周期,可开关布林线(20 根均线 ± 2σ)。所有数据都由你自己的电脑直接连币安和 Yahoo,不经过任何中转服务器。

## 两种用法

| 版本 | 位置 | 怎么用 |
|---|---|---|
| 网页版 | `web/qqqusdt_float.html` | 把这一个文件拷到电脑,用 Chrome / Edge(116 以上)打开,点右上角「浮窗」得到置顶小窗口 |
| 程序版 | Releases 里的 `.exe` / `.zip` | Windows 双击 exe;Mac 解压后打开 `.app`。窗口自带置顶,不需要浏览器 |

程序版内部就是把同一个网页放进系统自带的网页组件(Windows 的 WebView2、Mac 的 WebKit),所以两个版本画面和功能一致。

### Windows 首次运行
没有代码签名,Windows SmartScreen 会提示"未识别的应用":点「更多信息」→「仍要运行」。需要系统自带的 WebView2 运行库(Windows 10/11 一般已经有,没有的话装 Edge 即可)。

### Mac 首次运行
程序没有苹果的开发者签名和公证,macOS 会给下载来的文件加上隔离标记并拒绝运行(macOS 15 起「右键 → 打开」也绕不过去)。每个新下载的副本只需要在终端做一次,把 `.app` 放在「下载」或「桌面」等位置后,在它所在的目录执行:

```
xattr -cr QQQUSDT-Float.app
codesign --force --deep --sign - QQQUSDT-Float.app
```

之后直接双击即可。如果这两条命令自己提示 `Operation not permitted`,把 `.app` 挪到 `~/Applications` 再做。Apple 芯片用 `macos-arm64` 那个包,Intel 芯片用 `macos-intel` 那个。解压请用 Mac 自带的方式(双击 zip),不要用第三方解压工具,否则 `.app` 里的符号链接可能丢失。

## 多窗口(程序版)

- 右上角 **＋** 再开一个窗口,最多同时 3 个;新窗口出现在现有窗口的正下方,第一次依次是 QQQ、ETH、BTC(已经开着的品种会跳过)
- 每个窗口可以单独关闭,关掉中间的窗口后再点 ＋,新窗口会补在空出来的位置
- 每个窗口的品种、周期各自独立,可随时用下拉菜单换品种;下次启动会按上次关闭时的窗口数、品种、周期恢复
- 满 3 个窗口时点 ＋ 会在右上角提示"最多同时打开 3 个窗口"
- 网页版(浏览器)点 ＋ 会弹出另一个小窗口,显示菜单里的下一个品种

## 操作

- 顶部 `15m / 30m / 1h / 4h / 1d / 1w / 1M` 切换周期;「布林」勾选框开关布林线
- 滚轮缩放,拖动平移(向左拖可以把最新一根蜡烛拖离右边缘,右侧留出空白),双击复位
- 左上角默认显示最新一根蜡烛的开高低收(带「● 当前」),鼠标放在别的蜡烛上显示那一根
- 鼠标放在 QQQ 数字上,提示当前时段(盘前 / 盘中 / 盘后)和最后更新时间;休市没人成交时数字会变淡
- 网址后加 `?market=spot` 改看现货 QQQBUSDT;程序版命令行加 `--spot` 同理(QQQ 换成现货 QQQBUSDT,ETH、BTC 换成对应的币安现货 ETHUSDT、BTCUSDT)
- 网址后加 `?inst=eth`(或 `btc`、`qqq`)直接打开指定品种
- 程序版会记住窗口位置和大小,存放在:Windows `%APPDATA%\qqqusdt-float\windows.json`,Mac `~/Library/Application Support/qqqusdt-float/windows.json`;同目录的 `app.log` 记录开关窗口和错误(旧版的 `window.json` 不再使用,可以删除)

## 数据从哪里来

| 数据 | 地址 |
|---|---|
| 历史 K 线 | `https://fapi.binance.com/fapi/v1/klines`(币安 USDS-M 永续) |
| 实时 K 线和最新价 | `wss://fstream.binance.com/market/stream`(注意是 `/market/stream`,旧的 `/ws`、`/stream` 能连上但收不到数据) |
| QQQ 价格 | `wss://streamer.finance.yahoo.com/?version=2`(订阅 `QQQ`,消息是 base64 的 protobuf,页面里自己解码) |

币安数据流 10 秒没数据会自动改成每 3 秒轮询历史接口,圆点变黄。你的网络必须能访问上面这些地址。

## 已知限制

- 周线和月线的历史很短(永续合约的 K 线从 2026 年 4 月开始,周线约 30 根、月线个位数),不足 20 根时布林线不会出现,月线基本看不到布林线。
- Yahoo 推送只在有成交或报价时才发消息,休市时可能很久没有新价格,数字会变淡。
- 夜盘(美东 20:00 到次日 04:00)Yahoo 是否推送夜盘价,开发时没有验证过。
- 没有价格提醒、没有开机自启,这是有意不做的。

## 开发和更新

```
web/qqqusdt_float.html     网页版,也是程序版的界面(唯一的界面源码)
app/main.py                程序版外壳(pywebview)
.github/workflows/build-qqqusdt.yml(仓库根目录)打标签时自动构建 Windows / Mac 程序并上传到 Releases
```

本地运行程序版(在本目录下):

```
pip install -r app/requirements.txt
python app/main.py                         # 正常运行
python app/main.py --selftest out.json     # 自检:开 3 个窗口、换品种、关一个再补一个,把页面读到的数字写进 out.json
```

发布新版本(标签带项目前缀,只会构建这个项目):

```
git tag qqqusdt-v0.1.4
git push origin qqqusdt-v0.1.4      # GitHub Actions 构建并把文件挂到这个标签的 Release 上
```

只改网页版的话,改 `web/qqqusdt_float.html` 后提交即可;程序版要重新打 tag 才会带上新页面。
