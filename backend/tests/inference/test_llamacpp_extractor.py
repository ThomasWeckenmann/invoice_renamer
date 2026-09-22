"""Tests for the local llama.cpp backend, using fake model/tokenizer objects
so these run without downloading real weights.
"""

from pathlib import Path

import pytest

from invoice_renamer.inference.llamacpp_extractor import LlamaCppExtractor
from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile
from invoice_renamer.models.installer import install_dir_for

_VALID_REVISION = "a" * 40


def _entry(
    *,
    extra_gguf_files: list[ModelFile] | None = None,
    prompt_template: str | None = "chatml",
    context_size: int | None = 4096,
) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id="tiny-gguf-model",
        display_name="Tiny GGUF Model",
        license="apache-2.0",
        repository="example-org/tiny-gguf-model",
        revision=_VALID_REVISION,
        memory_tier=MemoryTier.SMALL,
        prompt_template=prompt_template,
        context_size=context_size,
        files=[
            ModelFile(path="model.gguf", sha256="a" * 64, size_bytes=1),
            ModelFile(path="tokenizer.json", sha256="b" * 64, size_bytes=1),
            ModelFile(path="tokenizer_config.json", sha256="c" * 64, size_bytes=1),
            *(extra_gguf_files or []),
        ],
    )


class _FakeLlamaTokenizer:
    """Stands in for a transformers tokenizer with apply_chat_template."""

    def __init__(
        self,
        *,
        chat_template_result: str = "rendered prompt",
        eos_token: str | None = "<|endoftext|>",
        bos_token: str | None = "<|bos|>",
        chat_template: str | None = "fake-chat-template",
    ) -> None:
        self._chat_template_result = chat_template_result
        self.eos_token = eos_token
        self.bos_token = bos_token
        self.chat_template = chat_template
        self.apply_chat_template_calls: list[dict[str, object]] = []

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        add_generation_prompt: bool,
        tokenize: bool,
        enable_thinking: bool,
        **kwargs: object,
    ) -> str:
        assert add_generation_prompt is True
        assert tokenize is False
        assert enable_thinking is False
        self.apply_chat_template_calls.append(
            {
                "messages": messages,
                "add_generation_prompt": add_generation_prompt,
                "tokenize": tokenize,
                "enable_thinking": enable_thinking,
            }
        )
        return self._chat_template_result


class _FakeLlamaModel:
    """Stands in for llama_cpp.Llama - tokenize() and __call__() are wired
    together the same way the real binding is used: whatever tokenize()
    returns is what a test should expect __call__ to receive as `prompt`."""

    def __init__(
        self,
        completion_text: str = "completion text",
        *,
        tokenize_result: list[int] | None = None,
        completion_tokens: int = 2,
        finish_reason: str = "stop",
    ) -> None:
        self._completion_text = completion_text
        self._tokenize_result = tokenize_result if tokenize_result is not None else [1, 2, 3]
        self._completion_tokens = completion_tokens
        self._finish_reason = finish_reason
        self.generate_calls: list[dict[str, object]] = []
        self.tokenize_calls: list[dict[str, object]] = []
        self.closed = False

    def tokenize(self, text: bytes, *, add_bos: bool, special: bool) -> list[int]:
        self.tokenize_calls.append({"text": text, "add_bos": add_bos, "special": special})
        return self._tokenize_result

    def create_completion(
        self,
        prompt: list[int],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        repeat_penalty: float,
        stop: list[str],
        stream: bool,
    ) -> dict:
        self.generate_calls.append(
            {
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "repeat_penalty": repeat_penalty,
                "stop": stop,
                "stream": stream,
            }
        )
        return {
            "choices": [{"text": self._completion_text, "finish_reason": self._finish_reason}],
            "usage": {"completion_tokens": self._completion_tokens},
        }

    def close(self) -> None:
        self.closed = True


def _patch_llama_cpp(monkeypatch: pytest.MonkeyPatch, fake_model: _FakeLlamaModel) -> None:
    """Patch llama_cpp.Llama to return our fake model."""
    monkeypatch.setattr(
        "invoice_renamer.inference.llamacpp_extractor.Llama",
        lambda **kwargs: fake_model,
    )


def _patch_tokenizer(monkeypatch: pytest.MonkeyPatch, fake_tokenizer: _FakeLlamaTokenizer) -> None:
    """Patch AutoTokenizer.from_pretrained to return our fake tokenizer."""

    def fake_from_pretrained(source: str, **kwargs: object) -> _FakeLlamaTokenizer:
        assert kwargs.get("local_files_only") is True
        assert kwargs.get("trust_remote_code") is False
        assert kwargs.get("revision") is None
        return fake_tokenizer

    monkeypatch.setattr(
        "invoice_renamer.inference.llamacpp_extractor.AutoTokenizer.from_pretrained",
        fake_from_pretrained,
    )


