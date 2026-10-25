@echo off
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller pyserial matplotlib
python -m PyInstaller --noconfirm --clean --onefile --windowed --name SvasaLens --hidden-import matplotlib.backends.backend_tkagg --add-data "fonts;fonts" svasalens_dashboard.py
echo.
echo Done. Your app is in the "dist" folder: dist\SvasaLens.exe
pause
