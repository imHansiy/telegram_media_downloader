import asyncio
import aiohttp

async def test_upload(base_url, target_path):
    print(f"\nTesting Upload to {target_path}...")
    try:
        auth = aiohttp.BasicAuth("admin", "2002zengyuan")
        # Ensure base_url ends without slash and target_path starts with slash
        full_url = f"{base_url.rstrip('/')}{target_path}"
        # Add a dummy file name
        full_url = f"{full_url.rstrip('/')}/test_upload_antigravity.txt"
        
        print(f"  Uploading to: {full_url}")
        
        async with aiohttp.ClientSession(auth=auth) as session:
            data = b"Hello, this is a test upload from Antigravity!"
            async with session.put(full_url, data=data) as resp:
                print(f"    Result: {resp.status}")
                print(f"    Reason: {resp.reason}")
                text = await resp.text()
                if text:
                    print(f"    Response: {text[:200]}")
                    
                if resp.status in [200, 201, 204]:
                    print("    SUCCESS: File uploaded.")
                else:
                    print(f"    FAILED: {resp.status}")

    except Exception as e:
        print(f"Error: {e}")

async def main():
    print("Starting WebDAV Upload Test...")
    # Base DAV URL
    base_url = "https://pan.007666.xyz/dav"
    
    # Path requested by user
    target_dir = "/Crypt/资源号OneDrive/telegram"
    
    await test_upload(base_url, target_dir)

if __name__ == "__main__":
    asyncio.run(main())
