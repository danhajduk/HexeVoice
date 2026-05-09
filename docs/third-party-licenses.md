# Third-Party License Notes

HexeVoice declares runtime Python dependencies in `requirements.txt`. This file records dependency license notes that affect source or binary distribution.

## Python-SoXR / libsoxr

- Package: `soxr`
- Upstream: https://github.com/dofuuz/python-soxr
- Documentation: https://python-soxr.readthedocs.io
- Use in HexeVoice: streaming sample-rate conversion for Piper TTS WAV artifacts.
- License: LGPL-2.1-or-later, following libsoxr.

The Python-SoXR package includes a modified copy of libsoxr. When distributing an environment, container image, appliance image, or binary bundle that includes this dependency, preserve the dependency's license notice and source attribution as required by the LGPL-2.1-or-later terms.
