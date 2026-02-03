"""provide upload cloud drive"""
import asyncio
import functools
import importlib
import inspect
import os
import re
from asyncio import subprocess
from subprocess import Popen
from typing import Callable
from zipfile import ZipFile
import urllib.parse

from loguru import logger

from utils import platform
import aiohttp



# pylint: disable = R0902
class CloudDriveConfig:
    """Rclone Config"""

    def __init__(
        self,
        enable_upload_file: bool = False,
        before_upload_file_zip: bool = False,
        after_upload_file_delete: bool = True,
        rclone_path: str = os.path.join(
            os.path.abspath("."), "rclone", f"rclone{platform.get_exe_ext()}"
        ),
        remote_dir: str = "",
        upload_adapter: str = "rclone",
        webdav_url: str = "",
        webdav_username: str = "",
        webdav_password: str = "",
    ):
        self.enable_upload_file = enable_upload_file
        self.before_upload_file_zip = before_upload_file_zip
        self.after_upload_file_delete = after_upload_file_delete
        self.rclone_path = rclone_path
        self.remote_dir = remote_dir
        self.upload_adapter = upload_adapter
        self.webdav_url = webdav_url
        self.webdav_username = webdav_username
        self.webdav_password = webdav_password
        self.before_upload_file_zip = before_upload_file_zip
        self.after_upload_file_delete = after_upload_file_delete
        self.rclone_path = rclone_path
        self.remote_dir = remote_dir
        self.upload_adapter = upload_adapter
        self.dir_cache: dict = {}  # for remote mkdir
        self.total_upload_success_file_count = 0
        self.aligo = None

    def pre_run(self):
        """pre run init aligo"""
        if self.enable_upload_file and self.upload_adapter == "aligo":
            CloudDrive.init_upload_adapter(self)


