# Skeptic Review Fixes: Round 2

## Context

Second skeptic review found 21 remaining mixins, trivial tests, magic bytes security theater, and docs/code mismatch. All fixes are pre-release blockers.

## Changes

### 1. Raise file limit 500→800

Update Makefile, CLAUDE.md, pre-commit hooks, CI. This enables merging mixins back without artificial splits.

### 2. Eliminate ALL 21 remaining mixins

Merge small mixins into host classes (the 800 limit makes this possible):
- UnifiedSegmentAnalyzer: absorb SegmentScoringMixin + CandidateGenerationMixin
- ContentAnalyzer: absorb ContentParsingMixin (same file)
- VideoAnalysisCache: absorb DatabaseMigrationsMixin + DatabaseQueryMixin
- ClipExtractor: absorb ClipEncodingMixin
- TaichiTitleRenderer: absorb TaichiParticlesMixin + TaichiTextMixin
- TitleRenderer: absorb TextRenderingMixin
- AudioContentAnalyzer: absorb PANNsAnalysisMixin + EnergyAnalysisMixin
- RunDatabase: absorb RunQueriesMixin + ActiveJobsMixin

Target: `grep -rn "class.*Mixin" src/` returns 0.

### 3. Delete remaining trivial tests

- test_generators.py: constructor tests, ABC instantiation test, DummyGenerator boilerplate
- test_llm_config.py: Pydantic default tests
- test_scoring.py: mock call count assertions (replace with output assertions)
- test_pipeline_integration.py: callback "was called" assertion (assert values instead)

### 4. Delete security.py magic bytes

Files come from Immich (trusted source). Keep path validation and API key sanitization.

### 5. Delete split comments

Remove "Split from X to keep files under N lines" in config_models_extra.py and anywhere else.

### 6. Improve make critique

Add checks for: remaining mixins, mock-heavy tests (>3 mocks), files at 790-800 lines.

### 7. Fix docs/code mismatch

Update CLAUDE.md line limit from 500 to 800. Verify ARCHITECTURE.md matches reality.
