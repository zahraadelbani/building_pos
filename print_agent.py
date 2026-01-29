import os
import sys
import time
import pathlib
from typing import Optional

import requests
from dotenv import load_dotenv


# =========================
# Force libusb backend (Windows)
# =========================
def _get_windows_libusb_backend():
    if os.name != "nt":
        return None

    base = pathlib.Path(__file__).resolve().parent

    candidates = [
        base / ".venv" / "Lib" / "site-packages" / "libusb_package" / "libusb-1.0.dll",
        base / ".venv" / "Scripts" / "libusb-1.0.dll",
    ]

    dll = next((p for p in candidates if p.exists()), None)
    if dll is None:
        raise RuntimeError("libusb-1.0.dll not found in venv")

    import usb.backend.libusb1 as libusb1
    backend = libusb1.get_backend(find_library=lambda _: str(dll))

    if backend is None:
        raise RuntimeError(f"Failed to load libusb backend from {dll}")

    return backend


USB_BACKEND = _get_windows_libusb_backend()


# =========================
# Helpers
# =========================
def _env(name: str, default: Optional[str] = None) -> str:
    value = os.environ.get(name)
    return value.strip() if value else (default or "")


def _parse_int(value: str) -> int:
    value = (value or "").strip()
    if value.lower().startswith("0x"):
        return int(value, 16)
    return int(value)


# =========================
# Printer
# =========================
def _build_printer():
    printer_backend = _env("PRINTER_BACKEND", "usb").lower()
    if printer_backend != "usb":
        raise RuntimeError("Only USB printers are supported")

    from escpos.printer import Usb

    vendor_id = _parse_int(_env("PRINTER_USB_VENDOR_ID"))
    product_id = _parse_int(_env("PRINTER_USB_PRODUCT_ID"))

    kwargs = {
        "backend": USB_BACKEND,  # ✅ THIS WAS MISSING
    }

    interface = _env("PRINTER_USB_INTERFACE")
    out_ep = _env("PRINTER_USB_OUT_EP")
    in_ep = _env("PRINTER_USB_IN_EP")
    profile = _env("PRINTER_PROFILE")

    if interface:
        kwargs["interface"] = _parse_int(interface)
    if out_ep:
        kwargs["out_ep"] = _parse_int(out_ep)
    if in_ep:
        kwargs["in_ep"] = _parse_int(in_ep)
    if profile:
        kwargs["profile"] = profile

    return Usb(vendor_id, product_id, **kwargs)


def _print_logo(printer):
    logo_path = _env("RECEIPT_LOGO_PATH")
    if not logo_path:
        return

    if not os.path.exists(logo_path):
        raise FileNotFoundError(f"Logo not found: {logo_path}")

    impl = _env("RECEIPT_LOGO_IMPL", "bitImageColumn")
    width = _env("RECEIPT_LOGO_WIDTH")

    if width:
        from PIL import Image
        target_w = _parse_int(width)
        img = Image.open(logo_path).convert("L")
        w, h = img.size
        new_h = int(h * (target_w / w))
        img = img.resize((target_w, new_h))
        printer.image(img, impl=impl)
    else:
        printer.image(logo_path, impl=impl)


# =========================
# Main loop
# =========================
def main():
    load_dotenv()

    base_url = _env("SERVER_BASE_URL")
    device_id = _env("DEVICE_ID")
    token = _env("PRINT_AGENT_TOKEN")

    if not base_url or not device_id or not token:
        print("Missing env vars", file=sys.stderr)
        return 2

    poll_url = base_url.rstrip("/") + "/api/printing/jobs/poll/"
    delay_seconds = float(_env("PRINTER_PRINT_DELAY_SECONDS", "0.7"))

    session = requests.Session()
    session.headers.update({
        "X-Device-Id": device_id,
        "X-Print-Agent-Token": token,
    })

    while True:
        try:
            resp = session.get(poll_url, params={"limit": 5}, timeout=10)
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])
        except Exception as e:
            print(f"[poll] error: {e}", file=sys.stderr)
            time.sleep(2)
            continue

        if not jobs:
            time.sleep(1)
            continue

        for job in jobs:
            job_id = job["id"]
            receipt_text = job.get("payload", {}).get("receipt_text", "")

            try:
                printer = _build_printer()
                _print_logo(printer)
                printer.text(receipt_text)
                printer.cut()
                printer.close()

                session.post(
                    f"{base_url}/api/printing/jobs/{job_id}/ack-printed/",
                    timeout=10,
                ).raise_for_status()

                time.sleep(delay_seconds)

            except Exception as e:
                session.post(
                    f"{base_url}/api/printing/jobs/{job_id}/ack-failed/",
                    json={"error": str(e)},
                    timeout=10,
                )
                print(f"[print] job {job_id} failed: {e}", file=sys.stderr)
                time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
