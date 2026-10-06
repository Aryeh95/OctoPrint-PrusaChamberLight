"""Chamber light control for the Prusa CORE One.

On/off over USB (Prusa-Firmware-Buddy, src/marlin_stubs/M150.cpp and
src/leds/side_strip_handler.cpp): M151 sets a temporary "custom colour" on the
side strip that overrides the normal active/dimmed state for D milliseconds.
On the CORE One only the white channel is wired (xBuddy extension white LED),
and M151 always sets white to 0, so any M151 turns the chamber light off.

- off: M151 with D = 2^32-1 ms (~49.7 days, the largest uint32 value)
- on:  M151 with D = 0, so the override expires at once and the printer's own
       brightness/dimming settings take over again

Brightness (src/connect/command.cpp, src/connect/planner.cpp): no G-code reaches
SideStripHandler::set_max_brightness(). The only remote path is Prusa Connect,
which sends the printer SET_VALUE with {"chamber.led_intensity": 0-100}. So
brightness goes through the (unofficial) Connect web API, using the same
request the Connect website makes (POST .../commands/sync with command
SET_CHAMBER_LED_INTENSITY).

Login: the Connect website is an OAuth client of account.prusa3d.com. Its
access token (JWT) lives about two hours; it renews it by POSTing
grant_type=refresh_token to /o/token/ with its public client id, and every
renewal returns a new refresh token (the old one stops working). This plugin
does the same with a refresh token the user copies from the website's
localStorage, so it keeps its own login alive indefinitely.

The Connect part is opt-in (connect_enabled) because it uses an unofficial API.
"""

import base64
import json
import threading
import time

import flask
import requests

import octoprint.plugin
from octoprint.access.permissions import Permissions
from octoprint.events import Events
from octoprint.util import RepeatedTimer

MAX_DURATION_MS = 4294967295
MAX_FADE_MS = 10000
CONNECT_URL = "https://connect.prusa3d.com/app/printers/{uuid}"
TOKEN_URL = "https://account.prusa3d.com/o/token/"
CLIENT_ID = "MRHTlZhZqkNrrQ6FUPtjyusAz8nc59ErHXP8XkS4"  # Connect website's public client id

RENEW_CHECK_INTERVAL_S = 10 * 60
RENEW_AHEAD_BACKGROUND_S = 30 * 60
RENEW_AHEAD_REQUEST_S = 2 * 60


