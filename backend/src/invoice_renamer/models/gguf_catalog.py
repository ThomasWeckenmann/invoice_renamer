"""The app's real (non-placeholder) GGUF model list, used by the llama.cpp
runtime in place of catalog_data.py's Transformers/safetensors entries.

Every field below was independently verified against the live Hugging Face
API and by downloading and re-hashing each file locally, not invented or
copied from anywhere in the plan that introduced this module; re-verify
against the repository before bumping a revision. Each entry's GGUF file
and its tokenizer/chat-template files are pinned to two separate sources
(see ModelFile.repository/revision in catalog.py): no existing GGUF release
for either model also ships the tokenizer/template assets
transformers.AutoTokenizer needs, so the tokenizer files are pinned to the
same base-model repository/revision already used by that model's
Transformers entry in catalog_data.py. The one exception to "not invented"
is description, a short qualitative comparison carried over from
catalog_data.py's own benchmark-backed text rather than repository
metadata - GGUF quantization hasn't been separately benchmarked yet.
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
    description=(
        "Better extraction accuracy in our benchmarks, but slower inference and higher memory use"
    ),
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

QWEN3_0_6B_GGUF = ModelCatalogEntry(
    id="qwen3-0.6b",
    display_name="Qwen3 0.6B",
    license="Apache 2.0",
    # Qwen's own official GGUF repository only ships a single Q8_0 quant
    # (no Q4_K_M/Q5_K_M range); Unsloth AI (a verified HF organization,
    # widely used for GGUF quantization) is the reputable third-party
    # source used here instead.
    repository="unsloth/Qwen3-0.6B-GGUF",
    revision="50968a4468ef4233ed78cd7c3de230dd1d61a56b",
    memory_tier=MemoryTier.SMALL,
    prompt_template="chatml",
    description="Faster inference and lower memory use",
    files=[
        ModelFile(
            path="Qwen3-0.6B-Q4_K_M.gguf",
            sha256="ac2d97712095a558e31573f62f466a3f9d93990898b0ec79d7c974c1780d524a",
            size_bytes=396_705_472,
        ),
        # Tokenizer/chat-template assets: no GGUF release of this model ships
        # these, so they're pinned to the same base-model repository/revision
        # already verified for QWEN3_0_6B in catalog_data.py.
        ModelFile(
            path="tokenizer.json",
            sha256="aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
            size_bytes=11_422_654,
            repository="Qwen/Qwen3-0.6B",
            revision="c1899de289a04d12100db370d81485cdf75e47ca",
        ),
        ModelFile(
            path="tokenizer_config.json",
            sha256="d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101",
            size_bytes=9_732,
            repository="Qwen/Qwen3-0.6B",
            revision="c1899de289a04d12100db370d81485cdf75e47ca",
        ),
        ModelFile(
            path="merges.txt",
            sha256="8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5",
            size_bytes=1_671_853,
            repository="Qwen/Qwen3-0.6B",
            revision="c1899de289a04d12100db370d81485cdf75e47ca",
        ),
        ModelFile(
            path="vocab.json",
            sha256="ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
            size_bytes=2_776_833,
            repository="Qwen/Qwen3-0.6B",
            revision="c1899de289a04d12100db370d81485cdf75e47ca",
        ),
    ],
)

SHORTLISTED_CATALOG: list[ModelCatalogEntry] = [
    GRANITE_3_3_2B_INSTRUCT_GGUF,
    QWEN3_0_6B_GGUF,
]
