# float-charts

挂在桌面上的行情小窗口。每个项目一个目录,各自独立构建和发布,同一类的新工作也放在这里。

| 项目 | 说明 | 下载 |
|---|---|---|
| [`qqqusdt-float/`](qqqusdt-float/) | 实时蜡烛图,下拉菜单选币安 QQQUSDT(顶行同时显示 Yahoo QQQ 价)/ OKX ETH 永续 / OKX BTC 永续,最多 3 个窗口,15m / 30m / 1h / 4h / 1d / 1w / 1M 周期,布林线 | Releases 里标签为 `qqqusdt-v*`(更早的 `v0.1.x` 是合并前的旧版本) |
| [`ashare-float/`](ashare-float/) | A 股实时蜡烛图,分时 / 小时 / 日 / 周 / 月,布林线,最多 3 个窗口依次向下排列,数据来自腾讯和东方财富 | Releases 里标签为 `ashare-v*` |

两个项目都有 Windows、Apple 芯片 Mac、Intel Mac 三种程序,也都带一个单文件网页版(`web/` 下)。使用方法见各目录的 README。

## 约定

- 数据都由使用者自己的电脑直接取,不经过任何中转服务器
- 界面是一个独立的 HTML 文件,程序版(`app/main.py`,pywebview)只是给它加上原生窗口、置顶和记忆位置
- 每个项目对应一个工作流 `.github/workflows/build-<项目>.yml`,只在 `<项目前缀>-v*` 标签或手动触发时运行,并且只构建自己的目录

## 新增项目

1. 新建一个目录(例如 `foo-float/`),按现有项目的结构放 `web/`、`app/`、`README.md`
2. 复制一份 `.github/workflows/build-ashare.yml`,把项目目录、程序名和标签前缀改掉
3. 在上面的表格里加一行
