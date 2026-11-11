"""Local installer status and session revocation. No password is needed or printed.

This is not an HTTP endpoint. Anyone able to run it already needs trusted access
to the configured local book file. It does not provide CRM/business isolation.
"""
import argparse
import json
import accounting
import control_auth


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('action', choices=['status', 'revoke-sessions', 'disable'])
    args = parser.parse_args(argv)
    conn = accounting.connect(args.database, create=False)
    try:
        # Read and validate an already-current book, without migrating it.
        if accounting.schema_version(conn) != accounting.latest_version():
            raise ValueError("Upgrade the local book separately before managing identities")
        accounting.verify_schema(conn)
        if args.action == 'status':
            result = control_auth.identity_status(conn, args.username)
        else:
            fn = control_auth.revoke_sessions if args.action == 'revoke-sessions' else control_auth.disable_identity
            result = {'action': args.action, 'sessions_revoked': fn(conn, args.username)}
        print(json.dumps(result, sort_keys=True))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
