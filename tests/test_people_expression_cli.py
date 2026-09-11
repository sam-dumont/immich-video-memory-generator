"""Public grouped-person requests retain same-asset scope and identity."""

from datetime import date, datetime
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from immich_memories.api.models import AssetType, Person
from immich_memories.api.person_expression import PersonExpression
from immich_memories.automation.candidates import (
    CandidateCategory,
    MemoryCandidate,
    make_memory_key,
)
from immich_memories.automation.generation_request import GenerationRequest
from immich_memories.filename_builder import build_memory_output_path, build_output_filename
from immich_memories.timeperiod import DateRange
from tests.conftest import make_asset

TEXT = '("Adult A" OR "Adult B") AND "Child"'
EXPRESSION = PersonExpression.parse(TEXT)
PEOPLE = [
    Person(id="a", name="Adult A"),
    Person(id="a-hidden", name="Adult A"),
    Person(id="b", name="Adult B"),
    Person(id="c", name="Child"),
]


class Client:
    def __init__(self):
        self.calls = []
        self.roster_reads = []
        self.media = {}
        for media in ("VIDEO", "IMAGE"):
            rows = []
            for label, ids in (
                ("a-child", {"a", "c"}),
                ("hidden-child", {"a-hidden", "c"}),
                ("b-child", {"b", "c"}),
                ("child-only", {"c"}),
                ("adults-only", {"a", "b"}),
            ):
                asset = make_asset(f"{media}-{label}")
                asset = asset.model_copy(
                    update={"type": AssetType(media), "people": [p for p in PEOPLE if p.id in ids]}
                )
                rows.append(asset)
            self.media[media] = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get_all_people(self, *, with_hidden=False):
        self.roster_reads.append(with_hidden)
        return PEOPLE

    def selected(self, media, person):
        self.calls.append((media, person))
        return [row for row in self.media[media] if person in {p.id for p in row.people}]

    def get_videos_for_person_and_date_range(self, person_id, _window):
        return self.selected("VIDEO", person_id)

    def get_photos_for_date_range(self, _window, *, person_id=None, **_kwargs):
        return self.selected("IMAGE", person_id)


def invoke(tmp_path, args, client=None):
    from immich_memories.cli import main

    config = tmp_path / "config.yaml"
    config.write_text("immich:\n  url: http://immich.invalid\n  api_key: not-a-real-key\n")
    # WHY: fakes config-dir init, the Immich client, and the pipeline entry the CLI reaches.
    with (
        # WHY: init_config_dir would create a real config dir under the user's home.
        patch("immich_memories.cli.init_config_dir"),
        # WHY: SyncImmichClient is the Immich HTTP boundary; this returns the fake client.
        patch("immich_memories.api.immich.SyncImmichClient", return_value=client) as constructor,
        # WHY: the pipeline is a stand-in; the test inspects the kwargs it was handed.
        patch(
            "immich_memories.cli.generate.run_pipeline_and_generate",
            return_value=(tmp_path / "out.mp4", False, None),
        ) as pipeline,
    ):
        result = CliRunner().invoke(main, ["-c", str(config), "generate", "--quiet", *args])
    return result, constructor, pipeline


def test_public_cli_queries_every_face_identity_once_and_passes_the_name_tree(tmp_path):
    client = Client()
    result, _, pipeline = invoke(
        tmp_path,
        [
            "--memory-type",
            "multi_person",
            "--year",
            "2024",
            "--include-photos",
            "--no-live-photos",
            "--no-music",
            "--people-expression",
            TEXT,
        ],
        client,
    )
    assert result.exit_code == 0, (result.output, result.exception)
    kwargs = pipeline.call_args.kwargs
    assert {asset.id for asset in kwargs["assets"]} == {
        f"VIDEO-{label}" for label in ("a-child", "hidden-child", "b-child")
    }
    assert {asset.id for asset in kwargs["photo_assets"]} == {
        f"IMAGE-{label}" for label in ("a-child", "hidden-child", "b-child")
    }
    assert kwargs["memory_preset_params"]["person_expression"] == EXPRESSION.to_dict()
    assert kwargs["person_names"] == list(EXPRESSION.leaf_values)
    assert client.roster_reads == [True]
    assert len(client.calls) == len(set(client.calls)) == 8


@pytest.mark.parametrize("explicit_type", [False, True])
def test_grouped_people_custom_dates_reach_the_same_source_route_without_a_year(
    tmp_path, explicit_type
):
    client = Client()
    windows = []
    original = client.get_videos_for_person_and_date_range

    def record_window(person_id, window):
        windows.append(window)
        return original(person_id, window)

    client.get_videos_for_person_and_date_range = record_window
    result, _, pipeline = invoke(
        tmp_path,
        [
            "--start",
            "2024-01-01",
            "--end",
            "2024-06-30",
            "--people-expression",
            TEXT,
            "--duration",
            "90",
            "--include-photos",
            "--include-live-photos",
            "--no-render",
            "--no-music",
            *(["--memory-type", "multi_person"] if explicit_type else []),
        ],
        client,
    )

    assert result.exit_code == 0, (result.output, result.exception)
    expected = DateRange(datetime(2024, 1, 1), datetime(2024, 6, 30, 23, 59, 59))
    kwargs = pipeline.call_args.kwargs
    assert kwargs["memory_type"] == "multi_person"
    assert kwargs["date_range"] == expected
    assert kwargs["date_ranges"] == [expected]
    assert windows and all(window == expected for window in windows)
    assert kwargs["memory_preset_params"]["person_expression"] == EXPRESSION.to_dict()
    assert {asset.id for asset in kwargs["assets"]} == {
        f"VIDEO-{label}" for label in ("a-child", "hidden-child", "b-child")
    }
    assert {asset.id for asset in kwargs["photo_assets"]} == {
        f"IMAGE-{label}" for label in ("a-child", "hidden-child", "b-child")
    }
    assert kwargs["duration"] == 90
    assert kwargs["no_render"] is True


