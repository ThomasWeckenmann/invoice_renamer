"""Tests for the local Transformers backend, using fake model/tokenizer objects
so these run without downloading real weights.
"""

import pytest
import torch
from pytest import mark, raises

from invoice_renamer.inference.transformers_extractor import TransformersExtractor

_VALID_REVISION = "a" * 40


class _FakeEncoding(dict[str, torch.Tensor]):
    """Stands in for a real BatchEncoding: dict-like (so **encoded works) and
    supports .to(device) like apply_chat_template(..., return_tensors="pt") returns.
    """

    def to(self, device: str) -> "_FakeEncoding":
        return self


class _FakeTokenizer:
    def __init__(
        self,
        new_tokens: list[int],
        *,
        decoded_text: str = "decoded text",
        raw_decoded_text: str | None = None,
    ) -> None:
        self._new_tokens = new_tokens
        self._decoded_text = decoded_text
        # Falls back to decoded_text when not given, matching a tokenizer where
        # stripping special tokens made no difference to the visible text.
        self._raw_decoded_text = raw_decoded_text if raw_decoded_text is not None else decoded_text
        self.chat_template_calls: list[list[dict[str, str]]] = []
        self.decode_calls: list[list[int]] = []

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        add_generation_prompt: bool,
        return_tensors: str,
        return_dict: bool,
        enable_thinking: bool,
    ) -> _FakeEncoding:
        assert add_generation_prompt is True
        assert return_tensors == "pt"
        assert return_dict is True
        assert enable_thinking is False
        self.chat_template_calls.append(messages)
        return _FakeEncoding(
            input_ids=torch.tensor([[1, 2, 3]]), attention_mask=torch.tensor([[1, 1, 1]])
        )

    def decode(self, token_ids: torch.Tensor, *, skip_special_tokens: bool) -> str:
        self.decode_calls.append(token_ids.tolist())
        return self._decoded_text if skip_special_tokens else self._raw_decoded_text


class _FakeModel:
    def __init__(self, new_tokens: list[int]) -> None:
        self._new_tokens = new_tokens
        self.generate_calls: list[dict[str, object]] = []

    def to(self, device: str) -> "_FakeModel":
        return self

    def eval(self) -> None:
        pass

    def generate(
        self,
        *,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        max_new_tokens: int,
        do_sample: bool,
        repetition_penalty: float,
        streamer: object,
    ) -> torch.Tensor:
        self.generate_calls.append(
            {
                "max_new_tokens": max_new_tokens,
                "do_sample": do_sample,
                "repetition_penalty": repetition_penalty,
                "streamer": streamer,
            }
        )
        return torch.cat([input_ids, torch.tensor([self._new_tokens])], dim=1)


def test_generate_sends_prompt_as_a_single_chat_message() -> None:
    tokenizer = _FakeTokenizer(new_tokens=[9, 9])
    extractor = TransformersExtractor(_FakeModel(new_tokens=[9, 9]), tokenizer)

    extractor.generate("extract this")

    assert tokenizer.chat_template_calls == [[{"role": "user", "content": "extract this"}]]


def test_generate_decodes_only_the_newly_generated_tokens() -> None:
    tokenizer = _FakeTokenizer(new_tokens=[9, 9])
    extractor = TransformersExtractor(_FakeModel(new_tokens=[9, 9]), tokenizer)

    result = extractor.generate("extract this")

    assert result == "decoded text"
    assert tokenizer.decode_calls == [[9, 9]]


def test_generate_decodes_greedily_with_the_configured_token_budget() -> None:
    model = _FakeModel(new_tokens=[9])
    extractor = TransformersExtractor(
        model,
        _FakeTokenizer(new_tokens=[9]),
        max_new_tokens=7,
        repetition_penalty=1.3,
        stream=False,
    )

    extractor.generate("extract this")

    assert model.generate_calls == [
        {"max_new_tokens": 7, "do_sample": False, "repetition_penalty": 1.3, "streamer": None}
    ]


def test_generate_streams_by_default_and_can_be_turned_off() -> None:
    streaming_model = _FakeModel(new_tokens=[9])
    TransformersExtractor(streaming_model, _FakeTokenizer(new_tokens=[9])).generate("x")
    assert streaming_model.generate_calls[0]["streamer"] is not None

    silent_model = _FakeModel(new_tokens=[9])
    TransformersExtractor(silent_model, _FakeTokenizer(new_tokens=[9]), stream=False).generate("x")
    assert silent_model.generate_calls[0]["streamer"] is None


def test_generate_prints_the_prompt_token_count_before_generating_when_streaming(
    capsys: "pytest.CaptureFixture[str]",
) -> None:
    model = _FakeModel(new_tokens=[9])
    TransformersExtractor(model, _FakeTokenizer(new_tokens=[9])).generate("x")

    assert "prompt_tokens=3" in capsys.readouterr().err


def test_generate_returns_and_warns_with_diagnostics_on_a_degenerate_generation(
    capsys: "pytest.CaptureFixture[str]",
) -> None:
    # Tokens came back (2 of them) but decoded to nothing once special tokens
    # are stripped. The diagnostic (token counts, raw unstripped text) must be
    # both printed live AND returned as the "response", so it survives into
    # extract_invoice()'s warning and a saved report - not just the terminal.
    tokenizer = _FakeTokenizer(
        new_tokens=[9, 9], decoded_text="", raw_decoded_text="<|endoftext|><|endoftext|>"
    )
    extractor = TransformersExtractor(_FakeModel(new_tokens=[9, 9]), tokenizer)

    result = extractor.generate("extract this")

    assert "prompt_tokens=3" in result
    assert "completion_tokens=2" in result
    assert "<|endoftext|><|endoftext|>" in result
    assert result in capsys.readouterr().err


def test_generate_reports_zero_completion_tokens_on_a_true_immediate_stop(
    capsys: "pytest.CaptureFixture[str]",
) -> None:
    # No new tokens came back at all - the model emitted EOS as its very
    # first token. Distinguishable from the case above by the token count.
    tokenizer = _FakeTokenizer(new_tokens=[], decoded_text="")
    extractor = TransformersExtractor(_FakeModel(new_tokens=[]), tokenizer)

    result = extractor.generate("extract this")

    assert "completion_tokens=0" in result


def test_tokenizer_property_exposes_the_injected_tokenizer() -> None:
    tokenizer = _FakeTokenizer(new_tokens=[])
    extractor = TransformersExtractor(_FakeModel(new_tokens=[]), tokenizer)

    assert extractor.tokenizer is tokenizer


@mark.parametrize(
    "revision",
    [
        "",
        "main",
        "latest",
        "head",
        "dev",
        "v1.0",
        "a" * 39,  # one short of a real sha
        "A" * 40,  # uppercase hex isn't what HF reports
        "g" * 40,  # not hex
    ],
)
def test_load_rejects_anything_but_a_full_commit_hash(revision: str) -> None:
    with raises(ValueError, match="commit hash"):
        TransformersExtractor.load("some/repo", revision)


def test_load_accepts_a_pinned_commit_hash_format() -> None:
    # Confirms the regex itself accepts a well-formed hash; load() still goes on
    # to hit the network, which is out of scope for this offline test suite.
    from invoice_renamer.inference.transformers_extractor import _PINNED_REVISION

    assert _PINNED_REVISION.match(_VALID_REVISION)
