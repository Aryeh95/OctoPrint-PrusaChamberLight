# Privacy notes

This plugin has no server of its own and collects nothing. It never contacts the plugin author.

## On/off (always available)

Works entirely between OctoPrint and the printer over USB. No network traffic.

## Brightness through Prusa Connect (optional, off by default)

When you turn this feature on and store a login, the plugin talks to two Prusa Research services over HTTPS:

| Service | When | What is sent |
|---|---|---|
| `account.prusa3d.com` | At most every ~2 hours, and when you store a login | Your Prusa Connect refresh token, to get a new access token. Prusa also returns a new refresh token, which replaces the old one. |
| `connect.prusa3d.com` | When you move the brightness slider or click *Test connection* | Your access token, the printer's UUID and the brightness value (0–100). |

Nothing is sent anywhere else, and nothing is sent while the feature is off.

### What is stored

The access token and refresh token are kept in OctoPrint's `config.yaml` on your OctoPrint server, in plain text, the same
way other OctoPrint plugins store API keys. They are never sent to the browser. Anyone who can read that file can control
your printers through Prusa Connect. To revoke them, clear the login in the plugin settings, or change your Prusa account
password or sign out of all sessions at [account.prusa3d.com](https://account.prusa3d.com).

The printer's UUID and the last brightness value are also stored in `config.yaml`. The UUID is shown only to OctoPrint
users with the *Settings* permission.

### Prusa's side

How Prusa Research handles the data it receives is covered by its own
[privacy policy](https://www.prusa3d.com/page/privacy-policy_231258/). This plugin is not affiliated with Prusa Research.
