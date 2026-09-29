# Questy Windmill Deployment

The Windmill entrypoint is `f/questy/run_all_accounts.py`. It reads one secret
variable from the target workspace:

- `f/questy/notification_webhook`: an outgoing webhook URL

Create the notification variable manually in Windmill. Account data is a
normal `accounts` script input and can be supplied by a flow input or the
generated script form.

Create a schedule for `f/questy/run_all_accounts` with the desired interval.
Use asynchronous execution and keep the concurrency limit at one so an
all-account run cannot overlap another run. Leave the inputs at their defaults
for scheduled execution: `account=all`, actionable quests, RPC and Gateway
enabled, and notifications enabled.

Create an authenticated HTTP trigger for the same script if manual webhook
execution is required. Use it only as a trigger with the flow's configured
arguments; configure account selection, filters, toggles, dry-run mode, and
notifications through the Windmill script input form or flow inputs.

Deploy from a trusted machine with the Windmill CLI, or configure Windmill Git
Sync to pull this repository. No GitHub-to-Windmill connection is required.
Keep variables, secrets, and resources configured in Windmill itself.

Run `python tools/prepare_windmill.py` before syncing. It embeds the canonical
modules into the single tracked `f/questy/run_all_accounts.py` script. The
generated script should not be edited manually; update `modules/` and rebuild
it instead.
