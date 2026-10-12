"""Break one fix at a time and confirm the test written for it goes red. Restores every file.
Usage: python tools\\mutation_check.py   (from the app folder)
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MUTATIONS = [
    ("chatreader/controller.py", "if session is not None and session != self._session:", "if False:",
     "test_lines_from_a_stopped_chat_thread_are_ignored"),
    ("chatreader/speechqueue.py", "(item.raw or item.text)", "(item.text)", "test_same_line_from_three_people_is_read_once"),
    ("chatreader/controller.py", 'if reason == "paused":', 'if reason == "never":', "test_pause_while_the_voice_is_being_made_keeps_the_message"),
    ("chatreader/controller.py", "if item.message_id and item.message_id in self._deleted_ids:", "if False:",
     "test_guards_for_a_message_already_taken_from_the_line"),
    ("chatreader/controller.py", "                    self._interrupt(\"deleted_by_moderator\")", "                    pass",
     "test_delete_while_being_spoken_cuts_it_off"),
    ("chatreader/irc.py", "                if self.stopped:\n                    return\n", "", "test_no_message_is_delivered_after_stop"),
    ("chatreader/irc.py", "if time.time() - started >= self.STABLE_AFTER_S:", "if True:", "test_reconnect_wait_grows"),
    ("chatreader/controller.py", "self.queue.put(Item(text=text, raw=text, name=\"Test\"), allow_repeat=True)",
     "self.queue.put(Item(text=text, raw=text, name=\"Test\"))", "test_test_voice_can_be_pressed_again"),
    ("chatreader/controller.py", "        self._interrupt(reason)\n\n    def set_paused", "\n    def set_paused", "test_stop_reading_cuts_the_current_message"),
]


def run(test: str) -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-k", test, "-x", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0


bad = 0
for rel, old, new, test in MUTATIONS:
    path = ROOT / rel
    original = path.read_text("utf-8")
    if old not in original:
        print(f"SETUP ERROR  {test}: text to break not found in {rel}")
        bad += 1
        continue
    mutated = original.replace(old, new, 1)
    try:
        path.write_text(mutated, "utf-8")
        red = not run(test)
    finally:
        path.write_text(original, "utf-8")
    green = run(test)
    ok = red and green
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'}  {test}: broken code -> {'fails' if red else 'STILL PASSES'}; restored -> {'passes' if green else 'FAILS'}")
sys.exit(1 if bad else 0)
