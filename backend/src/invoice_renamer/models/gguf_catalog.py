"""The app's real (non-placeholder) GGUF model list, used by the llama.cpp
runtime in place of catalog_data.py's Transformers/safetensors entries.

Every field below was independently verified against the live Hugging Face
API and by downloading and re-hashing each file locally, not invented or
copied from anywhere in the plan that introduced this module; re-verify
against the repository before bumping a revision. Each entry's GGUF file
and its tokenizer/chat-template files are pinned to two separate sources
(see ModelFile.repository/revision in catalog.py): no existing GGUF release
for either model also ships the tokenizer/template assets
transformers.AutoTokenizer needs. For Granite, the tokenizer files are
pinned to the same base-model repository/revision already used by that
model's Transformers entry in catalog_data.py. Llama 3.2's real base-model
repository (meta-llama/Llama-3.2-3B-Instruct) is gated and needs an
accepted license + HF auth token this app's installer doesn't support, so
its tokenizer files are pinned to Unsloth AI's ungated full-precision
mirror instead - see that entry's own comments. Granite's description is
carried over from catalog_data.py's own benchmark-backed text (GGUF
quantization itself hasn't been separately benchmarked). Llama 3.2's
description reflects the user's own side-by-side comparison of the two GGUF
entries (Granite ahead); not yet written up in docs/model_benchmark_findings.md.
"""

from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile

GRANITE_3_3_2B_INSTRUCT_GGUF = ModelCatalogEntry(
    id="granite-3.3-2b-instruct",
    display_name="Granite 3.3 2B Instruct",
    license="Apache 2.0",
    # ibm-granite's own first-party GGUF conversion of their own model -
    # preferred over a third-party quantizer for provenance.
    repository="ibm-granite/granite-3.3-2b-instruct-GGUF",
    revision="7cdf86ccd1f1bb3491c9b7017b033f2e51367397",
    memory_tier=MemoryTier.SMALL,
    prompt_template="granite-instruct",
    # 16384 (native support: 131072). KV cache cost measured directly
    # against this real GGUF file (llama-cpp-python's own load log, not just
    # computed): 320 MiB at n_ctx=4096, scaling linearly to ~1.25 GiB at
    # 16384 - well inside this entry's 8 GB memory_tier floor alongside the
    # ~1.5 GB Q4_K_M weights. Chosen after a real invoice's extraction
    # prompt alone hit ~3.7k tokens against the previous shared n_ctx=4096
    # placeholder; 16384 leaves several times that much headroom for a
    # larger invoice plus a JSON-repair round (which resends the original
    # prompt once, not twice - see build_repair_prompt in prompts.py).
    context_size=16384,
    description="Better extraction accuracy in our benchmarks",
    files=[
        ModelFile(
            path="granite-3.3-2b-instruct-Q4_K_M.gguf",
            sha256="ac71e9e32c0bea919b409c5918f69ca74339854b0319c5065e4e9fb6d95c4852",
            size_bytes=1_545_303_328,
        ),
        # Tokenizer/chat-template assets: no GGUF release of this model ships
        # these, so they're pinned to the same base-model repository/revision
        # already verified for GRANITE_3_3_2B_INSTRUCT in catalog_data.py.
        ModelFile(
            path="tokenizer.json",
            sha256="91168e938f05796aa6dcca7e485e4b30ab52785320c7a6391ecef86e6c84681e",
            size_bytes=3_476_578,
            repository="ibm-granite/granite-3.3-2b-instruct",
            revision="707f574c62054322f6b5b04b6d075f0a8f05e0f0",
        ),
        ModelFile(
            path="tokenizer_config.json",
            sha256="f65a6a5a911424c85f157c40cfbdf06e025814c755480ba2e998d7fba1178664",
            size_bytes=9_930,
            repository="ibm-granite/granite-3.3-2b-instruct",
            revision="707f574c62054322f6b5b04b6d075f0a8f05e0f0",
        ),
        ModelFile(
            path="merges.txt",
            sha256="303127a244b0078878156c17229f36d11b7a3a3f8e47b7cfdbb304ff46be5030",
            size_bytes=441_810,
            repository="ibm-granite/granite-3.3-2b-instruct",
            revision="707f574c62054322f6b5b04b6d075f0a8f05e0f0",
        ),
        ModelFile(
            path="vocab.json",
            sha256="80ab859339a2525fdfbda14bc39df02dffb824aefdaf86426217bbb146d17e01",
            size_bytes=776_995,
            repository="ibm-granite/granite-3.3-2b-instruct",
            revision="707f574c62054322f6b5b04b6d075f0a8f05e0f0",
        ),
        ModelFile(
            path="special_tokens_map.json",
            sha256="21ce694081bb9ae1bd4bc64549e72e0799ebb74705e6b650e3585d85b71ebdc1",
            size_bytes=801,
            repository="ibm-granite/granite-3.3-2b-instruct",
            revision="707f574c62054322f6b5b04b6d075f0a8f05e0f0",
        ),
        ModelFile(
            path="added_tokens.json",
            sha256="bb33d55934aa82d29cc62f3d19cdbc60f315763f6ccee21bdfd8b3bde2f33d3b",
            size_bytes=207,
            repository="ibm-granite/granite-3.3-2b-instruct",
            revision="707f574c62054322f6b5b04b6d075f0a8f05e0f0",
        ),
    ],
)

