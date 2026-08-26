"""Run the real site against a throwaway database, for trying things out.

Your real orders live in data/ as JSON files. Setting DATABASE_URL sends every
read and write to SQLite instead, so this cannot open those files at all -- not
by accident, not on a bad code path. Delete sandbox.db to start over.

    python sandbox.py          (or double-click Sandbox.bat)
"""

import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "sandbox.db"

# Set before importing app: store.py reads DATABASE_URL when it builds the engine.
os.environ["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("SECRET_KEY", "sandbox-only-not-a-real-secret")
os.environ.setdefault("HOST", "0.0.0.0")        # reachable from your phone

# Deliberately NOT 8420. The real app uses that, and Windows sockets allow two
# processes to bind the same port -- neither one errors, and which of them
# answers any given request is anyone's guess. A sandbox request silently
# served by the real app would write to real orders, so we stay off its port
# and additionally refuse to start if anything is already answering (below).
PORT = int(os.environ.get("PORT", 8421))


def port_is_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def lan_address():
    """The IP a phone on the same wifi would use. Connecting a UDP socket picks
    the interface the OS would route out of without sending a packet, which
    beats guessing when there are several adapters (wifi, ethernet, VPN)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def seed(app_module):
    """A few fake orders so the page isn't empty. Only ever on a fresh database."""
    import store

    today = app_module.core.today_str()
    if store.load_day(today)["orders"]:
        return
    with store.edit_day(today) as day:
        day["place"] = "Sandbox Diner"
        for name, desc, method in [
            ("Alex", "Chicken katsu", "cash"),
            ("Sam", "Saimin", "cash"),
            ("Jo", "Garlic shrimp plate", "venmo"),
        ]:
            order = {"name": name, "items": [{"desc": desc, "price_cents": None}],
                     "paid_cents": None, "method": method}
            if method == "venmo":
                order["venmo_user"] = "@jo-example"
            day["orders"].append(order)
    print("  seeded 3 example orders")


def main():
    if port_is_busy(PORT):
        sys.exit(f"Port {PORT} is already in use — something is answering on it.\n"
                 f"Stop it first, or run with a different port:\n"
                 f"    set PORT=8422 && python sandbox.py\n"
                 f"Refusing to start: sharing a port risks sending sandbox "
                 f"requests to the real app.")

    fresh = not DB.exists()
    import app as webapp

    if fresh:
        seed(webapp)

    ip = lan_address()
    bar = "-" * 62
    print(f"\n{bar}")
    print("  SANDBOX — your real orders are not loaded and cannot be touched.")
    print(f"  Everything here is written to {DB.name}, which git ignores.")
    print(bar)
    print(f"\n  This PC     http://127.0.0.1:{PORT}")
    if ip:
        print(f"  Your phone  http://{ip}:{PORT}          <- same wifi")
    else:
        print("  Your phone  (couldn't work out this machine's network address)")
    print(f"\n  Admin       http://127.0.0.1:{PORT}/admin"
          f"   password: {os.environ['ADMIN_PASSWORD']}")
    print(f"\n  Windows may ask to allow Python through the firewall — say yes to")
    print("  Private networks, or the phone URL won't answer.")
    print(f"\n  Ctrl+C to stop. Delete {DB.name} to wipe the sandbox.\n{bar}\n")

    webapp.app.run(host=os.environ["HOST"], port=PORT, debug=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSandbox stopped. Your real data was never opened.")
        sys.exit(0)
