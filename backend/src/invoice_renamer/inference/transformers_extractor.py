"""Local Hugging Face model backend for invoice extraction via Transformers.

`load` is the only place that talks to the Hugging Face Hub: it pins an exact
revision and refuses `trust_remote_code`, so a catalog entry's repository can
never resolve to code the app hasn't been told to trust. `load_installed`
loads from a picker-managed local directory instead, with
`local_files_only=True` enforced so it can never fall back to the network.
"""

import re
import sys
from pathlib import Path

import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    PreTrainedModel,
    PreTrainedTokenizerBase,
    TextStreamer,
)

from invoice_renamer.models.catalog import ModelCatalogEntry
from invoice_renamer.models.installer import install_dir_for

# HF repos are git repos; a real pinned revision is a full 40-hex-char commit
# SHA. Anything else (a branch, a tag, a short hash) can move underneath us.
_PINNED_REVISION = re.compile(r"^[0-9a-f]{40}$")


def _load_model_and_tokenizer(
    source: str, *, revision: str | None, local_files_only: bool, device: str
) -> tuple[PreTrainedModel, PreTrainedTokenizerBase]:
    # local_files_only is only passed at all when true, so load()'s calls stay
    # byte-for-byte identical to before this helper existed - no unrelated
    # kwarg for a regression test (or a future caller inspecting call args) to
    # trip over on the hub-loading path.
    extra_kwargs: dict[str, object] = {"local_files_only": True} if local_files_only else {}
    tokenizer = AutoTokenizer.from_pretrained(
        source, revision=revision, trust_remote_code=False, **extra_kwargs
    )
    model = AutoModelForCausalLM.from_pretrained(
        source, revision=revision, dtype=torch.bfloat16, trust_remote_code=False, **extra_kwargs
    )
    model.to(device)  # type: ignore[arg-type]
    model.eval()  # type: ignore[no-untyped-call]
    # Confirms where the weights actually landed instead of trusting the
    # requested device - .to() can silently no-op if the backend rejects it.
    actual_device = next(model.parameters()).device
    print(f"[TransformersExtractor] model loaded on device: {actual_device}", file=sys.stderr)
    return model, tokenizer


class TransformersExtractor:
    def __init__(
        self,
        model: PreTrainedModel,
        tokenizer: PreTrainedTokenizerBase,
        *,
        device: str = "cpu",
        max_new_tokens: int = 512,
        repetition_penalty: float = 1.15,
        stream: bool = True,
    ) -> None:
        self._model = model
        self._tokenizer = tokenizer
        self._device = device
        self._max_new_tokens = max_new_tokens
        self._repetition_penalty = repetition_penalty
        self._stream = stream

    @classmethod
    def load(
        cls,
        repository: str,
        revision: str,
        *,
        device: str = "cpu",
        max_new_tokens: int = 512,
        repetition_penalty: float = 1.15,
        stream: bool = True,
    ) -> "TransformersExtractor":
        if not _PINNED_REVISION.match(revision):
            raise ValueError(f"revision must be a full 40-character commit hash, got {revision!r}")

        model, tokenizer = _load_model_and_tokenizer(
            repository, revision=revision, local_files_only=False, device=device
        )
        return cls(
            model,
            tokenizer,
            device=device,
            max_new_tokens=max_new_tokens,
            repetition_penalty=repetition_penalty,
            stream=stream,
        )

    @classmethod
    def load_installed(
        cls,
        entry: ModelCatalogEntry,
        data_dir: Path,
        *,
        device: str = "cpu",
        max_new_tokens: int = 512,
        repetition_penalty: float = 1.15,
        stream: bool = False,
    ) -> "TransformersExtractor":
        install_dir = install_dir_for(entry, data_dir)
        model, tokenizer = _load_model_and_tokenizer(
            str(install_dir), revision=None, local_files_only=True, device=device
        )
        return cls(
            model,
            tokenizer,
            device=device,
            max_new_tokens=max_new_tokens,
            repetition_penalty=repetition_penalty,
            stream=stream,
        )

    @property
    def tokenizer(self) -> PreTrainedTokenizerBase:
        return self._tokenizer

    def generate(self, prompt: str) -> str:
        # apply_chat_template(..., return_tensors="pt") returns a BatchEncoding
        # (input_ids plus attention_mask), not a bare tensor - confirmed against
        # a real tokenizer, since that shape is easy to get wrong by assumption.
        messages = [{"role": "user", "content": prompt}]
        encoded = self._tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
            # Qwen3's hybrid think/non-think chat template defaults to
            # thinking mode, emitting a <think>...</think> block with no
            # markdown fence before the JSON - burning most of the token
            # budget and breaking parsing outright. Models whose template
            # doesn't reference this kwarg (everything else) just ignore it.
            enable_thinking=False,
        ).to(self._device)  # type: ignore[union-attr]

        prompt_length = encoded["input_ids"].shape[-1]
        streamer = None
        if self._stream:
            print(
                f"[TransformersExtractor] generating (prompt_tokens={prompt_length})...",
                file=sys.stderr,
                flush=True,
            )
            # Prints each token as it's produced instead of only after the whole
            # call returns - the only way to see live whether generation is
            # actually progressing (and how fast) versus stuck before it starts.
            streamer = TextStreamer(self._tokenizer, skip_prompt=True, skip_special_tokens=True)

        with torch.no_grad():
            output = self._model.generate(  # type: ignore[operator]
                **encoded,
                max_new_tokens=self._max_new_tokens,
                do_sample=False,
                repetition_penalty=self._repetition_penalty,
                streamer=streamer,
            )
        completion = output[0][prompt_length:]
        text = str(self._tokenizer.decode(completion, skip_special_tokens=True))
        if not text.strip():
            # skip_special_tokens=True hides what actually came back when that's
            # exactly the question; decoding again without it shows the literal
            # tokens (e.g. a repeated special/control token) instead of guessing.
            raw_text = str(self._tokenizer.decode(completion, skip_special_tokens=False))
            diagnostic = (
                f"<empty completion: prompt_tokens={prompt_length}, "
                f"completion_tokens={completion.shape[-1]}, raw={raw_text!r}>"
            )
            print(f"[TransformersExtractor] warning: {diagnostic}", file=sys.stderr)
            # Returned (not just printed) so it survives into extract_invoice()'s
            # warning and the saved report - still invalid JSON, so it fails
            # parsing exactly like the empty string would have.
            return diagnostic
        return text
