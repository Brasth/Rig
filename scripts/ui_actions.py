"""Bounded, durable popup actions independent of a popup's lifetime."""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

import cancellation
import jobs
import work_queue
from ui_store import private_directory, read, token, write


class Actions:
    def __init__(self, repo, completed=lambda request, result: None):
        self.repo = Path(repo).resolve()
        self.folder = private_directory(self.repo / ".rig" / "ui" / "actions")
        self.completed = completed
        self.lock = threading.RLock()
        self.lanes = {False: threading.BoundedSemaphore(2), True: threading.BoundedSemaphore(2)}
        self.running = set()
        self.uncertain = {}
        # A previous process may have committed an action before losing its
        # response. Do not turn that uncertainty into an automatic second action.
        for path in self.folder.glob("*.json"):
            value = read(path, {})
            if value.get("status") == "pending":
                value.update(status="unknown", error="Observer restarted; inspect the item before retrying")
                write(path, value)

    def path(self, action_id):
        if not isinstance(action_id, str) or not action_id or len(action_id) > 160:
            raise ValueError("invalid action ID")
        return self.folder / (token(action_id) + ".json")

    def get(self, action_id):
        if action_id in self.uncertain:
            return self.uncertain[action_id]
        value = read(self.path(action_id))
        if not value:
            return {"action_id": action_id, "status": "unknown", "error": "No recorded action; inspect state before retrying"}
        return {k: v for k, v in value.items() if k != "request"}

    def submit(self, request):
        request = dict(request)
        action_id = request.pop("action_id", None) or uuid.uuid4().hex
        path = self.path(action_id)
        signature = json.dumps(request, sort_keys=True)
        cancellation_lane = request.get("op") in {"stop", "cancel_queue"}
        lane = self.lanes[cancellation_lane]
        with self.lock:
            old = read(path)
            if old:
                if old.get("request") != signature:
                    raise ValueError("Action ID already belongs to a different request")
                return self.get(action_id)
            if not lane.acquire(blocking=False):
                return {"action_id": action_id, "status": "error", "error": "Action lane busy; retry"}
            value = {"action_id": action_id, "status": "pending", "request": signature}
            try:
                write(path, value)
            except BaseException:
                lane.release()
                raise
            self.running.add(action_id)

        def execute():
            try:
                result = self.execute(request)
                value.update(status="done", result=result)
            except (Exception, SystemExit) as error:
                value.update(status="error", error=str(error) or "Action failed")
            try:
                write(path, value)
            except OSError as error:
                self.uncertain[action_id] = {"action_id": action_id, "status": "unknown",
                    "error": f"Action receipt could not be saved; inspect state before retrying: {error}"}
            try:
                self.completed(request, self.get(action_id))
            except Exception:
                # Receipt truth is already recorded. A failed UI notification
                # must not reverse or repeat the underlying committed action.
                pass
            finally:
                # Keep the service alive until receipt notification finishes.
                with self.lock:
                    self.running.discard(action_id)
                lane.release()

        try:
            threading.Thread(target=execute, daemon=True, name="rig-ui-action").start()
        except BaseException:
            with self.lock:
                self.running.discard(action_id)
            lane.release()
            value.update(status="error", error="Unable to start action")
            write(path, value)
            raise
        return {"action_id": action_id, "status": "pending"}

    def target(self, value):
        if not isinstance(value, dict):
            raise ValueError("Select a current job before performing this action")
        target = dict(value)
        target["path"] = Path(target.get("path") or target.get("dir") or "").resolve()
        target["directory_identity"] = tuple(target.get("directory_identity") or ())
        if target["path"].parent != self.repo / ".rig" / "jobs":
            raise ValueError("Job is outside this repository")
        cancellation.current(target)
        return target

    def execute(self, request):
        op = request.get("op")
        if op == "enqueue":
            text = request.get("text")
            key = request.get("submission_id")
            if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                raise ValueError("Queue text must contain 1–2000 characters")
            if not isinstance(key, str) or not key or len(key) > 160:
                raise ValueError("Enqueue requires a stable submission ID")
            return work_queue.add_item(self.repo, text, idempotency_key=key)
        if op == "cancel_queue":
            return work_queue.cancel_item(self.repo, request.get("id") or "", expected_status="pending")
        if op == "stop":
            return jobs.cancel_target(self.target(request.get("target") or request.get("job")), "user-popup", return_details=True)
        if op == "reply":
            target = self.target(request.get("target") or request.get("job"))
            job = jobs.load_job(target["path"])
            if not job:
                raise ValueError("Job disappeared")
            job["ask"] = request.get("ask") or {}
            if not job["ask"]:
                raise ValueError("Select and inspect the current approval request")
            cancellation.current(target)
            text = jobs.answer_pending(job, request.get("behavior"), request.get("message") or "")
            if text.startswith("rig:"):
                raise ValueError(text)
            return {"text": text}
        raise ValueError("Unsupported UI action")
