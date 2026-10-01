@echo off
REM รัน AS400 RPA เวอร์ชัน EHLLAPI ด้วย Python 32-bit ใน py32\ (ต้องมี ehlapi32.dll 32-bit + pyodbc 32-bit)
REM เปิดจอ 5250 (pcsws) ให้ sign-in ไว้ก่อนรัน
cd /d "%~dp0"
"%~dp0py32\python.exe" "%~dp0as400_rpa_ehllapi.py" %*
pause
