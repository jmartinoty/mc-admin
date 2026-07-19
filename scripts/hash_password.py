#!/usr/bin/env python3
"""Génère un hash Argon2 à coller dans config/roles.yml (champ `password_hash`).

Usage :
    python scripts/hash_password.py
Le mot de passe est saisi sans être affiché ni stocké ailleurs.
Nécessite argon2-cffi (pip install -r requirements.txt).
"""
import getpass
import sys


def main() -> int:
    try:
        from argon2 import PasswordHasher
    except ImportError:
        print("argon2-cffi requis : pip install -r requirements.txt", file=sys.stderr)
        return 1

    pw = getpass.getpass("Mot de passe : ")
    if pw != getpass.getpass("Confirmer : "):
        print("Les mots de passe ne correspondent pas.", file=sys.stderr)
        return 1
    print(PasswordHasher().hash(pw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
