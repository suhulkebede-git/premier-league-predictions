import json
import tempfile
import unittest
from pathlib import Path

from plpredict.names import UnknownNameError
from plpredict.predictions import (
    InvalidPredictionError,
    load_predictions,
    open_sheet,
    parse_predictions,
    save_predictions,
)
from tests.helpers import CLUBS, DEFAULT_PICKS, test_aliases, write_sheet

ORDER_A = list(CLUBS)
ORDER_B = list(reversed(CLUBS))


class ParsePredictionsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "preds.xlsx"

    def tearDown(self):
        self.dir.cleanup()

    def parse(self, people, **kw):
        write_sheet(self.path, people, **kw)
        return parse_predictions(open_sheet(self.path), test_aliases())

    def test_parses_people_across_both_columns_and_bands(self):
        pset = self.parse([("ED", ORDER_A, None), ("LUKE", ORDER_B, None),
                           ("JACK", ORDER_A, None)])
        self.assertEqual([p.person for p in pset.predictions], ["ED", "LUKE", "JACK"])
        self.assertEqual(pset.predictions[1].order, tuple(ORDER_B))

    def test_blank_block_is_skipped(self):
        with self.assertLogs("plpredict.predictions", "INFO") as logs:
            pset = self.parse([("ABE", None, None), ("ED", ORDER_A, None)])
        self.assertEqual([p.person for p in pset.predictions], ["ED"])
        self.assertTrue(any("ABE" in line and "blank" in line for line in logs.output))

    def test_season_and_last_season_tables(self):
        pset = self.parse([("ED", ORDER_A, None)])
        self.assertEqual(pset.season_clubs, tuple(CLUBS))
        self.assertEqual(pset.promoted_clubs, ("Coventry City", "Hull City", "Ipswich Town"))
        self.assertEqual(pset.last_season_points["Arsenal"], 85)
        self.assertEqual(pset.last_season_points["AFC Bournemouth"], 57)
        self.assertNotIn("Burnley", pset.last_season_points)
        self.assertEqual(len(pset.last_season_points), 17)

    def test_award_picks_are_normalised(self):
        awards = self.parse([("ED", ORDER_A, None)]).predictions[0].awards
        self.assertEqual(len(awards), 20)
        self.assertEqual(awards["pfa_player"].value, "Bruno Fernandes")
        self.assertEqual(awards["pfa_player"].raw, "Bruno F")
        self.assertEqual(awards["first_sacked_manager"].value, "Sergej Jakirovic")
        self.assertEqual(awards["title_margin"].value, 6)
        self.assertEqual(awards["biggest_increase"].value, "Tottenham Hotspur")
        self.assertEqual(awards["most_clean_sheets"].value, "Arsenal")
        self.assertEqual(awards["golden_boot"].value, "Erling Haaland")
        self.assertIs(awards["relegation_final_day"].value, False)
        self.assertIs(awards["any_under_25"].value, True)

    def test_either_or_club_pick_keeps_second_as_alt(self):
        pick = self.parse([("UGWE", ORDER_A, None)]).predictions[0].awards["most_draws"]
        self.assertEqual(pick.value, "Crystal Palace")
        self.assertEqual(pick.alt, "Brighton & Hove Albion")
        self.assertIsNone(
            self.parse([("ED", ORDER_A, None)]).predictions[0].awards["pfa_player"].alt
        )

    def test_numeric_margin_cell(self):
        picks = list(DEFAULT_PICKS)
        picks[2] = 8.0
        awards = self.parse([("ED", ORDER_A, picks)]).predictions[0].awards
        self.assertEqual(awards["title_margin"].value, 8)

    def test_bad_margin_reported(self):
        picks = list(DEFAULT_PICKS)
        picks[2] = "about six"
        with self.assertRaisesRegex(InvalidPredictionError, "not a whole number"):
            self.parse([("ED", ORDER_A, picks)])

    def test_three_way_pick_reported(self):
        picks = list(DEFAULT_PICKS)
        picks[8] = "Leeds or Hull or Fulham"
        with self.assertRaisesRegex(InvalidPredictionError, "more than two clubs"):
            self.parse([("ED", ORDER_A, picks)])

    def test_unknown_names_all_reported_together(self):
        order = list(ORDER_A)
        order[9] = "Fullham"
        picks = list(DEFAULT_PICKS)
        picks[10] = "Messi"
        with self.assertLogs("plpredict.names", "WARNING"):
            with self.assertRaises(UnknownNameError) as ctx:
                self.parse([("ED", order, picks)])
        raw = {entry[1] for entry in ctx.exception.unmapped}
        self.assertEqual(raw, {"Fullham", "Messi"})

    def test_duplicate_club_reported(self):
        order = list(ORDER_A)
        order[1] = order[0]
        with self.assertRaisesRegex(InvalidPredictionError, "more than once: AFC Bournemouth"):
            self.parse([("ED", order, None)])

    def test_duplicate_person_reported(self):
        with self.assertRaisesRegex(InvalidPredictionError, "appears more than once"):
            self.parse([("ED", ORDER_A, None), ("ed", ORDER_B, None)])

    def test_missing_pick_reported(self):
        picks = list(DEFAULT_PICKS)
        picks[5] = None
        order = list(ORDER_A)
        write_sheet(self.path, [("ED", order, picks)])
        with self.assertRaisesRegex(InvalidPredictionError, "no pick for 'Club with Most Red"):
            parse_predictions(open_sheet(self.path), test_aliases())

    def test_no_people_reported(self):
        with self.assertRaisesRegex(InvalidPredictionError, "No filled-in"):
            self.parse([("ABE", None, None)])

    def test_season_club_list_must_be_complete(self):
        clubs = list(CLUBS)
        clubs[4] = None
        with self.assertRaisesRegex(InvalidPredictionError, "blank cells"):
            self.parse([("ED", ORDER_A, None)], season_clubs=clubs)

    def test_season_club_list_must_be_unique(self):
        clubs = list(CLUBS)
        clubs[4] = "Arsenal"
        with self.assertRaisesRegex(InvalidPredictionError, "duplicate"):
            self.parse([("ED", ORDER_A, None)], season_clubs=clubs)


