#!/usr/bin/env python3
"""
Module: User Management CLI
Version: 1.0.0
Development Iteration: v1
Developer: Kent Benson

UV Environment: uv run python manage_users.py add user@email.com "User Name"

CLI tool to add/remove/list users in backend/data/users.json.
Passwords are bcrypt-hashed before storage.
"""

import getpass
import sys

from backend.auth import USERS_FILE, load_users, save_users, hash_password


def cmd_add(username: str, display_name: str):
    users = load_users()
    if username in users:
        print(f"User '{username}' already exists. Use 'remove' first to reset.")
        sys.exit(1)

    password = getpass.getpass(f"Password for {username}: ")
    if not password:
        print("Password cannot be empty.")
        sys.exit(1)
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        sys.exit(1)

    users[username] = {
        "display_name": display_name,
        "password_hash": hash_password(password),
    }
    save_users(users)
    print(f"Added user '{username}' ({display_name})")
    print(f"Users file: {USERS_FILE}")


def cmd_remove(username: str):
    users = load_users()
    if username not in users:
        print(f"User '{username}' not found.")
        sys.exit(1)
    del users[username]
    save_users(users)
    print(f"Removed user '{username}'")


def cmd_list():
    users = load_users()
    if not users:
        print("No users configured.")
        return
    print(f"{'Username':<30} {'Display Name'}")
    print("-" * 50)
    for username, info in users.items():
        admin = " [ADMIN]" if info.get("is_admin") else ""
        print(f"{username:<30} {info.get('display_name', '')}{admin}")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print('  uv run python manage_users.py add <email> "<display_name>"')
        print("  uv run python manage_users.py remove <email>")
        print("  uv run python manage_users.py list")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "add":
        if len(sys.argv) < 4:
            print('Usage: uv run python manage_users.py add <email> "<display_name>"')
            sys.exit(1)
        cmd_add(sys.argv[2], sys.argv[3])
    elif command == "remove":
        if len(sys.argv) < 3:
            print("Usage: uv run python manage_users.py remove <email>")
            sys.exit(1)
        cmd_remove(sys.argv[2])
    elif command == "list":
        cmd_list()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
