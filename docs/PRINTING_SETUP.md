# Offline Printing (Server Queue + Local USB Agent)

## Overview
- Django server enqueues `PrintJob` rows.
- Each cashier PC runs `print_agent.py`, which polls the server, prints to its own USB ESC/POS printer, and ACKs success/failure.
- Browsers do not print.

## Server setup
1) Apply migrations:
   - `.\.venv\Scripts\python.exe manage.py migrate`
2) Set server environment:
   - `PRINT_AGENT_TOKEN` (required, shared with agents)
   - `PRINT_JOB_MAX_ATTEMPTS` (optional, default 3)
   You can copy `.env.example` to `.env` and load it in your process manager (or set system env vars).
3) On each cashier PC, set a device id in the browser:
   - Open `http://<server-ip>:8000/device/`
   - Set `DEVICE_ID` (examples: `cashier-1`, `cashier-2`)

## Cashier PC (agent) setup (Windows)
1) Install Python 3.11+.
2) Create a venv (recommended):
   - `python -m venv .venv`
   - `.\.venv\Scripts\pip.exe install -r requirements.txt`
3) Create a `.env` next to `print_agent.py`:
   - `SERVER_BASE_URL=http://<server-ip>:8000`
   - `DEVICE_ID=cashier-1`
   - `PRINT_AGENT_TOKEN=<same as server>`
   - Set your USB printer IDs:
     - `PRINTER_USB_VENDOR_ID=0x____`
     - `PRINTER_USB_PRODUCT_ID=0x____`
     - `PRINTER_PROFILE=TM-T88III` (example)
4) Run the agent:
   - `.\.venv\Scripts\python.exe print_agent.py`

## Windows USB notes
- Many ESC/POS printers require a libusb-compatible driver (often installed via Zadig).
- If you get USB backend errors, you may need `libusb-1.0.dll` available to Python (sometimes copying it next to `python.exe` or `print_agent.py` works, depending on your setup).

## How printing works in the POS
- Successful payment automatically queues:
  - Sale receipt (Customer copy)
  - Sale receipt (Store copy)
- Refund queues:
  - Refund receipt (Customer copy)
  - Refund receipt (Store copy)
- “Queue Profit Copy” queues the internal profit report (not for customers).

