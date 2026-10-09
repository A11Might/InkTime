# InkTime · 会讲故事的墨水屏相框

[English](README.md) · 中文

一个完全运行在自己电脑上的小工具：它会用 AI 读懂你的照片，每天挑出最值得回味的一个场景，
配上一句画外之意，推送到桌上的墨水屏相框；手机碰一碰设备，就能看到那张照片的原图和故事。

<p align="center">
  <img src="docs/home.png" width="100%" alt="工作台"/>
</p>
<p align="center"><i>工作台：画廊筛选 / 屏上文案即时编辑 / 墨水屏实时预览</i></p>

## 它能做什么

- **每天一个回忆**：AI 给每张照片打 0~100 的「回忆分」，每天早上从「历史上的今天」挑出没回忆过的最优场景，今天没有就往前回退
- **自动写文案**：为照片配一句 8~20 字的旁白，明令不走套路、不灌鸡汤
- **定时+一键推送**：服务常驻时每天定点自动推，控制台里也可以想推哪张点哪张，每次推送都有记录
- **相似照片自动分叠**：连拍、同场景换姿势聚成一叠算一个场景（dHash 指纹 + 时间聚类），封面你来挑，推的也是封面
- **碰一碰回看**：手机 NFC 触碰设备，打开一个页面看原图、文案、拍摄日期和城市
- **隐私可控**：照片和数据库都存在自己电脑上；选本地模型的话，照片不会经过任何云端

## 三步开始

只想先看看效果？做完第 1 步的安装后，`python3 mock/seed_mock.py` 会生成一批演示照片和数据库，
随后 `python3 app.py` 打开完整控制台：http://127.0.0.1:8788 —— 不用 AI key、不用自己的照片、也不用设备。

### 第 1 步：安装（只需一次）

把下面两行命令复制粘贴到「终端」（macOS）或「PowerShell」（Windows）里：

```bash
python3 -m venv .venv && source .venv/bin/activate    # Windows 用户改为：.venv\Scripts\activate
pip install -r requirements.txt
```

