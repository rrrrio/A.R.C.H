# Browser Voice Acceptance Evidence

`make acceptance-audio` runs the isolated Chrome voice path twenty times with the fixed local WAV, then runs twenty controlled interruption cases. It writes two ignored JSON reports below `data/benchmarks/results/`:

```text
browser-voice-normal.json
browser-voice-barge-in.json
```

Each report contains the per-run browser-observed server latency events and nearest-rank p50, p95, and maximum values for its required stage. The driver subscribes to a transient browser event rather than relying on the bounded visible latency history. Normal runs require exactly one `playback-first-audio` event. Interruption runs require exactly one `playback-stopped` event after the controlled barge-in fixture. The command stops on a failed run or a missing/ambiguous event instead of producing a partial success report.

## Latest Synthetic Run

On 2026-11-10, the first complete reports each contained 20 successful isolated Chrome runs. The normal-path server-to-transport `playback-first-audio` result was p50 `4.576 s`, p95 `4.768 s`, and maximum `4.799 s`. The controlled interruption `playback-stopped` result was p50 `1.673 s`, p95 `2.009 s`, and maximum `2.048 s`.

These values are regression observations, not acceptance of the product latency targets. They include the complete disposable local browser pipeline and cannot be compared directly with speaker-audible or human barge-in measurements.

## Evidence Boundary

These reports exercise a disposable HTTPS gateway, generated administrator account, fake Chrome microphone, WebRTC session, local ASR, local LLM, local TTS, and browser UI. The time is measured by the server’s monotonic clock to the first PCM frame scheduled for browser transport, or to the interruption frame. It is useful regression evidence for the application path.

It does not measure speaker output, human-perceived start time, microphone hardware, LAN jitter, CA trust on another device, or an operator’s live interruption. Do not use the report to claim the project’s `<= 1500 ms` audible-first-audio or `<= 250 ms` human barge-in targets are device-validated. Those require the separate target-device procedure in the project ExecPlan.
