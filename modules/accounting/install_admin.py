"""Local installer command. Collect passwords at the terminal, never arguments."""
import argparse
import getpass
import accounting
import control_auth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--role', choices=['admin', 'viewer'], default='admin')
    parser.add_argument('--disable', action='store_true')
    args = parser.parse_args()
    password = getpass.getpass('New password: ')
    if password != getpass.getpass('Repeat new password: '):
        raise SystemExit('Passwords do not match')
    conn = accounting.connect(args.database, create=False)
    try:
        accounting.init_db(conn)
        control_auth.provision(conn, args.username, password, args.role, not args.disable)
    finally:
        conn.close()
    print('Local control identity saved; previous sessions revoked.')


if __name__ == '__main__':
    main()
