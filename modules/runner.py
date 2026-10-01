import random
import threading
import time
import traceback

from .api import ApiError
from .gateway import GatewayPresence
from .quest import parse_quests_response
from .rpc import DiscordRpc, RpcError

VIDEO_SLEEP = (7.0, 9.5)
VIDEO_JITTER = 0.01
HEARTBEAT_INTERVALS = {
    "PLAY_ON_DESKTOP": (58.0, 62.0),
    "STREAM_ON_DESKTOP": (28.0, 32.0),
    "PLAY_ACTIVITY": (19.0, 22.0),
}
DEFAULT_HEARTBEAT_INTERVAL = (20.0, 22.0)
MAX_NO_PROGRESS_BEATS = 5
# Bound PLAY_ON_DESKTOP stalls so the quest-level retry can trigger.
# A per-account desktop_idle_limit still wins when explicitly configured.
DEFAULT_DESKTOP_NO_PROGRESS_LIMIT = 15
POST_ENROLL_SLEEP = (0.8, 1.5)
QUEST_STAGGER = (1.5, 4.0)
SKIPPABLE_STATUS = {403, 404, 410}
MAX_QUEST_ATTEMPTS = 3
RETRY_BACKOFF = (2.0, 4.0)


class Cancelled(Exception):
    pass


