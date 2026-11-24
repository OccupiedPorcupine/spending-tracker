"""`spend` command-line tool, run inside the container:

    docker compose exec app spend enroll     print a one-time passkey enrolment code (15 minutes)
    docker compose exec app spend passkeys   list registered passkeys
    docker compose exec app spend sessions --end-all   sign out every device
    docker compose exec app spend ledger     ledger counts and the last 10 entries (IDs and statuses only)
"""
import argparse
import datetime as dt

from .auth import CODE_MAX_FAILURES, CODE_TTL_MS, new_enroll_code
from .db import connect, migrate


def _ts(ms):
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M") if ms else "-"


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="spend")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("enroll", help="print a one-time code for registering a passkey")
    sub.add_parser("passkeys", help="list registered passkeys")
    s = sub.add_parser("sessions", help="list or end sessions")
    s.add_argument("--end-all", action="store_true")
    sub.add_parser("ledger", help="ledger summary")
    a = p.parse_args(argv)

    conn = connect()
    migrate(conn)
    try:
        if a.cmd == "enroll":
            code = new_enroll_code(conn)
            conn.commit()
            print(f"\n  Enrolment code: {code}\n")
            print(f"  Valid for {CODE_TTL_MS // 60000} minutes, for one passkey. {CODE_MAX_FAILURES} wrong tries burn it.")
            print("  Open https://spend.munyau.space/enroll on the device and enter it.\n")
        elif a.cmd == "passkeys":
            for r in conn.execute("SELECT * FROM credentials ORDER BY created_at"):
                print(f"{r['id'][:12]}…  {r['name']:<20} created {_ts(r['created_at'])}  last used {_ts(r['last_used'])}")
        elif a.cmd == "sessions":
            if a.end_all:
                n = conn.execute("DELETE FROM sessions").rowcount
                conn.commit()
                print(f"ended {n} sessions")
            for r in conn.execute("SELECT * FROM sessions ORDER BY last_seen DESC"):
                print(f"{r['id_hash'][:10]}…  created {_ts(r['created_at'])}  last seen {_ts(r['last_seen'])}")
        elif a.cmd == "ledger":
            for r in conn.execute("SELECT status, COUNT(*) n FROM entries GROUP BY status"):
                print(f"{r['status']:<8} {r['n']}")
            for r in conn.execute("SELECT id, status, sheet_row, attempts, created_at FROM entries ORDER BY created_at DESC LIMIT 10"):
                print(f"{r['id']}  {r['status']:<8} row {r['sheet_row'] or '-':<5} tries {r['attempts']}  {_ts(r['created_at'])}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
