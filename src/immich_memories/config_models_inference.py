"""Where the application asks for image-model facts: in process, or the inference service."""

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

# The producers the service serves, in the order it serves them. "heads" is the
# DINOv2 encoder with the six public context heads; the other two are the detectors.
SERVED_PRODUCERS = ("heads", "nsfw_marqo", "doc_docling")


class InferenceConfig(BaseModel):
    facts_base_url: str = Field(
        default="",
        description="Inference service base URL; blank runs heads and detectors in process",
    )
    timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=300,
        description="Maximum wait for one remote picture-facts request",
    )
    producers: list[str] = Field(
        default_factory=lambda: list(SERVED_PRODUCERS),
        description="Which producers the service answers for; the rest run in process",
    )
    fallback_to_local: bool = Field(
        default=True,
        description="When the service cannot be reached, run the in-process producers instead",
    )

    @property
    def enabled(self) -> bool:
        return bool(self.facts_base_url)

    @field_validator("facts_base_url")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return ""
        try:
            url = urlsplit(value)
            valid = (
                url.scheme in {"http", "https"}
                and bool(url.hostname)
                and url.username is url.password is None
                and not url.query
                and not url.fragment
            )
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "facts_base_url needs an HTTP(S) URL without credentials, query or fragment"
            )
        return value

    @field_validator("producers")
    @classmethod
    def _served(cls, value: list[str]) -> list[str]:
        unknown = [name for name in value if name not in SERVED_PRODUCERS]
        if unknown:
            raise ValueError(
                f"unknown inference producers {unknown}; the service serves {list(SERVED_PRODUCERS)}"
            )
        if not value:
            raise ValueError("producers cannot be empty; leave facts_base_url blank instead")
        return list(dict.fromkeys(value))
