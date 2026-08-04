"""Regression check for the Kargo image-tag ordering contract."""

from pathlib import Path


CI_FILE = Path(__file__).parents[2] / ".gitlab-ci.yml"
LEGACY_TAG = "v1.0.0-build.52-667e382a"


def prerelease_key(tag: str) -> tuple[tuple[int, int | str], ...]:
    prerelease = tag.removeprefix("v1.0.0-")
    return tuple(
        (0, int(part)) if part.isdecimal() else (1, part)
        for part in prerelease.split(".")
    )


def test_pipeline_tag_outranks_legacy_tag() -> None:
    ci = CI_FILE.read_text(encoding="utf-8")
    expected_pattern = "v${VERSION}-build.z.${CI_PIPELINE_IID}.${CI_COMMIT_SHORT_SHA}"

    assert expected_pattern in ci
    candidate = "v1.0.0-build.z.101.f10c64de"
    assert prerelease_key(candidate) > prerelease_key(LEGACY_TAG)


if __name__ == "__main__":
    test_pipeline_tag_outranks_legacy_tag()
    print("PASS: Kargo will rank the pipeline tag above legacy tags")
