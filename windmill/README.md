# Questy Windmill Deployment

The Windmill entrypoint is `f/questy/run_all_accounts.py`. It reads two secret
variables from the target workspace:

- `f/questy/accounts`: the existing accounts JSON array
- `f/questy/notification_webhook`: an outgoing webhook URL

Create both variables manually in Windmill. They are intentionally excluded
from GitHub deployment.

Create a schedule for `f/questy/run_all_accounts` with the desired interval.
Use asynchronous execution and keep the concurrency limit at one so an
all-account run cannot overlap another run.

Create an authenticated HTTP trigger for the same script if manual webhook
execution is required. The webhook can pass `enable_rpc` and `enable_gateway`,
but it must not accept account tokens.

Deploy from a trusted machine with the Windmill CLI, or configure Windmill Git
Sync to pull this repository. No GitHub-to-Windmill connection is required.
Keep variables, secrets, and resources configured in Windmill itself.

The deployment workflow runs `tools/prepare_windmill.py` before syncing. That
copies the canonical modules into `f/questy/runtime/`, where Windmill supports
Python relative imports. The generated runtime files are deployment artifacts
and should not be edited manually.
