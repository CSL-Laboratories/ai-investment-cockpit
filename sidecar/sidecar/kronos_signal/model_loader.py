"""Load the vendored KronosPredictor from the pinned MIT sources.

The upstream package is not installed from PyPI. ``vendor/kronos/PIN.json`` records
the commit. Weights stay on Hugging Face and are fetched by ``from_pretrained``.
"""

from __future__ import annotations

import sys
from pathlib import Path

VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "kronos"
MODELS = {
    "mini": {
        "model_id": "NeoQuasar/Kronos-mini",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-2k",
        "max_context": 2048,
    },
    "small": {
        "model_id": "NeoQuasar/Kronos-small",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context": 512,
    },
}


class KronosUnavailable(RuntimeError):
    """Torch or the vendored sources cannot be imported."""


def load_predictor(size: str, *, device: str = "cpu"):
    if size not in MODELS:
        raise KronosUnavailable(f"unknown Kronos size {size}")
    if not (VENDOR_ROOT / "model" / "kronos.py").is_file():
        raise KronosUnavailable("vendored Kronos sources are missing")
    root = str(VENDOR_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        import torch
        from model import Kronos, KronosPredictor, KronosTokenizer
    except ImportError as exc:
        raise KronosUnavailable(
            "Kronos inference needs torch, pandas, einops, huggingface_hub, safetensors, and tqdm"
        ) from exc
    spec = MODELS[size]
    tokenizer = KronosTokenizer.from_pretrained(spec["tokenizer_id"])
    model = Kronos.from_pretrained(spec["model_id"])
    torch.set_num_threads(min(4, torch.get_num_threads()))
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=spec["max_context"])
    predictor.model.eval()
    predictor.tokenizer.eval()
    return predictor
