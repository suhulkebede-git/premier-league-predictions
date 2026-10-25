import json
import tempfile
import unittest
from pathlib import Path

from plpredict.names import (
    Aliases,
    NameResolver,
    UnknownNameError,
    load_aliases,
    normalise,
    suggest,
)
from tests.helpers import CLUBS, test_aliases


class NormaliseTests(unittest.TestCase):
    def test_strips_accents_case_and_spaces(self):
        self.assertEqual(normalise("  Martin  Ødegaard "), "martin odegaard")
        self.assertEqual(normalise("João Pedro"), "joao pedro")

    def test_numbers_become_text(self):
        self.assertEqual(normalise(8.0), "8.0")

    def test_none_is_rejected(self):
        with self.assertRaises(ValueError):
            normalise(None)


class LoadAliasesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "aliases.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_keys_are_normalised(self):
        self.path.write_text(json.dumps({"clubs": {"  SPURS ": "Tottenham Hotspur"},
                                         "answers": {"Hell Nah": False}}))
        aliases = load_aliases(self.path)
        self.assertEqual(aliases.clubs, {"spurs": "Tottenham Hotspur"})
        self.assertEqual(aliases.answers, {"hell nah": False})
        self.assertEqual(aliases.players, {})

    def test_missing_file_raises(self):
        with self.assertLogs("plpredict.names", "ERROR"):
            with self.assertRaises(FileNotFoundError):
                load_aliases(self.path)

    def test_bad_json_raises_value_error(self):
        self.path.write_text("{not json")
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            load_aliases(self.path)

    def test_section_must_be_object(self):
        self.path.write_text(json.dumps({"clubs": ["Spurs"]}))
        with self.assertRaisesRegex(ValueError, "must be an object"):
            load_aliases(self.path)

    def test_answers_must_be_booleans(self):
        self.path.write_text(json.dumps({"answers": {"yes": "true"}}))
        with self.assertRaisesRegex(ValueError, "true or false"):
            load_aliases(self.path)

    def test_repo_alias_file_loads(self):
        repo_file = Path(__file__).resolve().parents[1] / "data" / "aliases.json"
        aliases = load_aliases(repo_file)
        self.assertEqual(aliases.clubs["manchester yanited"], "Manchester United")
        self.assertIs(aliases.answers["fuck no"], False)


class SuggestTests(unittest.TestCase):
    def test_close_typo(self):
        self.assertEqual(suggest("Fullham", CLUBS), "Fulham")

    def test_nothing_close(self):
        self.assertIsNone(suggest("Real Madrid", CLUBS))


class NameResolverTests(unittest.TestCase):
    def setUp(self):
        self.names = NameResolver(test_aliases(), CLUBS)

    def test_exact_club_any_case(self):
        self.assertEqual(self.names.club("  arsenal "), "Arsenal")

    def test_club_alias(self):
        self.assertEqual(self.names.club("Spurs"), "Tottenham Hotspur")

    def test_goalkeeper_counts_as_club(self):
        self.assertEqual(self.names.club("Raya"), "Arsenal")

    def test_unknown_club_is_collected_with_suggestion(self):
        with self.assertLogs("plpredict.names", "WARNING"):
            self.assertIsNone(self.names.club("Fullham"))
        self.assertEqual(self.names.unmapped, [("clubs", "Fullham", "Fulham")])

    def test_optional_club_miss_is_not_collected(self):
        self.assertIsNone(self.names.club("Burnley", required=False))
        self.assertEqual(self.names.unmapped, [])

    def test_alias_to_non_season_club_is_an_error(self):
        names = NameResolver(Aliases(clubs={"wolves": "Wolverhampton Wanderers"}), CLUBS)
        with self.assertRaisesRegex(ValueError, "not a season club"):
            names.club("Wolves")

    def test_duplicate_season_clubs_rejected(self):
        with self.assertRaises(ValueError):
            NameResolver(test_aliases(), ["Arsenal", "Arsenal"])

    def test_player_alias_and_canonical_spelling(self):
        self.assertEqual(self.names.person_name("Big Erl", "players"), "Erling Haaland")
        self.assertEqual(self.names.person_name("erling haaland", "players"), "Erling Haaland")

    def test_unknown_player_collected(self):
        with self.assertLogs("plpredict.names", "WARNING"):
            self.assertIsNone(self.names.person_name("Messi", "players"))
        self.assertEqual(self.names.unmapped[0][:2], ("players", "Messi"))

    def test_answers(self):
        self.assertIs(self.names.answer("Hell nah"), False)
        self.assertIs(self.names.answer("YES"), True)

    def test_unknown_answer_collected(self):
        with self.assertLogs("plpredict.names", "WARNING"):
            self.assertIsNone(self.names.answer("maybe"))

    def test_repeated_miss_listed_once(self):
        with self.assertLogs("plpredict.names", "WARNING"):
            self.names.club("Fullham")
            self.names.club("Fullham")
        self.assertEqual(len(self.names.unmapped), 1)

    def test_raise_lists_every_miss(self):
        with self.assertLogs("plpredict.names", "WARNING"):
            self.names.club("Fullham")
            self.names.answer("maybe")
        with self.assertRaises(UnknownNameError) as ctx:
            self.names.raise_if_unmapped()
        self.assertEqual(len(ctx.exception.unmapped), 2)
        self.assertIn("did you mean 'Fulham'", str(ctx.exception))

    def test_no_raise_when_all_mapped(self):
        self.names.club("Spurs")
        self.names.raise_if_unmapped()


if __name__ == "__main__":
    unittest.main()
