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
    ("chatreader/controller.py", "            strip_markdown_links(event)\n", "", "test_markdown_link_is_read_as_its_text_only"),
    ("chatreader/audio.py", "    return np.clip(data, -1.0, 1.0)", "    return data", "test_samples_above_full_scale_are_held_at_the_limit"),
    ("chatreader/voicefiles.py", "    if have != spec.size or sha256_of(part) != spec.sha256:", "    if have != spec.size:",
     "test_a_download_that_fails_its_check_is_thrown_away"),
    ("chatreader/voicefiles.py", "    if start:\n        headers[\"Range\"] = f\"bytes={start}-\"\n", "", "test_a_cut_connection_keeps_what_arrived"),
    ("chatreader/voicefiles.py", "    if found is not None:\n        return found\n    folder = download_dir()", "    folder = download_dir()",
     "test_files_in_the_settings_folder_are_found_too"),
    ("chatreader/voicefiles.py", "        return seen.get(\"sha256\") == spec.sha256\n    digest", "        pass\n    digest",
     "test_files_already_present_make_no_request_and_are_hashed_only_once"),
    ("chatreader/viewmodel.py", "    if download_error and not ready:", "    if False:", "test_download_states"),
    ("chatreader/controller.py", "text=cut_at_word(decision.text, self.cfg.max_chars)", "text=decision.text", "test_spoken_text_is_cut_at_the_character_cap"),
    ("chatreader/controller.py", "            self.ctx.last_speaker = \"\"      # too long ago: say the name again", "            pass",
     "test_name_is_dropped_for_a_quick_follow_up_and_said_again_later"),
    ("chatreader/controller.py", "        self.set_paused(True)\n        self._clear(\"cleared\")", "        self._clear(\"cleared\")", "test_panic_clears_and_stays_silent_until_resume"),
    ("chatreader/__main__.py", "        platform._wmi_query = refuse\n", "", "test_platform_answers_without_wmi_and_without_starting_a_program"),
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
