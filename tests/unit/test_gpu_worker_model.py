"""Unit tests for Parakeet model loading device selection.

Railway has no GPU runtime (confirmed 2026-07-10), so production runs the
inference service CPU-mode behind a CUDA stub: dlopen succeeds, cuInit
fails, torch.cuda.is_available() is False. load_model must map the model
to CPU explicitly in that case -- and to CUDA when a real GPU exists.

nemo and torch are lazy imports inside load_model, so these tests inject
fakes via sys.modules and never need the NVIDIA stack.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import thinktank.gpu_worker.model as model_module


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test starts and ends with no cached model."""
    model_module._model = None
    yield
    model_module._model = None


def _fake_stack(cuda_available: bool):
    """Build fake torch/nemo modules and return (patcher, from_pretrained_mock)."""
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda_available))

    loaded_model = MagicMock(name="parakeet")
    from_pretrained = MagicMock(return_value=loaded_model)
    fake_asr = MagicMock()
    fake_asr.models.EncDecRNNTBPEModel.from_pretrained = from_pretrained

    fake_nemo = MagicMock()
    fake_nemo.collections.asr = fake_asr

    modules = {
        "torch": fake_torch,
        "nemo": fake_nemo,
        "nemo.collections": fake_nemo.collections,
        "nemo.collections.asr": fake_asr,
    }
    return patch.dict(sys.modules, modules), from_pretrained, loaded_model


class TestDeviceSelection:
    def test_no_gpu_maps_to_cpu(self):
        """Stubbed CUDA (is_available False) must load with map_location='cpu'."""
        patcher, from_pretrained, loaded = _fake_stack(cuda_available=False)
        with patcher:
            result = model_module.load_model()

        assert result is loaded
        kwargs = from_pretrained.call_args.kwargs
        assert kwargs["map_location"] == "cpu"
        loaded.eval.assert_called_once()

    def test_gpu_maps_to_cuda(self):
        """Real GPU runtime loads with map_location='cuda'."""
        patcher, from_pretrained, _ = _fake_stack(cuda_available=True)
        with patcher:
            model_module.load_model()

        assert from_pretrained.call_args.kwargs["map_location"] == "cuda"

    def test_singleton_loads_once(self):
        """Second call returns the cached model without reloading."""
        patcher, from_pretrained, loaded = _fake_stack(cuda_available=False)
        with patcher:
            first = model_module.load_model()
            second = model_module.load_model()

        assert first is second is loaded
        from_pretrained.assert_called_once()
