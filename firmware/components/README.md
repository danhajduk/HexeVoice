# Firmware Components

`endpoint_runtime` is the current buildable endpoint component. It keeps the
existing runtime code together while the firmware grows a second app boundary
for recovery/provisioning.

As recovery work lands, stable code can move from `endpoint_runtime` into
smaller shared components such as protocol, audio pipeline, wake word,
provisioning, storage, OTA, board support, and UI. Keep app entrypoints under
`firmware/apps/*/main`; shared code belongs here.
