@echo off
cd /d C:\Users\nithi\zerodhaBot
powershell.exe -WindowStyle Hidden -Command "Start-Process -FilePath 'C:\Users\nithi\AppData\Local\Programs\Python\Python312\pythonw.exe' -ArgumentList 'C:\Users\nithi\zerodhaBot\watchdog.py' -WorkingDirectory 'C:\Users\nithi\zerodhaBot' -WindowStyle Hidden"
