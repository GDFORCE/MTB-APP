# MTB-APP

Clinical-trials app: Expo (React Native) frontend + FastAPI/MongoDB backend.

Active development happens on the `full-app-build` branch.

## Prerequisites
- Git
- Node.js 20+ (`node -v`)
- Python 3.11+ (`python --version`)
- **Expo Go** app installed on the phone (Play Store / App Store)
- A MongoDB Atlas connection string
- Phone and laptop on the **same Wi-Fi network**

## 1. Clone
```
git clone https://github.com/GDFORCE/MTB-APP.git
cd MTB-APP
git checkout full-app-build
```

## 2. Backend (FastAPI)
```
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env        # Windows   (Mac/Linux: cp .env.example .env)
```
Open `backend/.env` and fill in at least `MONGO_URL`, `JWT_SECRET`,
`JWT_REFRESH_SECRET` (see comments in the file). Keep `DEV_OTP_MODE=true`
for testing — signup OTPs are then the fixed `DEV_OTP_CODE`.

Start it (bound to all interfaces so the phone can reach it):
```
uvicorn server:app --host 0.0.0.0 --port 8000
```

## 3. Frontend (Expo)
In a second terminal:
```
cd frontend
npm install
copy .env.example .env        # Windows   (Mac/Linux: cp .env.example .env)
```
Open `frontend/.env` and set `EXPO_PUBLIC_BACKEND_URL` to the laptop's LAN IP,
e.g. `http://192.168.1.5:8000` (find the IP with `ipconfig` / `ifconfig`).

Start Expo:
```
npx expo start
```
Scan the QR code with the phone — Expo Go opens the app.

## Troubleshooting
- **Phone can't connect / network error in app**: laptop firewall is usually
  blocking Node or Python. Allow them through the firewall (Windows prompts on
  first run — choose Allow), and double-check both devices share the same Wi-Fi.
- **QR scan opens but bundle never loads**: try `npx expo start --tunnel`
  (works across networks, slower).
- **Changed `.env`**: restart `expo start` — Expo only reads env vars at startup.
