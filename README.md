# Questy

Questy is a multi-account quest automation client for tracking and running quests from a command line or interactive terminal UI.

It lets you:

- store multiple account tokens in a local JSON file
- list and filter quests across accounts
- run automated quest actions for each account
- launch a textual TUI dashboard for quick management

## Features

- Account management: add, list, and remove accounts by index or label
- Quest inspection: filter by status and task type
- Automation runner: process all configured accounts with a threaded worker model
- TUI mode: interactive interface for managing quest activity
- Portable build: single-file executable

## Project layout

- `questy.py` — entry point; starts the TUI by default when no command is passed
- `modules/cli.py` — CLI parser and command handlers
- `modules/accounts.py` — account storage and loading logic
- `modules/runner.py` — quest execution flow
- `modules/tui.py` — interactive text interface
- `build.ps1` — builds a packaged Windows executable
- `tests/test_questy.py` — project tests

## Requirements

- Python 3.10+
- `pip`

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Quick start

1. Install dependencies.
2. Add an account token:

```bash
python questy.py add --token YOUR_TOKEN --label "Main"
```

3. View quests:

```bash
python questy.py quests --status actionable
```

4. Run automation for all saved accounts:

```bash
python questy.py run
```

5. Launch the TUI:

```bash
python questy.py tui
```

If you run the app with no command, it opens the TUI automatically:

```bash
python questy.py
```

## Common commands

List saved accounts:

```bash
python questy.py accounts
```

Remove an account by index or label:

```bash
python questy.py remove 0
python questy.py remove "Main"
```

Filter quests by status and task type:

```bash
python questy.py quests --status actionable --status completed --type WATCH_VIDEO
```

Disable optional features during automation:

```bash
python questy.py run --no-enroll --no-rpc --no-gateway
```

Use a custom accounts file:

```bash
python questy.py --file my_accounts.json accounts
```

## Account storage

Accounts are stored in `accounts.json` by default in the project root. The file can contain either:

- a list of account objects/tokens
- a mapping of labels to tokens

Example:

```json
[
  {
    "label": "Main",
    "token": "YOUR_TOKEN"
  }
]
```

## Build executable

On Windows, you can build a single-file executable with:

```powershell
./build.ps1
```

This creates a packaged binary under `build/dist/questy.exe`.

## Notes

- This project is designed to work with a configured account token and a matching backend service.
- Some automation options, such as Discord RPC and gateway presence, can be disabled if you want a lighter or more limited run.
- The included tests provide coverage for quest parsing and runner behaviors. Run them with:

```bash
python -m unittest discover -s tests
```

## Disclaimer and legal notice

This project is an independent educational and research project and is not affiliated with, endorsed by, sponsored by, or connected to Discord, Inc. or any of its affiliates, products, services, or trademarks.

This software is provided for educational, learning, and personal experimentation purposes only. It is not intended to be used for commercial exploitation, monetization, or closed-source distribution of derivative software.

Users may use, study, modify, and redistribute this software and its source code under the terms of the GNU General Public License v3.0 (GPL-3.0). This project is licensed under GNU GENERAL PUBLIC LICENSE.

No one may make their own software based on this project and then close the source or sell it as proprietary or paid software. Any derivative work must remain open and must comply with the GPL terms, including preserving the same license and making source code available to recipients.

This project must not be used to claim official support, affiliation, or endorsement from Discord or any third party.

## License

This project is licensed under the GNU General Public License v3.0.

See the full license text in the repository root as `LICENSE` if present, or obtain a copy from: https://www.gnu.org/licenses/gpl-3.0.en.html
