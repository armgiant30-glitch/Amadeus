@echo off
rem Companion-only launch: no wallpaper host, no Slice, no Canvas, no visible
rem main window. The backend starts the repository-owned Tk Companion card and
rem keeps the existing session, context, ASR/TTS and subtitle contracts.
chcp 65001 >NUL
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set LANG=zh_CN.UTF-8
set OPEN_JTALK_DICT_DIR=%~dp0open_jtalk_dic\open_jtalk_dic_utf_8-1.11
set RAG_ENABLED_FOR_LOCAL=0
set VTS_ENABLED=0
set VTS_HEARTBEAT_ENABLED=0
set VTS_RECONNECT_ENABLED=0
set AMADEUS_COMPANION=1
cd /d "%~dp0electron"
npm run electron:dev
