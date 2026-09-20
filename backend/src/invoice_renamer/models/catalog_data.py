"""The app's real (non-placeholder) open/local model shortlist.

Every field below is copied from Hugging Face's model API (config sha,
per-file LFS sha256/size) at the pinned revision, not invented; re-verify
against the repository before bumping a revision. The one exception is
description, a short qualitative comparison backed by
docs/model_benchmark_findings.md rather than repository metadata.
"""

from invoice_renamer.models.catalog import MemoryTier, ModelCatalogEntry, ModelFile

GRANITE_3_3_2B_INSTRUCT = ModelCatalogEntry(
    id="granite-3.3-2b-instruct",
    display_name="Granite 3.3 2B Instruct",
    license="Apache 2.0",
    repository="ibm-granite/granite-3.3-2b-instruct",
    revision="707f574c62054322f6b5b04b6d075f0a8f05e0f0",
    memory_tier=MemoryTier.SMALL,
    prompt_template="granite-instruct",
    description=(
        "Better extraction accuracy in our benchmarks, but slower inference and higher memory use"
    ),
    files=[
        ModelFile(
            path="added_tokens.json",
            sha256="bb33d55934aa82d29cc62f3d19cdbc60f315763f6ccee21bdfd8b3bde2f33d3b",
            size_bytes=207,
        ),
        ModelFile(
            path="config.json",
            sha256="9202d328d8368958aab7dad89a8e6aa35c250b3a2eedd2d8850ee0bcceca65f6",
            size_bytes=787,
        ),
        ModelFile(
            path="generation_config.json",
            sha256="9c95e80167f08fbeb1feba239e0749507c25d18ffab43ffa10887821eed21c38",
            size_bytes=132,
        ),
        ModelFile(
            path="merges.txt",
            sha256="303127a244b0078878156c17229f36d11b7a3a3f8e47b7cfdbb304ff46be5030",
            size_bytes=441_810,
        ),
        ModelFile(
            path="model-00001-of-00002.safetensors",
            sha256="12880d33c0ad4726af5cf8c07406905f9b496253c58ee46f52be8bde8ccf2254",
            size_bytes=4_999_999_840,
        ),
        ModelFile(
            path="model-00002-of-00002.safetensors",
            sha256="a8757c5bf7627933e7fddbd9bab0533491b4dc0962820e0617f356ca1a379ffa",
            size_bytes=67_121_712,
        ),
        ModelFile(
            path="model.safetensors.index.json",
            sha256="32ea3f438335d51c8e630a83898ceb23bdffd34291a0eade07474211928fa243",
            size_bytes=29_835,
        ),
        ModelFile(
            path="special_tokens_map.json",
            sha256="21ce694081bb9ae1bd4bc64549e72e0799ebb74705e6b650e3585d85b71ebdc1",
            size_bytes=801,
        ),
        ModelFile(
            path="tokenizer.json",
            sha256="91168e938f05796aa6dcca7e485e4b30ab52785320c7a6391ecef86e6c84681e",
            size_bytes=3_476_578,
        ),
        ModelFile(
            path="tokenizer_config.json",
            sha256="f65a6a5a911424c85f157c40cfbdf06e025814c755480ba2e998d7fba1178664",
            size_bytes=9_930,
        ),
        ModelFile(
            path="vocab.json",
            sha256="80ab859339a2525fdfbda14bc39df02dffb824aefdaf86426217bbb146d17e01",
            size_bytes=776_995,
        ),
    ],
)

QWEN3_0_6B = ModelCatalogEntry(
    id="qwen3-0.6b",
    display_name="Qwen3 0.6B",
    license="Apache 2.0",
    repository="Qwen/Qwen3-0.6B",
    revision="c1899de289a04d12100db370d81485cdf75e47ca",
    memory_tier=MemoryTier.SMALL,
    prompt_template="chatml",
    description="Faster inference and lower memory use",
    files=[
        ModelFile(
            path="config.json",
            sha256="660db3b73d788119c04535e48cf9be5f55bc3100841a718637ae695b442f27dd",
            size_bytes=726,
        ),
        ModelFile(
            path="generation_config.json",
            sha256="2325da0f15bb848e018c5ae071b7943332e9f871d6b60e2ed22ca97d4cb993d2",
            size_bytes=239,
        ),
        ModelFile(
            path="merges.txt",
            sha256="8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5",
            size_bytes=1_671_853,
        ),
        ModelFile(
            path="model.safetensors",
            sha256="f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b",
            size_bytes=1_503_300_328,
        ),
        ModelFile(
            path="tokenizer.json",
            sha256="aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
            size_bytes=11_422_654,
        ),
        ModelFile(
            path="tokenizer_config.json",
            sha256="d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101",
            size_bytes=9_732,
        ),
        ModelFile(
            path="vocab.json",
            sha256="ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
            size_bytes=2_776_833,
        ),
    ],
)

SHORTLISTED_CATALOG: list[ModelCatalogEntry] = [
    GRANITE_3_3_2B_INSTRUCT,
    QWEN3_0_6B,
]
