# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Tests for session_learner — specifically the learned-vocabulary read path.

Minimal regression coverage for the UTF-8 BOM class of bug fixed across the
voice package's vocab readers (vocabulary.py, analyzer.py, consolidator.py,
metrics.py, context_vocab.py). session_learner._write_learned merges INTO an
existing vocabulary_learned.json before writing; if that read silently comes
back empty on a BOM'd file, every previously learned correction is dropped
from disk with no error surfaced anywhere.
"""

import json

from contextpulse_voice import session_learner


class TestWriteLearnedSurvivesBom:
    def test_merges_with_bom_existing_file(self, tmp_path, monkeypatch):
        learned_file = tmp_path / "vocabulary_learned.json"
        learned_file.write_bytes(b"\xef\xbb\xbf" + json.dumps({"existing": "Existing"}).encode("utf-8"))

        monkeypatch.setattr(session_learner, "LEARNED_VOCAB_FILE", learned_file)
        monkeypatch.setattr(session_learner, "VOICE_DATA_DIR", tmp_path)

        session_learner._write_learned([{"original": "new term", "corrected": "NewTerm", "count": 3}])

        data = json.loads(learned_file.read_text(encoding="utf-8"))
        assert data.get("existing") == "Existing", "BOM'd pre-existing entry must survive the merge"
        assert data.get("new term") == "NewTerm"
