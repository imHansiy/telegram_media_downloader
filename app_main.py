"""ModelScope Studio entrypoint for Telegram Media Downloader."""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if __name__ == "__main__":
    import runpy
    entry_path = os.path.join(BASE_DIR, "media_downloader.py")
    runpy.run_path(entry_path, run_name="__main__")
