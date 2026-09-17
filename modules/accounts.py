import json
import os

ACCOUNTS_FILE = "accounts.json"


def load_accounts(path=ACCOUNTS_FILE):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    accounts = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                accounts.append({"token": item})
            elif isinstance(item, dict) and item.get("token"):
                accounts.append(item)
    elif isinstance(data, dict):
        for label, token in data.items():
            if isinstance(token, str):
                accounts.append({"label": label, "token": token})
    return accounts


def save_accounts(accounts, path=ACCOUNTS_FILE):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(accounts, fh, indent=2)


def add_account(token, label=None, username=None, display_name=None, path=ACCOUNTS_FILE):
    accounts = load_accounts(path)
    for account in accounts:
        if account.get("token") == token:
            if label:
                account["label"] = label
            if username is not None:
                account["username"] = username
            if display_name is not None:
                account["display_name"] = display_name
            save_accounts(accounts, path)
            return False
    entry = {"token": token}
    if label:
        entry["label"] = label
    if username is not None:
        entry["username"] = username
    if display_name is not None:
        entry["display_name"] = display_name
    accounts.append(entry)
    save_accounts(accounts, path)
    return True


def remove_account(index_or_label, path=ACCOUNTS_FILE):
    accounts = load_accounts(path)
    for i, account in enumerate(accounts):
        if str(i) == str(index_or_label) or account.get("label") == index_or_label:
            removed = accounts.pop(i)
            save_accounts(accounts, path)
            return removed
    return None
