# QQQUSDT Float

一个可以挂在桌面上的小窗口,实时显示币安 QQQUSDT(永续合约)的蜡烛图,顶行只有两个数字:

- **QQQ**:Yahoo 的 QQQ 最新价(盘前、盘中、盘后、夜盘都取最新)
- **QQQUSDT**:币安 QQQUSDT 永续合约最新价

支持 15 分钟 / 30 分钟 / 1 小时 / 4 小时周期,可开关布林线(20 根均线 ± 2σ)。所有数据都由你自己的电脑直接连币安和 Yahoo,不经过任何中转服务器。

## 两种用法

| 版本 | 位置 | 怎么用 |
|---|---|---|
| 网页版 | `web/qqqusdt_float.html` | 把这一个文件拷到电脑,用 Chrome / Edge(116 以上)打开,点右上角「浮窗」得到置顶小窗口 |
| 程序版 | Releases 里的 `.exe` / `.zip` | Windows 双击 exe;Mac 解压后打开 `.app`。窗口自带置顶,不需要浏览器 |

程序版内部就是把同一个网页放进系统自带的网页组件(Windows 的 WebView2、Mac 的 WebKit),所以两个版本画面和功能一致。

### Windows 首次运行
没有代码签名,Windows SmartScreen 会提示"未识别的应用":点「更多信息」→「仍要运行」。需要系统自带的 WebView2 运行库(Windows 10/11 一般已经有,没有的话装 Edge 即可)。

### Mac 首次运行
没有签名,直接双击会被 Gatekeeper 拦住:在 `.app` 上右键 →「打开」,或在终端执行 `xattr -cr QQQUSDT-Float.app` 后再打开。Apple 芯片用 `macos-arm64` 那个包,Intel 芯片用 `macos-intel` 那个。

## 操作

- 顶部 `15m / 30m / 1h / 4h` 切换周期;「布林」勾选框开关布林线
- 滚轮缩放,拖动平移,双击复位
- 左上角默认显示最新一根蜡烛的开高低收(带「● 当前」),鼠标放在别的蜡烛上显示那一根
- 鼠标放在 QQQ 数字上,提示当前时段(盘前 / 盘中 / 盘后)和最后更新时间;休市没人成交时数字会变淡
- 网址后加 `?market=spot` 改看现货 QQQBUSDT;程序版命令行加 `--spot` 同理
- 程序版会记住窗口位置和大小,存放在:Windows `%APPDATA%\qqqusdt-float\window.json`,Mac `~/Library/Application Support/qqqusdt-float/window.json`

## 数据从哪里来

| 数据 | 地址 |
|---|---|
| 历史 K 线 | `https://fapi.binance.com/fapi/v1/klines`(币安 USDS-M 永续) |
| 实时 K 线和最新价 | `wss://fstream.binance.com/market/stream`(注意是 `/market/stream`,旧的 `/ws`、`/stream` 能连上但收不到数据) |
| QQQ 价格 | `wss://streamer.finance.yahoo.com/?version=2`(订阅 `QQQ`,消息是 base64 的 protobuf,页面里自己解码) |

币安数据流 10 秒没数据会自动改成每 3 秒轮询历史接口,圆点变黄。你的网络必须能访问上面这些地址。

## 已知限制

- Yahoo 推送只在有成交或报价时才发消息,休市时可能很久没有新价格,数字会变淡。
- 夜盘(美东 20:00 到次日 04:00)Yahoo 是否推送夜盘价,开发时没有验证过。
- 没有价格提醒、没有开机自启,这是有意不做的。

## 开发和更新

```
web/qqqusdt_float.html     网页版,也是程序版的界面(唯一的界面源码)
app/main.py                程序版外壳(pywebview)
.github/workflows/build.yml 打 tag 时自动构建 Windows / Mac 程序并上传到 Releases
```

本地运行程序版:

```
pip install -r app/requirements.txt
python app/main.py                         # 正常运行
python app/main.py --selftest out.json     # 自检:打开窗口 12 秒,把页面读到的数字写进 out.json
```

发布新版本:

```
git tag v0.1.1
git push origin v0.1.1      # GitHub Actions 会构建并把文件挂到这个 tag 的 Release 上
```

只改网页版的话,改 `web/qqqusdt_float.html` 后提交即可;程序版要重新打 tag 才会带上新页面。