建议顺手装上 [exiftool](https://exiftool.org/)，它负责把照片 GPS 解析成城市名。不装也能用，只是没有地点。

```bash
brew install exiftool    # Windows: choco install exiftool · Debian/Ubuntu: sudo apt-get install libimage-exiftool-perl
```

### 第 2 步：告诉它照片在哪、用哪个 AI

复制 `config_example.py` 并改名为 `config.py`，打开填三样东西（都有中文注释）：

1. **照片在哪**：`IMAGE_DIR` 填你照片文件夹的路径，例如 `/Users/张三/Pictures/我的照片`
2. **结果存哪**：`DB_PATH` 是分析结果的保存文件，放在照片文件夹里就行，命名为 `photos.db`
   （和上面拼起来就是：`/Users/张三/Pictures/我的照片/photos.db`）
3. **AI 用哪家**：任何 OpenAI 兼容的 `/v1/chat/completions` 视觉端点都行，区别主要在隐私——
   - **商用 API**：照片会上传到模型服务商的服务器分析。买一份视觉大模型的 key（阿里云/智谱等），
     填 `api_url`、`api_key`，`model_name` 填多模态模型名（如 `glm-4.5v`）
   - **自建模型**：照片完全不出你的电脑。装 [LM Studio](https://lmstudio.ai)，下载视觉模型
     （如 `mlx-community/Qwen3.5-9B-6bit`，约 8GB）并在 Developer 页启动本地服务；`api_url` 填
     `http://127.0.0.1:1234/v1/chat/completions`，`api_key` 留空。对电脑性能要求较高：
     作者实测 M2 Pro / 16GB 内存，每张照片（两次调用）约 2 分钟

`API_CHANNELS` 是按优先级排列的列表——某个渠道失败或限流时，会自动切到下一个。

### 第 3 步：跑起来

```bash
python3.11 analyze_photos.py    # 分析照片：第一次要等一会儿，中断了再跑会自动接续
python3.11 app.py               # 打开控制台：http://127.0.0.1:8788
                                # 服务常驻期间，每天到点还会自动推送（见下文「每天自动推送」）
```

控制台里：左边是照片画廊，右边是墨水屏预览，点「推送到设备」即可。
想推哪张点哪张，屏上文案也可以随手改。

> **小技巧：先拿几张照片试水**
> 从照片库里挑十来张，拷到一个新文件夹（比如桌面上的 `测试照片`），只分析这一小批：
>
> ```bash
> python3.11 analyze_photos.py ~/Desktop/测试照片          # 只分析这个文件夹
> python3.11 analyze_photos.py ~/Desktop/测试照片 --limit 5  # 再省一点：只处理前 5 张
> ```
>
> 跑完打开控制台看看效果，满意了再把整个照片库交给它。

### 让墨水屏收到推送

需要一个 [Dot. Quote/0](https://dot.mindreset.tech) 墨水屏，并在 `config.py` 里填上设备凭证（App 里可以查到）：

```python
DOT_API_KEY = "dot_app_XXXX"    # Dot. App → 更多 → API Key → 创建
DOT_DEVICE_ID = "设备序列号"
```

另有两个可选项（都不填也能用）：`DOT_TASK_KEY` 在设备上有多个「图像 API」内容时指定推给哪个；
`DOT_TASK_ALIAS` 给内容起个看得懂的显示名，会出现在 Dot. App 的任务列表里。

| Quote/0 实机 | 碰一碰手机端 |
|:---:|:---:|
| <img src="docs/device_real.jpg" width="480"/> | <img src="docs/tap_mobile.png" width="213"/> |

*左：Quote/0 实机正在展示推送的照片与画外之意；右：手机 NFC 碰一碰打开的回看页面*

注意：碰一碰链接指向这台电脑的局域网 IP（8788 端口），推送那一刻写定——手机要和电脑在同一网络，
电脑 IP 变了旧链接也会失效。

### 每天自动推送（可选）

定时器就长在控制台服务里：`app.py` 常驻时，每天到点自动挑一个「历史上的今天」的场景推到屏上，
不需要 launchd / crontab。默认每天 08:00，想改时间或不想要自动推送，在 `config.py` 里加：

```python
AUTO_PUSH = False    # 关掉自动推送
PUSH_HOUR = 8        # 每天几点推（24 小时制）
PUSH_MINUTE = 0
```

Mac 睡眠错过了点，服务会在唤醒后补推；推送失败会隔 10 分钟自动重试。定时推送不强制翻屏：
照片先存进设备，等墨水屏自己的唤醒周期再把内容刷出来（省电、不吵）；控制台里手动点
「推送到设备」则是立刻刷新。推送前想先看看今天会选中哪张：

```bash
python3.11 daily_push.py --dry-run   # 只显示选片结果，不真推
python3.11 daily_push.py             # 手动立即推一次（今天推过会跳过，--force 强制再推）
python3.11 daily_push.py --date 2024-10-01 --dry-run   # 预演别的日子
```

选片规则一句话版：候选以**场景**为单位——相似照片叠算一个场景，单张各算一个；先在「历史上的今天」
里找没回忆过的达标场景（一天天往前回退，最多一年），再退而求其次挑最久没回忆的场景，最后用全库
最高分兜底。达标线用 `MEMORY_THRESHOLD` 调，每天张数用 `DAILY_COUNT` 调。完整规则见
[docs/photo-selection.md](docs/photo-selection.md)。

## 项目文档

- [docs/photo-selection.md](docs/photo-selection.md) — 分叠与选片的完整规则
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — 更新日志

## 致谢

- [dai-hongtao/InkTime](https://github.com/dai-hongtao/InkTime) — 项目想法与照片分析思路
- [Dot.](https://dot.mindreset.tech) — Quote/0 墨水屏设备与 OpenAPI
- [ZinggJM/GxEPD2](https://github.com/ZinggJM/GxEPD2)、[Pillow](https://python-pillow.org/) — 墨水屏生态与图像处理
- 城市索引基于 [GeoNames](https://www.geonames.org/)（CC BY 4.0）制作；视觉语言取自 [dejev.app](https://dejev.app/zh-Hans)

## License

MIT