LLAMA_3_2_3B_INSTRUCT_GGUF = ModelCatalogEntry(
    id="llama-3.2-3b-instruct",
    display_name="Llama 3.2 3B Instruct",
    # Meta's Llama 3.2 Community License, not Apache-2.0 like the other
    # entry - acceptable for this app's local, non-redistributed use, but a
    # real term difference worth keeping distinct from "Apache 2.0" here.
    license="Llama 3.2 Community License",
    # Meta doesn't publish a first-party GGUF; meta-llama/Llama-3.2-3B-Instruct
    # itself is a gated repository (requires an accepted license + HF auth
    # token this app's installer doesn't support), so both the GGUF weights
    # and the tokenizer/template mirror below are pinned to Unsloth AI's
    # ungated repositories instead - the same verified-HF-org standard
    # already used for Qwen3's GGUF in the entry this one replaces.
    repository="unsloth/Llama-3.2-3B-Instruct-GGUF",
    revision="e7d0997e49c9cb00d88b4c1a6a16aa894b0bbc31",
    memory_tier=MemoryTier.SMALL,
    prompt_template="llama3",
    # 16384 (native support: 131072). KV cache cost measured directly
    # against this real GGUF file: 1792 MiB at n_ctx=16384 (matches Qwen's
    # per-token cost exactly - both have 28 layers, 8 kv heads, head_dim
    # 128), well inside this entry's 8 GB memory_tier floor alongside the
    # ~1.9 GB Q4_K_M weights. See the Granite entry above for why 16384.
    context_size=16384,
    description="Alternative instruction-tuned model. Lower benchmarks. Experimental.",
    files=[
        ModelFile(
            path="Llama-3.2-3B-Instruct-Q4_K_M.gguf",
            sha256="6c99cc00ae910f6a532a80022cb4bc1939094527a089c29294b841c0bd87f74d",
            size_bytes=2_019_377_600,
        ),
        # Tokenizer/chat-template assets: no GGUF release of this model ships
        # these, and the real base-model repository (meta-llama/Llama-3.2-3B-
        # Instruct) is gated, so these are pinned to Unsloth's ungated
        # full-precision mirror of the same model instead. Llama 3's
        # tokenizer is tokenizer.json-only (no separate merges.txt/vocab.json
        # the way Qwen/Granite's GPT2-style tokenizers need) - confirmed
        # empirically, not assumed, by loading AutoTokenizer with exactly
        # this file set and nothing else.
        ModelFile(
            path="tokenizer.json",
            sha256="6b9e4e7fb171f92fd137b777cc2714bf87d11576700a1dcd7a399e7bbe39537b",
            size_bytes=17_209_920,
            repository="unsloth/Llama-3.2-3B-Instruct",
            revision="006f5dcd1393c3add266de40994ba96225e9689d",
        ),
        ModelFile(
            path="tokenizer_config.json",
            sha256="9ddd255c19fe319c8d4e891163540382e9fbda99f394674f2a929efc47d57458",
            size_bytes=54_669,
            repository="unsloth/Llama-3.2-3B-Instruct",
            revision="006f5dcd1393c3add266de40994ba96225e9689d",
        ),
        ModelFile(
            path="special_tokens_map.json",
            sha256="94e708c3f5e64acf85bbe5ad01467a1248faadb73e83b41793087ecced586e8f",
            size_bytes=454,
            repository="unsloth/Llama-3.2-3B-Instruct",
            revision="006f5dcd1393c3add266de40994ba96225e9689d",
        ),
    ],
)

SHORTLISTED_CATALOG: list[ModelCatalogEntry] = [
    GRANITE_3_3_2B_INSTRUCT_GGUF,
    LLAMA_3_2_3B_INSTRUCT_GGUF,
]