class OpenSheetTests(unittest.TestCase):
    def test_missing_file(self):
        with self.assertLogs("plpredict.predictions", "ERROR"):
            with self.assertRaises(FileNotFoundError):
                open_sheet(Path("/nonexistent/preds.xlsx"))

    def test_not_an_xlsx(self):
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
            f.write(b"plain text")
            f.flush()
            with self.assertRaisesRegex(InvalidPredictionError, "Cannot open"):
                open_sheet(Path(f.name))


class SaveLoadTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        write_sheet(root / "p.xlsx", [("ED", ORDER_A, None), ("UGWE", ORDER_B, None)])
        self.pset = parse_predictions(open_sheet(root / "p.xlsx"), test_aliases())
        self.json_path = root / "out" / "predictions.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_round_trip(self):
        save_predictions(self.pset, self.json_path)
        self.assertEqual(load_predictions(self.json_path), self.pset)

    def test_missing_file(self):
        with self.assertLogs("plpredict.predictions", "ERROR"):
            with self.assertRaises(FileNotFoundError):
                load_predictions(self.json_path)

    def test_malformed_json(self):
        self.json_path.parent.mkdir(parents=True)
        self.json_path.write_text("[]")
        with self.assertRaisesRegex(InvalidPredictionError, "not a valid predictions file"):
            load_predictions(self.json_path)

    def test_order_must_cover_season_clubs(self):
        save_predictions(self.pset, self.json_path)
        data = json.loads(self.json_path.read_text())
        data["predictions"][0]["order"][0] = data["predictions"][0]["order"][1]
        self.json_path.write_text(json.dumps(data))
        with self.assertRaisesRegex(InvalidPredictionError, "not a permutation"):
            load_predictions(self.json_path)

    def test_awards_must_be_complete(self):
        save_predictions(self.pset, self.json_path)
        data = json.loads(self.json_path.read_text())
        del data["predictions"][1]["awards"]["golden_boot"]
        self.json_path.write_text(json.dumps(data))
        with self.assertRaisesRegex(InvalidPredictionError, "golden_boot"):
            load_predictions(self.json_path)

    def test_repo_predictions_file_is_valid(self):
        repo_file = Path(__file__).resolve().parents[1] / "data" / "predictions.json"
        pset = load_predictions(repo_file)
        self.assertEqual(len(pset.predictions), 9)


if __name__ == "__main__":
    unittest.main()
