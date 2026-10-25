import json
import shutil
import tempfile
import unittest
from pathlib import Path

from plpredict.award_form import AwardFormError, apply_award, parse_issue_body, run_form
from plpredict.main import cli

REPO_DATA = Path(__file__).resolve().parents[1] / "data"


def body(award="PFA Player", winner="Bruno F", gameweek="", final="No", confirm=False):
    tick = "X" if confirm else " "
    return (f"### Award\n\n{award}\n\n### Winner\n\n{winner or '_No response_'}\n\n"
            f"### Gameweek it happened\n\n{gameweek or '_No response_'}\n\n"
            f"### Is this final?\n\n{final}\n\n"
            f"### Checks\n\n- [{tick}] The name is spelled correctly even though nobody picked it\n")


class ParseTests(unittest.TestCase):
    def test_reads_every_field(self):
        e = parse_issue_body(body(winner="Bruno F", gameweek="12", final="Yes - it's decided"))
        self.assertEqual((e.key, e.winners, e.gameweek, e.final, e.new_name_ok),
                         ("pfa_player", ("Bruno F",), 12, True, False))

    def test_ties_split_on_commas_and_and(self):
        e = parse_issue_body(body(award="Club with Most Red Cards", winner="Spurs, Leeds and Hull"))
        self.assertEqual(e.winners, ("Spurs", "Leeds", "Hull"))

    def test_none_clears(self):
        self.assertEqual(parse_issue_body(body(winner="None")).winners, ())

    def test_gameweek_accepts_gw_prefix(self):
        self.assertEqual(parse_issue_body(body(gameweek="GW 7")).gameweek, 7)

    def test_confirm_checkbox(self):
        self.assertTrue(parse_issue_body(body(confirm=True)).new_name_ok)

    def test_rejects_bad_input(self):
        for kwargs, fragment in [
            ({"award": "Golden Boot Winner"}, "Unknown award"),
            ({"winner": ""}, "Winner"),
            ({"gameweek": "39"}, "1 to 38"),
            ({"gameweek": "soon"}, "1 to 38"),
        ]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(AwardFormError, fragment):
                    parse_issue_body(body(**kwargs))

    def test_rejects_non_form_issue(self):
        with self.assertRaisesRegex(AwardFormError, "issue form"):
            parse_issue_body("Hey can someone add the PFA winner")


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.data = Path(self.dir.name) / "data"
        shutil.copytree(REPO_DATA, self.data)

    def tearDown(self):
        self.dir.cleanup()

    def awards(self):
        return json.loads((self.data / "manual_awards.json").read_text())

    def aliases(self):
        return json.loads((self.data / "aliases.json").read_text())

    def test_saves_alias_resolved_player(self):
        with self.assertLogs("plpredict", "INFO"):
            r = apply_award(parse_issue_body(body(winner="brunooooo", gameweek="38", final="Yes")), self.data)
        self.assertEqual(r.leaders, ("Bruno Fernandes",))
        self.assertEqual(self.awards()["pfa_player"],
                         {"leaders": ["Bruno Fernandes"], "settled": True, "from_gameweek": 38})
        self.assertIn("_comment", self.awards())  # other keys kept

    def test_club_tie_and_dedupe(self):
        with self.assertLogs("plpredict", "INFO"):
            apply_award(parse_issue_body(
                body(award="Club with Most Red Cards", winner="Spurs, Tottenham Hotspur, Leeds")), self.data)
        self.assertEqual(self.awards()["most_red_cards"]["leaders"], ["Tottenham Hotspur", "Leeds United"])

    def test_yes_no_award(self):
        with self.assertLogs("plpredict", "INFO"):
            apply_award(parse_issue_body(
                body(award="Will the relegation battle go to the final day?", winner="hell nah", final="Yes")),
                self.data)
        self.assertEqual(self.awards()["relegation_final_day"]["leaders"], [False])

    def test_typo_suggests_and_writes_nothing(self):
        before = (self.data / "manual_awards.json").read_text()
        with self.assertRaisesRegex(AwardFormError, r"did you mean \*\*Bukayo Saka\*\*"):
            apply_award(parse_issue_body(body(winner="Bukayo Sakka")), self.data)
        self.assertEqual((self.data / "manual_awards.json").read_text(), before)

    def test_unknown_club_rejected_even_when_confirmed(self):
        with self.assertRaisesRegex(AwardFormError, "isn't a club"):
            apply_award(parse_issue_body(
                body(award="Club with Most Red Cards", winner="Real Madrid", confirm=True)), self.data)

    def test_bad_yes_no_rejected(self):
        with self.assertRaisesRegex(AwardFormError, "yes/no"):
            apply_award(parse_issue_body(
                body(award="Will the relegation battle go to the final day?", winner="maybe")), self.data)

    def test_confirmed_new_manager_is_added_to_aliases(self):
        with self.assertLogs("plpredict", "INFO"):
            r = apply_award(parse_issue_body(body(award="First Sacked Manager", winner="Thomas Frank",
                                                  gameweek="9", confirm=True)), self.data)
        self.assertEqual(r.added_aliases, ("Thomas Frank",))
        self.assertEqual(self.aliases()["managers"]["thomas frank"], "Thomas Frank")
        self.assertEqual(self.awards()["first_sacked_manager"]["leaders"], ["Thomas Frank"])

    def test_clear_resets_award(self):
        with self.assertLogs("plpredict", "INFO"):
            apply_award(parse_issue_body(body(winner="Bruno F", gameweek="30")), self.data)
            r = apply_award(parse_issue_body(body(winner="none", gameweek="30", final="Yes")), self.data)
        self.assertEqual(r.leaders, ())
        self.assertEqual(self.awards()["pfa_player"],
                         {"leaders": [], "settled": False, "from_gameweek": None})

    def test_run_form_reply(self):
        with self.assertLogs("plpredict", "INFO"):
            ok, reply = run_form(body(award="Club with Most Red Cards", winner="Leeds"), self.data)
        self.assertTrue(ok)
        self.assertIn("Leeds United", reply)
        self.assertIn("overrides the automatic", reply)
        with self.assertLogs("plpredict", "WARNING"):
            ok, reply = run_form(body(winner="Nobodyknown Guy"), self.data)
        self.assertFalse(ok)
        self.assertIn("Nothing was saved", reply)

    def test_cli_exit_codes(self):
        body_file, reply_file = Path(self.dir.name) / "b.md", Path(self.dir.name) / "r.md"
        body_file.write_text(body(winner="Big Erl"))
        args = ["--data-dir", str(self.data), "--log-level", "ERROR", "apply-award",
                "--body-file", str(body_file), "--reply-file", str(reply_file)]
        self.assertEqual(cli(args), 0)
        self.assertIn("Erling Haaland", reply_file.read_text())
        body_file.write_text(body(winner="Erling Halaand"))
        self.assertEqual(cli(args), 3)
        self.assertIn("did you mean", reply_file.read_text())


if __name__ == "__main__":
    unittest.main()
