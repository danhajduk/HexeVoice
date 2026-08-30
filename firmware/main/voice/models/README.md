# microWakeWord Model Assets

These model assets are embedded into the firmware image through `firmware/main/CMakeLists.txt`.

| Asset | Source | SHA-256 | Bytes |
| --- | --- | --- | ---: |
| `alexa.json` | `https://raw.githubusercontent.com/esphome/micro-wake-word-models/main/models/v2/alexa.json` | `1d999798b35b1fe2606465b75ab840be51c1811d2909d5e620cefb6e96f8abd0` | 377 |
| `alexa.tflite` | `https://raw.githubusercontent.com/esphome/micro-wake-word-models/main/models/v2/alexa.tflite` | `9011a8155b04de858c48038529235cbc0e42e9fca05a55bf588cb80a653a723b` | 55856 |
| `stop.json` | `https://github.com/kahrendt/microWakeWord/releases/download/stop/stop.json` | `bd13aeb1b83852649dc4fb6135cb160ff68716d14612b06f6a405342c57447aa` | 375 |
| `stop.tflite` | `https://github.com/kahrendt/microWakeWord/releases/download/stop/stop.tflite` | `b5a18c4ad681a89950dfade31011e1631bdcb333e93c84519a1a63ff4f071146` | 45744 |

The firmware currently uses the `.json` files for checked-in provenance and embeds the `.tflite` files as binary data.
