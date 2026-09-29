import json
import os
import tempfile

ACCOUNTS_FILE = "accounts.json"


class AccountsError(Exception):
    """Friendly error for unreadable/corrupt accounts files."""


def _decode_all_documents(text):
    """Decode one or more concatenated JSON documents.

    Returns a list of parsed values. Raises json.JSONDecodeError if the
    first document is invalid.
    """
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    documents = []
    while True:
        while idx < length and text[idx] in " \t\r\n":
            idx += 1
        if idx >= length:
            break
        value, end = decoder.raw_decode(text, idx)
        documents.append(value)
        idx = end
    return documents


def load_accounts(path=ACCOUNTS_FILE):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise AccountsError(f"cannot read accounts file {path!r}: {exc}") from exc
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # Common corruption: two JSON values concatenated (e.g. two lists
        # appended to the same file, "][", or JSONL). Try to recover by
        # decoding every document and merging them.
        if "Extra data" not in str(exc):
            raise AccountsError(
                f"invalid accounts file {path!r}: {exc}\n"
                f"Fix the JSON in {path!r} or move it aside and re-add accounts."
            ) from exc
        try:
            documents = _decode_all_documents(text)
        except json.JSONDecodeError:
            raise AccountsError(
                f"invalid accounts file {path!r}: {exc}\n"
                f"Fix the JSON in {path!r} or move it aside and re-add accounts."
            ) from exc
        if not documents:
            raise AccountsError(
                f"invalid accounts file {path!r}: {exc}\n"
                f"Fix the JSON in {path!r} or move it aside and re-add accounts."
            ) from exc
        if len(documents) == 1:
            data = documents[0]
        else:
            # Merge multiple documents (lists concatenated, dicts merged, ...).
            merged = []
            for doc in documents:
                parsed = parse_accounts(doc)
                if not parsed and isinstance(doc, (list, dict)):
                    # parse_accounts drops entries without tokens; keep going.
                    continue
                merged.extend(parsed)
            return merged
    return parse_accounts(data)


def parse_accounts(data):
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
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=".accounts-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(accounts, fh, indent=2)
            fh.write("\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


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
