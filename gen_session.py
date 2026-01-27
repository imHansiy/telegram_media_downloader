import asyncio
import os
import sys

# Add current directory to sys.path to ensure we can import modules
sys.path.append(os.getcwd())

from pyrogram import Client
from module.app import Application


async def main():
    print("正在加载配置文件 config.yaml ...")

    # Load configuration
    try:
        app_config = Application("config.yaml", "data.yaml", "media_downloader")
        app_config.load_config()
    except Exception as e:
        print(f"加载配置失败: {e}")
        print("请确保 config.yaml 文件存在且配置正确。")
        return

    if not app_config.api_id or not app_config.api_hash:
        print("错误: config.yaml 中未找到 api_id 或 api_hash")
        return

    print(f"读取到 API_ID: {app_config.api_id}")
    print("正在启动 Pyrogram 客户端进行登录...")
    print("注意: 如果开启了两步验证，请输入密码。")

    # Initialize Client directly with Pyrogram to generate string
    # We use in-memory session for generation
    async with Client(
        "temp_gen_session",
        api_id=app_config.api_id,
        api_hash=app_config.api_hash,
        in_memory=True,
    ) as app:
        session_string = await app.export_session_string()
        print("\n" + "=" * 50)
        print("生成成功！请复制下面的 Session String (不要泄露给他人):")
        print("=" * 50 + "\n")
        print(session_string)
        print("\n" + "=" * 50)
        print("使用说明:")
        print("1. 复制上面的字符串。")
        print("2. 在 Render/Koyeb 等平台设置环境变量:")
        print("   Key: PYROGRAM_SESSION_STRING")
        print("   Value: (粘贴上面的字符串)")
        print("=" * 50)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已取消")
    except Exception as e:
        print(f"\n发生错误: {e}")
