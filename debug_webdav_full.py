import asyncio
import aiohttp
import os
import urllib.parse
from loguru import logger
import sys

# Setup minimal logger to stdout
logger.remove()
logger.add(sys.stdout, format="{message}")

async def test_full_logic():
    print("Starting Full Logic Simulation...")
    
    # Configuration
    webdav_url = "https://pan.007666.xyz/dav"
    webdav_username = "admin"
    webdav_password = "2002zengyuan"
    remote_dir = "Crypt/资源号OneDrive/telegram"
    file_name = "Simulation_Test_File.txt" # Simulating a file
    
    # ---------------- COPY OF LOGIC FROM cloud_drive.py ----------------
    
    # Auth
    auth = aiohttp.BasicAuth(webdav_username, webdav_password)
    headers = {"Content-Type": "application/octet-stream"}

    # Path Calc
    rel_path = file_name
    
    base_url = webdav_url.rstrip("/")
    remote_root = remote_dir.strip("/")
    
    full_rel_path = f"{remote_root}/{rel_path}".strip("/")
    
    # Standard URL construction
    remote_url = f"{base_url}/{full_rel_path}"
    
    # Manually encoded URL construction (Alternative to test)
    # encoded_path = urllib.parse.quote(full_rel_path)
    # remote_url_encoded = f"{base_url}/{encoded_path}"
    
    print(f"Base URL: {base_url}")
    print(f"Full Rel Path: {full_rel_path}")
    print(f"Remote URL: {remote_url}")

    async def progress_stream():
        yield b"Simulated content for upload."

    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            # 1. MKCOL Logic
            parent_dir = os.path.dirname(full_rel_path).replace("\\", "/")
            print(f"Parent Dir: {parent_dir}")
            
            if parent_dir and parent_dir != ".":
                dirs = parent_dir.split("/")
                current_path = ""
                for d in dirs:
                    if not d: continue
                    current_path = f"{current_path}/{d}"
                    mkcol_url = f"{base_url}{current_path}"
                    print(f"MKCOL attempting: {mkcol_url}")
                    try:
                        async with session.request("MKCOL", mkcol_url) as resp:
                            print(f"  MKCOL Result: {resp.status}")
                            if resp.status not in [201, 405]:
                                print(f"  MKCOL Warning: {resp.status}")
                    except Exception as e:
                        print(f"  MKCOL Error: {e}")

            # 2. PUT Logic
            print(f"PUT attempting: {remote_url}")
            async with session.put(remote_url, data=progress_stream(), headers=headers) as resp:
                print(f"PUT Result: {resp.status}")
                text = await resp.text()
                print(f"PUT Response: {text[:200]}")
                
    except Exception as e:
        print(f"Global Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_full_logic())
