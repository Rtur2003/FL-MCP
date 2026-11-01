"""Back-office commands: python -m flmcp_service.admin <command> (uses the FLMCP_* settings)."""

import argparse
import json

from flmcp_service import accounts, ledger, plans
from flmcp_service.app import Settings
from flmcp_service.db import DB


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="flmcp-admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    lic = sub.add_parser("create-license", help="issue a licence key (shown once)")
    lic.add_argument("--email", required=True)
    lic.add_argument("--plan", required=True)
    lic.add_argument("--days", type=int, help="length of the licence; default: the plan's")
    grant = sub.add_parser("grant", help="add credits to an account")
    grant.add_argument("--email", required=True)
    grant.add_argument("--credits", type=int, required=True)
    grant.add_argument("--days", type=int, help="credits expire after this many days")
    show = sub.add_parser("show", help="balance, licences and recent movements")
    show.add_argument("--email", required=True)
    sub.add_parser(
        "outbox", help="print the licence keys waiting to be emailed, then mark them sent"
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    cfg, db = plans.load(settings.config), DB(settings.db)
    with db.tx() as conn:
        now = db.now()
        if args.cmd == "create-license":
            aid = accounts.account_for(conn, args.email, now)
            end = now + args.days * 86400 if args.days else None
            lid, key = accounts.create_license(conn, aid, args.plan, now, cfg, end)
            row = conn.execute("SELECT * FROM licenses WHERE id=?", (lid,)).fetchone()
            accounts.start_period(conn, row, now, row["period_end"], now, cfg)
            print(key)
        elif args.cmd == "grant":
            aid = accounts.account_for(conn, args.email, now)
            end = now + args.days * 86400 if args.days else None
            ledger.grant(conn, aid, "adjust", args.credits, now, end)
            print(ledger.balance(conn, aid, now))
        elif args.cmd == "show":
            row = conn.execute(
                "SELECT id FROM accounts WHERE email=?", (args.email.strip().lower(),)
            ).fetchone()
            if row is None:
                raise SystemExit("no such account")
            ledger.sweep(conn, row["id"], now, cfg)
            lics = conn.execute(
                "SELECT plan, status, period_end FROM licenses WHERE account_id=?", (row["id"],)
            ).fetchall()
            print(
                json.dumps(
                    {
                        "balance": ledger.balance(conn, row["id"], now),
                        "licenses": [dict(r) for r in lics],
                        "entries": ledger.history(conn, row["id"]),
                    },
                    indent=2,
                )
            )
        else:
            rows = conn.execute("SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY id").fetchall()
            for r in rows:
                print(r["payload"])
                conn.execute("UPDATE outbox SET sent_at=?, payload='{}' WHERE id=?", (now, r["id"]))


if __name__ == "__main__":
    main()
