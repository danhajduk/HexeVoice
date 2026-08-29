# Voice Placement Testing

Active placement tests are operator initiated. A test window is opened for one
endpoint and room, then the operator speaks the expected phrase from the
position being checked.

Start a test:

```bash
curl -X POST http://127.0.0.1:9004/api/voice/placement-tests \
  -H "Content-Type: application/json" \
  -d '{"endpoint_id":"esp-pe-1","room":"kitchen","position_label":"island","expected_phrase":"Hexe turn on the kitchen lights","ttl_seconds":300}'
```

The next matching endpoint turn is treated as an active placement sample. The
backend runs STT, audio-quality analysis, ambient/SNR analysis when metrics are
available, and Speaker ID when the pipeline exposes it. Assistant routing and
TTS are suppressed for the placement sample.

Results are listed at:

```bash
curl "http://127.0.0.1:9004/api/voice/placement-tests?endpoint_id=esp-pe-1"
```

Reports include phrase-match similarity, Speaker ID match/reliability when an
expected speaker is supplied, audio-quality warnings, SNR/clipping/level signals,
cross-position consistency, an overall score, and a recommendation.

By default, raw placement-test audio is processed in memory and discarded after
STT, Speaker ID, and audio-quality analysis. `debug_record_audio` is accepted as
an explicit operator flag for future bench capture flows, but current backend
placement reports still persist metrics only and record `raw_audio.persisted:
false`. Passive unattended placement calibration must remain metric-only and
must not run STT or Speaker ID.

Passive ambient placement calibration is a separate operator-started mode for
long-window room analysis. It schedules a selected endpoint for periodic
privacy-safe ambient metric samples, defaults to a 24-hour window with a
10-minute sample interval, and supports up to 48-hour windows.

```bash
curl -X POST http://127.0.0.1:9004/api/voice/placement-calibrations \
  -H "Content-Type: application/json" \
  -d '{"endpoint_id":"esp-pe-1","room":"kitchen","zone":"north"}'
```

Endpoints report numeric samples to the returned calibration ID:

```bash
curl -X POST http://127.0.0.1:9004/api/voice/placement-calibrations/placement-cal-abc123/samples \
  -H "Content-Type: application/json" \
  -d '{"metrics":{"ambient_rms":0.02,"peak":0.12,"clipping_ratio":0,"speech_like_activity":false}}'
```

Passive samples store only sanitized numeric metrics and boolean
speech-like-activity presence. Unattended passive samples do not call STT, do
not call Speaker ID, do not retain raw ambient audio by default, and ignore
payload fields that look like raw audio or transcript data. `debug_record_audio`
is accepted as a scheduling flag for future endpoint debug capture, but backend
sample storage still records `raw_audio.persisted: false`; any future
debug-retained passive ambient audio must expire after one day.

Status, cancellation, cleanup, and long-window report APIs are:

```bash
curl "http://127.0.0.1:9004/api/voice/placement-calibrations?endpoint_id=esp-pe-1"
curl -X POST http://127.0.0.1:9004/api/voice/placement-calibrations/placement-cal-abc123/cancel
curl -X POST http://127.0.0.1:9004/api/voice/placement-calibrations/cleanup
curl http://127.0.0.1:9004/api/voice/placement-calibrations/placement-cal-abc123/report
```

The long-window report combines passive ambient statistics with matching active
placement test reports for the same endpoint, room, and zone. It reports average
ambient RMS by hour, peak noise periods, speech-like activity frequency,
clipping frequency, SNR distribution when samples or active anchors have SNR,
active-test STT success, active-test Speaker ID reliability, and an overall
placement score/recommendation.
