#!/usr/bin/env python3
"""
KDE Connect MCP Server - FastMCP Implementation
Provides integration with KDE Connect devices through Model Context Protocol
"""

import sys
import os
import unicodedata

# Import FastMCP first, before adding system paths
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from fastmcp import FastMCP

# Add system site-packages for dbus-python after FastMCP import
sys.path.append('/usr/lib/python3/dist-packages')
import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


# Initialize D-Bus
DBusGMainLoop(set_as_default=True)


# Android Telephony.Sms.MESSAGE_TYPE constants
_SMS_TYPE_NAMES = {
    0: "all", 1: "received", 2: "sent", 3: "draft",
    4: "outbox", 5: "failed", 6: "queued",
}


def _parse_sms_message(msg) -> Dict[str, Any]:
    """Parse a D-Bus SMS message struct into a plain dict.

    Struct layout (matches `conversationCreated`/`conversationUpdated` signals
    emitted by `org.kde.kdeconnect.device.conversations`):
        (eventField, body, [(address,), ...], date_ms, type,
         read, threadID, messageID, subID, attachments)
    """
    import datetime
    date_ms = int(msg[3])
    msg_type = int(msg[4])
    return {
        "thread_id": int(msg[6]),
        "message_id": int(msg[7]),
        "body": str(msg[1]),
        "addresses": [str(a[0]) for a in msg[2]],
        "date_ms": date_ms,
        "date": datetime.datetime.fromtimestamp(date_ms / 1000).isoformat(
            sep=" ", timespec="seconds"
        ),
        "type": msg_type,
        "direction": _SMS_TYPE_NAMES.get(msg_type, f"type_{msg_type}"),
        "read": bool(int(msg[5])),
        "sub_id": int(msg[8]),
        "attachment_count": len(msg[9]),
    }


