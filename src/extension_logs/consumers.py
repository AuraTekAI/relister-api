"""
Real-time control channel between the admin webapp and a dealer's running
browser extension.

Two client roles connect to the same `ws/extension/` endpoint:

  * role=extension  — a dealer's extension. Authenticated as that user; joins the
                      `ext-<uid>` group and receives `command` messages. It streams
                      logs / acks / status up, which are fanned out to observers.

  * role=admin&target=<uid> — an admin (is_staff) watching/controlling dealer
                      <uid>. Joins the `obs-<uid>` group (receives the extension's
                      log/ack/status/presence stream) and may send `command`
                      messages, which are routed to `ext-<uid>`.

Security: only the authenticated dealer's own extension can join `ext-<uid>`
(target is forced to the token's user id for the extension role); only staff may
use the admin role. Remote commands enqueue work in the extension — they still
flow through its publish guards and anti-detection cooldowns.
"""

import json
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer

# Commands the extension understands. Kept here so an unknown/typo'd command from
# the webapp is dropped at the edge instead of reaching the extension.
KNOWN_COMMANDS = {
    "stop_auto",         # pause auto-processing before/after the current step
    "start_auto",        # resume auto-processing
    "override_auto",     # force a fresh auto-processing run now
    "remove_duplicates", # run the duplicate-removal pass now
    "delete_listing",    # payload: { fb_listing_id | listing_id }
    "relist_listing",    # payload: { fb_listing_id | listing_id }
    "republish_listing", # payload: { listing_id }
    "report_status",     # ask the extension to emit its current status
    "set_log_stream",    # payload: { on: bool } — start/stop live log streaming
    "refresh",           # re-push the FB + unpublished snapshot so the admin sees latest
}


class ExtensionControlConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)  # unauthenticated
            return

        qs = parse_qs((self.scope.get("query_string") or b"").decode())
        self.role = (qs.get("role") or ["extension"])[0]

        if self.role == "admin":
            if not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
                await self.close(code=4403)  # forbidden
                return
            try:
                self.target_uid = int((qs.get("target") or [""])[0])
            except (ValueError, TypeError):
                await self.close(code=4400)  # bad request — missing/invalid target
                return
        else:
            # Extension role: it may only control its OWN user's channel.
            self.role = "extension"
            self.target_uid = user.id

        self.user = user
        self.ext_group = f"ext-{self.target_uid}"
        self.obs_group = f"obs-{self.target_uid}"

        if self.role == "extension":
            await self.channel_layer.group_add(self.ext_group, self.channel_name)
        else:
            await self.channel_layer.group_add(self.obs_group, self.channel_name)

        await self.accept()

        if self.role == "extension":
            await self._to_observers({"type": "presence", "online": True})
        else:
            # Probe any connected extension so the admin UI can show live status
            # (and presence) right away instead of waiting for the next event.
            await self._to_extension({"type": "command", "command": "report_status", "id": "presence-probe", "payload": {}})

    async def disconnect(self, code):
        role = getattr(self, "role", None)
        if role == "extension":
            await self.channel_layer.group_discard(self.ext_group, self.channel_name)
            await self._to_observers({"type": "presence", "online": False})
        elif role == "admin":
            await self.channel_layer.group_discard(self.obs_group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            msg = json.loads(text_data or "{}")
        except (ValueError, TypeError):
            return

        if self.role == "admin":
            if msg.get("type") != "command":
                return
            command = msg.get("command")
            if command not in KNOWN_COMMANDS:
                await self.send(text_data=json.dumps({"type": "error", "error": f"unknown command: {command}"}))
                return
            payload = msg.get("payload") or {}
            cmd_id = msg.get("id")
            await self._to_extension({"type": "command", "command": command, "payload": payload, "id": cmd_id})
            # Echo to observers so the command shows up in the live log too.
            await self._to_observers({
                "type": "command_sent",
                "command": command,
                "payload": payload,
                "id": cmd_id,
                "by": getattr(self.user, "email", None),
            })
        else:  # extension → observers
            if msg.get("type") in ("log", "ack", "status", "presence"):
                await self._to_observers(msg)

    # ── group fan-out helpers ────────────────────────────────────────────────
    async def _to_extension(self, data):
        await self.channel_layer.group_send(self.ext_group, {"type": "command.message", "data": data})

    async def _to_observers(self, data):
        await self.channel_layer.group_send(self.obs_group, {"type": "stream.message", "data": data})

    async def command_message(self, event):
        # Delivered only to members of ext-<uid> (the extension).
        await self.send(text_data=json.dumps(event["data"]))

    async def stream_message(self, event):
        # Delivered only to members of obs-<uid> (the admins).
        await self.send(text_data=json.dumps(event["data"]))
