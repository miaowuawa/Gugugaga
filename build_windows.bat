@echo off
setlocal

REM Build a self-contained Windows console executable from this repository root.
REM Python 3.9+ on Windows is required. The resulting file is dist\QigumiGrabber.exe.
py -m pip install --upgrade pip
if errorlevel 1 goto :error
py -m pip install -r requirements.txt
if errorlevel 1 goto :error
py -m pip install --upgrade pyinstaller
if errorlevel 1 goto :error

py -m PyInstaller --noconfirm --clean --onefile --console --name QigumiGrabber --paths "%CD%\.." --hidden-import qigumi_grabber.grab_window --hidden-import qigumi_grabber.alipay --hidden-import qigumi_grabber.serverchan --hidden-import qigumi_grabber.proxy --hidden-import qigumi_grabber.notify --hidden-import win32com.client --hidden-import pythoncom --hidden-import pywintypes __main__.py
if errorlevel 1 goto :error

echo.
echo Build succeeded: %CD%\dist\QigumiGrabber.exe
exit /b 0

:error
echo.
echo Build failed. Review the error above.
exit /b 1
