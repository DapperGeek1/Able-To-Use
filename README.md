# Able to Use for Home Assistant

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow.svg?logo=buy-me-a-coffee)](https://buymeacoffee.com/dappergeek)

<a href="https://buymeacoffee.com/dappergeek"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-orange.png" alt="Buy Me a Coffee" height="60"></a>

This custom integration adds devices from an existing Able to Use account. It follows the `custom_components` layout used by [Tuya Local](https://github.com/make-all/tuya-local) and uses Able’s cloud. It requires internet access on the Home Assistant host and the controller.

## Install

Use Home Assistant 2025.12 or later.

### Home Assistant Marketplace

Use the built-in Marketplace on Home Assistant 2026.11 or later.

[![Open Able to Use in the Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=DapperGeek1&repository=Able-To-Use&category=integration)

1. Open the repository with the button above. If it hasn't been added, go to **Settings → Marketplace → Add from link** and enter `https://github.com/DapperGeek1/Able-To-Use`. Connect GitHub if prompted and choose **Integration** if asked for a type.
2. Open **Able to Use** in the Marketplace and select **Install**. Restart Home Assistant if prompted.
3. If the setup form doesn't open automatically, use the button under [Configuration](#configuration).

### HACS

On Home Assistant versions before 2026.11, open **HACS → ⋮ → Custom repositories**. Add `https://github.com/DapperGeek1/Able-To-Use` with type **Integration**. Open **Able to Use**, select **Download**, and restart Home Assistant.

### Manual installation

Download this repository using **Code → Download ZIP** and extract it. Copy the entire `custom_components/able_to_use` folder into the `custom_components` directory in your Home Assistant configuration folder, then restart Home Assistant.

## Configuration

[![Add the Able to Use integration in Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=able_to_use)

1. After installation, use the button to open setup or go to **Settings → Devices & services → Add integration** and select **Able to Use**.
2. Enter your Able email and password. Leave **Firebase project API key override** blank to use the Able to Use Android app’s public Firebase project identifier.
3. Open the **Able to Use** integration under **Devices & services** to see the devices and entities discovered for your account.

Home Assistant keeps your refresh token in its config entry and discards the password after sign-in. Keep Home Assistant backups private.

### Get the project API key

Leave the **Firebase project API key override** blank during setup or reauthentication unless you need a different key. If Able changes the key or your Android app variant uses a different one, use a copy of your Able to Use APK and Android SDK Build Tools. The helper reads `google_api_key` without installing or executing the app. `--aapt` is only needed when `aapt` is outside your `PATH`.

```powershell
python tools/extract_project_key.py "PATH_TO_ABLE_APK" --aapt "PATH_TO_AAPT_EXE" | Set-Clipboard
```

On other platforms, omit `| Set-Clipboard` and copy the printed value into the override field. Existing installations keep their configured key. Your Able account still authorizes access to your devices.

## Entities

| Entity | What it exposes |
| --- | --- |
| Gate cover | Reported position and state, plus dedicated Open, Close and Stop when those actions are enabled for the account. Gates with all three actions and a readable position also support setting the position. The `gate` device class enables Home Assistant’s [Gate triggers and conditions](https://www.home-assistant.io/integrations/gate/). |
| Gate buttons | The account’s enabled gate actions, such as Start, Stop, Partial Start, Open, Close and Pedestrian. Start sends Able’s toggle. Partial Start checks that the gate is closed and uses the motor’s own partial-opening setting. |
| Partial open percentage and Move to partial position | Set a Home Assistant target for each supported gate. The button moves toward the saved percentage from either direction. |
| Sun-shading cover and buttons | Open, Close, Stop, and target position where the action list provides them. Named fixed actions include move to the saved favourite position when available. |
| Light | On/off control for each supported relay channel of a single or dual light. |
| Sensors | Reported state, position, moving, favourite position, last activity, firmware, and relay status where applicable. Position and motion follow Able’s readings after commands. |

Device and action discovery runs automatically. The action list is refreshed every five minutes. The default status poll is 15 seconds and can be changed under the integration’s options. A timed-out command is sent once. Check the gate before pressing again.

For standard actions, status polling switches to roughly once per second after Able accepts the action and returns to the configured interval when Able reports the target or a stop. For an action with no known target, such as Partial Start, fast polling ends after the reported state settles. It also ends if the cloud stops responding, a command produces no detectable change within 12 seconds, or the 90-second limit is reached.

### Move to a custom percentage

In the gate’s Home Assistant device page, set **Partial open percentage** under **Configuration**. It defaults to 30% and is saved per gate. Press **Move to partial position** to use that setting. A gate already stopped at the requested position stays there. Press **Stop** to cancel a move.

The gate cover also accepts a percentage through `cover.set_cover_position`. The action uses the percentage it receives and keeps the saved button setting. Both controls require a readable gate position and the account’s dedicated Open, Close and Stop actions. While the move is active, Home Assistant aims to read Able’s cloud status every 500 ms. Each request finishes before the next one starts. For intermediate positions, Home Assistant sends Stop when the reported position reaches or crosses the target. Cloud delay and motor travel can cause an overshoot. If Able reports a different final position, Home Assistant shows the percentage it reached.

### Google Home voice control

Expose the gate’s `cover` entity through the [Google Assistant integration](https://www.home-assistant.io/integrations/google_assistant/). In a manual setup, keep `cover` in `exposed_domains` and set `secure_devices_pin` under `google_assistant:` to enable voice opening. For example, use `secure_devices_pin: !secret google_gate_pin` and put your own numeric PIN in `secrets.yaml` as a quoted string.

After updating Able to Use and restarting Home Assistant, say **“Hey Google, sync my devices”** or run `google_assistant.request_sync` in Home Assistant. Then say **“Hey Google, open Outer Gate to 30 percent”**, using the name shown in Google Home. Provide your PIN when Google asks for it.

## Use HA Gate Card

Install [HA Gate Card](https://github.com/ADNPolymerase/ha-gate-card) from the Marketplace or HACS. Use your actual entity IDs from **Settings → Devices & services → Entities**. These are examples for a device named Outer Gate.

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

The cover uses its dedicated Open, Close and Stop controls. To use your saved percentage in the card’s pedestrian slot, set `pedestrian_entity: button.outer_gate_move_to_partial_position` using your button’s entity ID. The slot also accepts Able’s Partial Start or Pedestrian button. Use a separate Home Assistant button card for Start when you want the gate’s toggle.

## Development

With Home Assistant 2025.12 installed in a development Python environment, run `python -m unittest discover -s tests`. The tests use simulated Able responses and send no commands to physical devices.
