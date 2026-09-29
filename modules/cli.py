import argparse
import json
import os
import sys
import threading

from .accounts import ACCOUNTS_FILE, AccountsError, add_account, load_accounts, remove_account
from .api import ApiError, Client
from .filters import STATUS_FILTERS, matches_quest, normalize_statuses, normalize_types, quest_status
from .quest import SUPPORTED_TASK_TYPES, parse_quests_response
from .runner import run_account

QUEST_SORT_CHOICES = ("account", "name", "task", "progress", "target", "status", "expires")
OUTPUT_FORMATS = ("text", "json")


def make_log(quiet):
    lock = threading.Lock()

    def log(message):
        if not quiet:
            with lock:
                print(message, flush=True)

    return log


def mask_token(token):
    token = token or ""
    return token[:6] + "..." + token[-4:] if len(token) > 12 else "***"


def account_label(account, index):
    return account.get("label") or f"account-{index}"


def cmd_accounts(args):
    try:
        accounts = load_accounts(args.file)
    except AccountsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not accounts:
        if getattr(args, "format", "text") == "json":
            print("[]")
        else:
            print(f"No accounts in {args.file}. Add one: questy add --token <TOKEN>")
        return
    show_token = getattr(args, "show_token", False)
    if getattr(args, "format", "text") == "json":
        payload = []
        for i, account in enumerate(accounts):
            token = account.get("token", "")
            payload.append(
                {
                    "index": i,
                    "label": account.get("label") or f"account-{i}",
                    "username": account.get("username"),
                    "display_name": account.get("display_name"),
                    "token": token if show_token else mask_token(token),
                }
            )
        print(json.dumps(payload, indent=2))
        return
    for i, account in enumerate(accounts):
        label = account_label(account, i)
        token = account.get("token", "")
        shown = token if show_token else mask_token(token)
        identity = account.get("display_name") or account.get("username") or "unknown"
        print(f"{i}: {label} [{identity}] ({shown})")


def _resolve_token(args):
    token = getattr(args, "token", None)
    if token == "-":
        token = sys.stdin.read().strip()
    if not token:
        token = os.environ.get("QUESTY_TOKEN", "")
    if not token:
        print(
            "error: no token provided "
            "(use --token <TOKEN>, --token - to read from stdin, or set QUESTY_TOKEN)",
            file=sys.stderr,
        )
        sys.exit(2)
    return token


