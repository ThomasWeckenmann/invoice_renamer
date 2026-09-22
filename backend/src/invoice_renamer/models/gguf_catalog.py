"""Pinned GGUF models and their tokenizer assets offered by the app.
File hashes bind each download to verified upstream content.
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
    description="Good extraction accuracy in our benchmarks",
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

QWEN3_4B_INSTRUCT_2507_GGUF = ModelCatalogEntry(
    id="qwen3-4b-instruct-2507",
    display_name="Qwen3 4B Instruct 2507",
    license="Apache 2.0",
    repository="unsloth/Qwen3-4B-Instruct-2507-GGUF",
    revision="a06e946bb6b655725eafa393f4a9745d460374c9",
    memory_tier=MemoryTier.MEDIUM,
    prompt_template="chatml",
    # The 16K context allocates a 2304 MiB KV cache, in addition to weights
    # and runtime buffers. Use a conservative 16 GiB host-memory floor.
    context_size=16384,
    description="Experimental — not yet benchmarked for invoice extraction",
    files=[
        ModelFile(
            path="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            sha256="3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
            size_bytes=2_497_281_120,
        ),
        # Use Qwen's original tokenizer/template and retain its Apache license.
        ModelFile(
            path="tokenizer.json",
            sha256="aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
            size_bytes=11422654,
            repository="Qwen/Qwen3-4B-Instruct-2507",
            revision="cdbee75f17c01a7cc42f958dc650907174af0554",
        ),
        ModelFile(
            path="tokenizer_config.json",
            sha256="a62ff0a2472a0fa1b8eaabcb57c59b58afa42a22831dc141400b6e0cf2b65ce3",
            size_bytes=9377,
            repository="Qwen/Qwen3-4B-Instruct-2507",
            revision="cdbee75f17c01a7cc42f958dc650907174af0554",
        ),
        ModelFile(
            path="merges.txt",
            sha256="599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3",
            size_bytes=1671839,
            repository="Qwen/Qwen3-4B-Instruct-2507",
            revision="cdbee75f17c01a7cc42f958dc650907174af0554",
        ),
        ModelFile(
            path="vocab.json",
            sha256="ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
            size_bytes=2776833,
            repository="Qwen/Qwen3-4B-Instruct-2507",
            revision="cdbee75f17c01a7cc42f958dc650907174af0554",
        ),
        ModelFile(
            path="LICENSE",
            sha256="832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e",
            size_bytes=11343,
            repository="Qwen/Qwen3-4B-Instruct-2507",
            revision="cdbee75f17c01a7cc42f958dc650907174af0554",
        ),
    ],
)

SHORTLISTED_CATALOG: list[ModelCatalogEntry] = [
    GRANITE_3_3_2B_INSTRUCT_GGUF,
    QWEN3_4B_INSTRUCT_2507_GGUF,
]