def _install_gguf(tmp_path: Path, entry: ModelCatalogEntry | None = None) -> Path:
    install_dir = install_dir_for(entry or _entry(), tmp_path)
    install_dir.mkdir(parents=True)
    (install_dir / "model.gguf").write_bytes(b"fake")
    return install_dir


def test_generate_sends_prompt_as_a_single_chat_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_model = _FakeLlamaModel()
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    extractor.generate("extract this")

    assert fake_tokenizer.apply_chat_template_calls == [
        {
            "messages": [{"role": "user", "content": "extract this"}],
            "add_generation_prompt": True,
            "tokenize": False,
            "enable_thinking": False,
        }
    ]


def test_generate_tokenizes_the_rendered_prompt_with_special_tokens_recognized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_model = _FakeLlamaModel(tokenize_result=[7, 8, 9])
    fake_tokenizer = _FakeLlamaTokenizer(chat_template_result="RENDERED PROMPT")
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    extractor.generate("extract this")

    assert fake_model.tokenize_calls[0]["text"] == b"RENDERED PROMPT"
    assert fake_model.tokenize_calls[0]["special"] is True


def test_generate_passes_the_tokenized_prompt_to_completion_not_the_raw_string(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The completion call must receive exactly the token ids tokenize()
    # produced, never the raw rendered string - passing a string would let
    # llama.cpp retokenize with its own (different, unconfirmed) defaults.
    fake_model = _FakeLlamaModel(tokenize_result=[7, 8, 9])
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    extractor.generate("extract this")

    assert fake_model.generate_calls[0]["prompt"] == [7, 8, 9]


def test_generate_adds_bos_only_when_the_template_did_not_already_render_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_model = _FakeLlamaModel()
    fake_tokenizer = _FakeLlamaTokenizer(
        chat_template_result="plain rendered prompt", bos_token="<|bos|>"
    )
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    extractor.generate("extract this")

    assert fake_model.tokenize_calls[0]["add_bos"] is True


def test_generate_skips_bos_when_the_template_already_rendered_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_model = _FakeLlamaModel()
    fake_tokenizer = _FakeLlamaTokenizer(
        chat_template_result="<|bos|>already has one", bos_token="<|bos|>"
    )
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    extractor.generate("extract this")

    assert fake_model.tokenize_calls[0]["add_bos"] is False


def test_generate_uses_greedy_decoding_with_configured_token_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_model = _FakeLlamaModel(tokenize_result=[1, 2, 3])
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(
        _entry(), tmp_path, device="cpu", max_new_tokens=7, repetition_penalty=1.3
    )
    extractor.generate("extract this")

    assert fake_model.generate_calls == [
        {
            "prompt": [1, 2, 3],
            "max_tokens": 7,
            "temperature": 0.0,
            "top_p": 1.0,
            "repeat_penalty": 1.3,
            "stop": ["<|endoftext|>"],
            "stream": False,
        }
    ]


def test_generate_returns_decoded_completion_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_model = _FakeLlamaModel(completion_text="the extracted json")
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    result = extractor.generate("extract this")

    assert result == "the extracted json"


def test_generate_returns_and_warns_with_diagnostics_on_empty_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_model = _FakeLlamaModel(completion_text="", completion_tokens=0)
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    result = extractor.generate("extract this")

    assert "prompt_tokens=3" in result
    assert "completion_tokens=0" in result
    assert "stripped=''" in result
    assert result in capsys.readouterr().err


def test_generate_reports_the_real_completion_token_count_when_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Distinguishes "the model emitted tokens that decoded to nothing
    # visible" from "no tokens came back at all" - both stripped-empty,
    # but not the same failure.
    fake_model = _FakeLlamaModel(completion_text="   ", completion_tokens=5)
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    result = extractor.generate("extract this")

    assert "completion_tokens=5" in result


def test_generate_reports_context_limit_failure_when_prompt_too_long(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_model = _FakeLlamaModel(tokenize_result=list(range(5000)))
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")

    with pytest.raises(ValueError, match="exceeds context window"):
        extractor.generate("x" * 10000)


def test_generate_reports_incomplete_completion_when_output_limit_is_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # finish_reason="length" means max_new_tokens cut generation short, not
    # that the model actually finished - even if the text so far happens to
    # look like valid JSON, it must not be accepted as a complete extraction.
    fake_model = _FakeLlamaModel(
        completion_text='{"seller": "Foo"', finish_reason="length", completion_tokens=7
    )
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu", max_new_tokens=7)
    result = extractor.generate("extract this")

    assert "incomplete completion" in result
    assert "max_new_tokens=7" in result
    assert '{"seller": "Foo"' in result


def test_generate_does_not_log_invoice_content_to_stderr_on_truncation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The truncated text can hold real, partially-extracted invoice content
    # (unlike the empty-completion case) - it belongs only in the returned
    # diagnostic (the caller's saved-report channel), never in worker stderr.
    sensitive_text = '{"seller": "Sensitive Invoice Seller GmbH", "gross_total": 1234.56'
    fake_model = _FakeLlamaModel(
        completion_text=sensitive_text, finish_reason="length", completion_tokens=9
    )
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu", max_new_tokens=9)
    result = extractor.generate("extract this")

    assert sensitive_text in result
    stderr = capsys.readouterr().err
    assert sensitive_text not in stderr
    assert "incomplete completion" in stderr
    assert "max_new_tokens=9" in stderr


def test_generate_does_not_flag_a_normal_stop_as_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_model = _FakeLlamaModel(completion_text="the extracted json", finish_reason="stop")
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    result = extractor.generate("extract this")

    assert result == "the extracted json"


def test_load_installed_rejects_a_catalog_entry_with_no_prompt_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(prompt_template=None)
    _install_gguf(tmp_path, entry)
    _patch_tokenizer(monkeypatch, _FakeLlamaTokenizer())
    _patch_llama_cpp(monkeypatch, _FakeLlamaModel())

    with pytest.raises(ValueError, match="hasn't been verified"):
        LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")


def test_load_installed_rejects_an_unsupported_prompt_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(prompt_template="some-unverified-format")
    _install_gguf(tmp_path, entry)
    _patch_tokenizer(monkeypatch, _FakeLlamaTokenizer())
    _patch_llama_cpp(monkeypatch, _FakeLlamaModel())

    with pytest.raises(ValueError, match="hasn't been verified"):
        LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")


@pytest.mark.parametrize("prompt_template", ["chatml", "granite-instruct", "llama3"])
def test_load_installed_accepts_known_prompt_templates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, prompt_template: str
) -> None:
    entry = _entry(prompt_template=prompt_template)
    _install_gguf(tmp_path, entry)
    _patch_tokenizer(monkeypatch, _FakeLlamaTokenizer())
    _patch_llama_cpp(monkeypatch, _FakeLlamaModel())

    LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")


def test_load_installed_rejects_a_catalog_entry_with_no_context_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(context_size=None)
    _install_gguf(tmp_path, entry)
    _patch_tokenizer(monkeypatch, _FakeLlamaTokenizer())
    _patch_llama_cpp(monkeypatch, _FakeLlamaModel())

    with pytest.raises(ValueError, match="no context_size set"):
        LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")


def test_load_installed_rejects_an_installed_tokenizer_with_no_chat_template(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An allowlisted prompt_template label doesn't prove the install bundle
    # actually shipped a usable chat-template asset - catch that here with a
    # clear error instead of failing deep inside apply_chat_template().
    entry = _entry()
    _install_gguf(tmp_path, entry)
    _patch_tokenizer(monkeypatch, _FakeLlamaTokenizer(chat_template=None))
    _patch_llama_cpp(monkeypatch, _FakeLlamaModel())

    with pytest.raises(ValueError, match="no chat_template"):
        LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")


def test_load_installed_sets_repetition_penalty_window_to_the_full_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Defaults to 64 in the binding otherwise - much narrower than
    # TransformersExtractor's whole-sequence repetition penalty.
    _install_gguf(tmp_path)
    _patch_tokenizer(monkeypatch, _FakeLlamaTokenizer())

    created_kwargs: list[dict[str, object]] = []
    monkeypatch.setattr(
        "invoice_renamer.inference.llamacpp_extractor.Llama",
        lambda **kwargs: (created_kwargs.append(kwargs), _FakeLlamaModel())[1],
    )

    LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu", n_ctx=8192)

    assert created_kwargs[0]["last_n_tokens_size"] == 8192


def test_load_installed_lets_an_explicit_n_ctx_override_the_catalog_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    created_kwargs: list[dict[str, object]] = []
    monkeypatch.setattr(
        "invoice_renamer.inference.llamacpp_extractor.Llama",
        lambda **kwargs: (created_kwargs.append(kwargs), _FakeLlamaModel())[1],
    )

    # _entry()'s own context_size defaults to 4096; passing n_ctx here must
    # win over it.
    LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu", n_ctx=8192)

    assert created_kwargs[0]["n_ctx"] == 8192


def test_load_installed_defaults_n_ctx_to_the_catalog_entrys_context_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    entry = _entry(context_size=16384)
    _install_gguf(tmp_path, entry)

    created_kwargs: list[dict[str, object]] = []
    monkeypatch.setattr(
        "invoice_renamer.inference.llamacpp_extractor.Llama",
        lambda **kwargs: (created_kwargs.append(kwargs), _FakeLlamaModel())[1],
    )

    LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")

    assert created_kwargs[0]["n_ctx"] == 16384


def test_tokenizer_property_exposes_the_injected_tokenizer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_model = _FakeLlamaModel()
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")

    assert extractor.tokenizer is fake_tokenizer


def test_load_installed_resolves_install_dir_and_forces_local_files_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry()
    install_dir = _install_gguf(tmp_path, entry)

    tokenizer_calls: list[tuple[str, dict[str, object]]] = []

    def fake_tokenizer_from_pretrained(source: str, **kwargs: object) -> _FakeLlamaTokenizer:
        tokenizer_calls.append((source, kwargs))
        return _FakeLlamaTokenizer()

    _patch_llama_cpp(monkeypatch, _FakeLlamaModel())
    monkeypatch.setattr(
        "invoice_renamer.inference.llamacpp_extractor.AutoTokenizer.from_pretrained",
        fake_tokenizer_from_pretrained,
    )

    LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")

    assert tokenizer_calls[0] == (
        str(install_dir),
        {"revision": None, "trust_remote_code": False, "local_files_only": True},
    )


def test_load_installed_fails_when_catalog_declares_no_gguf_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = ModelCatalogEntry(
        id="no-gguf",
        display_name="No GGUF",
        license="apache-2.0",
        repository="example-org/no-gguf",
        revision=_VALID_REVISION,
        memory_tier=MemoryTier.SMALL,
        prompt_template="chatml",
        context_size=4096,
        files=[ModelFile(path="tokenizer.json", sha256="b" * 64, size_bytes=1)],
    )
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)

    _patch_tokenizer(monkeypatch, _FakeLlamaTokenizer())
    _patch_llama_cpp(monkeypatch, _FakeLlamaModel())

    with pytest.raises(FileNotFoundError, match="declares no .gguf file"):
        LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")


def test_load_installed_fails_when_the_declared_gguf_file_is_missing_on_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry()
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    # Declared in entry.files, but never written to disk.

    _patch_tokenizer(monkeypatch, _FakeLlamaTokenizer())
    _patch_llama_cpp(monkeypatch, _FakeLlamaModel())

    with pytest.raises(FileNotFoundError, match="missing from install dir"):
        LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")


def test_load_installed_rejects_a_catalog_entry_declaring_multiple_gguf_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry(extra_gguf_files=[ModelFile(path="model-2.gguf", sha256="d" * 64, size_bytes=1)])
    install_dir = install_dir_for(entry, tmp_path)
    install_dir.mkdir(parents=True)
    (install_dir / "model.gguf").write_bytes(b"fake")
    (install_dir / "model-2.gguf").write_bytes(b"fake")

    _patch_tokenizer(monkeypatch, _FakeLlamaTokenizer())
    _patch_llama_cpp(monkeypatch, _FakeLlamaModel())

    with pytest.raises(ValueError, match="declares 2 .gguf files"):
        LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")


def test_load_installed_maps_device_to_n_gpu_layers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entry = _entry()
    _install_gguf(tmp_path, entry)

    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_tokenizer(monkeypatch, fake_tokenizer)

    created_kwargs: list[dict[str, object]] = []

    def fake_llama_factory(**kwargs: object) -> _FakeLlamaModel:
        created_kwargs.append(kwargs)
        return _FakeLlamaModel()

    monkeypatch.setattr(
        "invoice_renamer.inference.llamacpp_extractor.Llama",
        fake_llama_factory,
    )

    LlamaCppExtractor.load_installed(entry, tmp_path, device="cpu")
    assert created_kwargs[0]["n_gpu_layers"] == 0

    created_kwargs.clear()
    LlamaCppExtractor.load_installed(entry, tmp_path, device="mps")
    assert created_kwargs[0]["n_gpu_layers"] == -1

    created_kwargs.clear()
    LlamaCppExtractor.load_installed(entry, tmp_path, device="cuda")
    assert created_kwargs[0]["n_gpu_layers"] == -1


def test_close_frees_model_resources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_model = _FakeLlamaModel()
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    extractor.close()

    assert fake_model.closed is True


def test_generate_prints_prompt_token_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_model = _FakeLlamaModel(tokenize_result=[1, 2, 3, 4, 5])
    fake_tokenizer = _FakeLlamaTokenizer()
    _patch_llama_cpp(monkeypatch, fake_model)
    _patch_tokenizer(monkeypatch, fake_tokenizer)
    _install_gguf(tmp_path)

    extractor = LlamaCppExtractor.load_installed(_entry(), tmp_path, device="cpu")
    extractor.generate("extract this")

    assert "prompt_tokens=5" in capsys.readouterr().err
