#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlretrieve


SAMPLES = [
    {
        "path": "runtime/stt/samples/rehasp/rehasp_16k_std_lucy_hvd0005_rep001.wav",
        "url": "https://datashare.ed.ac.uk/bitstream/handle/10283/561/rehasp_16k_std_lucy_hvd0005_rep001.wav?isAllowed=y&sequence=5",
        "transcript": "Rice is often served in round bowls.",
    },
    {
        "path": "runtime/stt/samples/rehasp/rehasp_16k_std_lucy_hvd0005_rep040.wav",
        "url": "https://datashare.ed.ac.uk/bitstream/handle/10283/561/rehasp_16k_std_lucy_hvd0005_rep040.wav?isAllowed=y&sequence=6",
        "transcript": "Rice is often served in round bowls.",
    },
]


def main() -> int:
    for sample in SAMPLES:
        path = Path(sample["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            print(f"downloading {sample['url']} -> {path}")
            urlretrieve(sample["url"], path)
        else:
            print(f"exists {path}")

    manifest_path = Path("runtime/stt/samples/rehasp_manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "name": "REHASP 0.5 sample audio",
                "source": "Repeated Harvard Sentence Prompts corpus version 0.5",
                "source_url": "https://datashare.ed.ac.uk/handle/10283/561",
                "license": "Creative Commons Attribution 4.0 International",
                "attribution": (
                    "G. E. Henter, T. Merritt, M. Shannon, C. Mayo, and S. King, "
                    "\"Measuring the perceptual effects of modelling assumptions in speech synthesis "
                    "using stimuli constructed from repeated natural speech,\" Proc. Interspeech, 2014."
                ),
                "clips": SAMPLES,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
