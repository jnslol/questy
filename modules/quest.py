SUPPORTED_TASK_TYPES = (
    "WATCH_VIDEO",
    "WATCH_VIDEO_ON_MOBILE",
    "PLAY_ON_DESKTOP",
    "STREAM_ON_DESKTOP",
    "PLAY_ACTIVITY",
)


def _pick(obj, snake, camel):
    if not isinstance(obj, dict):
        return None
    return obj.get(snake, obj.get(camel))


class Quest:
    def __init__(self, raw):
        self.raw = raw
        self.id = raw.get("id")
        config = raw.get("config") or {}
        self.expires_at = _pick(config, "expires_at", "expiresAt")
        current_task_config = config.get("task_config_v2") or config.get("taskConfigV2")
        legacy_task_config = config.get("task_config") or config.get("taskConfig")
        if isinstance(current_task_config, dict) and current_task_config.get("tasks"):
            self.task_config = current_task_config
        else:
            self.task_config = legacy_task_config or current_task_config or {}
        messages = config.get("messages") or {}
        self.name = (
            messages.get("quest_name")
            or messages.get("questName")
            or config.get("questName")
            or self.id
        )
        application = config.get("application") or {}
        self.application_id = application.get("id")
        self.application_name = application.get("name")
        self.executable_path = application.get("executable_path")
        self.user_status = raw.get("user_status") or {}
        self.completed_at = _pick(self.user_status, "completed_at", "completedAt")
        self.enrolled_at = _pick(self.user_status, "enrolled_at", "enrolledAt")
        self.stream_progress_seconds = _pick(
            self.user_status, "stream_progress_seconds", "streamProgressSeconds"
        )
        self.progress = self.user_status.get("progress") or {}
        self.traffic_metadata_sealed = raw.get("traffic_metadata_sealed")

    def task_type(self):
        tasks = self.task_config.get("tasks") or {}
        for key in tasks:
            if key in SUPPORTED_TASK_TYPES:
                return key
        return None

    def target(self):
        tasks = self.task_config.get("tasks") or {}
        task = tasks.get(self.task_type() or "")
        if not isinstance(task, dict):
            return 0
        target = task.get("target")
        try:
            return float(target)
        except (TypeError, ValueError):
            return 0

    def application_id_for_task(self):
        task = (self.task_config.get("tasks") or {}).get(self.task_type() or "")
        applications = task.get("applications") if isinstance(task, dict) else None
        if isinstance(applications, list) and applications:
            application = applications[0]
            if isinstance(application, dict) and application.get("id"):
                return application["id"]
        return self.application_id

    def local_progress(self):
        task_type = self.task_type()
        if task_type:
            entry = self.progress.get(task_type) or {}
            value = entry.get("value")
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
        if self.stream_progress_seconds is not None:
            try:
                return float(self.stream_progress_seconds)
            except (TypeError, ValueError):
                pass
        return 0.0

    def progress_updated_at(self):
        task_type = self.task_type()
        if task_type:
            entry = self.progress.get(task_type) or {}
            timestamp = entry.get("updated_at")
            heartbeat = entry.get("heartbeat") or {}
            return timestamp or heartbeat.get("last_beat_at")
        return None

    def is_completed(self):
        return self.completed_at is not None

    def is_expired(self, now=None):
        if not self.expires_at:
            return False
        import datetime

        ts = self.expires_at
        if isinstance(ts, str):
            try:
                ts = ts.replace("Z", "+00:00")
                expiry = datetime.datetime.fromisoformat(ts)
            except ValueError:
                return False
        elif isinstance(ts, (int, float)):
            expiry = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        else:
            return False
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=datetime.timezone.utc)
        now = now or datetime.datetime.now(datetime.timezone.utc)
        return now >= expiry

    def is_actionable(self):
        return (
            not self.is_completed()
            and not self.is_expired()
            and self.task_type() is not None
            and self.target() > 0
        )


def parse_quests_response(data):
    if not isinstance(data, dict):
        raise ValueError("expected quest envelope object")
    quests = [Quest(q) for q in data.get("quests") or [] if isinstance(q, dict)]
    excluded = data.get("excluded_quests") or []
    blocked_until = data.get("quest_enrollment_blocked_until")
    suspended_until = data.get("quest_access_suspended_until")
    return quests, excluded, blocked_until, suspended_until
