import argparse
import json
import sys
import threading

from .accounts import ACCOUNTS_FILE, add_account, load_accounts, remove_account
from .api import ApiError, Client
from .filters import STATUS_FILTERS, matches_quest, normalize_statuses, normalize_types, quest_status
from .quest import parse_quests_response
from .runner import run_account


def make_log(quiet):
    lock = threading.Lock()

    def log(message):
        if not quiet:
            with lock:
                print(message, flush=True)

    return log


def cmd_accounts(args):
    accounts = load_accounts(args.file)
    if not accounts:
        print(f"No accounts in {args.file}. Add one: questy add --token <TOKEN>")
        return
    for i, account in enumerate(accounts):
        label = account.get("label") or f"account-{i}"
        token = account["token"]
        masked = token[:6] + "..." + token[-4:] if len(token) > 12 else "***"
        identity = account.get("display_name") or account.get("username") or "unknown"
        print(f"{i}: {label} [{identity}] ({masked})")


def cmd_add(args):
    try:
        user = Client(args.token).get_me()
    except ApiError as exc:
        print(f"token validation failed: {exc}")
        sys.exit(1)
    username = user.get("username") if isinstance(user, dict) else None
    display_name = (user.get("global_name") or username) if isinstance(user, dict) else None
    created = add_account(args.token, args.label, username, display_name, args.file)
    if created:
        print(f"account added: {display_name or username or user.get('id')}")
    else:
        print("account already exists (label updated if given)")


def cmd_remove(args):
    removed = remove_account(args.target, args.file)
    if removed:
        print("account removed")
    else:
        print("no matching account")


def quest_matches(quest, status, types):
    return matches_quest(quest, status, types)


def cmd_list_quests(args):
    log = make_log(args.quiet)
    accounts = load_accounts(args.file)
    if not accounts:
        print(f"No accounts in {args.file}")
        sys.exit(1)
    types = normalize_types(args.type)
    statuses = normalize_statuses(args.status or ("actionable", "completed"))
    for i, account in enumerate(accounts):
        label = account.get("label") or f"account-{i}"
        try:
            data = Client(account["token"], trace=args.trace and log).get_quests()
            quests, excluded, blocked, suspended = parse_quests_response(data)
            shown = [q for q in quests if quest_matches(q, statuses, types)]
            print(
                f"{label}: {len(shown)}/{len(quests)} quests shown "
                f"(status={','.join(sorted(statuses))}, types={','.join(sorted(types)) or 'all'}), "
                f"{len(excluded)} excluded"
            )
            for quest in shown:
                status = quest_status(quest)
                task = quest.task_type() or "-"
                print(
                    f"  - {quest.name} [{task}] progress "
                    f"{quest.local_progress():.0f}/{quest.target():.0f} ({status})"
                )
            if blocked:
                print(f"  enrollment blocked until: {blocked}")
            if suspended:
                print(f"  quest access suspended until: {suspended}")
        except ApiError as exc:
            print(f"{label}: FAILED ({exc})")


def cmd_run(args):
    log = make_log(args.quiet)
    accounts = load_accounts(args.file)
    if not accounts:
        print(f"No accounts in {args.file}")
        sys.exit(1)
    cancel_event = threading.Event()
    threads = []
    all_errors = {}
    for i, account in enumerate(accounts):
        label = account.get("label") or f"account-{i}"
        client = Client(account["token"], trace=args.trace and log)
        thread = threading.Thread(
            target=lambda c=client, l=label: all_errors.update(
                {l: run_account(c, l, log, cancel_event, not args.no_enroll, enable_rpc=not args.no_rpc, enable_gateway=not args.no_gateway)[1]}
            )
        )
        thread.start()
        threads.append(thread)
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        cancel_event.set()
        for thread in threads:
            thread.join()
        log("cancelled")
        sys.exit(130)
    failed = {label: errs for label, errs in all_errors.items() if errs}
    if failed:
        print(json.dumps(failed, indent=2))
        sys.exit(1)


def cmd_tui(args):
    from .tui import QuestyTui

    QuestyTui(accounts_file=args.file).run()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="questy",
        description="Multi-account quest automation client",
    )
    parser.add_argument("--file", default=ACCOUNTS_FILE, help="accounts JSON file")
    sub = parser.add_subparsers(dest="command", required=True)

    p_accounts = sub.add_parser("accounts", help="list accounts")
    p_accounts.set_defaults(func=cmd_accounts)

    p_add = sub.add_parser("add", help="add an account by token")
    p_add.add_argument("--token", required=True)
    p_add.add_argument("--label")
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove", help="remove account by index or label")
    p_remove.add_argument("target")
    p_remove.set_defaults(func=cmd_remove)

    p_list = sub.add_parser("quests", help="list quests for all accounts")
    p_list.add_argument(
        "--status",
        choices=STATUS_FILTERS,
        action="append",
        default=None,
        help="filter by quest status",
    )
    p_list.add_argument(
        "--type",
        action="append",
        default=[],
        metavar="TASK",
        help="filter by task type (repeatable: WATCH_VIDEO, WATCH_VIDEO_ON_MOBILE, PLAY_ON_DESKTOP, STREAM_ON_DESKTOP, PLAY_ACTIVITY)",
    )
    p_list.add_argument("--trace", action="store_true")
    p_list.add_argument("--quiet", action="store_true")
    p_list.set_defaults(func=cmd_list_quests)

    p_run = sub.add_parser("run", help="run quests for all accounts")
    p_run.add_argument("--trace", action="store_true")
    p_run.add_argument("--quiet", action="store_true")
    p_run.add_argument("--no-enroll", action="store_true", help="disable auto-enroll")
    p_run.add_argument("--no-rpc", action="store_true", help="disable Discord desktop IPC activity registration")
    p_run.add_argument("--no-gateway", action="store_true", help="disable online game presence Gateway session")
    p_run.set_defaults(func=cmd_run)

    p_tui = sub.add_parser("tui", help="launch the interactive TUI")
    p_tui.set_defaults(func=cmd_tui)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
