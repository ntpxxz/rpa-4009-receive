@echo off
REM Build as400-rpa-ehllapi.exe (32-bit) and copy to release\
REM ต้องมี py32\ อยู่ใน parent folder (D:\TJ825\S_APP\as400-rpa\py32\)
REM หรือเปลี่ยน PY32 ด้านล่างให้ชี้ถูก

set PY32=D:\TJ825\S_APP\as400-rpa\py32\python.exe
set SPEC=as400-rpa-ehllapi.spec
set RELEASE=%~dp0release

echo [1/3] Building...
"%PY32%" -m PyInstaller "%SPEC%" --clean --noconfirm
if errorlevel 1 ( echo BUILD FAILED & pause & exit /b 1 )

echo [2/3] Copying to release\...
if not exist "%RELEASE%" mkdir "%RELEASE%"
if not exist "%RELEASE%\logs" mkdir "%RELEASE%\logs"
copy /y "%~dp0dist\as400-rpa-ehllapi.exe" "%RELEASE%\"

echo [3/3] Done.
echo   EXE : %RELEASE%\as400-rpa-ehllapi.exe
echo   Fill in %RELEASE%\.env before deploying.
pause
