"""Local model backend for invoice extraction via llama.cpp (GGUF weights).

`load_installed` loads a GGUF model and its tokenizer/template assets from a
picker-managed local directory, with `local_files_only=True` enforced so it
can never fall back to the network. Chat templates are rendered via
transformers' tokenizer (preserving Qwen3's `enable_thinking=False` behavior)
and the resulting tokens - not the raw rendered string - are handed to
llama.cpp's raw completion API, so this module controls tokenization (BOS
insertion, special-token recognition) exactly once rather than risking a
second, differently-configured retokenization inside the completion call.
"""

import sys
from pathlib import Path

from llama_cpp import Llama
from transformers import AutoTokenizer, PreTrainedTokenizerBase

from invoice_renamer.models.catalog import ModelCatalogEntry
from invoice_renamer.models.installer import install_dir_for

# This backend deliberately does NOT let prompt_template select or override
# a literal Jinja template - it always renders via the installed tokenizer's
# own embedded default (see generate()). A hand-authored override template
# is a real regression risk, not just extra work: Qwen3's actual shipped
# template contains model-specific conditional logic for enable_thinking
# (its hybrid think/non-think suppression, confirmed load-bearing per
# docs/model_benchmark_findings.md) that a generic reimplementation of "the
# chatml format" would not replicate. TransformersExtractor never overrode
# the template either. `prompt_template` is instead an allowlist confirming
# a catalog entry's chat format has actually been verified to work with
# that default-template approach; load_installed() also confirms the
# installed tokenizer actually has a chat_template at all. A future catalog
# entry with an unverified/different template shape must fail loudly here,
# not silently render with whatever the tokenizer happens to ship.
_SUPPORTED_PROMPT_TEMPLATES = frozenset({"chatml", "granite-instruct", "llama3"})


