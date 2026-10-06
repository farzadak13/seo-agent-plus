"""Dashboard accounts and sessions.

    models.py        accounts (one tenant each) and sessions
    passwords.py     scrypt hashing
    store.py         accounts, their email / Google indexes, and sessions
    service.py       sign in, recognise a session, sign out, attempt limits
    google_login.py  "Sign in with Google": identity only
    __main__.py      the operator's commands (python -m app.accounts)
"""