class CloudDrive:
    """rclone support"""

    @staticmethod
    def init_upload_adapter(drive_config: CloudDriveConfig):
        """Initialize the upload adapter."""
        if drive_config.upload_adapter == "aligo":
            Aligo = importlib.import_module("aligo").Aligo
            drive_config.aligo = Aligo()

    @staticmethod
    def rclone_mkdir(drive_config: CloudDriveConfig, remote_dir: str):
        """mkdir in remote"""
        with Popen(
            f'"{drive_config.rclone_path}" mkdir "{remote_dir}/"',
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        ):
            pass

    @staticmethod
    def aligo_mkdir(drive_config: CloudDriveConfig, remote_dir: str):
        """mkdir in remote by aligo"""
        if drive_config.aligo and not drive_config.aligo.get_folder_by_path(remote_dir):
            drive_config.aligo.create_folder(name=remote_dir, check_name_mode="refuse")

    @staticmethod
    def zip_file(local_file_path: str) -> str:
        """
        Zip local file
        """

        file_path_without_extension = os.path.splitext(local_file_path)[0]
        zip_file_name = file_path_without_extension + ".zip"

        with ZipFile(zip_file_name, "w") as zip_writer:
            zip_writer.write(local_file_path)

        return zip_file_name

    # pylint: disable = R0914
    @staticmethod
    async def rclone_upload_file(
        drive_config: CloudDriveConfig,
        save_path: str,
        local_file_path: str,
        progress_callback: Callable = None,
        progress_args: tuple = (),
    ) -> bool:
        """Use Rclone upload file"""
        upload_status: bool = False
        try:
            remote_dir = (
                drive_config.remote_dir
                + "/"
                + os.path.dirname(local_file_path).replace(save_path, "")
                + "/"
            ).replace("\\", "/")

            if not drive_config.dir_cache.get(remote_dir):
                CloudDrive.rclone_mkdir(drive_config, remote_dir)
                drive_config.dir_cache[remote_dir] = True

            zip_file_path: str = ""
            file_path = local_file_path
            if drive_config.before_upload_file_zip:
                zip_file_path = CloudDrive.zip_file(local_file_path)
                file_path = zip_file_path
            else:
                file_path = local_file_path

            cmd = (
                f'"{drive_config.rclone_path}" copy "{file_path}" '
                f'"{remote_dir}/" --create-empty-src-dirs --ignore-existing --progress'
            )
            proc = await asyncio.create_subprocess_shell(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            if proc.stdout:
                async for output in proc.stdout:
                    s = output.decode(errors="replace")
                    print(s)
                    if "Transferred" in s and "100%" in s and "1 / 1" in s:
                        logger.info(f"upload file {local_file_path} success")
                        drive_config.total_upload_success_file_count += 1
                        if drive_config.after_upload_file_delete:
                            os.remove(local_file_path)
                        if drive_config.before_upload_file_zip:
                            os.remove(zip_file_path)
                        upload_status = True
                    else:
                        pattern = (
                            r"Transferred: (.*?) / (.*?), (.*?)%, (.*?/s)?, ETA (.*?)$"
                        )
                        transferred_match = re.search(pattern, s)

                        if transferred_match:
                            if progress_callback:
                                func = functools.partial(
                                    progress_callback,
                                    transferred_match.group(1),
                                    transferred_match.group(2),
                                    transferred_match.group(3),
                                    transferred_match.group(4),
                                    transferred_match.group(5),
                                    *progress_args,
                                )

                            if inspect.iscoroutinefunction(progress_callback):
                                await func()

            await proc.wait()
        except Exception as e:
            logger.error(f"{e.__class__} {e}")
            return False

        return upload_status

    @staticmethod
    def aligo_upload_file(
        drive_config: CloudDriveConfig, save_path: str, local_file_path: str
    ):
        """aliyun upload file"""
        upload_status: bool = False
        if not drive_config.aligo:
            logger.warning("please config aligo! see README.md")
            return False

        try:
            remote_dir = (
                drive_config.remote_dir
                + "/"
                + os.path.dirname(local_file_path).replace(save_path, "")
                + "/"
            ).replace("\\", "/")

            if not drive_config.dir_cache.get(remote_dir):
                CloudDrive.aligo_mkdir(drive_config, remote_dir)
                aligo_dir = drive_config.aligo.get_folder_by_path(remote_dir)
                if aligo_dir:
                    drive_config.dir_cache[remote_dir] = aligo_dir.file_id

            zip_file_path: str = ""
            file_paths = []
            if drive_config.before_upload_file_zip:
                zip_file_path = CloudDrive.zip_file(local_file_path)
                file_paths.append(zip_file_path)
            else:
                file_paths.append(local_file_path)

            res = drive_config.aligo.upload_files(
                file_paths=file_paths,
                parent_file_id=drive_config.dir_cache[remote_dir],
                check_name_mode="refuse",
            )

            if len(res) > 0:
                drive_config.total_upload_success_file_count += len(res)
                if drive_config.after_upload_file_delete:
                    os.remove(local_file_path)

                if drive_config.before_upload_file_zip:
                    os.remove(zip_file_path)

                upload_status = True

        except Exception as e:
            logger.error(f"{e.__class__} {e}")
            return False

        return upload_status

    @staticmethod
    async def upload_file(
        drive_config: CloudDriveConfig, save_path: str, local_file_path: str
    ) -> bool:
        """Upload file
        Parameters
        ----------
        drive_config: CloudDriveConfig
            see @CloudDriveConfig

        save_path: str
            Local file save path config

        local_file_path: str
            Local file path

        Returns
        -------
        bool
            True or False
        """
        if not drive_config.enable_upload_file:
            return False

        ret: bool = False
        if drive_config.upload_adapter == "rclone":
            ret = await CloudDrive.rclone_upload_file(
                drive_config, save_path, local_file_path
            )
        elif drive_config.upload_adapter == "aligo":
            ret = CloudDrive.aligo_upload_file(drive_config, save_path, local_file_path)

        return ret

    @staticmethod
    async def webdav_upload_stream(
        drive_config: CloudDriveConfig,
        save_path: str,
        file_name: str,
        stream_generator,
        total_size: int,
        progress_callback: Callable = None,
        progress_args: tuple = (),
        max_retries: int = 3,
    ) -> bool:
        """Stream upload to WebDAV with retry support"""
        if not drive_config.webdav_url:
            logger.error("WebDAV URL is not configured")
            return False

        # Streaming Upload
        auth = None
        if drive_config.webdav_username:
            auth = aiohttp.BasicAuth(drive_config.webdav_username, drive_config.webdav_password)

        headers = {
            "Content-Type": "application/octet-stream",
            "User-Agent": "TelegramMediaDownloader/1.0",
            "Expect": "",  # Disable Expect: 100-continue to avoid hangs with some WebDAV servers
        }

        # Calculate relative path to preserve folder structure (e.g. ChatName/File.mp4)
        try:
            # handle cases where file_name might be absolute or relative
            if os.path.isabs(file_name) and save_path:
                rel_path = os.path.relpath(file_name, save_path)
            else:
                 # fallback if it's already relative or save_path mismatch
                rel_path = os.path.basename(file_name)
        except Exception:
            rel_path = os.path.basename(file_name)
            
        # Normalize to forward slashes for WebDAV
        rel_path = rel_path.replace("\\", "/")
        
        # Construct base remote URL
        base_url = drive_config.webdav_url.rstrip("/")
        remote_root = drive_config.remote_dir.strip("/")
        
        # Full path to the file on WebDAV (without protocol) -> used for splitting directories
        # e.g. Crypt/OneDrive/Telegram/ChannelName/Video.mp4
        full_rel_path = f"{remote_root}/{rel_path}".strip("/")
        
        # Final URL
        # Explicitly encode path segments to handle special chars/Chinese correctly
        # Split by / to preserve directory structure, then quote each component
        parts = full_rel_path.split("/")
        # quote each part but keep / separators
        encoded_path = "/".join(urllib.parse.quote(p) for p in parts)
        remote_url = f"{base_url}/{encoded_path}"
        
        logger.info(f"[WebDAV] Uploading to (Encoded): {remote_url}")
        if remote_url != f"{base_url}/{full_rel_path}":
             logger.info(f"[WebDAV] Original Path was: {full_rel_path}")

        # Wrap the generator to report progress
        async def progress_stream():
            uploaded = 0
            async for chunk in stream_generator:
                yield chunk
                uploaded += len(chunk)
                if progress_callback:
                     if inspect.iscoroutinefunction(progress_callback):
                         await progress_callback(uploaded, total_size, *progress_args)
                     else:
                         progress_callback(uploaded, total_size, *progress_args)

        # 10s for connect, 2 hours for total upload. Large videos need more time.
        timeout = aiohttp.ClientTimeout(total=7200, connect=10)

        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession(auth=auth, timeout=timeout) as session:
                    # 1. Ensure parent directories exist (MKCOL) - use cache to avoid duplicates
                    parent_dir = os.path.dirname(full_rel_path).replace("\\", "/")
                    if parent_dir and parent_dir != ".":
                        dirs = parent_dir.split("/")
                        current_path_parts = []
                        for d in dirs:
                            if not d: continue
                            current_path_parts.append(urllib.parse.quote(d))
                            # Join quoted parts
                            encoded_current_path = "/".join(current_path_parts)
                            mkcol_url = f"{base_url}/{encoded_current_path}"
                            
                            # Check cache to avoid duplicate MKCOL requests
                            if drive_config.dir_cache.get(encoded_current_path):
                                continue
                                
                            try:
                                async with session.request("MKCOL", mkcol_url) as resp:
                                    if resp.status == 201:
                                        # Successfully created
                                        drive_config.dir_cache[encoded_current_path] = True
                                        logger.info(f"[WebDAV] Created directory: {mkcol_url}")
                                    elif resp.status == 405:
                                        # Already exists (Method Not Allowed for existing dir)
                                        drive_config.dir_cache[encoded_current_path] = True
                                    elif resp.status == 423:
                                        # Locked - wait and continue, another process is creating it
                                        logger.warning(f"[WebDAV] Directory locked, waiting: {mkcol_url}")
                                        await asyncio.sleep(1)
                                        drive_config.dir_cache[encoded_current_path] = True
                                    else:
                                        logger.warning(f"[WebDAV] MKCOL {mkcol_url} returned {resp.status}")
                                        # Still mark as attempted to avoid infinite loops
                                        drive_config.dir_cache[encoded_current_path] = True
                            except Exception as e:
                                logger.warning(f"[WebDAV] MKCOL error: {e}")
                                # Mark as attempted anyway
                                drive_config.dir_cache[encoded_current_path] = True
                    
                    # 2. PUT stream
                    # Set Content-Length if size is known to help some WebDAV servers
                    if total_size > 0:
                        headers["Content-Length"] = str(total_size)
                        
                    async with session.put(remote_url, data=progress_stream(), headers=headers) as resp:
                        if resp.status in [200, 201, 204]:
                            logger.info(f"WebDAV upload success: {rel_path}")
                            return True
                        elif resp.status == 423:
                            # Locked - retry after delay
                            logger.warning(f"[WebDAV] File locked (423), retry {attempt + 1}/{max_retries}")
                            await asyncio.sleep(2 * (attempt + 1))  # Exponential backoff
                            continue
                        else:
                            text = await resp.text()
                            logger.error(f"WebDAV upload failed: {resp.status} - {text[:500]}")
                            return False
            except asyncio.TimeoutError:
                logger.error(f"WebDAV upload timeout (attempt {attempt + 1}/{max_retries}): {rel_path}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                return False
            except aiohttp.ClientError as e:
                logger.error(f"WebDAV connection error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                return False
            except Exception as e:
                logger.error(f"WebDAV unexpected error: {type(e).__name__}: {e}")
                import traceback
                logger.debug(traceback.format_exc())
                return False
        
        logger.error(f"WebDAV upload failed after {max_retries} retries: {rel_path}")
        return False

    @staticmethod
    async def test_webdav_connection(url: str, username: str, password: str) -> tuple[bool, str]:
        """Test WebDAV connectivity"""
        if not url:
            return False, "URL is empty"
        
        try:
            auth = None
            if username:
                auth = aiohttp.BasicAuth(username, password)
            
            async with aiohttp.ClientSession(auth=auth) as session:
                # 1. Try PROPFIND
                headers = {"Depth": "0", "Content-Type": "text/xml"} 
                
                check_urls = [url]
                if not url.endswith("/"):
                    check_urls.append(url + "/")
                
                for check_url in check_urls:
                    try:
                        async with session.request("PROPFIND", check_url, headers=headers) as resp:
                            if resp.status in [200, 207]:
                                return True, "Connection successful"
                            elif resp.status == 401:
                                return False, "Authentication failed (401)"
                            
                            # If 405 Method Not Allowed, try OPTIONS on this URL
                            if resp.status == 405:
                                async with session.request("OPTIONS", check_url) as opt_resp:
                                    if opt_resp.status in [200, 204]:
                                        dav = opt_resp.headers.get("DAV", "")
                                        if dav:
                                            return True, f"Connection successful (DAV: {dav})"
                                        return True, "Connection successful (OPTIONS)"
                                    elif opt_resp.status == 401:
                                        return False, "Authentication failed (401)"

                    except Exception as e:
                        logger.warning(f"PROPFIND failed for {check_url}: {e}")
                        continue

                return False, f"Connection failed (HTTP {resp.status if 'resp' in locals() else 'Unknown'})"

        except Exception as e:
            return False, f"Connection Failed: {str(e)}"
