# Able to Use for Home Assistant

This custom integration adds devices from an existing Able to Use account. It follows the `custom_components` layout used by [Tuya Local](https://github.com/make-all/tuya-local) and uses Able’s cloud. It requires internet access on the Home Assistant host and the controller.

## Install

Use Home Assistant 2025.12 or later.

1. In HACS, add `https://github.com/DapperGeek1/Able-To-Use` as a custom repository of type **Integration**. Search for **Able to Use**, download it, and restart Home Assistant. For a manual install, copy the `custom_components/able_to_use` directory into the `custom_components` folder in your Home Assistant configuration directory, then restart.
2. In **Settings → Devices & services → Add integration**, select **Able to Use**.
3. Enter your Able email and password and the Firebase project API key from your copy of the app. Home Assistant keeps the Firebase refresh token in its config entry and discards the password after sign-in. Keep Home Assistant backups private.

### Get the project API key

Use a copy of the Able to Use Android APK and Android SDK Build Tools. Run the helper on your own computer. It reads the `google_api_key` Android resource without installing or executing the app. `--aapt` is only needed when `aapt` is outside your `PATH`.

```powershell
python tools/extract_project_key.py "PATH_TO_ABLE_APK" --aapt "PATH_TO_AAPT_EXE" | Set-Clipboard
```

On other platforms, omit `| Set-Clipboard` and copy the printed value. Use it only in the Home Assistant setup form. This repository ships no vendor key, certificate, APK, account credentials, or tokens.

## Entities

| Entity | What it exposes |
| --- | --- |
| Gate cover | Reported position and state, plus dedicated Open, Close and Stop when those actions are enabled for the account. The `gate` device class also enables Home Assistant’s [Gate triggers and conditions](https://www.home-assistant.io/integrations/gate/). |
| Gate buttons | The account’s enabled gate actions, such as Start, Stop, Partial Start, Open, Close and Pedestrian. Start sends Able’s toggle. Partial Start checks that the gate is closed and uses the motor’s own partial-opening setting. |
| Sun-shading cover and buttons | Open, Close, Stop, and target position where the action list provides them. Named fixed actions include move to the saved favourite position when available. |
| Light | On/off control for each supported relay channel of a single or dual light. |
| Sensors | Reported state, position, moving, favourite position, last activity, firmware, and relay status where applicable. Position and motion follow Able’s readings after commands. |

Device and action discovery runs automatically. The action list is refreshed every five minutes. The default status poll is 15 seconds and can be changed under the integration’s options. A timed-out command is sent once. Check the gate before pressing again.

After Able accepts an action, status polling switches to roughly once per second while the device changes state. It returns to the configured interval when Able reports the target or a stop. For an action with no known target, such as Partial Start, fast polling ends after the reported state settles. It also ends if the cloud stops responding, a command produces no detectable change within 12 seconds, or the 90-second limit is reached.

## Use HA Gate Card

Install [HA Gate Card](https://github.com/ADNPolymerase/ha-gate-card) in HACS. Use your actual entity IDs from **Settings → Devices & services → Entities**. These are examples for a device named Outer Gate.

```yaml
type: custom:ha-gate-card
entity: cover.outer_gate
state_entity: sensor.outer_gate_reported_state
gate_type: sliding
show_position: true
draw_position: true
show_stop: true
confirm: true
pedestrian_entity: button.outer_gate_partial_start
```

The cover uses its dedicated Open, Close and Stop controls. The card’s pedestrian slot can point to your Partial Start button or to the separate Pedestrian button when available. Omit that line if your account exposes neither. Use a separate Home Assistant button card for Start when you want the gate’s toggle.

## Development

With Home Assistant 2025.12 installed in a development Python environment, run `python -m unittest discover -s tests`. The tests use simulated Able responses and send no commands to physical devices.
