
<h1 align="center">Telegram Media Downloader (电报资源下载器)</h1>

<p align="center">
<a href="https://github.com/tangyoha/telegram_media_downloader/actions"><img alt="Unittest" src="https://github.com/tangyoha/telegram_media_downloader/workflows/Unittest/badge.svg"></a>
<a href="https://codecov.io/gh/tangyoha/telegram_media_downloader"><img alt="Coverage Status" src="https://codecov.io/gh/tangyoha/telegram_media_downloader/branch/master/graph/badge.svg"></a>
<a href="https://github.com/tangyoha/telegram_media_downloader/blob/master/LICENSE"><img alt="License: MIT" src="https://black.readthedocs.io/en/stable/_static/license.svg"></a>
<a href="https://github.com/python/black"><img alt="Code style: black" src="https://img.shields.io/badge/code%20style-black-000000.svg"></a>
<a href="https://github.com/tangyoha/telegram_media_downloader/releases">
<img alt="Code style: black" src="https://img.shields.io/github/v/release/tangyoha/telegram_media_downloader?display_name=tag"></a>
</p>

<h3 align="center">
  <a href="./README.md">English</a><span> · </span>
  <a href="https://github.com/tangyoha/telegram_media_downloader/discussions/categories/ideas">新功能请求</a>
  <span> · </span>
  <a href="https://github.com/tangyoha/telegram_media_downloader/issues">报告 Bug</a>
  <span> · </span>
  帮助: <a href="https://github.com/tangyoha/telegram_media_downloader/discussions">讨论区</a>
  <span> & </span>
  <a href="https://t.me/TeegramMediaDownload">电报讨论群</a>
</h3>

## 🚀 概述
一个功能强大且灵活的电报媒体下载/转发工具。现在支持先进的云端流式上传和专业的 Web 管理界面。

### 重大更新 (v2.2.5+)
- **☁️ WebDAV 流式上传**: 支持将电报媒体直接流式上传到 WebDAV 服务器（如 Alist、Nextcloud、网盘等），**不占用本地磁盘空间**。
- **🖥️ 高级 Web 仪表盘**:
  - **精细化任务控制**: 支持对进行中的任务进行 单独暂停、恢复 或 取消。
  - **智能状态检查**: 引入“整理中”状态，明确区分数据传输阶段与网盘写盘确认阶段。
  - **准确路径显示**: 完美保留并展示完整的远程目录结构（频道名/日期/文件名）。
- **🗄️ PostgreSQL 持久化**: 引入对 PostgreSQL 的支持。会话、历史记录、任务队列以及控制状态现在全部持久化，**重启后任务控制状态依然保留**。
- **🐳 云原生优化**: 支持通过 `CONFIG_YAML` 环境变量直接注入配置。

## 🛠️ 安装

### 标准安装
```sh
git clone https://github.com/tangyoha/telegram_media_downloader.git
cd telegram_media_downloader
pip3 install -r requirements.txt
```

### Docker (推荐)
```sh
docker pull tangyoha/telegram_media_downloader:latest
# 第一次运行进行登录
docker run -it --rm -v $(pwd)/config.yaml:/app/config.yaml tangyoha/telegram_media_downloader
```

## ⚙️ 配置
所有配置均通过 `config.yaml` 或 `CONFIG_YAML` 环境变量进行管理。

### config.yaml 示例
```yaml
api_hash: your_api_hash
api_id: your_api_id
bot_token: your_bot_token

media_types:
- video
- photo
- document

save_path: ./downloads
generate_video_covers: true  # 下载完成后在视频旁保存 .cover.jpg，不依赖 AI
file_path_prefix:
- chat_title
- media_datetime

upload_drive:
  enable_upload_file: true
  upload_adapter: webdav  # 可选: rclone, aligo, webdav
  remote_dir: /telegram_backup
  after_upload_file_delete: true
  # WebDAV 专用配置
  webdav_url: https://your-alist-webdav-link
  webdav_username: admin
  webdav_password: your_password

# 数据库配置（可选，默认使用本地文件）
# DATABASE_URL: postgresql://user:password@host:port/dbname
# DB_KEEPALIVE_INTERVAL_SECONDS: 14400  # 可选，默认 4 小时
# DB_KEEPALIVE_APP_NAME: telegram_media_downloader  # 可选，心跳行标识

web_host: 0.0.0.0
web_port: 5000
web_login_secret: "123456"
bot_download_access_mode: self  # self 仅自己, allowed 指定用户, public 全部用户
allowed_user_ids: []  # bot_download_access_mode 为 allowed 时生效
language: ZH
max_download_task: 5
```

当配置 `DATABASE_URL` 后，后端会自动创建一个很小的 `app_keepalive` 表，并启动一个低频保活任务。
默认每 4 小时对该表写入一次，并立刻回读校验，确认数据库仍然具备真实的可写可读能力。
这个任务完全在后端执行，不依赖前端页面访问。

## 🕹️ 使用场景

### 1. Web 任务管理
访问 `http://localhost:5000` 即可管理任务。您可以实时监控进度、查看下载/上传速度，并对任务进行一键控制。

资源库支持跨目录内容分类、NSFW 细分、文件类型筛选与单文件重新分析。视频、图片、音频、文档和压缩包采用不同的证据提取方式，按所属类别生成简介。配置、兼容规则与格式限制见 [资源分类与分析](docs/resource-analysis.md)。

「设置 → 下载规则」可控制自动保存视频封面（默认开启，需要 FFmpeg/FFprobe）。新下载的视频会在同目录生成 `视频.mp4.cover.jpg`，并随视频上传到云盘；首页和悬浮卡直接加载图片，不再打开视频截帧。已有封面不会覆盖，旧视频不会自动补处理。详见 [私人片库](docs/private-library.md#封面与性能)。

资源卡片和详情页支持「删除资源」：确认完整路径后删除云盘文件及封面、本地副本移入下载目录的 `.resource-trash` 回收目录，同时清理索引、收藏和历史。不会删除父目录，正在传输或分析的资源会被阻止。删除范围、恢复方式和失败重试见 [删除资源](docs/private-library.md#删除资源)。

### 2. 云盘流式同步
通过设置 `upload_adapter: webdav`，资源先下载到本地断点文件，完整性校验通过后流式上传到云盘。自动封面使用这份已下载的视频，不重复下载；请为完整视频和封面预留本地空间。

### 3. 电报机器人操作
直接向您的机器人发送命令进行批量下载或状态查询。

### 4. 多账户并行运行
Web UI 的“Telegram 接入管理”支持保存多个 Telegram 账户。每个账户独立保存 Telegram
Session、身份信息、下载进度和运行状态；下载规则、云盘、Telegram API 凭据和 Bot 设置由
下载器全局共享。所有保存了 Session 且已启用的账户会同时启动，各自拥有独立 Telegram
client、下载队列和 worker，不需要设置“当前账户”。Bot 仍是进程级单实例，任务会优先路由
给与提交者身份匹配的运行账户。

## 🤝 贡献
请阅读 [贡献指南](./CONTRIBUTING.md) 以了解我们的开发规范。

### 赞助
[PayPal](https://paypal.me/tangyoha?country.x=C2&locale.x=zh_XC)

<p>
<img alt="Alipay" style="width:30%" src="./screenshot/alipay.JPG">
<img alt="WeChat" style="width:30%" src="./screenshot/wechat.JPG">
</p>
