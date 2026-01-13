import os
import sys
import time
from typing import Optional

import requests
from dotenv import load_dotenv


def _env(name: str, default: Optional[str] = None) -> str:
    value = os.environ.get(name)
    if value is None:
        return default or ""
    return value.strip()


def _parse_int(value: str) -> int:
    value = (value or "").strip()
    if not value:
        raise ValueError("empty")
    if value.lower().startswith("0x"):
        return int(value, 16)
    return int(value)


def _build_printer():
    backend = _env("PRINTER_BACKEND", "usb").lower()
    if backend != "usb":
        raise RuntimeError(f"Unsupported PRINTER_BACKEND={backend}. Only 'usb' is supported.")

    from escpos.printer import Usb  # pylint: disable=import-outside-toplevel

    vendor_id = _parse_int(_env("PRINTER_USB_VENDOR_ID"))
    product_id = _parse_int(_env("PRINTER_USB_PRODUCT_ID"))

    interface = _env("PRINTER_USB_INTERFACE", "")
    out_ep = _env("PRINTER_USB_OUT_EP", "")
    in_ep = _env("PRINTER_USB_IN_EP", "")
    profile = _env("PRINTER_PROFILE", "")

    kwargs = {}
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
    logo_path = _env("RECEIPT_LOGO_PATH", "")
    if not logo_path:
        return
    if not os.path.exists(logo_path):
        raise FileNotFoundError(f"Logo not found: {logo_path}")

    impl = _env("RECEIPT_LOGO_IMPL", "bitImageColumn") or "bitImageColumn"
    width = _env("RECEIPT_LOGO_WIDTH", "")

    if width:
        from PIL import Image  # pylint: disable=import-outside-toplevel

        target_w = _parse_int(width)
        img = Image.open(logo_path).convert("L")
        w, h = img.size
        if w != target_w and w > 0:
            new_h = max(1, int(h * (target_w / float(w))))
            img = img.resize((target_w, new_h))
        printer.image(img, impl=impl)
    else:
        printer.image(logo_path, impl=impl)


def main():
    load_dotenv()

    base_url = _env("SERVER_BASE_URL")
    device_id = _env("DEVICE_ID")
    token = _env("PRINT_AGENT_TOKEN")

    if not base_url or not device_id or not token:
        print("Missing required env vars: SERVER_BASE_URL, DEVICE_ID, PRINT_AGENT_TOKEN", file=sys.stderr)
        return 2

    poll_url = base_url.rstrip("/") + "/api/printing/jobs/poll/"
    delay_seconds = float(_env("PRINTER_PRINT_DELAY_SECONDS", "0.7") or "0.7")

    session = requests.Session()
    session.headers.update(
        {
            "X-Device-Id": device_id,
            "X-Print-Agent-Token": token,
        }
    )

    while True:
        try:
            resp = session.get(poll_url, params={"limit": 5}, timeout=10)
            resp.raise_for_status()
            jobs = resp.json().get("jobs") or []
        except Exception as e:
            print(f"[poll] error: {e}", file=sys.stderr)
            time.sleep(2)
            continue

        if not jobs:
            time.sleep(1)
            continue

        for job in jobs:
            job_id = job.get("id")
            payload = job.get("payload") or {}
            receipt_text = payload.get("receipt_text") or ""

            ack_printed_url = base_url.rstrip("/") + f"/api/printing/jobs/{job_id}/ack-printed/"
            ack_failed_url = base_url.rstrip("/") + f"/api/printing/jobs/{job_id}/ack-failed/"

            try:
                printer = _build_printer()
                _print_logo(printer)
                printer.text(receipt_text)
                printer.cut()
                printer.close()

                session.post(ack_printed_url, timeout=10).raise_for_status()
                time.sleep(delay_seconds)
            except Exception as e:
                try:
                    session.post(ack_failed_url, json={"error": str(e)}, timeout=10).raise_for_status()
                except Exception as e2:
                    print(f"[ack-failed] error: {e2}", file=sys.stderr)
                print(f"[print] job {job_id} failed: {e}", file=sys.stderr)
                time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())