def cmd_add(args):
    token = _resolve_token(args)
    if getattr(args, "no_validate", False):
        created = add_account(token, args.label, path=args.file)
        if created:
            print(f"account added: {args.label or 'unlabeled'} (unvalidated)")
        else:
            print("account already exists (label updated if given)")
        return
    try:
        user = Client(token).get_me()
    except ApiError as exc:
        print(f"token validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"token validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
    username = user.get("username") if isinstance(user, dict) else None
    display_name = (user.get("global_name") or username) if isinstance(user, dict) else None
    try:
        created = add_account(token, args.label, username, display_name, args.file)
    except AccountsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if created:
        ident = display_name or username or (user.get("id") if isinstance(user, dict) else None)
        print(f"account added: {ident}")
    else:
        print("account already exists (label updated if given)")


def cmd_remove(args):
    targets = args.target if isinstance(args.target, list) else [args.target]
    removed_any = False
    for target in targets:
        try:
            removed = remove_account(target, args.file)
        except AccountsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        if removed:
            label = removed.get("label") or target
            print(f"account removed: {label}")
            removed_any = True
        else:
            print(f"no matching account: {target}", file=sys.stderr)
    if not removed_any:
        sys.exit(1)


def quest_matches(quest, status, types):
    return matches_quest(quest, status, types)


def _resolve_selected_accounts(accounts, selector):
    try:
        return _select_accounts(accounts, selector)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        available = ", ".join(
            f"{i}={account_label(a, i)}" for i, a in enumerate(accounts)
        ) or "none"
        print(f"available accounts: {available}", file=sys.stderr)
        sys.exit(2)


def _sort_key_for(entry, sort_by, account_order=None):
    label, quest = entry
    status = quest_status(quest)
    task = quest.task_type() or ""
    progress = quest.local_progress()
    try:
        progress = float(progress)
    except (TypeError, ValueError):
        progress = 0.0
    try:
        target = float(quest.target())
    except (TypeError, ValueError):
        target = 0.0
    name = (quest.name or str(quest.id) or "").lower()
    expires = quest.expires_at or ""
    order = 0
    if account_order is not None:
        order = account_order.get(label, 0)
    if (sort_by or "account") == "account":
        # Preserve selected/file order for the default grouping.
        return (order, name)
    primary = {
        "name": name,
        "task": task,
        "progress": progress,
        "target": target,
        "status": status,
        "expires": str(expires),
    }.get(sort_by, name)
    return (primary, order, name)


def sort_quest_entries(entries, sort_by="account", reverse=False, account_order=None):
    sort_by = sort_by or "account"
    return sorted(
        entries,
        key=lambda e: _sort_key_for(e, sort_by, account_order),
        reverse=reverse,
    )


def quest_to_dict(label, quest):
    return {
        "account": label,
        "id": quest.id,
        "name": quest.name,
        "task_type": quest.task_type(),
        "progress": quest.local_progress(),
        "target": quest.target(),
        "status": quest_status(quest),
        "expires_at": quest.expires_at,
        "application_id": quest.application_id,
        "application_name": quest.application_name,
        "enrolled_at": quest.enrolled_at,
        "completed_at": quest.completed_at,
    }


def cmd_list_quests(args):
    log = make_log(args.quiet)
    try:
        accounts = load_accounts(args.file)
    except AccountsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not accounts:
        print(f"No accounts in {args.file}")
        sys.exit(1)
    selector = getattr(args, "account", None)
    selected = _resolve_selected_accounts(accounts, selector)
    if not selected:
        print("no accounts match the given --account selector")
        sys.exit(2)
    # Map selected account object -> original index for stable labels.
    index_of = {id(a): i for i, a in enumerate(accounts)}
    types = normalize_types(getattr(args, "type", None) or ())
    statuses = normalize_statuses(args.status or ("actionable", "completed"))
    sort_by = getattr(args, "sort", None) or "account"
    reverse = getattr(args, "reverse", False)
    output = getattr(args, "format", "text") or "text"

    entries = []  # (label, quest)
    notices = []  # (label, blocked, suspended, excluded_count, total)
    failures = []
    for account in selected:
        i = index_of.get(id(account), 0)
        label = account_label(account, i)
        try:
            data = Client(account["token"], trace=args.trace and log).get_quests()
            quests, excluded, blocked, suspended = parse_quests_response(data)
            shown = [q for q in quests if quest_matches(q, statuses, types)]
            notices.append((label, blocked, suspended, len(excluded), len(quests)))
            for quest in shown:
                entries.append((label, quest))
        except ApiError as exc:
            failures.append((label, str(exc)))
        except Exception as exc:
            failures.append((label, str(exc)))

    account_order = {}
    for order, account in enumerate(selected):
        i = index_of.get(id(account), 0)
        account_order.setdefault(account_label(account, i), order)
    entries = sort_quest_entries(entries, sort_by, reverse, account_order)

    if output == "json":
        payload = [quest_to_dict(label, quest) for label, quest in entries]
        print(json.dumps(payload, indent=2))
        for label, message in failures:
            print(f"{label}: FAILED ({message})", file=sys.stderr)
        return

    type_label = ",".join(sorted(types)) or "all"
    status_label = ",".join(sorted(statuses))
    for label, blocked, suspended, excluded_count, total in notices:
        count = sum(1 for entry_label, _ in entries if entry_label == label)
        print(
            f"{label}: {count}/{total} quests shown "
            f"(status={status_label}, types={type_label}), "
            f"{excluded_count} excluded"
        )
        if blocked:
            print(f"  enrollment blocked until: {blocked}")
        if suspended:
            print(f"  quest access suspended until: {suspended}")
    for label, quest in entries:
        status = quest_status(quest)
        task = quest.task_type() or "-"
        print(
            f"{label}: {quest.name} [{task}] progress "
            f"{quest.local_progress():.0f}/{quest.target():.0f} ({status})"
        )
    for label, message in failures:
        print(f"{label}: FAILED ({message})")
    if not entries and not failures:
        print(
            f"No quests match (status={status_label}, types={type_label}). "
            f"Try: questy quests --status all"
        )


def cmd_run(args):
    log = make_log(args.quiet)
    try:
        accounts = load_accounts(args.file)
    except AccountsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not accounts:
        print(f"No accounts in {args.file}")
        sys.exit(1)
    selector = getattr(args, "account", None)
    # Validate selectors early for a clean error (no partial run).
    selected_preview = _resolve_selected_accounts(accounts, selector)
    if not selected_preview:
        print("no accounts match the given --account selector")
        sys.exit(2)
    statuses = list(getattr(args, "status", None) or [])
    task_types = list(getattr(args, "type", None) or ())
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        results = run_all_accounts(
            accounts,
            log=log,
            trace=args.trace,
            auto_enroll=not args.no_enroll,
            enable_rpc=not args.no_rpc,
            enable_gateway=not args.no_gateway,
            account=selector or "all",
            task_types=task_types or None,
            statuses=statuses or None,
            dry_run=dry_run,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        log("cancelled")
        sys.exit(130)
    output = getattr(args, "format", "text") or "text"
    if output == "json":
        print(json.dumps(results, indent=2, default=str))
    else:
        if dry_run:
            for label, result in results.items():
                count = len(result.get("statuses", {}))
                errors = result.get("errors", [])
                suffix = f", errors: {'; '.join(errors)}" if errors else ""
                log(f"[{label}] dry run selected {count} quests{suffix}")
        else:
            for label, result in results.items():
                errors = result.get("errors", [])
                statuses_map = result.get("statuses", {})
                done = sum(1 for v in statuses_map.values() if v == "completed")
                suffix = f", errors: {'; '.join(errors)}" if errors else ""
                log(f"[{label}] {done}/{len(statuses_map)} completed{suffix}")
    failed = {label: result["errors"] for label, result in results.items() if result["errors"]}
    if failed and output != "json":
        print(json.dumps(failed, indent=2))
        sys.exit(1)
    if failed:
        sys.exit(1)


def run_all_accounts(
    accounts,
    log=print,
    trace=False,
    auto_enroll=True,
    enable_rpc=True,
    enable_gateway=True,
    cancel_event=None,
    account="all",
    task_types=None,
    statuses=None,
    dry_run=False,
):
    if cancel_event is None:
        cancel_event = threading.Event()
    selected_accounts = _select_accounts(accounts, account)
    quest_filter = None
    if task_types or statuses:
        selected_statuses = statuses or ("actionable",)
        selected_types = task_types or ()
        quest_filter = lambda quest: matches_quest(quest, selected_statuses, selected_types)
    threads = []
    results = {}
    results_lock = threading.Lock()
    # Keep original indices so repeated labels stay unique in results.
    index_of = {id(a): i for i, a in enumerate(accounts)}
    for account_entry in selected_accounts:
        i = index_of.get(id(account_entry), 0)
        base_label = account_entry.get("label") or f"account-{i}"
        label = base_label
        suffix = 1
        while label in results or any(
            t is not None and getattr(t, "_questy_label", None) == label for t in threads
        ):
            # Avoid collisions when two accounts share a label; run() below
            # fills results by label so keys must be unique.
            suffix += 1
            label = f"{base_label}#{suffix}"
        client = Client(account_entry["token"], trace=log if trace else None)

        def worker(c=client, l=label):
            if dry_run:
                account_result = _inspect_account(c, l, log, quest_filter)
            else:
                runner, errors = run_account(
                    c,
                    l,
                    log,
                    cancel_event,
                    auto_enroll,
                    enable_rpc=enable_rpc,
                    enable_gateway=enable_gateway,
                    quest_filter=quest_filter,
                )
                account_result = {
                    "errors": errors,
                    "statuses": dict(runner.results),
                }
            with results_lock:
                results[l] = account_result

        thread = threading.Thread(
            target=worker,
        )
        thread._questy_label = label
        thread.start()
        threads.append(thread)
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        cancel_event.set()
        for thread in threads:
            thread.join()
        raise
    return results


def _select_accounts(accounts, selector):
    if selector is None or selector == "all" or selector == "":
        return list(accounts)
    if isinstance(selector, str):
        selector = [selector]
    selected = []
    for value in selector:
        match = None
        for index, account in enumerate(accounts):
            label = account.get("label") or f"account-{index}"
            if str(value) == str(index) or str(value) == label:
                match = account
                break
        if match is None:
            raise ValueError(f"unknown account selector: {value}")
        if match not in selected:
            selected.append(match)
    return selected


def _inspect_account(client, label, log, quest_filter):
    try:
        quests, excluded, blocked, suspended = parse_quests_response(client.get_quests())
        if quest_filter:
            quests = [quest for quest in quests if quest_filter(quest)]
        log(f"[{label}] dry run: {len(quests)} quests selected ({len(excluded)} excluded)")
        return {
            "errors": [],
            "statuses": {str(quest.id): quest_status(quest) for quest in quests},
            "blocked_until": blocked,
            "suspended_until": suspended,
        }
    except Exception as exc:
        log(f"[{label}] dry run failed: {exc}")
        return {"errors": [f"error: {exc}"], "statuses": {}}


def cmd_version(args):
    from . import __version__

    print(f"questy {__version__}")


def cmd_doctor(args):
    try:
        accounts = load_accounts(args.file)
    except AccountsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    output = getattr(args, "format", "text") or "text"
    if not accounts:
        if output == "json":
            print(json.dumps({"file": args.file, "accounts": []}, indent=2))
        else:
            print(f"No accounts in {args.file}. Add one: questy add --token <TOKEN>")
        return
    results = []
    for i, account in enumerate(accounts):
        label = account_label(account, i)
        try:
            user = Client(account.get("token", "")).get_me()
            identity = None
            if isinstance(user, dict):
                identity = user.get("global_name") or user.get("username") or user.get("id")
            results.append({"label": label, "ok": True, "identity": identity})
        except Exception as exc:
            results.append({"label": label, "ok": False, "error": str(exc)})
    if output == "json":
        print(json.dumps({"file": args.file, "accounts": results}, indent=2))
    else:
        print(f"accounts file: {args.file} ({len(accounts)} account(s))")
        for entry in results:
            if entry["ok"]:
                print(f"{entry['label']}: OK ({entry['identity'] or 'unknown'})")
            else:
                print(f"{entry['label']}: FAILED ({entry['error']})")
    if any(not entry["ok"] for entry in results):
        sys.exit(1)


def cmd_tui(args):
    from .tui import QuestyTui

    QuestyTui(accounts_file=args.file).run()


def cmd_help(args, parser=None):
    if parser is None:
        parser = build_parser()
    topic = getattr(args, "topic", None)
    if topic:
        try:
            sub = parser._subparsers._group_actions[0].choices[topic]
        except KeyError:
            print(f"unknown help topic: {topic}", file=sys.stderr)
            sys.exit(2)
        print(sub.format_help())
    else:
        parser.print_help()


def _add_add_args(p):
    p.add_argument(
        "--token",
        default=None,
        help="account authorization token ('-' reads from stdin, or set QUESTY_TOKEN)",
    )
    p.add_argument("--label", help="friendly account label")
    p.add_argument(
        "--no-validate",
        action="store_true",
        help="skip token validation via users/@me",
    )


def _add_remove_args(p):
    p.add_argument("target", nargs="+", help="index or label (repeatable)")


def build_parser():
    from . import __version__

    parser = argparse.ArgumentParser(
        prog="questy",
        description="Multi-account quest automation client",
    )
    parser.add_argument(
        "--file",
        default=os.environ.get("QUESTY_ACCOUNTS_FILE", ACCOUNTS_FILE),
        help="accounts JSON file (or set QUESTY_ACCOUNTS_FILE)",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p_accounts = sub.add_parser(
        "accounts",
        aliases=["list-accounts"],
        help="manage saved accounts (default: list)",
    )
    p_accounts.add_argument(
        "--format", choices=OUTPUT_FORMATS, default="text", help="output format"
    )
    p_accounts.add_argument(
        "--show-token", action="store_true", help="show full tokens (default: masked)"
    )
    p_accounts.set_defaults(func=cmd_accounts)
    acc_sub = p_accounts.add_subparsers(
        dest="accounts_command", metavar="accounts-command"
    )
    p_acc_list = acc_sub.add_parser("list", help="list saved accounts")
    p_acc_list.add_argument(
        "--format", choices=OUTPUT_FORMATS, default="text", help="output format"
    )
    p_acc_list.add_argument(
        "--show-token", action="store_true", help="show full tokens (default: masked)"
    )
    p_acc_list.set_defaults(func=cmd_accounts)
    p_acc_add = acc_sub.add_parser("add", help="add an account by token")
    _add_add_args(p_acc_add)
    p_acc_add.set_defaults(func=cmd_add)
    p_acc_remove = acc_sub.add_parser(
        "remove", aliases=["rm"], help="remove account(s) by index or label"
    )
    _add_remove_args(p_acc_remove)
    p_acc_remove.set_defaults(func=cmd_remove)

    p_add = sub.add_parser("add", help="add an account by token")
    _add_add_args(p_add)
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove", aliases=["rm"], help="remove account(s) by index or label")
    _add_remove_args(p_remove)
    p_remove.set_defaults(func=cmd_remove)

    p_list = sub.add_parser(
        "quests",
        aliases=["list", "ls", "list-quests"],
        help="list quests for selected accounts",
    )
    p_list.add_argument(
        "-a",
        "--account",
        action="append",
        default=None,
        metavar="ACCOUNT",
        help="account index or label to include (repeatable, default: all)",
    )
    p_list.add_argument(
        "--status",
        choices=STATUS_FILTERS,
        action="append",
        default=None,
        help="filter by quest status (repeatable)",
    )
    p_list.add_argument(
        "--type",
        "--task-type",
        dest="type",
        action="append",
        default=[],
        metavar="TASK",
        help=f"filter by task type (repeatable, choices: {', '.join(SUPPORTED_TASK_TYPES)})",
    )
    p_list.add_argument(
        "--sort",
        choices=QUEST_SORT_CHOICES,
        default="account",
        help="sort quests by field (default: account)",
    )
    p_list.add_argument("--reverse", action="store_true", help="reverse sort order")
    p_list.add_argument(
        "--format", choices=OUTPUT_FORMATS, default="text", help="output format"
    )
    p_list.add_argument("--trace", action="store_true")
    p_list.add_argument("--quiet", action="store_true")
    p_list.set_defaults(func=cmd_list_quests)

    p_run = sub.add_parser("run", help="run quests for selected accounts")
    p_run.add_argument(
        "-a",
        "--account",
        action="append",
        default=None,
        metavar="ACCOUNT",
        help="account index or label to run (repeatable, default: all)",
    )
    p_run.add_argument(
        "--status",
        choices=[s for s in STATUS_FILTERS if s != "all"],
        action="append",
        default=None,
        help="only run quests with this status (repeatable, default: actionable)",
    )
    p_run.add_argument(
        "--type",
        "--task-type",
        dest="type",
        action="append",
        default=None,
        metavar="TASK",
        help=f"only run quests with this task type (repeatable, choices: {', '.join(SUPPORTED_TASK_TYPES)})",
    )
    p_run.add_argument(
        "--dry-run",
        "--dry",
        action="store_true",
        help="list what would run without executing heartbeats",
    )
    p_run.add_argument(
        "--format", choices=OUTPUT_FORMATS, default="text", help="output format"
    )
    p_run.add_argument("--trace", action="store_true")
    p_run.add_argument("--quiet", action="store_true")
    p_run.add_argument("--no-enroll", action="store_true", help="disable auto-enroll")
    p_run.add_argument("--no-rpc", action="store_true", help="disable Discord desktop IPC activity registration")
    p_run.add_argument("--no-gateway", action="store_true", help="disable online game presence Gateway session")
    p_run.set_defaults(func=cmd_run)

    p_version = sub.add_parser("version", help="print version and exit")
    p_version.set_defaults(func=cmd_version)

    p_doctor = sub.add_parser(
        "doctor", help="validate saved account tokens and show config status"
    )
    p_doctor.add_argument(
        "--format", choices=OUTPUT_FORMATS, default="text", help="output format"
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_tui = sub.add_parser("tui", help="launch the interactive TUI")
    p_tui.set_defaults(func=cmd_tui)

    p_help = sub.add_parser("help", help="show help (questy help [command])")
    p_help.add_argument(
        "topic",
        nargs="?",
        default=None,
        help="command to show help for",
    )
    p_help.set_defaults(func=lambda args: cmd_help(args, parser))

    return parser


def main(argv=None):
    parser = build_parser()
    # Friendly `questy help` / `questy help <command>` without argparse erroring.
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except AccountsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