class LlamaCppExtractor:
    """llama.cpp-backed implementation of the LanguageModel protocol.

    Uses a transformers tokenizer (no torch model) to render chat templates,
    then feeds the resulting tokens to llama.cpp's raw completion endpoint.
    """

    def __init__(
        self,
        *,
        model_path: str,
        tokenizer: PreTrainedTokenizerBase,
        n_ctx: int,
        max_new_tokens: int = 512,
        repetition_penalty: float = 1.15,
        n_gpu_layers: int = 0,
        verbose: bool = False,
    ) -> None:
        self._model = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            # Defaults to 64 (confirmed in the installed binding's source),
            # meaning the repetition penalty would otherwise only look at the
            # last 64 tokens - much narrower than TransformersExtractor's
            # repetition_penalty, which considers the whole generated
            # sequence. Set to the full context so behavior stays comparable
            # regardless of how long a given prompt/completion turns out to be.
            last_n_tokens_size=n_ctx,
            verbose=verbose,
        )
        self._tokenizer = tokenizer
        self._n_ctx = n_ctx
        self._max_new_tokens = max_new_tokens
        self._repetition_penalty = repetition_penalty

    @classmethod
    def load_installed(
        cls,
        entry: ModelCatalogEntry,
        data_dir: Path,
        *,
        device: str = "cpu",
        n_ctx: int | None = None,
        max_new_tokens: int = 512,
        repetition_penalty: float = 1.15,
        verbose: bool = False,
    ) -> "LlamaCppExtractor":
        """Load a GGUF model and its tokenizer/template from the local install dir.

        `local_files_only=True` is enforced; the method never touches the
        network. `n_ctx` defaults to the catalog entry's own `context_size` -
        pass an explicit value only to override it (e.g. standalone
        validation against a model with no catalog entry). A catalog entry
        with no `context_size` set fails loudly here rather than silently
        falling back to one shared placeholder across every model.
        """
        if entry.prompt_template not in _SUPPORTED_PROMPT_TEMPLATES:
            raise ValueError(
                f"catalog entry {entry.id!r} has prompt_template="
                f"{entry.prompt_template!r}, which this backend hasn't been "
                f"verified against (supported: {sorted(_SUPPORTED_PROMPT_TEMPLATES)})"
            )

        if n_ctx is None:
            if entry.context_size is None or entry.context_size <= 0:
                raise ValueError(
                    f"catalog entry {entry.id!r} has no context_size set; "
                    "llama.cpp needs an explicit per-model n_ctx, not a shared default"
                )
            n_ctx = entry.context_size

        install_dir = install_dir_for(entry, data_dir)

        # Load tokenizer (and chat template) from the local install directory.
        # No torch import occurs; only the tokenizer files are read.
        tokenizer = AutoTokenizer.from_pretrained(
            str(install_dir),
            revision=None,
            trust_remote_code=False,
            local_files_only=True,
        )
        if not getattr(tokenizer, "chat_template", None):
            # A catalog entry can name a prompt_template this backend
            # allowlists without the installed bundle actually shipping the
            # matching chat-template asset (e.g. an incomplete Block 2
            # install bundle) - generate() would otherwise fail deep inside
            # apply_chat_template() with a far less specific error.
            raise ValueError(
                f"catalog entry {entry.id!r} (prompt_template={entry.prompt_template!r}) "
                f"has no chat_template in its installed tokenizer config"
            )

        model_path = str(_resolve_gguf_file(entry, install_dir))

        # cpu -> no offload; mps/cuda -> offload every layer. Confirming the
        # binding actually built with GPU support (rather than silently
        # running on CPU despite n_gpu_layers=-1) is unverified without real
        # hardware - see the plan's live-validation block.
        n_gpu_layers = 0 if device == "cpu" else -1

        return cls(
            model_path=model_path,
            tokenizer=tokenizer,
            n_ctx=n_ctx,
            max_new_tokens=max_new_tokens,
            repetition_penalty=repetition_penalty,
            n_gpu_layers=n_gpu_layers,
            verbose=verbose,
        )

    @property
    def tokenizer(self) -> PreTrainedTokenizerBase:
        return self._tokenizer

    def generate(self, prompt: str) -> str:
        # Render the chat template using the transformers tokenizer. This
        # preserves the exact same behavior as TransformersExtractor,
        # including Qwen3's enable_thinking=False.
        messages = [{"role": "user", "content": prompt}]
        rendered_prompt = self._tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=False,
            enable_thinking=False,
        )
        # apply_chat_template with tokenize=False returns a string
        assert isinstance(rendered_prompt, str)

        # Tokenize exactly once, then hand llama.cpp the resulting token ids
        # (not the raw string) so nothing retokenizes the prompt a second
        # time with different defaults:
        # - special=True: the rendered text contains literal control-token
        #   text (e.g. "<|im_start|>") that must be recognized as the single
        #   special token it represents, not split into ordinary subwords -
        #   the whole point of applying a chat template.
        # - add_bos: only add one BOS token even if the chat template's own
        #   text already spells out the tokenizer's BOS marker (some do,
        #   some don't) - adding it unconditionally here risks a duplicate.
        bos_token = self._tokenizer.bos_token
        already_has_bos = bos_token is not None and rendered_prompt.startswith(bos_token)
        prompt_tokens = self._model.tokenize(
            rendered_prompt.encode("utf-8"), add_bos=not already_has_bos, special=True
        )
        prompt_length = len(prompt_tokens)

        if prompt_length + self._max_new_tokens > self._n_ctx:
            raise ValueError(
                f"Prompt ({prompt_length} tokens) + max_new_tokens ({self._max_new_tokens}) "
                f"exceeds context window ({self._n_ctx})"
            )

        # llama.cpp already stops generation at the model's own EOS token
        # automatically; this is a text-level backstop only, for the case
        # where a specific GGUF's declared EOS doesn't match its chat
        # template's real turn-end marker - unconfirmed without a real model.
        stop = [self._tokenizer.eos_token] if self._tokenizer.eos_token else []

        print(
            f"[LlamaCppExtractor] generating (prompt_tokens={prompt_length}, "
            f"max_new_tokens={self._max_new_tokens})...",
            file=sys.stderr,
            flush=True,
        )

        # Greedy decoding (temperature=0, top_p=1) to match
        # TransformersExtractor's do_sample=False behavior. Passing the
        # already-tokenized prompt (not rendered_prompt) means this call
        # performs no tokenization of its own, and (confirmed against
        # llama_cpp's own source) skips its internal BOS/EOS insertion
        # entirely for a list prompt - this module's own add_bos decision
        # above is the only one that applies. create_completion() is used
        # instead of __call__() only because __call__()'s type stub is
        # narrower (str-only) even though both accept List[int] identically
        # at runtime - __call__ just forwards to create_completion().
        result = self._model.create_completion(
            prompt=prompt_tokens,
            max_tokens=self._max_new_tokens,
            temperature=0.0,
            top_p=1.0,
            repeat_penalty=self._repetition_penalty,
            stop=stop,
            stream=False,
        )
        completion_text = result["choices"][0]["text"]  # type: ignore[index]
        completion_tokens = result["usage"]["completion_tokens"]  # type: ignore[index]
        finish_reason = result["choices"][0]["finish_reason"]  # type: ignore[index]

        if finish_reason == "length":
            # Cut off by max_new_tokens, not because the model actually
            # finished - the text so far may be truncated mid-value and
            # merely happen to still look parseable. Wrap it so it can't be
            # mistaken for a complete extraction; extract_invoice()'s repair
            # loop gets a chance to retry, same as any other bad response.
            # The full text goes only into the returned diagnostic (the
            # established channel for surfacing a response into the user's
            # own saved report, same as extractor.py's terminal-failure
            # path) - unlike that text, worker stderr is a log, and this
            # completion can hold real invoice content, so only counts and
            # the termination reason are printed there.
            diagnostic = (
                f"<incomplete completion: hit max_new_tokens="
                f"{self._max_new_tokens} before the model finished, "
                f"prompt_tokens={prompt_length}, text={completion_text!r}>"
            )
            print(
                "[LlamaCppExtractor] warning: incomplete completion "
                f"(prompt_tokens={prompt_length}, max_new_tokens={self._max_new_tokens})",
                file=sys.stderr,
            )
            return diagnostic

        if not completion_text.strip():
            # Empty completion after stripping. Unlike TransformersExtractor,
            # this can't show genuinely raw/unstripped text: llama.cpp's
            # detokenize() defaults to special=False (control tokens
            # omitted) and create_completion()'s response carries no raw
            # token ids to re-decode with special=True instead - confirmed
            # against the installed binding's source, not assumed. This is a
            # real, currently-unclosed gap versus the old diagnostic's
            # ability to reveal e.g. a repeated-EOS degenerate generation.
            diagnostic = (
                f"<empty completion: prompt_tokens={prompt_length}, "
                f"completion_tokens={completion_tokens}, stripped={completion_text!r}>"
            )
            print(f"[LlamaCppExtractor] warning: {diagnostic}", file=sys.stderr)
            return diagnostic

        return completion_text

    def close(self) -> None:
        """Explicitly free the llama.cpp model resources."""
        if hasattr(self, "_model") and self._model is not None:
            self._model.close()
            self._model = None  # type: ignore[assignment]


def _resolve_gguf_file(entry: ModelCatalogEntry, install_dir: Path) -> Path:
    """Resolves the single GGUF file an installed catalog entry declares -
    tied to the checksummed files the installer actually verified, rather
    than globbing the install directory for any file that happens to end in
    .gguf, which could pick up an unrelated leftover file."""
    gguf_files = [file for file in entry.files if file.path.endswith(".gguf")]
    if not gguf_files:
        raise FileNotFoundError(f"catalog entry {entry.id!r} declares no .gguf file")
    if len(gguf_files) > 1:
        # Multi-shard GGUF models aren't supported yet - out of scope for
        # the two single-file models this backend currently targets.
        raise ValueError(
            f"catalog entry {entry.id!r} declares {len(gguf_files)} .gguf files; "
            "only a single-file GGUF model is supported"
        )
    resolved = install_dir / gguf_files[0].path
    if not resolved.exists():
        raise FileNotFoundError(f"declared GGUF file missing from install dir: {resolved}")
    return resolved
