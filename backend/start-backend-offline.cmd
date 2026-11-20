@echo off
REM Protocol upload WITHOUT any AI provider: the PDF is read for real, and the analysis comes from
REM the answers prepared for that exact file in app\kit\offline_answers (see app\kit\offline.py).
REM A file with no prepared answer is refused with a clear message; nothing is guessed or sent out.
cd /d "%~dp0"
set TEMP=D:\mtb_tmp2
set TMP=D:\mtb_tmp2
set MONGO_URL=mongodb://127.0.0.1:27017/?replicaSet=rs0
set DB_NAME=mtb_local_dev
set "OPENAI_API_KEY="
set "KIT_SAMPLE_MODEL="
set "KIT_OFFLINE=1"
python -m uvicorn server:app --host 0.0.0.0 --port 8000
