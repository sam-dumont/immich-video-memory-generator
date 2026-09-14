# Read by Asset.model_dump in source_metadata_digest (analysis/
# editorial_bound_sample.py:15), which hashes the whole model to bind the source
# metadata Immich reported. Every field on Asset and on the models it nests is
# read there, and a field removed is a difference the evidence stops noticing.
# Nothing names them on the reading side, so vulture cannot see any of it.
f_number  # unused variable (src/immich_memories/api/models.py:60)
iso  # unused variable (src/immich_memories/api/models.py:61)
focal_length  # unused variable (src/immich_memories/api/models.py:62)
lens_model  # unused variable (src/immich_memories/api/models.py:69)
is_hidden  # unused variable (src/immich_memories/api/models.py:82)
updated_at  # unused variable (src/immich_memories/api/models.py:83)
objects  # unused variable (src/immich_memories/api/models.py:130)
device_id  # unused variable (src/immich_memories/api/models.py:139)
thumbhash  # unused variable (src/immich_memories/api/models.py:144)
file_modified_at  # unused variable (src/immich_memories/api/models.py:146)
updated_at  # unused variable (src/immich_memories/api/models.py:148)
is_trashed  # unused variable (src/immich_memories/api/models.py:157)
smart_info  # unused variable (src/immich_memories/api/models.py:168)

# An enum member resolved from the wire value rather than by name: parse_type
# does AssetType(v.upper()), so an Immich asset typed AUDIO round-trips as
# itself instead of collapsing into OTHER.
AUDIO  # unused variable (src/immich_memories/api/models.py:50)

# Rendered into the prompt the editorial model reads.
# render_person_period_facts does json.dumps(asdict(fact)) and that string is
# spliced into the judge line at editorial_structure_lines.py:118 via
# with_person_context. The model is the reader; no source line names the keys,
# and dropping one would silently change the cut.
person_token  # unused variable (src/immich_memories/analysis/editorial_person_period_facts.py:18)
current_relationship  # unused variable (src/immich_memories/analysis/editorial_person_period_facts.py:20)
first_library_month  # unused variable (src/immich_memories/analysis/editorial_person_period_facts.py:23)
sustained_onset_month  # unused variable (src/immich_memories/analysis/editorial_person_period_facts.py:24)
grounding_moment_ids  # unused variable (src/immich_memories/analysis/editorial_person_period_facts.py:25)

# onnxruntime SessionOptions attributes: set on the options object, then read
# by the C++ runtime when InferenceSession is constructed a line or two later.
_.intra_op_num_threads  # unused attribute (src/immich_memories/analysis/editorial_preparation_detectors.py:123)
_.intra_op_num_threads  # unused attribute (src/immich_memories/triage/encoder.py:125)
_.inter_op_num_threads  # unused attribute (src/immich_memories/triage/encoder.py:126)
_.graph_optimization_level  # unused attribute (src/immich_memories/triage/encoder.py:127)

# Protocol method parameter names on a stub whose body is `...`. A Protocol
# never references its own parameters, which is what trips vulture's
# unused-variable check. The shape mirrors onnxruntime's
# InferenceSession.run(output_names, input_feed), called positionally at :88.
input_feed  # unused variable (src/immich_memories/triage/encoder.py:33)
output_names  # unused variable (src/immich_memories/triage/encoder.py:33)

# conn.row_factory = sqlite3.Row -- an sqlite3.Connection attribute the stdlib
# C module reads when it materialises rows.
_.row_factory  # unused attribute (src/immich_memories/automation/notification_state.py:103)
_.row_factory  # unused attribute (src/immich_memories/automation/state_store.py:64)
_.row_factory  # unused attribute (src/immich_memories/cache/asset_score_cache.py:32)
_.row_factory  # unused attribute (src/immich_memories/cache/database.py:36)
_.row_factory  # unused attribute (src/immich_memories/operations/storage_report.py:36)
_.row_factory  # unused attribute (src/immich_memories/tracking/run_database.py:72)

