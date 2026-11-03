"""Global/preset layering, portable imports and atomic conflict handling."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from langtextflow import main
from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.glossary_transfer import (
    GlossaryImportRequest,
    GlossaryTransferFormat,
    export_glossary,
    parse_glossary_import,
)
from langtextflow.models import GlossaryEntry, ProductPreset, SessionContext, StartSessionRequest

CHURCH = ProductPreset.CHURCH
LECTURE = ProductPreset.LECTURE
CONFERENCE = ProductPreset.CONFERENCE


@pytest.fixture
def repository(tmp_path):
    repo = GlossaryRepository(str(tmp_path / "glossary.db"))
    repo.initialize()
    return repo


def term(presets=(), *, text="Grace", source="en", enabled=True, translation="global"):
    return GlossaryEntry(
        term=text,
        source_language=source,
        presets=list(presets),
        enabled=enabled,
        aliases=[f"{translation} alias"],
        translations={"ko": translation},
    )


def test_global_and_disjoint_presets_coexist_with_source_isolation(repository):
    repository.create(term())
    church = repository.create(term([CHURCH], text="grace", translation="church"))
    repository.create(term([LECTURE, CONFERENCE], translation="academic"))
    repository.create(term([CHURCH], source="ja", translation="Japanese source"))

    assert [x.translations["ko"] for x in repository.active_for(CHURCH, "en")] == ["church"]
    assert repository.active_for(CHURCH, "en")[0].aliases == church.aliases
    assert [x.translations["ko"] for x in repository.active_for(LECTURE, "en")] == ["academic"]
    assert [x.translations["ko"] for x in repository.active_for(ProductPreset.GENERAL, "en")] == [
        "global"
    ]
    assert repository.active_for(CHURCH, "ko") == []
    assert [x.translations["ko"] for x in repository.active_for(CHURCH, "ja")] == [
        "Japanese source"
    ]


@pytest.mark.parametrize(
    "scopes", [([], []), ([CHURCH], [CHURCH]), ([CHURCH, LECTURE], [LECTURE, CONFERENCE])]
)
def test_create_rejects_only_global_duplicates_or_intersecting_presets(repository, scopes):
    repository.create(term(scopes[0], text="Straße"))
    with pytest.raises(ValueError, match="duplicate glossary term"):
        repository.create(term(scopes[1], text="STRASSE", enabled=False))
    assert len(repository.list()) == 1


@pytest.mark.parametrize(
    "global_enabled,preset_enabled,expected",
    [
        (True, False, []),
        (False, True, ["church"]),
        (False, False, []),
    ],
)
def test_enabled_filter_runs_after_preset_override(
    repository, global_enabled, preset_enabled, expected
):
    repository.create(term(enabled=global_enabled))
    repository.create(term([CHURCH], enabled=preset_enabled, translation="church"))
    assert [x.translations["ko"] for x in repository.active_for(CHURCH, "en")] == expected
    assert [x.translations["ko"] for x in repository.active_for(LECTURE, "en")] == (
        ["global"] if global_enabled else []
    )


def test_scope_update_conflict_is_atomic_and_ids_survive(repository):
    global_row = repository.create(term())
    church = repository.create(term([CHURCH], translation="church"))
    lecture = repository.create(term([LECTURE], translation="lecture"))
    before = repository.list()
    with pytest.raises(ValueError, match="duplicate glossary term"):
        repository.update(lecture.id, term([CHURCH, LECTURE], translation="conflicting"))
    assert repository.list() == before
    updated = repository.update(lecture.id, term([CONFERENCE], translation="conference"))
    assert updated.id == lecture.id
    assert updated.created_at == lecture.created_at
    assert repository.get(global_row.id) == global_row
    assert repository.get(church.id) == church


def test_editing_between_global_and_disjoint_scopes_preserves_record_identity(repository):
    global_row = repository.create(term())
    church = repository.create(term([CHURCH], translation="church"))
    moved = repository.update(global_row.id, term([LECTURE], translation="lecture"))
    promoted = repository.update(church.id, term(translation="new global"))
    assert moved.id == global_row.id and moved.created_at == global_row.created_at
    assert promoted.id == church.id and promoted.created_at == church.created_at
    assert repository.active_for(CHURCH, "en")[0].translations["ko"] == "new global"
    assert repository.active_for(LECTURE, "en")[0].translations["ko"] == "lecture"
    before = repository.list()
    with pytest.raises(ValueError, match="duplicate glossary term"):
        repository.update(moved.id, term())
    assert repository.list() == before


def test_seed_preserves_any_existing_scope_and_disabled_operator_choice(repository):
    existing = repository.create(term([LECTURE], enabled=False, translation="operator"))
    repository.seed([term([CHURCH], text="grace", translation="seed")])
    repository.seed([term([CHURCH], translation="seed again")])
    assert repository.list() == [existing]
    assert repository.active_for(CHURCH, "en") == []


def test_active_snapshot_order_and_winners_do_not_depend_on_insertion_order(tmp_path):
    entries = [
        term(),
        term([CHURCH], text="grace", translation="church"),
        term(text="Alpha"),
        term(text="Zulu"),
    ]
    snapshots = []
    for index, rows in enumerate([entries, list(reversed(entries))]):
        repo = GlossaryRepository(str(tmp_path / f"order-{index}.db"))
        repo.initialize()
        for entry in rows:
            repo.create(entry)
        snapshots.append(repo.active_for(CHURCH, "en"))
    assert snapshots[0] == snapshots[1]
    assert [x.term for x in snapshots[0]] == ["Alpha", "Zulu", "grace"]


def test_upsert_identity_includes_sorted_scopes_and_preserves_other_layers(repository):
    global_row = repository.create(term())
    preset = repository.create(term([CHURCH, LECTURE], translation="old"))
    assert repository.import_entries(
        [
            term([LECTURE, CHURCH], text="GRACE", translation="new"),
            term([CONFERENCE], translation="conference"),
        ]
    ) == (1, 1, 0)
    assert repository.get(preset.id).translations == {"ko": "new"}
    assert repository.get(preset.id).created_at == preset.created_at
    assert repository.get(global_row.id) == global_row
    assert repository.import_entries(
        [term([CHURCH, LECTURE], translation="ignored")], conflict_policy="skip"
    ) == (0, 0, 1)
    assert repository.get(preset.id).translations == {"ko": "new"}


@pytest.mark.parametrize("first,second", [([], [CHURCH]), ([CHURCH], [])])
def test_skip_policy_creates_a_different_layer_instead_of_skipping_the_term(
    repository, first, second
):
    original = repository.create(term(first))
    assert repository.import_entries(
        [term(second, translation="new layer")], conflict_policy="skip"
    ) == (1, 0, 0)
    assert repository.get(original.id) == original
    assert len(repository.list()) == 2


@pytest.mark.parametrize("policy", ["upsert", "skip"])
def test_overlapping_different_scope_rolls_back_the_whole_import(repository, policy):
    repository.create(term())
    repository.create(term([CHURCH], translation="church"))
    before = repository.list()
    with pytest.raises(ValueError, match="overlapping preset scopes"):
        repository.import_entries(
            [
                term(translation="must not replace global"),
                term(text="new term"),
                term([CHURCH, LECTURE], translation="must not replace church"),
            ],
            conflict_policy=policy,
        )
    assert repository.list() == before
    assert repository.create(term(text="after failed import"))


@pytest.mark.parametrize("fmt", list(GlossaryTransferFormat))
def test_scoped_json_csv_round_trip_and_reimport_preserve_records(repository, tmp_path, fmt):
    rows = [term(), term([CHURCH], translation="church"), term([LECTURE], enabled=False)]
    for row in rows:
        repository.create(row)
    parsed = parse_glossary_import(
        GlossaryImportRequest(format=fmt, content=export_glossary(repository.list(), fmt))
    )
    target = GlossaryRepository(str(tmp_path / f"{fmt.value}.db"))
    target.initialize()
    assert target.import_entries(parsed) == (3, 0, 0)
    originals = {tuple(x.presets): x for x in target.list()}
    assert target.import_entries(list(reversed(parsed))) == (0, 3, 0)
    target.initialize()
    assert {tuple(x.presets): x.id for x in target.list()} == {
        scope: x.id for scope, x in originals.items()
    }
    assert target.active_for(CHURCH, "en") == repository.active_for(CHURCH, "en")
    assert target.active_for(LECTURE, "en") == []


def test_import_rejects_reordered_duplicate_scopes_and_payload_overlap(repository):
    for rows, message in [
        ([term([CHURCH, LECTURE]), term([LECTURE, CHURCH])], "duplicate term"),
        ([term([CHURCH]), term([CHURCH, LECTURE])], "overlapping preset scopes"),
    ]:
        with pytest.raises(ValueError, match=message):
            repository.import_entries(rows)
        assert repository.list() == []
        with pytest.raises(ValueError, match=message):
            parse_glossary_import(
                GlossaryImportRequest(
                    format="json",
                    content=json.dumps([row.model_dump(mode="json") for row in rows]),
                )
            )


def test_actual_session_merge_uses_preset_precedence_before_project_overrides(
    repository, monkeypatch
):
    repository.create(term())
    repository.create(term([CHURCH], translation="church"))
    repository.create(term([LECTURE], enabled=False))
    monkeypatch.setattr(main, "glossary_repository", repository)
    for preset, expected in [(CHURCH, ["church"]), (LECTURE, [])]:
        snapshot = main._with_saved_glossary(
            StartSessionRequest(
                source_language="en",
                target_languages=["ko"],
                context=SessionContext(preset=preset),
            )
        )
        assert [x.translations["ko"] for x in snapshot.context.glossary] == expected


def test_concurrent_overlapping_creates_cannot_both_pass_validation(repository):
    barrier = Barrier(2)

    def create(text):
        barrier.wait(timeout=5)
        try:
            return repository.create(term([CHURCH], text=text)).id
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(create, ["Straße", "STRASSE"]))
    assert sum(result is not None for result in results) == 1
    assert len(repository.list()) == 1
