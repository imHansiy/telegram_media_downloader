@echo off
REM Activate venv
call .venv\Scripts\activate

REM Set Database URL (Optional: Uncomment and set for remote DB debugging)
REM set DATABASE_URL=postgresql://user:password@host:port/dbname

REM Start Application
python media_downloader.py

pause