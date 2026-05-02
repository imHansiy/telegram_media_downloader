
<h1 align="center">Telegram Media Downloader</h1>

<p align="center">
<a href="https://github.com/tangyoha/telegram_media_downloader/actions"><img alt="Unittest" src="https://github.com/tangyoha/telegram_media_downloader/workflows/Unittest/badge.svg"></a>
<a href="https://codecov.io/gh/tangyoha/telegram_media_downloader"><img alt="Coverage Status" src="https://codecov.io/gh/tangyoha/telegram_media_downloader/branch/master/graph/badge.svg"></a>
<a href="https://github.com/tangyoha/telegram_media_downloader/blob/master/LICENSE"><img alt="License: MIT" src="https://black.readthedocs.io/en/stable/_static/license.svg"></a>
<a href="https://github.com/python/black"><img alt="Code style: black" src="https://img.shields.io/badge/code%20style-black-000000.svg"></a>
<a href="https://github.com/tangyoha/telegram_media_downloader/releases">
<img alt="Code style: black" src="https://img.shields.io/github/v/release/tangyoha/telegram_media_downloader?display_name=tag"></a>
</p>

<h3 align="center">
  <a href="./README_CN.md">中文</a><span> · </span>
  <a href="https://github.com/tangyoha/telegram_media_downloader/discussions/categories/ideas">Feature request</a>
  <span> · </span>
  <a href="https://github.com/tangyoha/telegram_media_downloader/issues">Report a bug</a>
  <span> · </span>
  Support: <a href="https://github.com/tangyoha/telegram_media_downloader/discussions">Discussions</a>
  <span> & </span>
  <a href="https://t.me/TeegramMediaDownload">Telegram Community</a>
</h3>

## 🚀 Overview
A versatile and powerful tool to download media from Telegram or forward it to other channels. Now features advanced cloud streaming and professional Web management.

### Key New Features (v2.2.5+)
- **☁️ WebDAV Streaming Upload**: Direct stream from Telegram to WebDAV (Alist, Nextcloud, etc.) without consuming local disk space.
- **🖥️ Advanced Web Dashboard**:
  - **Granular Control**: Pause, Resume, or Cancel individual active tasks.
  - **Smart Status**: "Finishing..." state for cloud file persistence confirmation.
  - **Accurate Paths**: Preserves and displays full directory structures (Channel/Date/File).
- **🗄️ PostgreSQL Persistence**: High-reliability storage for sessions, history, and task queues. Supports persistent control states across restarts.
- **🐳 Cloud-Native**: Support for `CONFIG_YAML` environment variable configuration.

## 🛠️ Installation

### Standard Installation
```sh
git clone https://github.com/tangyoha/telegram_media_downloader.git
cd telegram_media_downloader
pip3 install -r requirements.txt
```

### Docker (Recommended)
```sh
docker pull tangyoha/telegram_media_downloader:latest
# Run for the first time to login
docker run -it --rm -v $(pwd)/config.yaml:/app/config.yaml tangyoha/telegram_media_downloader
```

## ⚙️ Configuration
All configurations are managed via `config.yaml` or the `CONFIG_YAML` environment variable.

### config.yaml Example
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
  upload_adapter: webdav  # Supports: rclone, aligo, webdav
  remote_dir: /telegram_backup
  after_upload_file_delete: true
  # WebDAV Specific configuration
  webdav_url: https://your-alist-webdav-link
  webdav_username: admin
  webdav_password: your_password

# Database Configuration (Optional, default is local files)
# DATABASE_URL: postgresql://user:password@host:port/dbname
# DB_KEEPALIVE_INTERVAL_SECONDS: 14400  # Optional, defaults to 4 hours
# DB_KEEPALIVE_APP_NAME: telegram_media_downloader  # Optional keepalive row key

web_host: 0.0.0.0
web_port: 5000
web_login_secret: "123456"
language: ZH
max_download_task: 5
```

When `DATABASE_URL` is configured, the backend creates a tiny `app_keepalive` table and
runs a low-frequency keepalive task. By default it writes one row every 4 hours and
reads it back immediately to confirm the database is still truly writable and readable.
This runs entirely on the backend and does not depend on web page visits.

## 🕹️ Use Cases

### 1. Web Management
Access `http://localhost:5000` to manage your tasks. You can monitor progress, check transfer speeds, and control missions with one click.

### 2. Cloud Storage Streaming
By setting `upload_adapter: webdav`, the downloader will bypass local storage and stream data directly to your cloud drive, perfect for low-disk VPS environments.

### 3. Telegram Bot
Send commands to your bot to trigger batch downloads or check status.

## 🤝 Contributing
Read through our [contributing guidelines](./CONTRIBUTING.md) to learn about our submission process and coding rules.

### Sponsor
[PayPal](https://paypal.me/tangyoha?country.x=C2&locale.x=zh_XC)

<p>
<img alt="Alipay" style="width:30%" src="./screenshot/alipay.JPG">
<img alt="WeChat" style="width:30%" src="./screenshot/wechat.JPG">
</p>