def _norm(text: str) -> str:
    """Normalize text for diacritic- and case-insensitive comparison:
    NFD-decompose, strip combining marks (accents), casefold. E.g. both
    "objednávka" and "objednavka" normalize to "objednavka", so a query
    typed without diacritics still matches accented text and vice versa."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def _parse_since(since: str) -> int:
    """Parse an ISO-ish date or datetime string into epoch milliseconds,
    matching the `date_ms` field used throughout this module. Accepts
    "2026-07-01", "2026-07-01 12:00", "2026-07-01 12:00:00" and general
    ISO 8601 (e.g. "2026-07-01T12:00")."""
    import datetime
    s = since.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    dt = datetime.datetime.fromisoformat(s)
    return int(dt.timestamp() * 1000)


class KDEConnectDBus:
    """D-Bus interface for KDE Connect"""

    BUS_NAME = "org.kde.kdeconnect"
    DAEMON_PATH = "/modules/kdeconnect"
    DEVICE_PATH_PREFIX = "/modules/kdeconnect/devices"

    # How often to sample the daemon's conversation cache while waiting for
    # the phone to finish streaming thread heads.
    SMS_POLL_SECONDS = 2.0

    def __init__(self):
        self.bus = dbus.SessionBus()
        self.daemon = self.bus.get_object(self.BUS_NAME, self.DAEMON_PATH)

    def _get_device_interface(self, device_id: str, plugin: str = None) -> dbus.Interface:
        """Get D-Bus interface for device/plugin"""
        path = f"{self.DEVICE_PATH_PREFIX}/{device_id}"
        if plugin:
            path += f"/{plugin}"
        obj = self.bus.get_object(self.BUS_NAME, path)
        return dbus.Interface(
            obj,
            f"org.kde.kdeconnect.device.{plugin}" if plugin else "org.kde.kdeconnect.device"
        )

    def _get_properties(self, device_id: str, plugin: str) -> dbus.Interface:
        """Get properties interface for plugin"""
        path = f"{self.DEVICE_PATH_PREFIX}/{device_id}/{plugin}"
        obj = self.bus.get_object(self.BUS_NAME, path)
        return dbus.Interface(obj, "org.freedesktop.DBus.Properties")

    def list_devices(self, reachable_only: bool = True, paired_only: bool = True) -> List[str]:
        """List all devices"""
        daemon_iface = dbus.Interface(self.daemon, "org.kde.kdeconnect.daemon")
        return list(daemon_iface.devices(paired_only, reachable_only))

    def get_device_info(self, device_id: str) -> Dict[str, Any]:
        """Get device information"""
        path = f"{self.DEVICE_PATH_PREFIX}/{device_id}"
        obj = self.bus.get_object(self.BUS_NAME, path)
        props_iface = dbus.Interface(obj, "org.freedesktop.DBus.Properties")

        info = {
            "id": device_id,
            "name": str(props_iface.Get("org.kde.kdeconnect.device", "name")),
            "type": str(props_iface.Get("org.kde.kdeconnect.device", "type")),
            "is_paired": bool(props_iface.Get("org.kde.kdeconnect.device", "isPaired")),
            "is_reachable": bool(props_iface.Get("org.kde.kdeconnect.device", "isReachable"))
        }
        return info

    def get_battery(self, device_id: str) -> Dict[str, Any]:
        """Get battery status"""
        props = self._get_properties(device_id, "battery")
        return {
            "charge": int(props.Get("org.kde.kdeconnect.device.battery", "charge")),
            "is_charging": bool(props.Get("org.kde.kdeconnect.device.battery", "isCharging"))
        }

    def get_media_players(self, device_id: str) -> List[str]:
        """Get list of available media players"""
        props = self._get_properties(device_id, "mprisremote")
        try:
            players = props.Get("org.kde.kdeconnect.device.mprisremote", "playerList")
            return list(players) if players else []
        except:
            return []

    def set_media_player(self, device_id: str, player: str):
        """Set active media player"""
        props = self._get_properties(device_id, "mprisremote")
        props.Set("org.kde.kdeconnect.device.mprisremote", "player", player)

    def get_current_player(self, device_id: str) -> str:
        """Get current active player"""
        props = self._get_properties(device_id, "mprisremote")
        try:
            return str(props.Get("org.kde.kdeconnect.device.mprisremote", "player"))
        except:
            return ""

    def detect_active_player(self, device_id: str) -> Dict[str, Any]:
        """Detect which player is actually playing media"""
        import time

        players = self.get_media_players(device_id)
        current_player = self.get_current_player(device_id)
        props = self._get_properties(device_id, "mprisremote")

        active_players = []
        player_info = {}

        for player in players:
            try:
                # Switch to this player
                props.Set("org.kde.kdeconnect.device.mprisremote", "player", player)
                time.sleep(0.3)  # Wait for switch

                # Check if playing
                is_playing = bool(props.Get("org.kde.kdeconnect.device.mprisremote", "isPlaying"))
                title = str(props.Get("org.kde.kdeconnect.device.mprisremote", "title"))
                artist = str(props.Get("org.kde.kdeconnect.device.mprisremote", "artist"))

                player_info[player] = {
                    "is_playing": is_playing,
                    "has_media": bool(title or artist),
                    "title": title,
                    "artist": artist
                }

                if is_playing and (title or artist):
                    active_players.append(player)

            except Exception as e:
                player_info[player] = {"error": str(e)}

        # Restore original player
        if current_player:
            try:
                props.Set("org.kde.kdeconnect.device.mprisremote", "player", current_player)
            except:
                pass

        return {
            "active_players": active_players,
            "player_details": player_info,
            "original_player": current_player
        }

    def get_now_playing(self, device_id: str) -> Dict[str, Any]:
        """Get currently playing media information"""
        props = self._get_properties(device_id, "mprisremote")
        try:
            players = self.get_media_players(device_id)
            current_player = self.get_current_player(device_id)

            result = {
                "title": str(props.Get("org.kde.kdeconnect.device.mprisremote", "title")),
                "artist": str(props.Get("org.kde.kdeconnect.device.mprisremote", "artist")),
                "album": str(props.Get("org.kde.kdeconnect.device.mprisremote", "album")),
                "is_playing": bool(props.Get("org.kde.kdeconnect.device.mprisremote", "isPlaying")),
                "position": int(props.Get("org.kde.kdeconnect.device.mprisremote", "position")),
                "length": int(props.Get("org.kde.kdeconnect.device.mprisremote", "length")),
                "volume": int(props.Get("org.kde.kdeconnect.device.mprisremote", "volume")),
                "available_players": players,
                "current_player": current_player
            }
            return result
        except Exception as e:
            return {"error": str(e)}

    def media_control(self, device_id: str, action: str):
        """Control media playback"""
        iface = self._get_device_interface(device_id, "mprisremote")
        iface.sendAction(action)

    def send_ping(self, device_id: str, message: str = ""):
        """Send ping notification"""
        iface = self._get_device_interface(device_id, "ping")
        if message:
            iface.sendPing(message)
        else:
            iface.sendPing()

    def share_url(self, device_id: str, url: str):
        """Share URL to device"""
        iface = self._get_device_interface(device_id, "share")
        iface.shareUrl(url)

    def share_file(self, device_id: str, file_path: str):
        """Share file to device"""
        iface = self._get_device_interface(device_id, "share")
        file_url = f"file://{file_path}"
        iface.shareUrls([file_url])

    def ring_device(self, device_id: str):
        """Ring the device"""
        iface = self._get_device_interface(device_id, "findmyphone")
        iface.ring()

    def get_download_directory(self, device_id: str) -> str:
        """Get the download directory for received files"""
        import os
        home = os.path.expanduser("~")
        default_download = os.path.join(home, "Downloads")

        try:
            config_path = os.path.join(home, ".config", "kdeconnect", device_id + "_share", "config")
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    for line in f:
                        if line.startswith("incoming_path="):
                            return line.split("=", 1)[1].strip()
        except:
            pass

        return default_download

    def list_received_files(self, device_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """List recently received files from device"""
        import os
        import time

        download_dir = self.get_download_directory(device_id)

        if not os.path.exists(download_dir):
            return []

        files = []
        for filename in os.listdir(download_dir):
            filepath = os.path.join(download_dir, filename)
            if os.path.isfile(filepath):
                stat = os.stat(filepath)
                files.append({
                    "name": filename,
                    "path": filepath,
                    "size": stat.st_size,
                    "modified": time.ctime(stat.st_mtime),
                    "modified_timestamp": stat.st_mtime
                })

        # Sort by modification time (newest first)
        files.sort(key=lambda x: x["modified_timestamp"], reverse=True)

        return files[:limit]

    def open_received_file(self, file_path: str):
        """Open a received file with default application"""
        import subprocess
        subprocess.Popen(["xdg-open", file_path])

    def get_notifications(self, device_id: str) -> List[Dict[str, Any]]:
        """Get all active notifications with details"""
        iface = self._get_device_interface(device_id, "notifications")
        notification_ids = list(iface.activeNotifications())

        notifications = []
        for notif_id in notification_ids:
            try:
                # Get notification object
                notif_path = f"{self.DEVICE_PATH_PREFIX}/{device_id}/notifications/{notif_id}"
                notif_obj = self.bus.get_object(self.BUS_NAME, notif_path)
                notif_props = dbus.Interface(notif_obj, "org.freedesktop.DBus.Properties")

                # Get all properties
                notification = {
                    "id": notif_id,
                    "app_name": str(notif_props.Get("org.kde.kdeconnect.device.notifications.notification", "appName")),
                    "title": str(notif_props.Get("org.kde.kdeconnect.device.notifications.notification", "title")),
                    "text": str(notif_props.Get("org.kde.kdeconnect.device.notifications.notification", "text")),
                    "ticker": str(notif_props.Get("org.kde.kdeconnect.device.notifications.notification", "ticker")),
                    "dismissable": bool(notif_props.Get("org.kde.kdeconnect.device.notifications.notification", "dismissable")),
                    "has_icon": bool(notif_props.Get("org.kde.kdeconnect.device.notifications.notification", "hasIcon")),
                    "silent": bool(notif_props.Get("org.kde.kdeconnect.device.notifications.notification", "silent"))
                }

                # Try to get replyId if available
                try:
                    notification["reply_id"] = str(notif_props.Get("org.kde.kdeconnect.device.notifications.notification", "replyId"))
                except:
                    notification["reply_id"] = ""

                notifications.append(notification)
            except Exception as e:
                notifications.append({
                    "id": notif_id,
                    "error": str(e)
                })

        return notifications

    # ========== SMS / conversations ==========

    def _get_conversations_interface(self, device_id: str) -> dbus.Interface:
        """Conversations methods live on the root device object path with a
        dedicated interface name, unlike battery/notifications/mprisremote
        which use a `/<plugin>` subpath. Use this helper for SMS calls."""
        path = f"{self.DEVICE_PATH_PREFIX}/{device_id}"
        obj = self.bus.get_object(self.BUS_NAME, path)
        return dbus.Interface(obj, "org.kde.kdeconnect.device.conversations")

    def _run_conversation_round(
        self,
        thread_id: int,
        request_fn,
        round_timeout: float,
    ) -> tuple:
        """Run a single conversationUpdated/conversationLoaded collection
        round for ONE thread: invoke `request_fn`, collect
        `conversationUpdated` signals filtered to `thread_id`, and stop as
        soon as `conversationLoaded` fires for that thread (success, the
        `messageCount` is authoritative) or `round_timeout` elapses
        (fallback poison pill -- a timeout is NOT a success).

        Returns `(messages_by_id, reported_count_or_None, timed_out)`.
        """
        import time
        messages: Dict[int, Dict[str, Any]] = {}
        state = {"reported_count": None, "loaded": False}
        tid = int(thread_id)

        def on_updated(payload, *_, **__):
            try:
                if int(payload[6]) != tid:
                    return
                m = _parse_sms_message(payload)
                messages[m["message_id"]] = m
            except Exception:
                pass

        def on_loaded(conv_id, count, *_, **__):
            try:
                if int(conv_id) == tid:
                    state["reported_count"] = int(count)
                    state["loaded"] = True
            except Exception:
                pass

        match_updated = self.bus.add_signal_receiver(
            on_updated,
            signal_name="conversationUpdated",
            dbus_interface="org.kde.kdeconnect.device.conversations",
        )
        match_loaded = self.bus.add_signal_receiver(
            on_loaded,
            signal_name="conversationLoaded",
            dbus_interface="org.kde.kdeconnect.device.conversations",
        )

        loop = GLib.MainLoop()
        deadline = time.time() + round_timeout

        def check_done():
            if state["loaded"] or time.time() >= deadline:
                loop.quit()
                return False
            return True

        GLib.timeout_add(150, check_done)
        try:
            request_fn()
            loop.run()
        finally:
            match_updated.remove()
            match_loaded.remove()

        timed_out = not state["loaded"]
        return messages, state["reported_count"], timed_out

    def _cached_threads(self, iface) -> Dict[int, Dict[str, Any]]:
        """Read the daemon's conversation cache and reduce it to one entry
        (the newest message) per thread. `activeConversations()` is the
        authoritative, cumulative view -- it holds every thread head the
        phone has streamed so far, across all previous requests."""
        seen: Dict[int, Dict[str, Any]] = {}
        for t in iface.activeConversations():
            try:
                m = _parse_sms_message(t)
            except Exception:
                continue
            tid = m["thread_id"]
            if tid not in seen or m["date_ms"] > seen[tid]["date_ms"]:
                seen[tid] = m
        return seen

    def load_sms_threads(
        self, device_id: str, refresh: bool = True,
        wait_seconds: float = 20.0, quiet_seconds: float = 6.0,
    ) -> tuple:
        """Return `(threads, meta)` -- the newest message per SMS thread,
        sorted newest first, plus metadata about how complete the set is.

        The phone streams thread heads to the daemon slowly (measured at
        roughly 4-5 threads per second), so a full history of a few thousand
        threads takes minutes to arrive. The daemon accumulates them in
        `activeConversations()`; that cache is what we read.

        Do NOT collect `conversationCreated` signals and return those alone:
        the daemon emits that signal only for threads it does not already
        hold, so every repeat request yields a shrinking, arbitrary subset
        while the complete data sits unused in the cache.

        With `refresh=True` we ask the phone to (re)stream, then wait until
        the cache stops growing for `quiet_seconds` or `wait_seconds` runs
        out. `meta["complete"]` says which of the two ended the wait, so the
        caller can tell a finished load from a truncated one.
        """
        import time
        iface = self._get_conversations_interface(device_id)
        started = time.time()
        seen = self._cached_threads(iface)
        cached_before = len(seen)
        complete = True

        if refresh:
            complete = False
            iface.requestAllConversationThreads()
            previous = cached_before
            stable_since = None
            while time.time() - started < wait_seconds:
                time.sleep(self.SMS_POLL_SECONDS)
                seen = self._cached_threads(iface)
                if len(seen) == previous:
                    if stable_since is None:
                        stable_since = time.time()
                    elif time.time() - stable_since >= quiet_seconds:
                        complete = True
                        break
                else:
                    stable_since = None
                previous = len(seen)

        threads = sorted(seen.values(), key=lambda m: m["date_ms"], reverse=True)
        meta = {
            "complete": complete,
            "cached_before": cached_before,
            "cached_after": len(threads),
            "waited_seconds": round(time.time() - started, 1),
        }
        return threads, meta

    def sms_cache_status(self, device_id: str) -> Dict[str, Any]:
        """Cheap progress probe: how many threads the daemon holds right now
        and whether that number is still climbing. Fires no new request."""
        import time
        iface = self._get_conversations_interface(device_id)
        first = self._cached_threads(iface)
        time.sleep(self.SMS_POLL_SECONDS)
        second = self._cached_threads(iface)
        newest = max(second.values(), key=lambda m: m["date_ms"], default=None)
        oldest = min(second.values(), key=lambda m: m["date_ms"], default=None)
        return {
            "cached_threads": len(second),
            "still_loading": len(second) > len(first),
            "gained_during_probe": len(second) - len(first),
            "newest_message": newest["date"] if newest else None,
            "oldest_message": oldest["date"] if oldest else None,
        }

    def load_thread_messages(
        self, device_id: str, thread_id: int, since_ms: int = None,
        max_rounds: int = 6, per_round_window: int = 200,
        round_timeout: float = 8.0,
    ) -> tuple:
        """Deeply load ONE thread's messages by repeatedly calling
        `requestConversation` until the daemon reports the full message
        count via `conversationLoaded` (authoritative, not a guess).

        Each round asks `requestConversation(thread_id, 0, per_round_window)`
        and collects `conversationUpdated` signals for that thread, ending
        the round on the matching `conversationLoaded` (primary) or on
        `round_timeout` (a fallback -- a timeout is NOT success). Rounds
        repeat until one of:
          - the unique message count stops growing (convergence), or
          - `since_ms` is given and the oldest message collected so far is
            already older than it (no need to go deeper), or
          - `max_rounds` is exhausted.

        Returns `(messages, meta)`, messages sorted oldest first:
            meta = {
                "complete": bool,     # converged/reached_since, no timeout,
                                       # and reported_count matches (or is
                                       # unknown)
                "rounds": int,
                "messages": int,
                "reported_count": int | None,  # last conversationLoaded count
                "converged": bool,
                "reached_since": bool,
                "timed_out": bool,    # last round's timeout state
            }
        """
        iface = self._get_conversations_interface(device_id)
        tid = int(thread_id)
        seen: Dict[int, Dict[str, Any]] = {}
        reported_count = None
        converged = False
        reached_since = False
        timed_out = False
        rounds_done = 0

        for round_num in range(int(max_rounds)):
            rounds_done = round_num + 1
            before = len(seen)

            round_messages, count, round_timed_out = self._run_conversation_round(
                tid,
                lambda: iface.requestConversation(
                    dbus.Int64(tid), 0, int(per_round_window)
                ),
                round_timeout,
            )
            seen.update(round_messages)
            timed_out = round_timed_out
            if count is not None:
                reported_count = count

            if since_ms is not None and seen:
                oldest_ms = min(m["date_ms"] for m in seen.values())
                if oldest_ms <= since_ms:
                    reached_since = True
                    break

            if len(seen) == before:
                converged = True
                break

        count_matches = reported_count is None or reported_count == len(seen)
        complete = (converged or reached_since) and not timed_out and count_matches

        messages = sorted(seen.values(), key=lambda m: m["date_ms"])
        meta = {
            "complete": complete,
            "rounds": rounds_done,
            "messages": len(messages),
            "reported_count": reported_count,
            "converged": converged,
            "reached_since": reached_since,
            "timed_out": timed_out,
        }
        return messages, meta

    def load_threads_deep(
        self, device_id: str, thread_ids: List[int], since_ms: int = None,
        concurrency: int = 6, total_budget_seconds: float = 60.0,
    ) -> tuple:
        """Deeply load MULTIPLE threads concurrently.

        `_run_conversation_round` spins its own GLib MainLoop, so N of its
        instances cannot be nested to run in parallel. This method instead
        shares ONE signal receiver + ONE MainLoop across every thread and
        demultiplexes incoming signals by thread_id, while holding a sliding
        concurrency window: at most `concurrency` threads are in flight at
        once, and a finished slot (its `conversationLoaded` fired, or -- if
        `since_ms` is given -- it already reached that depth) is refilled
        from the pending queue. Verified safe up to 10 concurrent requests;
        do not raise `concurrency` above that without re-testing.

        Each thread gets a single `requestConversation(id, 0, 200)` round
        (no per-thread convergence looping, unlike `load_thread_messages`);
        a per-thread timeout applies on top of the shared
        `total_budget_seconds`, which is a hard cap on the whole call --
        threads left over when it expires are reported, never silently
        dropped.

        Returns `(results, coverage)`:
            results = {thread_id: [messages sorted oldest first], ...}
                      (present for every id in `thread_ids`, possibly empty)
            coverage = {
                "threads_selected": int,
                "threads_searched": int,       # got conversationLoaded or
                                                # reached since_ms depth
                "threads_timeout": [ids],      # launched but never finished
                "threads_not_reached": [ids],  # never even got a request
                "messages_searched": int,      # total messages collected,
                                                # across searched AND
                                                # timed-out threads
                "budget_exhausted": bool,
            }
        """
        import time
        tids = [int(t) for t in thread_ids]
        concurrency = max(1, int(concurrency))
        results: Dict[int, Dict[int, Dict[str, Any]]] = {t: {} for t in tids}

        if not tids:
            return {}, {
                "threads_selected": 0,
                "threads_searched": 0,
                "threads_timeout": [],
                "threads_not_reached": [],
                "messages_searched": 0,
                "budget_exhausted": False,
            }

        iface = self._get_conversations_interface(device_id)
        pending = list(tids)
        in_flight: Dict[int, Dict[str, Any]] = {}
        done_ids: set = set()
        timed_out_ids: set = set()

        start = time.time()
        deadline = start + float(total_budget_seconds)
        PER_THREAD_TIMEOUT = 15.0

        def on_updated(payload, *_, **__):
            try:
                tid = int(payload[6])
                if tid not in in_flight:
                    return
                m = _parse_sms_message(payload)
                results[tid][m["message_id"]] = m
                if since_ms is not None and m["date_ms"] <= since_ms:
                    in_flight[tid]["reached_since"] = True
            except Exception:
                pass

        def on_loaded(conv_id, count, *_, **__):
            try:
                tid = int(conv_id)
                if tid in in_flight:
                    in_flight[tid]["loaded"] = True
            except Exception:
                pass

        match_updated = self.bus.add_signal_receiver(
            on_updated,
            signal_name="conversationUpdated",
            dbus_interface="org.kde.kdeconnect.device.conversations",
        )
        match_loaded = self.bus.add_signal_receiver(
            on_loaded,
            signal_name="conversationLoaded",
            dbus_interface="org.kde.kdeconnect.device.conversations",
        )

        def launch(tid):
            in_flight[tid] = {
                "loaded": False,
                "reached_since": False,
                "deadline": min(time.time() + PER_THREAD_TIMEOUT, deadline),
            }
            iface.requestConversation(dbus.Int64(tid), 0, 200)

        loop = GLib.MainLoop()

        def pump():
            now = time.time()
            for tid in list(in_flight.keys()):
                state = in_flight[tid]
                if state["loaded"] or state["reached_since"]:
                    done_ids.add(tid)
                    del in_flight[tid]
                elif now >= state["deadline"]:
                    timed_out_ids.add(tid)
                    done_ids.add(tid)
                    del in_flight[tid]

            while pending and len(in_flight) < concurrency and now < deadline:
                launch(pending.pop(0))

            if now >= deadline:
                loop.quit()
                return False
            if not pending and not in_flight:
                loop.quit()
                return False
            return True

        try:
            while pending and len(in_flight) < concurrency:
                launch(pending.pop(0))
            if in_flight or pending:
                GLib.timeout_add(200, pump)
                loop.run()
        finally:
            match_updated.remove()
            match_loaded.remove()

        budget_exhausted = time.time() >= deadline
        # Anything still in flight when the loop ended (budget ran out) is a
        # timeout, not a "not reached" -- it was launched.
        for tid in list(in_flight.keys()):
            timed_out_ids.add(tid)
            done_ids.add(tid)
        not_reached_ids = [t for t in tids if t not in done_ids]
        searched_ids = [t for t in tids if t in done_ids and t not in timed_out_ids]

        out_results: Dict[int, List[Dict[str, Any]]] = {}
        total_messages = 0
        for tid in tids:
            msgs = sorted(results[tid].values(), key=lambda m: m["date_ms"])
            out_results[tid] = msgs
            total_messages += len(msgs)

        coverage = {
            "threads_selected": len(tids),
            "threads_searched": len(searched_ids),
            "threads_timeout": sorted(timed_out_ids),
            "threads_not_reached": sorted(not_reached_ids),
            "messages_searched": total_messages,
            "budget_exhausted": budget_exhausted,
        }
        return out_results, coverage

    def read_sms_conversation(
        self, device_id: str, thread_id: int, limit: int = 50,
        wait_seconds: float = 15.0,
    ) -> tuple:
        """Return the message history of a single thread, ordered
        chronologically (oldest first), using `load_thread_messages` --
        i.e. driven by the authoritative `conversationLoaded` count rather
        than a quiet-period guess. `limit` sizes the per-round request
        window (floored at 200) but does NOT truncate the result: the
        daemon's `end` argument is not an actual cap (see
        `load_thread_messages`/`_run_conversation_round`), convergence is.

        Returns `(messages, meta)` -- `meta` is `load_thread_messages`'s
        meta dict, whose `complete` says whether this really is the full
        thread history.
        """
        return self.load_thread_messages(
            device_id, int(thread_id),
            max_rounds=6, per_round_window=max(int(limit), 200),
            round_timeout=wait_seconds,
        )

    def send_sms(
        self, device_id: str, text: str,
        thread_id: int = None, phone: str = None,
    ) -> Dict[str, Any]:
        """Send an SMS. If `thread_id` is given, replies into that existing
        thread (preserves conversation continuity, can include multiple
        recipients automatically). If `phone` is given instead, starts a new
        thread. Caller MUST pre-confirm — this method always sends for real."""
        if not text:
            raise ValueError("text must not be empty")
        if (thread_id is None) == (phone is None):
            raise ValueError(
                "exactly one of thread_id / phone must be provided"
            )
        iface = self._get_conversations_interface(device_id)
        if thread_id is not None:
            iface.replyToConversation(
                dbus.Int64(int(thread_id)), str(text), dbus.Array([], signature="v")
            )
            return {"status": "sent", "thread_id": int(thread_id), "text": text}
        addresses = dbus.Array(
            [dbus.Dictionary({"address": str(phone)}, signature="sv")],
            signature="a{sv}",
        )
        iface.sendWithoutConversation(addresses, str(text), dbus.Array([], signature="v"))
        return {"status": "sent", "phone": phone, "text": text}


# Initialize KDE Connect interface
kdeconnect = KDEConnectDBus()

# Create FastMCP server
mcp = FastMCP("KDE Connect MCP Server")


# ========== Tool Definitions ==========

@mcp.tool()
def list_devices() -> Dict[str, Any]:
    """
    List all available KDE Connect devices

    Returns information about all paired and reachable KDE Connect devices,
    including their IDs, names, types, and connection status.

    Returns:
        A dictionary containing:
        - devices: List of device information dictionaries
        - count: Total number of devices found
    """
    devices = kdeconnect.list_devices()
    device_info = []
    for device_id in devices:
        try:
            info = kdeconnect.get_device_info(device_id)
            device_info.append(info)
        except Exception as e:
            device_info.append({"id": device_id, "error": str(e)})

    return {
        "devices": device_info,
        "count": len(device_info)
    }


@mcp.tool()
def get_battery(device_id: str) -> Dict[str, Any]:
    """
    Get battery status from a device

    Retrieves the current battery level and charging status from the specified device.

    Args:
        device_id: The unique identifier of the KDE Connect device (use list_devices to find device IDs)

    Returns:
        Battery information including charge percentage and charging status
    """
    return kdeconnect.get_battery(device_id)


@mcp.tool()
def get_now_playing(device_id: str) -> Dict[str, Any]:
    """
    Get currently playing media information

    Retrieves detailed information about the currently playing media on the device,
    including track title, artist, album, playback position, and available media players.

    Args:
        device_id: The unique identifier of the KDE Connect device (use list_devices to find device IDs)

    Returns:
        Media information including title, artist, album, playback status, and position
    """
    return kdeconnect.get_now_playing(device_id)


@mcp.tool()
def media_control(
    device_id: str,
    action: Literal["Play", "Pause", "PlayPause", "Next", "Previous", "Stop"]
) -> Dict[str, str]:
    """
    Control media playback on a device

    Send media control commands to the device to control playback of music, videos, or other media.

    Args:
        device_id: The unique identifier of the KDE Connect device
        action: Media control action to perform

    Returns:
        Status confirmation with the action that was performed
    """
    kdeconnect.media_control(device_id, action)
    return {"status": "success", "action": action}


@mcp.tool()
def send_notification(device_id: str, message: str) -> Dict[str, str]:
    """
    Send a notification to a device

    Display a notification message on the target device. Useful for reminders,
    alerts, or sending quick messages.

    Args:
        device_id: The unique identifier of the KDE Connect device
        message: The notification message to send (1-500 characters)

    Returns:
        Status confirmation
    """
    kdeconnect.send_ping(device_id, message)
    return {"status": "sent", "message": message}


@mcp.tool()
def share_url(device_id: str, url: str) -> Dict[str, str]:
    """
    Share a URL to a device

    Send a URL to the device, which will typically open in the device's default browser.

    Args:
        device_id: The unique identifier of the KDE Connect device
        url: The URL to share with the device (must start with http:// or https://)

    Returns:
        Status confirmation with the shared URL
    """
    kdeconnect.share_url(device_id, url)
    return {"status": "shared", "url": url}


@mcp.tool()
def share_file(device_id: str, file_path: str) -> Dict[str, str]:
    """
    Share a file to a device

    Transfer a file from this computer to the target device. The file will be
    saved to the device's configured download location.

    Args:
        device_id: The unique identifier of the KDE Connect device
        file_path: The absolute path to the file to share (e.g., /home/user/document.pdf)

    Returns:
        Status confirmation with the shared file path
    """
    kdeconnect.share_file(device_id, file_path)
    return {"status": "shared", "file": file_path}


@mcp.tool()
def ring_device(device_id: str) -> Dict[str, str]:
    """
    Make a device ring to help locate it

    Trigger the device to ring with maximum volume, even if it's on silent mode.
    Useful for finding a lost phone or device.

    Args:
        device_id: The unique identifier of the KDE Connect device (use list_devices to find device IDs)

    Returns:
        Status confirmation
    """
    kdeconnect.ring_device(device_id)
    return {"status": "ringing"}


@mcp.tool()
def get_media_players(device_id: str) -> Dict[str, Any]:
    """
    Get list of available media players on a device

    Retrieves all media player applications available on the device that support
    remote control through KDE Connect.

    Args:
        device_id: The unique identifier of the KDE Connect device (use list_devices to find device IDs)

    Returns:
        List of available players, current active player, and count
    """
    players = kdeconnect.get_media_players(device_id)
    current = kdeconnect.get_current_player(device_id)
    return {
        "available_players": players,
        "current_player": current,
        "count": len(players)
    }


@mcp.tool()
def set_media_player(device_id: str, player: str) -> Dict[str, str]:
    """
    Set the active media player on a device

    Switch to a specific media player for subsequent media control commands.
    Must be called before using media_control if multiple players are active.

    Args:
        device_id: The unique identifier of the KDE Connect device
        player: The name of the media player to activate (e.g., 'YouTube', 'Spotify', 'VLC')

    Returns:
        Status confirmation with the selected player
    """
    kdeconnect.set_media_player(device_id, player)
    return {
        "status": "player_set",
        "player": player
    }


@mcp.tool()
def detect_active_player(device_id: str) -> Dict[str, Any]:
    """
    Automatically detect which media player is currently playing

    Scans all available media players to find which one is actively playing media.
    Returns detailed information about each player's state.

    Args:
        device_id: The unique identifier of the KDE Connect device (use list_devices to find device IDs)

    Returns:
        List of active players with their playback status and current media information
    """
    return kdeconnect.detect_active_player(device_id)


@mcp.tool()
def get_notifications(device_id: str) -> Dict[str, Any]:
    """
    Get all active notifications from a device

    Retrieves all current notifications on the device, including app name,
    title, text content, and other metadata.

    Args:
        device_id: The unique identifier of the KDE Connect device (use list_devices to find device IDs)

    Returns:
        List of notifications with full details and count
    """
    notifications = kdeconnect.get_notifications(device_id)
    return {
        "notifications": notifications,
        "count": len(notifications)
    }


@mcp.tool()
def list_received_files(device_id: str, limit: int = 10) -> Dict[str, Any]:
    """
    List recently received files from a device

    Shows files that have been transferred from the device to this computer,
    sorted by most recent first.

    Args:
        device_id: The unique identifier of the KDE Connect device
        limit: Maximum number of files to return (1-100, default: 10)

    Returns:
        List of files with paths, sizes, modification times, and download directory
    """
    files = kdeconnect.list_received_files(device_id, limit)
    download_dir = kdeconnect.get_download_directory(device_id)
    return {
        "files": files,
        "count": len(files),
        "download_directory": download_dir
    }


@mcp.tool()
def open_file(file_path: str) -> Dict[str, str]:
    """
    Open a file with the default application

    Opens a file using the system's default application for that file type.
    Typically used to open files received from devices.

    Args:
        file_path: The absolute path to the file to open

    Returns:
        Status confirmation with the opened file path
    """
    kdeconnect.open_received_file(file_path)
    return {
        "status": "opened",
        "file_path": file_path
    }


# ========== SMS / Conversations Tools ==========

@mcp.tool()
def list_sms_conversations(
    device_id: str,
    refresh: bool = True,
    limit: int = 100,
    wait_seconds: float = 20.0,
    quiet_seconds: float = 6.0,
) -> Dict[str, Any]:
    """
    List SMS conversation threads (one entry per thread, with the most recent message)

    Returns the latest message of every SMS thread KDE Connect has received
    from the target device, newest first. Use this as the entry point — find
    the `thread_id` you want, then call `read_sms_conversation` for the full
    history.

    IMPORTANT — partial results are normal and are flagged, not hidden. The
    phone streams thread heads at roughly 4-5 per second, so a mailbox with a
    few thousand threads takes minutes to load fully. The daemon accumulates
    them across calls, so repeated calls keep improving the picture. Always
    read `complete` before drawing conclusions: when it is False the set is
    truncated, and an absent conversation means "not loaded yet", never "does
    not exist". Either raise `wait_seconds`, call again to let the cache fill
    further, or check progress with `sms_cache_status`.

    The daemon's cache is in-memory only: a `kdeconnectd` restart empties it,
    and the next call here starts the load over from zero.

    Args:
        device_id: KDE Connect device ID (from `list_devices`)
        refresh: If True, ask the phone to (re)stream thread heads and wait
            for the cache to settle. If False, return the daemon cache as it
            stands right now — instant, but may be empty or stale.
        limit: Maximum number of threads to return (newest first)
        wait_seconds: Hard cap on the wait (default 20s). Raise it for a full
            history load; expect minutes, not seconds.
        quiet_seconds: How long the cache must stop growing before the load
            counts as finished (default 6s)

    Returns:
        Dictionary with `threads` (list of {thread_id, body, addresses, date,
        direction, read, ...}), `count`, `complete` (whether the load
        finished), `total_cached` (threads in the daemon cache, may exceed
        `limit`) and, when truncated, a `warning`.
    """
    threads, meta = kdeconnect.load_sms_threads(
        device_id, refresh=refresh,
        wait_seconds=wait_seconds, quiet_seconds=quiet_seconds,
    )
    total = len(threads)
    threads = threads[: int(limit)]
    out = {
        "threads": threads,
        "count": len(threads),
        "complete": meta["complete"],
        "total_cached": total,
        "load": meta,
    }
    if not meta["complete"]:
        out["warning"] = (
            f"Thread list is INCOMPLETE — the phone was still streaming when "
            f"the {wait_seconds}s budget ran out ({meta['cached_before']} -> "
            f"{total} threads so far). A missing conversation means 'not "
            f"loaded yet', not 'does not exist'."
        )
    return out


@mcp.tool()
def sms_cache_status(device_id: str) -> Dict[str, Any]:
    """
    Check how much SMS history KDE Connect has loaded so far

    Cheap progress probe — samples the daemon's conversation cache twice a
    couple of seconds apart and reports whether it is still filling. Fires no
    new request, so it is safe to call repeatedly while a long load runs in
    the background.

    Use it before trusting an empty `search_sms` result: if `still_loading`
    is True, the history is incomplete and a miss proves nothing.

    Args:
        device_id: KDE Connect device ID (from `list_devices`)

    Returns:
        Dictionary with `cached_threads`, `still_loading`,
        `gained_during_probe`, `newest_message` and `oldest_message`.
    """
    return kdeconnect.sms_cache_status(device_id)


@mcp.tool()
def read_sms_conversation(
    device_id: str,
    thread_id: int,
    limit: int = 50,
    wait_seconds: float = 15.0,
) -> Dict[str, Any]:
    """
    Read the full message history of a single SMS thread

    Repeatedly requests the thread until the daemon confirms (via its
    `conversationLoaded` message count, not a quiet-period guess) that the
    load is complete, or until `wait_seconds` per round / round budget runs
    out. Messages are ordered chronologically (oldest first), so the
    conversation reads naturally top-to-bottom.

    Note: MMS messages often have an empty `body` (the content is an
    attachment) -- don't read an empty body as "no message here". The
    daemon's cache is in-memory: a `kdeconnectd` restart empties it and the
    next read starts the load over.

    Args:
        device_id: KDE Connect device ID (from `list_devices`)
        thread_id: Thread ID (from `list_sms_conversations`)
        limit: How many of the NEWEST messages to return (the thread is
            always loaded to convergence first, so `complete` stays
            meaningful); `total_messages` and `truncated` report what was
            held back. Also floors the per-round request window at 200.
        wait_seconds: Hard timeout per loading round

    Returns:
        Dictionary with `messages` (list of {body, direction, date,
        addresses, ...}), `count`, `complete` (whether this is confirmed to
        be the FULL thread history -- if False, treat the result as a
        partial, unsearched-surface load, not the whole conversation) and
        `load` (full loading metadata).
    """
    messages, meta = kdeconnect.read_sms_conversation(
        device_id, int(thread_id), limit=int(limit),
        wait_seconds=wait_seconds,
    )
    # The thread is always loaded to convergence -- that is what makes
    # `complete` meaningful -- but only the newest `limit` messages are
    # returned, so a 164-message thread does not blow up the caller's
    # context when it asked for 50. `total_messages` exposes the difference.
    total_messages = len(messages)
    if int(limit) > 0:
        messages = messages[-int(limit):]
    return {
        "thread_id": int(thread_id),
        "messages": messages,
        "count": len(messages),
        "total_messages": total_messages,
        "truncated": total_messages > len(messages),
        "complete": meta["complete"],
        "load": meta,
    }


@mcp.tool()
def search_sms(
    device_id: str,
    query: str,
    scope: Literal["body", "address", "both"] = "both",
    refresh: bool = True,
    limit: int = 50,
    wait_seconds: float = 20.0,
    since: Optional[str] = None,
    deep: bool = False,
    max_deep_threads: int = 50,
    deep_budget_seconds: float = 60.0,
) -> Dict[str, Any]:
    """
    Diacritic- and case-insensitive search across SMS threads

    By default (no `since`, `deep=False`) this only searches the NEWEST
    message of every thread (the thread "head") -- cheap, but it will miss a
    hit buried earlier in a conversation. Pass `since` and/or `deep=True` to
    also pull full thread content and search every message in it.

    Matching is diacritic- and case-insensitive (NFD-normalized, accents
    stripped, casefolded) on both the query and the text -- "objednavka" and
    "objednávka" are treated as the same query, which matters because
    service/notification SMS in Slovak/Czech commonly drop diacritics.

    MMS messages often have an empty `body` (their content is an
    attachment), so deep search over an MMS-heavy thread can miss it even
    when the phone-side content would "obviously" match. The daemon's
    thread/message cache is in-memory only -- a `kdeconnectd` restart empties
    it and the next search starts loading from zero again.

    IMPORTANT -- read `coverage.verdict`, not just `count`. It is one of:
      - "heads_only": neither `since` nor `deep` was given, so only thread
        heads were searched -- most of each conversation is unsearched.
      - "partial": deep search ran but did not fully cover the candidate
        threads (a timeout, an unreached thread, or the `max_deep_threads`
        cap trimmed the candidate set).
      - "complete": thread heads finished loading AND every deep candidate
        thread was fully searched within budget, with none dropped by the
        cap.
    When `count == 0` and `verdict != "complete"`, the result also carries
    `zero_matches_meaning` -- treat that as an unsearched surface, never as
    proof the message does not exist.

    Args:
        device_id: KDE Connect device ID
        query: Substring to look for (diacritic/case-insensitive)
        scope: Where to search — "body", "address", or "both" (default)
        refresh: If True, ask the phone to stream and wait for the thread-
            head cache to settle before searching
        limit: Maximum number of matching messages to return
        wait_seconds: Hard cap on the thread-head load wait (default 20s)
        since: ISO date/datetime (e.g. "2026-07-01" or "2026-07-01 12:00").
            When given, deep-searches every thread whose head is at or after
            this date (an older head means the thread has no activity in
            this window at all) and filters deep hits to messages at or
            after it too. Implies deep search.
        deep: Force deep search even without `since` — pulls full content
            for the `max_deep_threads` newest threads
        max_deep_threads: Cap on how many threads get deep-loaded; the rest
            are reported (never silently dropped) under
            `coverage.deep.threads_skipped_over_cap`
        deep_budget_seconds: Hard wall-clock cap on the whole deep phase

    Returns:
        Dictionary with `matches` (list of message dicts, each tagged
        `matched_via`: "head" or "deep"; newest first, deduplicated by
        `message_id`), `count`, `query`, `scope`, `complete` (== whether
        `coverage.verdict` is "complete"), `searched_threads` (thread heads
        searched), `load` (thread-head load metadata), `coverage` (the
        verdict object described above) and, when applicable, `warning` /
        `zero_matches_meaning`.
    """
    threads, meta = kdeconnect.load_sms_threads(
        device_id, refresh=refresh, wait_seconds=wait_seconds
    )
    q = _norm(query)

    def _matches_text(body: str, addresses: List[str]) -> bool:
        in_body = scope in ("body", "both") and q in _norm(body)
        in_addr = scope in ("address", "both") and any(
            q in _norm(a) for a in addresses
        )
        return in_body or in_addr

    all_matches: Dict[int, Dict[str, Any]] = {}
    for t in threads:
        if _matches_text(t["body"], t["addresses"]):
            m = dict(t)
            m["matched_via"] = "head"
            all_matches[m["message_id"]] = m

    since_ms = None
    if since:
        try:
            since_ms = _parse_since(since)
        except ValueError as e:
            return {
                "status": "error",
                "error": f"could not parse `since` ({since!r}): {e}",
            }

    deep_coverage = None
    if deep or since_ms is not None:
        if since_ms is not None:
            candidates = [t for t in threads if t["date_ms"] >= since_ms]
        else:
            candidates = list(threads)  # already sorted newest-first

        skipped_ids: List[int] = []
        if len(candidates) > int(max_deep_threads):
            skipped_ids = [t["thread_id"] for t in candidates[int(max_deep_threads):]]
            candidates = candidates[: int(max_deep_threads)]

        candidate_ids = [t["thread_id"] for t in candidates]
        deep_results, deep_coverage = kdeconnect.load_threads_deep(
            device_id, candidate_ids, since_ms=since_ms,
            concurrency=6, total_budget_seconds=deep_budget_seconds,
        )
        deep_coverage["threads_skipped_over_cap"] = {
            "count": len(skipped_ids), "thread_ids": skipped_ids,
        }

        for tid, msgs in deep_results.items():
            for m in msgs:
                if since_ms is not None and m["date_ms"] < since_ms:
                    continue
                if not _matches_text(m["body"], m["addresses"]):
                    continue
                if m["message_id"] in all_matches:
                    continue  # already have it as a head match
                mm = dict(m)
                mm["matched_via"] = "deep"
                all_matches[mm["message_id"]] = mm

    if since_ms is not None:
        # `since` scopes the whole search, head hits included -- otherwise a
        # date-scoped query would still surface years-old thread heads.
        all_matches = {
            mid: m for mid, m in all_matches.items()
            if m["date_ms"] >= since_ms
        }
    matches = sorted(all_matches.values(), key=lambda m: m["date_ms"], reverse=True)
    total_matches = len(matches)
    matches = matches[: int(limit)]

    if deep_coverage is None:
        verdict = "heads_only"
        # Nothing was truncated *within the requested scope* if every thread
        # head was loaded. The scope itself (heads only) is reported by
        # `verdict`, so the two fields stay independent.
        not_truncated = meta["complete"]
    else:
        deep_complete = (
            not deep_coverage["budget_exhausted"]
            and not deep_coverage["threads_timeout"]
            and not deep_coverage["threads_not_reached"]
            and deep_coverage["threads_skipped_over_cap"]["count"] == 0
        )
        not_truncated = meta["complete"] and deep_complete
        verdict = "complete" if not_truncated else "partial"

    coverage = {
        "verdict": verdict,
        "heads": {
            "complete": meta["complete"],
            "threads_seen": len(threads),
            "oldest_head": threads[-1]["date"] if threads else None,
        },
        "deep": deep_coverage,
    }

    out = {
        "matches": matches,
        "count": len(matches),
        "total_matches": total_matches,
        "query": query,
        "scope": scope,
        "complete": not_truncated,
        "searched_threads": len(threads),
        "load": meta,
        "coverage": coverage,
    }
    if len(matches) == 0 and verdict != "complete":
        out["zero_matches_meaning"] = (
            "unsearched surface remains — this is NOT evidence the message "
            "does not exist"
        )
    if not meta["complete"]:
        out["warning"] = (
            f"Thread heads: searched only the {len(threads)} loaded so far — "
            f"the phone was still streaming. Zero matches here is NOT "
            f"evidence that no such message exists; treat it as an "
            f"unsearched surface, raise wait_seconds or check "
            f"sms_cache_status."
        )
    return out


@mcp.tool()
def send_sms(
    device_id: str,
    text: str,
    confirm: bool = False,
    thread_id: int = None,
    phone: str = None,
) -> Dict[str, Any]:
    """
    Send an SMS — two-phase, requires explicit confirm=True to actually send

    Phase 1 (confirm=False, DEFAULT): returns a dry-run preview describing
    what would be sent. No SMS is dispatched.
    Phase 2 (confirm=True): actually sends the SMS via KDE Connect.

    Exactly one of `thread_id` or `phone` must be provided:
    - `thread_id`: reply into an existing conversation thread (preserves
      multi-recipient groups, preferred for replies)
    - `phone`: start a new conversation with a single recipient

    The caller (assistant) is responsible for showing the preview to the
    user and obtaining explicit consent before invoking with confirm=True.

    Args:
        device_id: KDE Connect device ID
        text: Message body
        confirm: If False, return preview only. If True, send for real.
        thread_id: Thread to reply to (mutually exclusive with `phone`)
        phone: Recipient phone number, e.g. "+421900123456" (mutually
            exclusive with `thread_id`)

    Returns:
        On preview: {status: "preview", would_send: {...}}.
        On send: {status: "sent", thread_id|phone, text}.
    """
    if (thread_id is None) == (phone is None):
        return {
            "status": "error",
            "error": "exactly one of thread_id / phone must be provided",
        }
    if not text:
        return {"status": "error", "error": "text must not be empty"}

    target = {"thread_id": int(thread_id)} if thread_id is not None else {"phone": phone}
    if not confirm:
        return {
            "status": "preview",
            "would_send": {**target, "text": text, "device_id": device_id},
            "hint": "Call again with confirm=true to actually send.",
        }
    return kdeconnect.send_sms(
        device_id, text,
        thread_id=int(thread_id) if thread_id is not None else None,
        phone=phone,
    )


# ========== Resources for Device Information ==========

@mcp.resource("kdeconnect://devices", name="Device List", description="List of all available KDE Connect devices")
def devices_resource() -> str:
    """Provides a list of all available devices in JSON format."""
    import json
    devices = kdeconnect.list_devices()
    device_info = []
    for device_id in devices:
        try:
            info = kdeconnect.get_device_info(device_id)
            device_info.append(info)
        except Exception as e:
            device_info.append({"id": device_id, "error": str(e)})

    return json.dumps({
        "devices": device_info,
        "count": len(device_info)
    }, indent=2)


@mcp.resource(
    "kdeconnect://{device_id}/now-playing",
    name="Now Playing Info",
    description="Currently playing media information for a specific device"
)
def now_playing_resource(device_id: str) -> str:
    """Provides now playing information for a device."""
    import json
    result = kdeconnect.get_now_playing(device_id)
    return json.dumps(result, indent=2)


# ========== Prompts for Natural Language Interaction ==========

@mcp.prompt(
    name="play_music",
    description="Natural language prompt to control music playback on a specific device",
    tags={"media", "control"}
)
def play_music_prompt(
    device_id: str = Field(description="Device ID to control"),
    action: str = Field(description="Action to perform: play, pause, next, previous, stop")
) -> str:
    """Generate a prompt for controlling music playback."""
    return f"Control music playback on device {device_id}: {action}"


@mcp.prompt(
    name="check_music",
    description="Natural language prompt to check what's currently playing on a specific device",
    tags={"media", "query"}
)
def check_music_prompt(
    device_id: str = Field(description="Device ID to check")
) -> str:
    """Generate a prompt for checking currently playing music."""
    return f"What music is currently playing on device {device_id}?"


@mcp.prompt(
    name="send_message",
    description="Natural language prompt to send a notification to a specific device",
    tags={"notification", "communication"}
)
def send_message_prompt(
    device_id: str = Field(description="Device ID to send notification to"),
    message: str = Field(description="Message to send")
) -> str:
    """Generate a prompt for sending a notification."""
    return f"Send notification to device {device_id}: {message}"


if __name__ == "__main__":
    # Run the MCP server
    mcp.run()
