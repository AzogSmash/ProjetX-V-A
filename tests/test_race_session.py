import asyncio
import time
import unittest

import main


class RaceSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_save = main.save_data
        main.save_data = lambda: None
        main.race_state_lock = asyncio.Lock()
        main.race_accepting = False
        main.race_is_running = False
        main.race_last_finished_at = 0.0
        main.race_bets = {"42": {"driver": 0, "amount": 100}}

    async def asyncTearDown(self):
        main.save_data = self.original_save

    async def test_any_member_path_can_open_betting(self):
        ok, _ = await main._open_race_betting()
        self.assertTrue(ok)
        self.assertTrue(main.race_accepting)

    async def test_second_open_is_rejected_without_erasing_bets(self):
        self.assertTrue((await main._open_race_betting())[0])
        main.race_bets["42"] = {"driver": 0, "amount": 100}
        ok, message = await main._open_race_betting()
        self.assertFalse(ok)
        self.assertIn("déjà ouverts", message)
        self.assertIn("42", main.race_bets)

    async def test_simultaneous_start_reserves_only_one_race(self):
        main.race_accepting = True
        first, second = await asyncio.gather(main._reserve_race_start(), main._reserve_race_start())
        successes = [result for result in (first, second) if result[1] is None]
        self.assertEqual(1, len(successes))
        self.assertTrue(main.race_is_running)
        self.assertFalse(main.race_accepting)

    async def test_post_race_cooldown_blocks_new_session(self):
        main.race_last_finished_at = time.monotonic()
        ok, message = await main._open_race_betting()
        self.assertFalse(ok)
        self.assertIn("Attendez", message)

    def test_commands_are_public_but_still_casino_commands(self):
        self.assertNotIn("ouvrir_course", main.ADMIN_LOCKED_CMDS)
        self.assertNotIn("lancer_course", main.ADMIN_LOCKED_CMDS)
        self.assertTrue({"ouvrir_course", "lancer_course"} <= main.CASINO_CMDS)


if __name__ == "__main__":
    unittest.main()
