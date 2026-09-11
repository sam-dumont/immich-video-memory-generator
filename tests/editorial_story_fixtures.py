"""Controlled story-first judgments shared by production-route safeguard tests."""

import json
import re

from tests.test_editorial_story_first_planner import StoryJudge


class ControlledStoryJudge(StoryJudge):
    """Declare the first offered occasion central and keep each source as a moment."""

    def answer(self, stage, prompt):
        raw = super().answer(stage, prompt)
        if stage.startswith("story-understanding"):
            result = json.loads(raw)
            result["about"] = result["stories"][0]["episodes"]
            return json.dumps(result)
        return raw


class AnnualStoryJudge(ControlledStoryJudge):
    """Separate each offered occasion and prefer the second occasion in each year."""

    def answer(self, stage, prompt):
        if stage.startswith("story-episodes"):
            rows = json.JSONDecoder().raw_decode(
                prompt.split("NEW FRAGMENTS TO PLACE", 1)[1].lstrip()
            )[0]
            first = int(re.search(r"Number new episodes S(\d+)", prompt)[1])
            keys = [f"S{first + index:04d}" for index in range(len(rows))]
            return json.dumps(
                {
                    "fragments": [
                        {"reading": row["reading"], "episode": key}
                        for row, key in zip(rows, keys, strict=True)
                    ],
                    "new_episodes": [
                        {
                            "id": key,
                            "title": f"Game occasion {key}",
                            "account": "People play a game.",
                            "role": "supporting",
                        }
                        for key in keys
                    ],
                }
            )
        if stage.startswith("story-weighing"):
            keys = re.findall(r"^(K\d{2}) \|", prompt, re.MULTILINE)
            return json.dumps(
                {
                    "about": [],
                    "weights": {key: "major" if int(key[1:]) % 2 == 0 else "minor" for key in keys},
                }
            )
        if stage.startswith("story-pick-"):
            count = int(re.search(r"gets (\d+) picture", prompt)[1])
            labels = re.findall(r"^(M\d{2}) \|", prompt, re.MULTILINE)
            return json.dumps({"keep": labels[-count:]})
        raw = super().answer(stage, prompt)
        if stage.startswith("story-understanding"):
            result = json.loads(raw)
            result["about"] = []
            return json.dumps(result)
        return raw
