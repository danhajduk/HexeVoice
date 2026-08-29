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
