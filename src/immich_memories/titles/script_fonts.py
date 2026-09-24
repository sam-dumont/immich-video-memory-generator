"""The Noto faces for scripts the wheel does not carry, installed once, on request.

The wheel carries Montserrat and Noto Sans for Latin, Greek, Cyrillic and
Vietnamese. Arabic, Hebrew, the Indic scripts, Thai, Armenian, Georgian,
Ethiopic, CJK and the rest are 44 MB, most of it CJK, so they are installed by
`immich-memories titles fonts --install` (the Docker image runs it at build
time). Every file is pinned to a commit and a SHA-256: a download that hashes
to anything else is refused. A render never fetches a font; it draws with what
is on disk and says which step adds the rest.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# notofonts/notofonts.github.io, the Noto project's own build of every script
# family (fonts/<Family>/unhinted/ttf/). Bumping it means re-hashing every pin.
NOTO_COMMIT = "e0ad9f160a2942bcdd249bb1958e8336742edef3"
# notofonts/noto-cjk: one collection per weight holds JP, KR, SC, TC and HK.
NOTO_CJK_TAG = "Sans2.004"
FONT_HOST = "raw.githubusercontent.com"
FONTS_DIR_ENV = "IMMICH_MEMORIES_FONTS_DIR"


@dataclass(frozen=True)
class ScriptFont:
    """One pinned Noto file."""

    file: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        if self.file.startswith("NotoSansCJK"):
            return f"https://{FONT_HOST}/notofonts/noto-cjk/{NOTO_CJK_TAG}/Sans/OTC/{self.file}"
        family = self.file.split("-")[0]
        return (
            f"https://{FONT_HOST}/notofonts/notofonts.github.io/{NOTO_COMMIT}"
            f"/fonts/{family}/unhinted/ttf/{self.file}"
        )

    @property
    def bold(self) -> bool:
        return "-Bold." in self.file


def _pin(file: str, sha256: str, size: int) -> ScriptFont:
    return ScriptFont(file, sha256, size)


# CJK last: its collection also covers Latin and Greek, and a script face
# should win the letters it was drawn for.
SCRIPT_FONTS: tuple[ScriptFont, ...] = (
    _pin(
        "NotoSansArabic-Bold.ttf",
        "f4cb79f842a07d1bcf597879574358fb7010f73761396d1d0f2445dfedb4d2ca",
        141852,
    ),
    _pin(
        "NotoSansArabic-Regular.ttf",
        "bd86ca02f087d7f3c3788ba458fb6b73744c7639ed276b8d870dba6def6c40d0",
        142140,
    ),
    _pin(
        "NotoSansArmenian-Bold.ttf",
        "1dd206046cd3f41efddec93359a8d6590e5e2d5fcb036b9025725b560cefe6c6",
        18752,
    ),
    _pin(
        "NotoSansArmenian-Regular.ttf",
        "73bde9f3c63aa5ed39236e8a2837224605210a98b54d65724676c0119fcaa24c",
        18772,
    ),
    _pin(
        "NotoSansBengali-Bold.ttf",
        "2fe8d779e0f19576d1fc11e39f9154b4326a17b693215017dd1f5a8898c3f2c3",
        102440,
    ),
    _pin(
        "NotoSansBengali-Regular.ttf",
        "5dceda02816fece18ea6796f474f5d3170f1d862f21b1e809cdc94b1caa4b6ec",
        103704,
    ),
    _pin(
        "NotoSansDevanagari-Bold.ttf",
        "ff2f76a23aad41e0608c2d7dbc4bacd247ff3bec78f0ec2a8fb106b561636e58",
        183412,
    ),
    _pin(
        "NotoSansDevanagari-Regular.ttf",
        "9c7d935139ea6a1e6ad9dbac4f6d27ece1e04bca8123c8888d00a0f9df4724cd",
        184228,
    ),
    _pin(
        "NotoSansEthiopic-Bold.ttf",
        "6b06274ea730962522b1fcbfdcb16cb5a385aa36734f31f5c9d0ca910541e880",
        288828,
    ),
    _pin(
        "NotoSansEthiopic-Regular.ttf",
        "86b9c07c049e68438388d00a34033fa28cabeef91b26e1bbed67512d360166d4",
        289244,
    ),
    _pin(
        "NotoSansGeorgian-Bold.ttf",
        "517722454bbe6587ada79a0509db8c9b292b6a40b025eebe69c151a6508efc7e",
        35820,
    ),
    _pin(
        "NotoSansGeorgian-Regular.ttf",
        "f4b229b126859725b75031dd8c051335d20c85e4eb6d525af28f3e83e471baa4",
        35300,
    ),
    _pin(
        "NotoSansGujarati-Bold.ttf",
        "3ce0cf6e1d0bfe2ef5823772eeb2f25da28ec0c2c1d99c0fce529025723e61a1",
        140812,
    ),
    _pin(
        "NotoSansGujarati-Regular.ttf",
        "18727143b8eadade0a59dca80959ef784bf8d55e07285d1b4096594dbf829f0a",
        142760,
    ),
    _pin(
        "NotoSansGurmukhi-Bold.ttf",
        "d16804da302ecb2a7defe0502e65136870445fd88f9fddaef60b68d5f1ee7386",
        35712,
    ),
    _pin(
        "NotoSansGurmukhi-Regular.ttf",
        "2454c944fda799d2861f11c03e492c453e33083dd1e8d99bd6baac8de71ad0f0",
        35956,
    ),
    _pin(
        "NotoSansHebrew-Bold.ttf",
        "dfdb3056de1f4542b888c77a1a8a750548a802e271479f56e52152423b64dde8",
        16900,
    ),
    _pin(
        "NotoSansHebrew-Regular.ttf",
        "04272f5600d0ec816d31d0df73b23aa8d3501ea359ebe820da31c11ffcf00853",
        16836,
    ),
    _pin(
        "NotoSansKannada-Bold.ttf",
        "e989c72f6fec5a0b3366af5f90e3ce8b2b8a350fc5286c6374f0353937e513f5",
        133868,
    ),
    _pin(
        "NotoSansKannada-Regular.ttf",
        "2870274619c2a4abe5d6b11fcb7c4a372eba765b6b7881d459b0eb0a609a68eb",
        135488,
    ),
    _pin(
        "NotoSansKhmer-Bold.ttf",
        "817382a538f299eb809ee9313ef5c52e0049e3487eb4d02678349755ed607ddf",
        67976,
    ),
    _pin(
        "NotoSansKhmer-Regular.ttf",
        "60c10dbfae33a44f1897cd939789bd173aedb7c7dc0a9af389a737e33cb7e548",
        67784,
    ),
    _pin(
        "NotoSansLao-Bold.ttf",
        "6fea2e9f231a4b9cfdeec90e9afaff8818b3d11f74cdb23559275f71eeff1ffd",
        21508,
    ),
    _pin(
        "NotoSansLao-Regular.ttf",
        "d3976b0ac08702c54999ff42eae295d1597fd73b44e0c57d7f4917524e18afbe",
        21720,
    ),
    _pin(
        "NotoSansMalayalam-Bold.ttf",
        "165d635d82b0eb0a04275b92b9334c1ece679b99f263727c24c7c92ebcab51e9",
        75916,
    ),
    _pin(
        "NotoSansMalayalam-Regular.ttf",
        "cc39bb1f7d63b582a2aec1abf2469a43b805a3523a27e0a0eee8cd32702541d5",
        77312,
    ),
    _pin(
        "NotoSansMyanmar-Bold.ttf",
        "c40a000f8093de13bdf1379a7c589faa2ea989319f33f244eaaa907033c6889f",
        146412,
    ),
    _pin(
        "NotoSansMyanmar-Regular.ttf",
        "f4be6ef43871516d8347c5217a2033991b8ecda2dd1200007c2fb431a66d64db",
        148628,
    ),
    _pin(
        "NotoSansOriya-Bold.ttf",
        "1c575e8aba40d6baf200e8abac534e9517f431d38cc8131715643037eec149da",
        130104,
    ),
    _pin(
        "NotoSansOriya-Regular.ttf",
        "a16645d056017927406546aa78e4ce15e782fd8783467267b75450453d007415",
        131216,
    ),
    _pin(
        "NotoSansSinhala-Bold.ttf",
        "438ce1325e788ec0501042dd6e798c0ec02231a42dbf42e72d17b338aa46742a",
        88508,
    ),
    _pin(
        "NotoSansSinhala-Regular.ttf",
        "46d5b54952a624e6e3981e0968aecc5464f3b1081131dd7a1fcd42fbf7966471",
        90296,
    ),
    _pin(
        "NotoSansTamil-Bold.ttf",
        "04a472e49fa83b387976756554fe179de631a950d79f652ebba14a39a44f5d71",
        46428,
    ),
    _pin(
        "NotoSansTamil-Regular.ttf",
        "6634d9cc97a726e670df41281dd32b167a6b9b71f2036e19671ff08fdde0c292",
        46600,
    ),
    _pin(
        "NotoSansTelugu-Bold.ttf",
        "44393b05a863057cc2cbfe86623fc20b906b00cf19a2ae9304cdd0f3608889db",
        165564,
    ),
    _pin(
        "NotoSansTelugu-Regular.ttf",
        "dcb86a5da09365a2d73d725e429403dca988e6d50c1b1d564579a25dbc18bd29",
        165212,
    ),
    _pin(
        "NotoSansThaana-Bold.ttf",
        "476597290f449013cb07477b1089219db333c6af9ec0e436a74139ea9b2c8536",
        27000,
    ),
    _pin(
        "NotoSansThaana-Regular.ttf",
        "7543935bdcef770c9d3dd54222651b29040e5ec82f3bc58542392b2c7a9cbd3c",
        27308,
    ),
    _pin(
        "NotoSansThai-Bold.ttf",
        "5227602d7f9108252cfb7d75d6f7b2a26bdb2fc2fa89a702f9063758fb2f86c7",
        20600,
    ),
    _pin(
        "NotoSansThai-Regular.ttf",
        "d4303fe9c63ebb72759ca8b6d2040c8ae81689f7d08d7b91c656154382b49313",
        20960,
    ),
    _pin(
        "NotoSansCJK-Bold.ttc",
        "faa5f3656a78b2e2d450d27fe8382c778bc2b6bb5ea29c986664a6a435056ceb",
        20050760,
    ),
    _pin(
        "NotoSansCJK-Regular.ttc",
        "b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a",
        19484784,
    ),
)


def script_fonts_dir() -> Path:
    """Where `--install` writes: $IMMICH_MEMORIES_FONTS_DIR, else ~/.immich-memories/fonts/noto."""
    configured = os.environ.get(FONTS_DIR_ENV)
    if configured:
        return Path(configured)
    return Path.home() / ".immich-memories" / "fonts" / "noto"


def installed_script_fonts(*, bold: bool) -> tuple[Path, ...]:
    """The pinned faces of this weight that are on disk, in fallback order."""
    folder = script_fonts_dir()
    found = (folder / font.file for font in SCRIPT_FONTS if font.bold == bold)
    return tuple(path for path in found if path.is_file())


def install_script_fonts(
    dest: Path | None = None,
    *,
    fetch: Callable[[str], bytes] | None = None,
) -> list[str]:
    """Download every pinned face missing from `dest`; return the files written.

    A file already there with the right hash is kept. A download whose hash is
    not the pin raises ValueError and nothing is written for it, so a build
    that runs this fails instead of shipping a font nobody checked.
    """
    folder = dest or script_fonts_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fetch = fetch or _http_fetch
    written: list[str] = []
    for font in SCRIPT_FONTS:
        target = folder / font.file
        if target.is_file() and _sha256(target.read_bytes()) == font.sha256:
            continue
        content = fetch(font.url)
        if _sha256(content) != font.sha256:
            msg = f"{font.file} from {font.url} does not match its pinned SHA-256"
            raise ValueError(msg)
        partial = target.with_suffix(target.suffix + ".part")
        partial.write_bytes(content)
        partial.replace(target)
        written.append(font.file)
        logger.info("Installed %s (%d KB)", font.file, font.size // 1024)
    return written


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _http_fetch(url: str) -> bytes:
    import httpx

    response = httpx.get(url, follow_redirects=True, timeout=120.0)
    response.raise_for_status()
    return response.content
