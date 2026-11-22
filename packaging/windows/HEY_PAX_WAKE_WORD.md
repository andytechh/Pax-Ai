# Hey PAX wake word

PAX uses a dedicated **Hey PAX** ONNX model. It does not substitute the pretrained
Hey Jarvis model. The listener and app are local/offline for wake detection; the
normal assistant session remains cloud-backed.

## Current status

- The app now expects `%LOCALAPPDATA%\PAX\models\hey_pax.onnx`.
- `pax_wake_listener.py` provides a standalone Windows listener.
- The frozen PAX executable accepts `--pax-wake-listener`,
  `--pax-install-wake-startup`, and `--pax-remove-wake-startup`.
- A trained and evaluated `hey_pax.onnx` model is still required. It is not
  checked into the repository and the CI build must not pretend the model exists.

## Train the model

Use the upstream openWakeWord custom-model training workflow and the supplied
`hey_pax_training.yml` configuration. The upstream workflow requires a Piper
sample generator, positive synthetic speech, background audio, room impulse
responses, and openWakeWord feature data. Its model-training documentation
recommends thousands of positive examples and large, diverse negative audio
sets; a model must be evaluated for false activations before being deployed.

Reference:
- https://github.com/dscripka/openWakeWord/blob/main/examples/custom_model.yml
- https://github.com/dscripka/openWakeWord/blob/main/README.md#training-new-models

The upstream training repo/notebook and required data assets are separate from
the PAX runtime dependencies. Do not run the full training process in the
Windows packaging CI job.

After training, copy the resulting ONNX file to:

```text
%LOCALAPPDATA%\PAX\models\hey_pax.onnx
```

Then run PAX and verify detection using multiple speakers, microphone distances,
background noise, and a held-out set of negative audio. Tune the threshold only
after measuring recall and false positives.

## Enable launch-at-logon

After the model is installed and evaluated, open an elevated or normal command
prompt (per-user Task Scheduler registration does not require admin) and run:

```powershell
PAX.exe --pax-install-wake-startup
```

Remove the startup listener with:

```powershell
PAX.exe --pax-remove-wake-startup
```

The listener starts PAX after detecting the phrase. It releases the microphone
before launching the desktop app and restarts after the desktop app exits.