class Runner:
    def __init__(self, client, account_label, log, cancel_event=None, auto_enroll=True, event=None, enable_rpc=False, enable_gateway=False, desktop_idle_limit=None, quest_filter=None):
        self.client = client
        self.label = account_label
        self.log = log
        self.cancel_event = cancel_event or threading.Event()
        self.auto_enroll = auto_enroll
        self.event = event or (lambda **kwargs: None)
        self.enable_rpc = enable_rpc
        self.enable_gateway = enable_gateway
        self.gateway = None
        self.rpc = None
        self.desktop_idle_limit = desktop_idle_limit
        self.quest_filter = quest_filter
        self.desktop_lock = threading.Lock()
        self.results = {}

    def _emit(self, quest, status=None, progress=None, message=None, progress_at=None):
        try:
            self.event(
                account=self.label,
                quest_id=str(quest.id),
                quest_name=quest.name,
                task_type=quest.task_type(),
                status=status,
                progress=progress,
                target=quest.target(),
                message=message,
                progress_at=progress_at or quest.progress_updated_at(),
            )
        except Exception:
            pass

    def _sleep(self, seconds):
        if self.cancel_event.wait(seconds):
            raise Cancelled()

    def _enroll(self, quest):
        if not self.auto_enroll or quest.enrolled_at:
            return
        self.log(f"[{self.label}] enrolling in {quest.name} ({quest.id})")
        self._emit(quest, status="enrolling", message="enrolling")
        self.client.enroll(quest.id, quest.traffic_metadata_sealed)
        self._sleep(random.uniform(*POST_ENROLL_SLEEP))

    def _ensure_stream_key(self, quest):
        channel_id = None
        user_id = None
        try:
            channels = self.client.get_channels()
            if isinstance(channels, list) and channels:
                channel_id = channels[0].get("id")
            me = self.client.get_me()
            user_id = me.get("id") if isinstance(me, dict) else None
        except ApiError:
            pass
        if channel_id and user_id:
            return f"call:{channel_id}:{user_id}"
        return f"call:{quest.id}:1"

    def _resolve_executable(self, quest):
        if quest.executable_path:
            return quest.executable_path
        application_id = quest.application_id_for_task()
        if not application_id:
            return None
        try:
            applications = self.client.get_applications_public(application_id)
            for application in applications or []:
                if str(application.get("id")) != str(application_id):
                    continue
                for exe in application.get("executables") or []:
                    if exe.get("os") == "win32" and not exe.get("is_launcher"):
                        return exe.get("name")
        except ApiError:
            pass
        return None

    def _response_progress(self, response, task_type):
        if not isinstance(response, dict):
            return None
        progress_map = response.get("progress") or {}
        entry = progress_map.get(task_type) or {}
        value = entry.get("value")
        if value is None:
            value = response.get("stream_progress_seconds")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _response_completed(self, response):
        if not isinstance(response, dict):
            return False
        return (
            response.get("completed_at") is not None
            or response.get("completedAt") is not None
        )

    def _response_progress_at(self, response, task_type):
        if not isinstance(response, dict):
            return None
        entry = (response.get("progress") or {}).get(task_type) or {}
        heartbeat = entry.get("heartbeat") or {}
        return entry.get("updated_at") or heartbeat.get("last_beat_at")

    def _confirm_completion(self, quest):
        try:
            quests, _, _, _ = parse_quests_response(self.client.get_quests())
            current = next((item for item in quests if str(item.id) == str(quest.id)), None)
            return current is not None and current.is_completed()
        except (ApiError, ValueError):
            return False

    def _run_video(self, quest):
        task_type = quest.task_type()
        target = quest.target()
        progress = quest.local_progress()
        self.log(f"[{self.label}] {quest.name}: {task_type} from {progress:.1f}s to {target:.0f}s")
        self._emit(quest, status="running", progress=progress, message="started")
        while progress < target:
            wait = random.uniform(*VIDEO_SLEEP)
            self._sleep(wait)
            progress = min(target, progress + wait + random.uniform(-VIDEO_JITTER, VIDEO_JITTER))
            response = self.client.video_progress(quest.id, round(progress, 6))
            if self._response_completed(response):
                self.log(f"[{self.label}] {quest.name}: video complete")
                self._emit(quest, status="completed", progress=target, message="complete")
                return True
            server_value = self._response_progress(response, task_type)
            if server_value is not None and server_value > progress:
                progress = server_value
            self._emit(
                quest,
                status="running",
                progress=progress,
                progress_at=self._response_progress_at(response, task_type),
            )
        if self._confirm_completion(quest):
            self.log(f"[{self.label}] {quest.name}: video complete")
            self._emit(quest, status="completed", progress=target, message="complete")
            return True
        self.log(f"[{self.label}] {quest.name}: active, awaiting Discord completion")
        return "active"

    def _run_heartbeat(self, quest):
        if quest.task_type() == "PLAY_ON_DESKTOP":
            with self.desktop_lock:
                return self._run_heartbeat_inner(quest)
        return self._run_heartbeat_inner(quest)

    def _run_heartbeat_inner(self, quest):
        task_type = quest.task_type()
        target = quest.target()
        interval = HEARTBEAT_INTERVALS.get(task_type, DEFAULT_HEARTBEAT_INTERVAL)
        progress = quest.local_progress()
        self.log(f"[{self.label}] {quest.name}: {task_type} from {progress:.0f}s to {target:.0f}s")
        self._emit(quest, status="running", progress=progress, message="started")
        application_id = quest.application_id_for_task()
        if task_type == "PLAY_ON_DESKTOP" and self.enable_gateway and application_id:
            try:
                if self.gateway is None:
                    self.gateway = GatewayPresence(self.client.token)
                    self.gateway.start(quest.application_name or quest.name, application_id)
                else:
                    self.gateway.set_activity(quest.application_name or quest.name, application_id)
            except Exception as exc:
                self.log(f"[{self.label}] Discord Gateway unavailable ({exc}); continuing without Gateway")
                if self.gateway:
                    self.gateway.close()
                    self.gateway = None
                self.enable_gateway = False
        if task_type == "PLAY_ON_DESKTOP" and self.enable_rpc and application_id:
            try:
                if self.rpc is None:
                    self.rpc = DiscordRpc(application_id)
                self.rpc.set_activity(quest.application_name or quest.name, time.time())
                self.log(f"[{self.label}] {quest.name}: registered desktop activity via Discord IPC")
            except Exception as exc:
                self.log(f"[{self.label}] {quest.name}: Discord IPC unavailable ({exc}); using REST heartbeat")
                if self.rpc:
                    self.rpc.close()
                    self.rpc = None
                self.enable_rpc = False
        payload = {"application_id": application_id or "", "terminal": False}
        if task_type != "PLAY_ON_DESKTOP":
            payload["stream_key"] = self._ensure_stream_key(quest)
        else:
            executable = self._resolve_executable(quest)
            if executable:
                payload["executable_path"] = executable

        no_progress = 0
        no_progress_limit = MAX_NO_PROGRESS_BEATS
        desktop_no_progress_limit = (
            self.desktop_idle_limit
            if self.desktop_idle_limit is not None
            else DEFAULT_DESKTOP_NO_PROGRESS_LIMIT
        )
        max_beats = int(target / min(interval)) * 3 + 10
        beats = 0
        try:
            while progress < target and beats < max_beats:
                self._sleep(random.uniform(*interval))
                beats += 1
                response = self.client.heartbeat(quest.id, payload)
                if self._response_completed(response):
                    try:
                        self.client.heartbeat(quest.id, {**payload, "terminal": True})
                    except ApiError as exc:
                        self.log(f"[{self.label}] {quest.name}: terminal heartbeat warning: {exc}")
                    self.log(f"[{self.label}] {quest.name}: heartbeat complete")
                    self._emit(quest, status="completed", progress=target, message="complete")
                    return True
                value = self._response_progress(response, task_type)
                if value is not None and value > progress:
                    progress = value
                    no_progress = 0
                else:
                    no_progress += 1
                    if task_type == "PLAY_ON_DESKTOP" and no_progress >= desktop_no_progress_limit:
                        self.log(f"[{self.label}] {quest.name}: active, awaiting Discord game credit")
                        self._emit(quest, status="active", progress=progress, message="awaiting game credit")
                        return "active"
                    if task_type != "PLAY_ON_DESKTOP" and no_progress >= no_progress_limit:
                        detail = (
                            "Discord accepted the heartbeat but did not credit the game; "
                            "PLAY_ON_DESKTOP requires Discord desktop running-game state"
                            if task_type == "PLAY_ON_DESKTOP"
                            else f"server reported no heartbeat progress for {no_progress} consecutive beats"
                        )
                        raise ApiError(500, detail)
                    if task_type == "PLAY_ON_DESKTOP" and no_progress == 1:
                        self.log(f"[{self.label}] {quest.name}: active, awaiting Discord game credit")
                self.log(f"[{self.label}] {quest.name}: progress {progress:.0f}/{target:.0f}s")
                self._emit(
                    quest,
                    status="running",
                    progress=progress,
                    progress_at=self._response_progress_at(response, task_type),
                )

            if progress >= target:
                self.client.heartbeat(quest.id, {**payload, "terminal": True})
                if self._confirm_completion(quest):
                    self.log(f"[{self.label}] {quest.name}: heartbeat complete")
                    self._emit(quest, status="completed", progress=target, message="complete")
                    return True
                self.log(f"[{self.label}] {quest.name}: active, awaiting Discord completion")
                return "active"
            raise ApiError(500, f"heartbeat aborted after {beats} beats without reaching target")
        finally:
            pass

    def close(self):
        if self.rpc:
            try:
                self.rpc.clear_activity()
            except Exception:
                pass
            self.rpc.close()
            self.rpc = None
        if self.gateway:
            self.gateway.close()
            self.gateway = None

    def _refresh_quest(self, quest):
        """Refetch fresh quest state so retries don't reuse stale progress."""
        try:
            quests, _, _, _ = parse_quests_response(self.client.get_quests())
            fresh = next((item for item in quests if str(item.id) == str(quest.id)), None)
            return fresh if fresh is not None else quest
        except Exception:
            return quest

    def _attempt_once(self, quest):
        """Run a single attempt. Returns 'completed', 'skipped*', or 'active'.

        Raises Cancelled, ApiError, or unexpected Exception on failure so the
        caller can retry. Skipped/unsupported outcomes are terminal and are
        stored directly.
        """
        if quest.is_completed():
            self.results[quest.id] = "skipped"
            self._emit(quest, status="completed", progress=quest.target(), message="already completed")
            return "skipped"
        if quest.is_expired():
            self.results[quest.id] = "skipped"
            self._emit(quest, status="expired", message="expired")
            return "skipped"
        if not quest.is_actionable():
            self.results[quest.id] = "skipped"
            self._emit(quest, status="unsupported", message="unsupported")
            return "skipped"
        try:
            self._enroll(quest)
        except ApiError as exc:
            if exc.status in SKIPPABLE_STATUS:
                self.results[quest.id] = "skipped (unavailable)"
                self._emit(quest, status="unavailable", message=f"not available ({exc.status})")
                return "skipped (unavailable)"
            raise
        task_type = quest.task_type()
        if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            execution_status = self._run_video(quest)
        else:
            execution_status = self._run_heartbeat(quest)
        if execution_status == "active":
            return "active"
        self.results[quest.id] = "completed"
        self._emit(quest, status="completed", progress=quest.target(), message="completed")
        return "completed"

    def run_quest(self, quest):
        try:
            self._sleep(random.uniform(*QUEST_STAGGER))
        except Cancelled:
            self.results[quest.id] = "cancelled"
            raise
        current = quest
        last_reason = "unknown failure"
        for attempt in range(1, MAX_QUEST_ATTEMPTS + 1):
            if attempt > 1:
                try:
                    self._sleep(random.uniform(*RETRY_BACKOFF))
                except Cancelled:
                    self.results[current.id] = "cancelled"
                    raise
                current = self._refresh_quest(current)
                if current.is_completed():
                    self.results[current.id] = "skipped"
                    self._emit(current, status="completed", progress=current.target(), message="already completed")
                    return
                self.log(f"[{self.label}] {current.name}: retrying after failure ({last_reason}) (attempt {attempt}/{MAX_QUEST_ATTEMPTS})")
            try:
                outcome = self._attempt_once(current)
                if outcome in ("completed", "skipped", "skipped (unavailable)"):
                    return
                # "active" means Discord has not credited completion yet.
                last_reason = "awaiting Discord completion/credit"
                if attempt < MAX_QUEST_ATTEMPTS:
                    self.log(f"[{self.label}] {current.name}: attempt {attempt}/{MAX_QUEST_ATTEMPTS} failed ({last_reason}), retrying...")
                    self._emit(current, status="active", progress=current.local_progress(), message=f"attempt {attempt}/{MAX_QUEST_ATTEMPTS} failed, retrying")
                    continue
                final = f"error: failed after {MAX_QUEST_ATTEMPTS} attempts: {last_reason}"
                self.results[current.id] = final
                self.log(f"[{self.label}] {current.name}: giving up after {MAX_QUEST_ATTEMPTS} attempts ({last_reason})")
                self._emit(current, status="error", message=final)
                return
            except Cancelled:
                self.results[current.id] = "cancelled"
                raise
            except ApiError as exc:
                if exc.status in SKIPPABLE_STATUS:
                    self.results[current.id] = "skipped (unavailable)"
                    self._emit(current, status="unavailable", message=f"not available ({exc.status})")
                    return
                if "captcha_key" in (exc.body or ""):
                    last_reason = "captcha required (solve manually in the client)"
                else:
                    last_reason = str(exc)
                if attempt < MAX_QUEST_ATTEMPTS:
                    self.log(f"[{self.label}] {current.name}: attempt {attempt}/{MAX_QUEST_ATTEMPTS} failed ({last_reason}), retrying...")
                    self._emit(current, status="error", message=f"attempt {attempt}/{MAX_QUEST_ATTEMPTS} failed, retrying: {last_reason}")
                    continue
                final = f"error: failed after {MAX_QUEST_ATTEMPTS} attempts: {last_reason}"
                self.results[current.id] = final
                self.log(f"[{self.label}] {current.name}: giving up after {MAX_QUEST_ATTEMPTS} attempts ({last_reason})")
                self._emit(current, status="error", message=final)
                return
            except Exception as exc:
                last_reason = f"{type(exc).__name__}: {exc}"
                if attempt < MAX_QUEST_ATTEMPTS:
                    self.log(f"[{self.label}] {current.name}: attempt {attempt}/{MAX_QUEST_ATTEMPTS} failed ({last_reason}), retrying...")
                    self._emit(current, status="error", message=f"attempt {attempt}/{MAX_QUEST_ATTEMPTS} failed, retrying: {last_reason}")
                    continue
                detail = f"{last_reason}\n{traceback.format_exc()}"
                final = f"error: failed after {MAX_QUEST_ATTEMPTS} attempts: {last_reason}"
                self.results[current.id] = final
                self.log(f"[{self.label}] {current.name}: giving up after {MAX_QUEST_ATTEMPTS} attempts ({last_reason})\n{traceback.format_exc().rstrip()}")
                self._emit(current, status="error", message=final)
                return

    def run(self):
        if self.cancel_event.is_set():
            raise Cancelled()
        data = self.client.get_quests()
        quests, excluded, blocked_until, suspended_until = parse_quests_response(data)
        if self.quest_filter:
            quests = [quest for quest in quests if self.quest_filter(quest)]
        if self._check_suspension(suspended_until):
            for quest in quests:
                self.results[quest.id] = "skipped (suspended)"
            return ["error: quest access suspended"]
        self._log_block(blocked_until)
        self.log(f"[{self.label}] {len(quests)} quests loaded ({len(excluded)} excluded)")
        threads = []
        errors = []
        for quest in quests:
            if self.cancel_event.is_set():
                break
            thread = threading.Thread(target=self.run_quest, args=(quest,))
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        for quest in quests:
            status = self.results.get(quest.id, "not run")
            if status == "not run":
                # A worker thread died without recording a result; surface it
                # as a retried failure instead of silently reporting 0/N.
                message = f"error: failed after {MAX_QUEST_ATTEMPTS} attempts: worker did not finish"
                self.results[quest.id] = message
                self.log(f"[{self.label}] {quest.name}: giving up after {MAX_QUEST_ATTEMPTS} attempts (worker did not finish)")
                self._emit(quest, status="error", message=message)
                status = message
            if str(status).startswith("error"):
                errors.append(quest.id)
        return errors

    def _check_suspension(self, suspended_until):
        if not suspended_until:
            return False
        import datetime

        try:
            ts = str(suspended_until).replace("Z", "+00:00")
            until = datetime.datetime.fromisoformat(ts)
        except ValueError:
            return False
        if until.tzinfo is None:
            until = until.replace(tzinfo=datetime.timezone.utc)
        if datetime.datetime.now(datetime.timezone.utc) < until:
            self.log(f"[{self.label}] quest access suspended until {suspended_until}")
            return True
        return False

    def _log_block(self, blocked_until):
        if not blocked_until:
            return
        import datetime

        try:
            ts = str(blocked_until).replace("Z", "+00:00")
            until = datetime.datetime.fromisoformat(ts)
            if until.tzinfo is None:
                until = until.replace(tzinfo=datetime.timezone.utc)
            if datetime.datetime.now(datetime.timezone.utc) < until:
                self.log(f"[{self.label}] enrollment blocked until {blocked_until}")
        except ValueError:
            pass


def run_account(client, account_label, log, cancel_event=None, auto_enroll=True, event=None, enable_rpc=False, enable_gateway=False, desktop_idle_limit=None, quest_filter=None):
    runner = Runner(
        client,
        account_label,
        log,
        cancel_event,
        auto_enroll,
        event,
        enable_rpc,
        enable_gateway,
        desktop_idle_limit,
        quest_filter,
    )
    try:
        errors = runner.run()
    except Cancelled:
        log(f"[{account_label}] cancelled")
        return runner, ["cancelled"]
    except ApiError as exc:
        log(f"[{account_label}] account failed: {exc}")
        return runner, [f"error: {exc}"]
    except Exception:
        import traceback

        log(f"[{account_label}] unexpected failure:\n{traceback.format_exc()}")
        return runner, ["error: unexpected"]
    finally:
        runner.close()
    return runner, errors
