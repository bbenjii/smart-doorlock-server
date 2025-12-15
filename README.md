# Smart Lock – Backend Server

This directory contains the backend server for the **Smart Lock** project.  
The server is responsible for handling API requests, WebSocket connections, and communication with smart lock devices and the mobile app.

---

## Prerequisites

Make sure you have the following installed:

- Python 3.10+ (recommended)
- pip
- virtualenv support (included with Python)

---

## Backend Setup

### 1. Create a virtual environment

From the backend project root:

```bash
python -m venv .venv
````

---

### 2. Activate the virtual environment

**macOS / Linux**

```bash
source .venv/bin/activate
```

**Windows (PowerShell)**

```powershell
.venv\Scripts\Activate.ps1
```

---

### 3. Install dependencies

With the virtual environment activated:

```bash
python -m pip install -r requirements.txt
```

---

## Running the Server

Once dependencies are installed, start the server using the appropriate entry point (for example with FastAPI + Uvicorn):

```bash
python main.py
```

---

## Notes

* Always activate the virtual environment before running the server.
* If the mobile app is running on a physical device, make sure the server is bound to your local IP.
* The backend URL should match the `EXPO_PUBLIC_API_URL` value used by the mobile app, "http://<your-computer-ip>:8000".

---

## Project Context

This backend is part of the **Smart Lock** system and works together with:

* The React Native mobile app
* The Smart Lock hardware devices


---


