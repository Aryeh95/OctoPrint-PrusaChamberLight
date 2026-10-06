# OctoPrint-PrusaChamberLight

Turn the chamber light of a **Prusa CORE One** on and off from OctoPrint, and optionally set its brightness.

- A lightbulb button in the navbar and a **Chamber Light** panel in the sidebar.
- On/off goes to the printer over the USB connection OctoPrint already has. Nothing else is needed.
- Brightness (0–100 %) is optional and goes through Prusa Connect, the same way the Prusa app does it.
  See [Brightness](#brightness-optional-experimental) for why and for the caveats.
- If the printer is power cycled while the light is off, the plugin turns it off again when OctoPrint reconnects.

> Not affiliated with or endorsed by Prusa Research. "Prusa" and "CORE One" are trademarks of Prusa Research a.s.

## Compatibility

| | |
|---|---|
| Printer | Prusa CORE One, tested with firmware 7.0.0. The CORE One L uses the same firmware code and should work, but is untested. |
| OctoPrint | Tested on 1.11.8 |
| Connection | USB (serial) |

Other Prusa printers ignore the command (MK4, MINI) or behave differently (XL); they are not supported.

## Installation

In OctoPrint, open **Settings → Plugin Manager → Get More → … from URL** and enter:

```
https://github.com/Aryeh95/OctoPrint-PrusaChamberLight/archive/main.zip
```

## How on/off works

The Buddy firmware has no documented G-code for the chamber light, but its source shows a way
([`M150.cpp`](https://github.com/prusa3d/Prusa-Firmware-Buddy/blob/master/src/marlin_stubs/M150.cpp),
[`side_strip_handler.cpp`](https://github.com/prusa3d/Prusa-Firmware-Buddy/blob/master/src/leds/side_strip_handler.cpp)):

- `M151` sets a temporary custom colour on the chamber LED strip for `D` milliseconds (default 400 ms, which is why
  people who tried it saw the light come straight back on).
- On the CORE One only the white channel of that strip is wired, and `M151` always sets white to 0, so `M151` turns
  the light off.

So the plugin sends:

| | G-code |
|---|---|
| Off | `M151 R0 G0 B0 D4294967295 T500` (the longest duration, about 49.7 days, with a 0.5 s fade) |
| On | `M151 R0 G0 B0 D0 T500` (ends the override, so the printer's own brightness and dimming settings apply again) |

The override is only kept in the printer's memory and is lost when the printer restarts, which is why the plugin
re-applies "off" when OctoPrint reconnects.

## Brightness (optional, experimental)

The firmware has no G-code that changes the chamber light's brightness. It can only be changed on the touchscreen or
through Prusa Connect. When you turn this feature on, the plugin sends the same request the Connect website sends when
you move its brightness slider.

**Caveats**

- It uses Prusa Connect's **unofficial** web API and the Connect website's own login. Prusa may change or block this
  at any time; if that happens, brightness stops working but on/off keeps working.
- It needs internet access and a Prusa account that has the printer in Prusa Connect.
- The login (a refresh token) is stored in plain text in OctoPrint's `config.yaml`. It is never sent to the browser,
  but anyone who can read that file can control your printers through Prusa Connect. Changing your Prusa account
  password or signing out everywhere revokes it.

**Setup**

1. In **Settings → Prusa Chamber Light**, tick **Brightness control through Prusa Connect** and click **Save**.
2. Log in at [connect.prusa3d.com](https://connect.prusa3d.com), open the browser's developer tools (F12),
   go to **Application → Local storage → https://connect.prusa3d.com** and copy the value of `auth.refresh_token`.
3. Paste it into **Connect login** and click **Store**. The status should change to *renews automatically*.
4. Click **Test connection**. It reads the current brightness from Prusa Connect.

The plugin renews its login itself. Each refresh token can be used only once, so after OctoPrint takes it over, the
Connect website in that browser may log you out once when it next renews. Log in there again; the website then has its
own separate login.

If Test connection says Prusa Connect does not know the printer, open the printer on connect.prusa3d.com and copy the
UUID from the address bar into **Printer UUID**.

## License

[AGPLv3](LICENSE)