def _jwt_claims(token):
    """Decoded (unverified) JWT payload, or {} if the token isn't a JWT."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims if isinstance(claims, dict) else {}
    except (IndexError, TypeError, ValueError):
        return {}


class ChamberLightPlugin(
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.EventHandlerPlugin,
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.ShutdownPlugin,
):
    def __init__(self):
        self._firmware_uuid = None
        self._machine_type = None
        self._token_lock = threading.Lock()
        self._login_error = None
        self._renew_timer = None

    # -- settings --------------------------------------------------------------

    def get_settings_defaults(self):
        return {
            # user settings
            "fade_ms": 500,
            "restore_on_connect": True,
            "connect_enabled": False,
            "printer_uuid": "",
            # remembered state
            "light_on": True,
            "brightness": 100,
            # credentials (never sent to the browser, see get_settings_restricted_paths)
            "connect_access_token": "",
            "connect_refresh_token": "",
        }

    def get_settings_version(self):
        return 2

    def on_settings_migrate(self, target, current):
        if current is None:
            # Pre-release installs had Connect always on: keep it on if a login is stored.
            if self._settings.get(["connect_refresh_token"]) or self._settings.global_get(
                ["plugins", "chamberlight", "connect_cookie"]
            ):
                self._settings.set_boolean(["connect_enabled"], True)
        if current is None or current < 2:
            # 0.4.0 called the access token "connect_cookie".
            old = self._settings.global_get(["plugins", "chamberlight", "connect_cookie"])
            if old:
                self._settings.set(["connect_access_token"], old)
                self._settings.global_remove(["plugins", "chamberlight", "connect_cookie"])

    def on_settings_save(self, data):
        if "fade_ms" in data:
            try:
                data["fade_ms"] = max(0, min(MAX_FADE_MS, int(data["fade_ms"])))
            except (TypeError, ValueError):
                del data["fade_ms"]
        diff = octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self._notify()  # e.g. show/hide the brightness slider for everyone
        return diff

    def get_settings_restricted_paths(self):
        # Login credentials: never send them to the browser.
        return {"never": [["connect_access_token"], ["connect_refresh_token"]]}

    # -- UI --------------------------------------------------------------------

    def get_assets(self):
        return {"js": ["js/chamberlight.js"]}

    def get_template_configs(self):
        return [
            {"type": "navbar", "custom_bindings": True},
            {"type": "sidebar", "name": "Chamber Light", "icon": "lightbulb", "custom_bindings": True},
            {"type": "settings", "name": "Prusa Chamber Light", "custom_bindings": True},
        ]

    def is_template_autoescaped(self):
        return True

    # -- lifecycle -------------------------------------------------------------

    def on_after_startup(self):
        # Renew in the background too, so the refresh token is used regularly
        # and never lapses even if nobody touches the brightness for weeks.
        self._renew_timer = RepeatedTimer(RENEW_CHECK_INTERVAL_S, self._background_renew, daemon=True)
        self._renew_timer.start()

    def on_shutdown(self):
        if self._renew_timer:
            self._renew_timer.cancel()

    def on_event(self, event, payload):
        # The override lives in printer RAM, so a printer power cycle turns the
        # light back on. Re-apply "off" whenever OctoPrint reconnects.
        if event == Events.CONNECTED and self._settings.get_boolean(["restore_on_connect"]):
            if not self._settings.get_boolean(["light_on"]):
                self._apply(False)

    def on_firmware_info(self, comm, firmware_name, firmware_data, *args, **kwargs):
        self._firmware_uuid = firmware_data.get("UUID") or None
        self._machine_type = firmware_data.get("MACHINE_TYPE") or None
        if self._machine_type and "COREONE" not in self._machine_type.upper():
            self._logger.warning("Printer reports %s; this plugin is only tested on the Prusa CORE One", self._machine_type)

    # -- API -------------------------------------------------------------------

    def is_api_protected(self):
        return True

    def get_api_commands(self):
        return {
            "on": [],
            "off": [],
            "toggle": [],
            "brightness": ["value"],
            "set_token": ["token"],
            "sync": [],
        }

    def on_api_get(self, request):
        return flask.jsonify(self._state(include_ids=Permissions.SETTINGS.can()))

    def on_api_command(self, command, data):
        if command == "set_token":
            if not Permissions.SETTINGS.can():
                flask.abort(403)
            error = self._store_credential(str(data["token"]))
            if error:
                return flask.make_response(error, 400)
            return flask.jsonify(self._state(include_ids=True))

        if not Permissions.CONTROL.can():
            flask.abort(403)

        if command in ("brightness", "sync") and not self._settings.get_boolean(["connect_enabled"]):
            return flask.make_response("Brightness control via Prusa Connect is turned off (Settings > Prusa Chamber Light)", 409)

        if command == "brightness":
            try:
                value = max(0, min(100, int(data["value"])))
            except (TypeError, ValueError):
                return flask.make_response("value must be 0-100", 400)
            error = self._connect_set_brightness(value)
            if error:
                return flask.make_response(error, 502)
            self._settings.set_int(["brightness"], value)
            self._settings.save()
            self._notify()
            return flask.jsonify(self._state())

        if command == "sync":
            error = self._connect_sync()
            if error:
                return flask.make_response(error, 502)
            return flask.jsonify(self._state())

        if not self._printer.is_operational():
            return flask.make_response("Printer is not connected", 409)

        if command == "toggle":
            light_on = not self._settings.get_boolean(["light_on"])
        else:
            light_on = command == "on"

        self._apply(light_on)
        self._settings.set_boolean(["light_on"], light_on)
        self._settings.save()
        self._notify()
        return flask.jsonify(self._state())

    # -- state -----------------------------------------------------------------

    def _state(self, include_ids=False):
        """Plugin state for the UI. Printer identifiers only go to users with the Settings permission."""
        state = {
            "light_on": self._settings.get_boolean(["light_on"]),
            "brightness": self._settings.get_int(["brightness"]),
            "connect_enabled": self._settings.get_boolean(["connect_enabled"]),
            "login_set": bool(self._settings.get(["connect_access_token"]) or self._settings.get(["connect_refresh_token"])),
            "auto_renew": bool(self._settings.get(["connect_refresh_token"])),
            "token_expires": self._token_expiry(),
            "login_error": self._login_error,
            "machine_type": self._machine_type,
        }
        if include_ids:
            state["printer_uuid"] = self._printer_uuid()
            state["firmware_uuid"] = self._firmware_uuid
        return state

    def _notify(self):
        self._plugin_manager.send_plugin_message(self._identifier, self._state())

    def _apply(self, light_on):
        duration = 0 if light_on else MAX_DURATION_MS
        fade = self._settings.get_int(["fade_ms"])
        self._printer.commands(f"M151 R0 G0 B0 D{duration} T{fade}", tags={"trigger:chamberlight"})
        self._logger.info("Chamber light %s", "on" if light_on else "off")

    def _printer_uuid(self):
        return (self._settings.get(["printer_uuid"]) or "").strip() or self._firmware_uuid

    # -- login -----------------------------------------------------------------

    def _store_credential(self, value):
        """Accepts either a refresh token (preferred, renews itself) or a bare access token."""
        value = value.strip().strip('"')
        for prefix in ("auth.access_token=", "auth.refresh_token=", "Bearer "):
            if value.startswith(prefix):
                value = value[len(prefix) :]
        value = value.split(";")[0].strip()
        if not value:
            return "Empty value"

        # Prusa's refresh tokens are JWTs too; they carry "type": "refresh".
        claims = _jwt_claims(value)
        if claims and claims.get("type") != "refresh":
            # Access token only: works until it expires, no renewal.
            self._settings.set(["connect_access_token"], value)
            self._settings.set(["connect_refresh_token"], "")
            self._settings.save()
            self._login_error = None
            self._notify()
            return None

        # Refresh token: exchange it right away, which also proves it works.
        self._settings.set(["connect_refresh_token"], value)
        self._settings.save()
        with self._token_lock:
            error = self._renew_locked()
        self._notify()
        return error

    def _token_expiry(self):
        """Expiry of the stored access token as a unix timestamp, or None if unreadable."""
        exp = _jwt_claims(self._settings.get(["connect_access_token"]) or "").get("exp")
        return int(exp) if isinstance(exp, (int, float)) else None

    def _ensure_fresh(self, min_valid_s, force=False):
        """Renews the access token if it expires within min_valid_s. Returns an error or None."""
        if not self._settings.get(["connect_refresh_token"]):
            expiry = self._token_expiry()
            if expiry is not None and expiry < time.time():
                return "The Prusa Connect token has expired. Paste a refresh token in Settings > Prusa Chamber Light so it renews itself"
            return None
        with self._token_lock:
            # Re-check under the lock: another thread may have just renewed.
            expiry = self._token_expiry()
            if not force and expiry is not None and expiry - time.time() > min_valid_s:
                return None
            return self._renew_locked()

    def _renew_locked(self):
        refresh_token = self._settings.get(["connect_refresh_token"])
        try:
            response = requests.post(
                TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID},
                headers={"Accept": "application/json"},
                timeout=15,
            )
        except requests.RequestException as e:
            # Network trouble: keep the tokens and try again later.
            return f"Could not reach Prusa Account to renew the login: {e}"

        if response.status_code in (400, 401):
            self._login_error = (
                "Prusa login was rejected (the refresh token was already used, revoked or expired). "
                "Paste a fresh auth.refresh_token in Settings > Prusa Chamber Light"
            )
            self._logger.warning("Token renewal rejected: HTTP %d %s", response.status_code, response.text[:200])
            self._notify()
            return self._login_error
        if not response.ok:
            return f"Prusa Account returned HTTP {response.status_code} while renewing the login"

        try:
            data = response.json()
            access_token = data["access_token"]
        except (ValueError, KeyError, TypeError):
            return "Prusa Account returned an unexpected reply while renewing the login"

        self._settings.set(["connect_access_token"], access_token)
        if data.get("refresh_token"):
            self._settings.set(["connect_refresh_token"], data["refresh_token"])
        self._settings.save()
        self._login_error = None
        self._logger.info("Renewed Prusa Connect login, valid until %s", time.ctime(self._token_expiry() or 0))
        self._notify()
        return None

    def _background_renew(self):
        # RepeatedTimer stops for good if the callback raises, so never let it.
        try:
            if self._settings.get_boolean(["connect_enabled"]) and self._settings.get(["connect_refresh_token"]):
                self._ensure_fresh(RENEW_AHEAD_BACKGROUND_S)
        except Exception:
            self._logger.exception("Background login renewal failed")

    # -- Connect API -----------------------------------------------------------

    def _connect_request(self, method, path, **kwargs):
        """Returns (response, error message)."""
        if not (self._settings.get(["connect_access_token"]) or self._settings.get(["connect_refresh_token"])):
            return None, "No Prusa Connect login set (Settings > Prusa Chamber Light)"
        uuid = self._printer_uuid()
        if not uuid:
            return None, "Printer UUID unknown: connect the printer or set it in Settings > Prusa Chamber Light"

        error = self._ensure_fresh(RENEW_AHEAD_REQUEST_S)
        if error:
            return None, error

        for attempt in range(2):
            token = self._settings.get(["connect_access_token"])
            try:
                response = requests.request(
                    method,
                    CONNECT_URL.format(uuid=uuid) + path,
                    headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                    timeout=10,
                    **kwargs,
                )
            except requests.RequestException as e:
                return None, f"Could not reach Prusa Connect: {e}"
            if response.status_code != 401 or attempt or not self._settings.get(["connect_refresh_token"]):
                break
            # Token rejected before its expiry (e.g. revoked): renew once and retry.
            error = self._ensure_fresh(0, force=True)
            if error:
                return None, error

        if response.status_code in (401, 403):
            return None, "Prusa Connect rejected the login. Paste a fresh auth.refresh_token in Settings > Prusa Chamber Light"
        if response.status_code == 404 and not path:
            return None, f"Prusa Connect does not know printer {uuid}. Check the printer UUID in Settings > Prusa Chamber Light"
        if response.status_code >= 300:
            return None, f"Prusa Connect returned HTTP {response.status_code} for {method} {path or '/'}: {response.text[:200]}"
        return response, None

    def _connect_set_brightness(self, value):
        body = {"command": "SET_CHAMBER_LED_INTENSITY", "kwargs": {"chamber.led_intensity": value}}
        response, error = self._connect_request("POST", "/commands/sync", json=body)
        if error:
            self._logger.warning("Setting brightness to %d%% failed: %s", value, error)
            return error
        self._logger.info("Chamber light brightness set to %d%% via Prusa Connect", value)
        return None

    def _connect_sync(self):
        """Read the current brightness from Connect's view of the printer."""
        response, error = self._connect_request("GET", "")
        if error:
            return error
        try:
            brightness = int(response.json()["chamber"]["led_intensity"])
        except (ValueError, KeyError, TypeError):
            return "Connected OK, but Prusa Connect's printer data has no chamber.led_intensity field"
        self._settings.set_int(["brightness"], brightness)
        self._settings.save()
        self._notify()
        return None

    # -- software update -------------------------------------------------------

    def get_update_information(self):
        return {
            "chamberlight": {
                "displayName": "Prusa Chamber Light",
                "displayVersion": self._plugin_version,
                "type": "github_release",
                "user": "Aryeh95",
                "repo": "OctoPrint-PrusaChamberLight",
                "current": self._plugin_version,
                "pip": "https://github.com/Aryeh95/OctoPrint-PrusaChamberLight/archive/{target_version}.zip",
            }
        }


__plugin_name__ = "Prusa Chamber Light"
__plugin_pythoncompat__ = ">=3.9,<4"
__plugin_privacypolicy__ = "https://github.com/Aryeh95/OctoPrint-PrusaChamberLight/blob/main/PRIVACY.md"


def __plugin_load__():
    global __plugin_implementation__, __plugin_hooks__
    __plugin_implementation__ = ChamberLightPlugin()
    __plugin_hooks__ = {
        "octoprint.comm.protocol.firmware.info": __plugin_implementation__.on_firmware_info,
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
    }
