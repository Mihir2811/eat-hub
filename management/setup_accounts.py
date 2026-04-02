# =============================================================================
# setup_accounts.py - Interactive first-run account setup
# =============================================================================

import getpass
from functions import init_db, create_user, get_user_by_username


def prompt(label: str, required: bool = True) -> str:
    while True:
        val = input(f"  {label}: ").strip()
        if val or not required:
            return val
        print("  This field is required.")


def prompt_password(label: str = "Password") -> str:
    while True:
        pw = getpass.getpass(f"  {label}: ")
        if len(pw) < 6:
            print("  Password must be at least 6 characters.")
            continue
        confirm = getpass.getpass(f"  Confirm {label}: ")
        if pw != confirm:
            print("  Passwords do not match. Try again.")
            continue
        return pw


def main():
    print("\n" + "=" * 55)
    print("  Restaurant System - Account Setup")
    print("=" * 55)

    init_db()

    created = []
    skipped = []

    print("\n[1] Admin Account")
    print("    This account has full system access.\n")

    admin_username = prompt("Admin username")
    existing = get_user_by_username(admin_username)
    if existing:
        print(f"  Username '{admin_username}' already exists - skipping.")
        skipped.append(admin_username)
        admin_id = existing["id"]
    else:
        admin_password = prompt_password("Admin password")
        admin = create_user(admin_username, admin_password, "admin")
        print(f"  Admin '{admin_username}' created.")
        created.append((admin_username, "admin"))
        admin_id = admin["id"]

    print("\n[2] Staff Accounts (waiter / kitchen)")
    print("    Press Enter with empty username when done.\n")

    while True:
        username = prompt("Username (blank to finish)", required=False)
        if not username:
            break

        existing = get_user_by_username(username)
        if existing:
            print(f"  Username '{username}' already exists - skipping.\n")
            skipped.append(username)
            continue

        print("  Role options: waiter, kitchen")
        while True:
            role = input("  Role: ").strip().lower()
            if role in ("waiter", "kitchen"):
                break
            print("  Enter 'waiter' or 'kitchen'.")

        password = prompt_password()
        create_user(username, password, role, admin_id)
        print(f"  Account '{username}' ({role}) created.\n")
        created.append((username, role))

    print("\n" + "=" * 55)
    print("  Setup complete")
    if created:
        print("\n  Created:")
        for name, role in created:
            print(f"    - {name} ({role})")
    if skipped:
        print("\n  Skipped (already existed):")
        for name in skipped:
            print(f"    - {name}")
    print("\n  Run the server:")
    print("  uvicorn main:app --host 0.0.0.0 --port 8000 --reload")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