# logging.lastResort, the stdlib module attribute callHandlers falls back to.
# Disabled while LiveDisplayLogHandler owns the terminal, restored afterwards.
_.lastResort  # unused attribute (src/immich_memories/logging_config.py:203)
_.lastResort  # unused attribute (src/immich_memories/logging_config.py:215)

# A field of a frozen dataclass, read through the generated __eq__ rather than
# by name: probe_cache.py:182 and :199 compare a freshly built ProbeKey against
# the cached one, which is what makes a changed mtime invalidate the entry.
mtime_ns  # unused variable (src/immich_memories/processing/probe_cache.py:27)

# pydantic-settings contract methods, called by the library and never by us.
# get_field_value is abstract on PydanticBaseSettingsSource, so without it the
# source cannot even be instantiated; settings_customise_sources is the hook
# BaseSettings.__init__ calls on every Config() to splice the YAML source in.
_.get_field_value  # unused method (src/immich_memories/config_loader.py:197)
_.settings_customise_sources  # unused method (src/immich_memories/config_loader.py:341)

# Reached only from checked-in developer scripts, which vulture does not scan:
# scripts/preview_trip_titles.py and scripts/demo_maps.py for the map frame,
# scripts/validate_local_audio.py for the stem check, and
# scripts/verify_hardware_encode.py for the assembly runner.
_.has_full_stems  # unused property (src/immich_memories/audio/music_generator_models.py:269)
_.run_ffmpeg_assembly  # unused method (src/immich_memories/processing/clip_encoder.py:314)
render_trip_map_frame  # unused function (src/immich_memories/titles/map_renderer.py:38)

# Reported to the owner rather than deleted. Each is either a seam only the
# tests use, or a wiring gap where the missing caller is the defect and removing
# the callee would cement it:
#   complete_run     the only writer of a successful run's LLM spend; the
#                    complete_artifact that replaced it never passes llm_metrics
#   cancel_run       the only writer of status "cancelled", which the CLI filter
#                    and the recovery page still expect to exist
#   validate_image_path  its video/audio twins are used in ~14 places while image
#                    paths are opened unvalidated; a gap, not dead weight
#   reranker_identity    provenance written at editorial_runtime_backend.py:204
#                    and recorded nowhere
#   response_sha256, unreadable_or_omitted_pages
#                    feed episode_diagnostics_sink, an optional callback that
#                    defaults to None and that only tests ever supply
#   get_active_display, render_final
#                    the public reads of state production reaches directly; the
#                    tests use them as the window onto it
#   reset_rate_limiter, reset_oidc_client
#                    clear module-global state so tests do not leak into each
#                    other; production never resets either
#   PACK_DIM         one consumer, tests/test_triage_engine.py:15
reranker_identity  # unused variable (src/immich_memories/analysis/editorial_structure_contract.py:158)
response_sha256  # unused variable (src/immich_memories/analysis/text_episode_answers.py:48)
unreadable_or_omitted_pages  # unused variable (src/immich_memories/analysis/text_episode_answers.py:49)
get_active_display  # unused function (src/immich_memories/cli/_helpers.py:38)
_.render_final  # unused method (src/immich_memories/cli/_live_display.py:343)
validate_image_path  # unused function (src/immich_memories/security.py:178)
_.complete_run  # unused method (src/immich_memories/tracking/run_tracker.py:212)
_.caption_provenance  # PreparationResult is serialized by dataclasses.asdict into the attempt snapshot.
_.cancel_run  # unused method (src/immich_memories/tracking/run_tracker.py:378)
PACK_DIM  # unused variable (src/immich_memories/triage/encoder.py:23)
reset_rate_limiter  # unused function (src/immich_memories/ui/auth.py:63)
reset_oidc_client  # unused function (src/immich_memories/ui/auth_oidc.py:144)
