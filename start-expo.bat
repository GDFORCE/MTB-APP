@echo off
REM Starts the Expo dev server and prints the QR code to scan with Expo Go.
REM
REM Before scanning, check frontend\.env: EXPO_PUBLIC_BACKEND_URL must point at
REM THIS PC's LAN address, not localhost or a stale IP. Run `ipconfig` to see
REM the current one. A wrong IP shows up as "Network request failed" in the app
REM with nothing useful in this window, so it is worth checking first.
REM
REM Your phone must be on the same Wi-Fi network as this PC.
title MTB EXPO
cd /d "%~dp0frontend"
echo Starting Expo. Scan the QR below with Expo Go.
echo.
type .env
echo.
npx expo start
pause
