
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
language: ZH
max_download_task: 5
```

当配置 `DATABASE_URL` 后，后端会自动创建一个很小的 `app_keepalive` 表，并启动一个低频保活任务。
默认每 4 小时对该表写入一次，并立刻回读校验，确认数据库仍然具备真实的可写可读能力。
这个任务完全在后端执行，不依赖前端页面访问。

## 🕹️ 使用场景

### 1. Web 任务管理
访问 `http://localhost:5000` 即可管理任务。您可以实时监控进度、查看下载/上传速度，并对任务进行一键控制。

### 2. 云盘流式同步
通过设置 `upload_adapter: webdav`，资源将跳过本地硬盘，直接从电报服务器同步到您的云盘。非常适合低硬盘空间的 VPS 环境。

### 3. 电报机器人操作
直接向您的机器人发送命令进行批量下载或状态查询。

## 🤝 贡献
请阅读 [贡献指南](./CONTRIBUTING.md) 以了解我们的开发规范。

### 赞助
[PayPal](https://paypal.me/tangyoha?country.x=C2&locale.x=zh_XC)

<p>
<img alt="Alipay" style="width:30%" src="./screenshot/alipay.JPG">
<img alt="WeChat" style="width:30%" src="./screenshot/wechat.JPG">
</p>
