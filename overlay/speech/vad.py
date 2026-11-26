"""Streaming Silero voice activity detector (the model ships with faster-whisper), 32 ms frames."""

from __future__ import annotations

import importlib.util
import os

import numpy as np

FRAME = 512
_CONTEXT = 64
_session = None


def _model_path() -> str:
    # Located without importing faster_whisper: that import pulls in ctranslate2 and CUDA.
    spec = importlib.util.find_spec("faster_whisper")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("faster-whisper is not installed")
    return os.path.join(list(spec.submodule_search_locations)[0], "assets", "silero_vad_v6.onnx")


def _get_session():
    """One ONNX session per process: run() is thread-safe and each StreamingVAD keeps its own state."""
    global _session
    if _session is None:
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        _session = onnxruntime.InferenceSession(_model_path(), providers=["CPUExecutionProvider"], sess_options=opts)
    return _session


class StreamingVAD:
    def __init__(self):
        self.session = _get_session()
        self.reset()

    def reset(self) -> None:
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros(_CONTEXT, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        """Speech probability for one FRAME of float32 samples."""
        x = np.concatenate([self.context, frame]).reshape(1, -1)
        out, self.h, self.c = self.session.run(None, {"input": x, "h": self.h, "c": self.c})
        self.context = frame[-_CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])