@pytest.mark.parametrize("text", ['"Adult A"', '"Adult A" OR "Adult A"'])
@pytest.mark.parametrize(
    "date_options,expected_type",
    [
        ([], "multi_person"),
        (["--month", "3"], "monthly_highlights"),
        (["--memory-type", "year_in_review"], "year_in_review"),
    ],
)
def test_single_leaf_expression_keeps_supported_inferred_route(
    tmp_path, text, date_options, expected_type
):
    client = Client()
    result, _, pipeline = invoke(
        tmp_path,
        [
            "--year",
            "2024",
            "--no-live-photos",
            "--no-music",
            "--people-expression",
            text,
            *date_options,
        ],
        client,
    )
    assert result.exit_code == 0, (result.output, result.exception)
    kwargs = pipeline.call_args.kwargs
    assert kwargs["memory_type"] == expected_type
    assert (
        kwargs["memory_preset_params"]["person_expression"]
        == PersonExpression.parse(text).to_dict()
    )
    assert kwargs["person_names"] == ["Adult A"]
    assert {asset.id for asset in kwargs["assets"]} == {
        "VIDEO-a-child",
        "VIDEO-hidden-child",
        "VIDEO-adults-only",
    }


@pytest.mark.parametrize(
    "names,expected_type",
    [(["Adult A"], "person_spotlight"), (["Adult A", "Child"], "multi_person")],
)
def test_flat_person_cli_inference_is_unchanged(tmp_path, names, expected_type):
    class FlatClient(Client):
        def get_person_by_name(self, name):
            return next(person for person in PEOPLE if person.name == name)

        def get_videos_for_all_persons(self, person_ids, _window):
            return [
                row
                for row in self.media["VIDEO"]
                if set(person_ids).issubset(person.id for person in row.people)
            ]

    result, _, pipeline = invoke(
        tmp_path,
        [
            "--year",
            "2024",
            "--no-live-photos",
            "--no-music",
            *(argument for name in names for argument in ("--person", name)),
        ],
        FlatClient(),
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert pipeline.call_args.kwargs["memory_type"] == expected_type
    assert pipeline.call_args.kwargs["memory_preset_params"].get("person_expression") is None


@pytest.mark.parametrize(
    "extra",
    [
        ["--person", "Adult A"],
        ["--person-match", "or"],
        ["--memory-type", "trip"],
        ["--memory-type", "person_spotlight"],
        ["--from-album", "An album"],
        ["--birthday"],
    ],
)
def test_ambiguous_or_unsupported_cli_scope_fails_before_client(tmp_path, extra):
    result, client, pipeline = invoke(
        tmp_path, ["--year", "2024", "--people-expression", TEXT, *extra]
    )
    assert result.exit_code != 0
    assert "Grouped people" in result.output or "separately" in result.output
    client.assert_not_called()
    pipeline.assert_not_called()


@pytest.mark.parametrize(
    "category",
    [
        CandidateCategory.MULTI_PERSON,
        CandidateCategory.MONTHLY_REVIEW,
        CandidateCategory.YEAR_IN_REVIEW,
        CandidateCategory.ON_THIS_DAY,
    ],
)
def test_automation_preserves_expression_as_one_shell_safe_argument(category):
    candidate = MemoryCandidate(
        "multi_person",
        category,
        date(2024, 1, 1),
        date(2024, 12, 31),
        list(EXPRESSION.leaf_values),
        "prior-key",
        0.8,
        "People together",
        10,
        {"person_expression": EXPRESSION.to_dict()},
    )
    request = GenerationRequest.from_candidate(candidate, upload=False)
    argv = request.to_argv()
    expression_arg = next(arg for arg in argv if arg.startswith("--people-expression="))
    assert PersonExpression.parse(expression_arg.split("=", 1)[1]) == EXPRESSION
    assert not any(arg.startswith("--person=") for arg in argv)
    assert request.memory_key != "prior-key"


def test_different_groupings_cannot_collide_in_names_or_automation_keys(tmp_path):
    other = PersonExpression.parse('"Adult A" OR ("Adult B" AND "Child")')
    window = DateRange(datetime(2024, 1, 1), datetime(2024, 12, 31, 23, 59, 59))
    common = {
        "output_dir": tmp_path,
        "person_names": list(EXPRESSION.leaf_values),
        "memory_type": "multi_person",
        "date_range": window,
        "container": "mp4",
    }
    assert build_memory_output_path(**common, person_expression=EXPRESSION) != (
        build_memory_output_path(**common, person_expression=other)
    )
    names = list(EXPRESSION.leaf_values)
    assert make_memory_key(
        "multi_person", window.start.date(), window.end.date(), names, person_expression=EXPRESSION
    ) != make_memory_key(
        "multi_person", window.start.date(), window.end.date(), names, person_expression=other
    )
    filenames = [
        build_output_filename(
            "multi_person",
            {"person_names": names, "person_expression": expr.to_dict()},
            None,
            window.start.date(),
            window.end.date(),
        )
        for expr in (EXPRESSION, other)
    ]
    assert filenames[0] != filenames[1]
