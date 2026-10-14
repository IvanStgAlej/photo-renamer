@echo off
cd /d "%~dp0"
python -c "import exifread" 2>nul || python -m pip install "exifread>=3.0"
start "" pythonw -m photo_renamer
