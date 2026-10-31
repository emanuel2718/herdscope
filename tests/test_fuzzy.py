"""The fzf port: ranking parity with fzf itself, highlighting and the search syntax."""

import os
import random
import shutil
import subprocess
import unittest

from herdscope import fuzzy


class FuzzyTests(unittest.TestCase):
    TEXTS = [
        "Replacing Vue’s TypeScript 6 compatib… rana:4 opencode idle ~/git/work/rana",
        "nvim ~/git/work/rana rana:1",
        "~/git/ranamono-v2 ranamono-v2:1",
        "src/components/UserProfile.vue",
        "FooBar baz_qux 123abc",
        "a/b/c/d.e f-g h:i",
        "abc abc abcabc",
        "Vue-router setup review rana:3 claude done",
        "Résumé café naïve",
        "cafe resume naive",
    ]

    @unittest.skipUnless(shutil.which("fzf"), "fzf is not installed")
    def test_ranking_matches_the_fzf_binary(self):
        queries = [
            "rana vue",
            "rana | ^ vue",
            "nvim | ! rana",
            "rana",
            "vue",
            "nv",
            "wrk",
            "'rana",
            "^nvim",
            "rana$",
            "!rana",
            "rana | foo",
            "'abc'",
            "FB",
            "fb",
            "Bar",
            "!^nvim rana",
            "s/c/U",
            "123",
            "done$",
            "ue",
            "a",
            "rv",
            "gwr",
            "^~/g",
            "cafe",
            "naive",
            "resume",
            "Resume",
            "é",
            "ï",
            "!café",
            "^Rés",
        ]
        for query in queries:
            expected = subprocess.run(
                ["fzf", "--filter", query, "--tiebreak=index"],
                input="\n".join(self.TEXTS),
                text=True,
                capture_output=True,
                env={**os.environ, "FZF_DEFAULT_OPTS": ""},
            )
            self.assertEqual(fuzzy.rank(self.TEXTS, query), expected.stdout.splitlines(), query)

    def test_ranking_scores_equal_the_full_match(self):
        # Ranking scores every candidate without filling fzf's score matrix; it must agree with
        # the full match, which also finds the positions to highlight. Small alphabets make
        # repeats, word boundaries and case changes common.
        rng = random.Random(7)
        for _ in range(20000):
            alphabet = rng.choice(["ab", "abc ", "aAbB_-/ 1", "abcdefghij /:._-ABC123"])
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
            pattern = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 6))).lower()
            folded = fuzzy.lower(text)
            full = fuzzy.fuzzy(text, folded, pattern)
            self.assertEqual(fuzzy.fuzzy_score(text, folded, pattern), full and full[0], (text, pattern))

    def test_highlights_the_best_alignment_not_the_leftmost(self):
        text = self.TEXTS[0]
        score, positions = fuzzy.Query("rana vue").match(text)
        self.assertEqual("".join(text[i] for i in positions), "Vuerana")
        self.assertEqual(positions[:3], [text.index("Vue") + i for i in range(3)])

    def test_case_folding_keeps_one_character_per_position(self):
        for text in ("İstanbul", "ΟΔΟΣ ΣΟΦΙΑ", "Straße", "Ünïcödé"):
            self.assertEqual(len(fuzzy.lower(text)), len(text), text)
        self.assertEqual(fuzzy.Query("stanbul").match("İstanbul")[1], list(range(1, 8)))
        self.assertEqual(fuzzy.normalize("Ünïcödé × Æ"), "Unicode × Æ")

    def test_extended_syntax_and_smart_case(self):
        match = lambda query, text: fuzzy.Query(query).match(text) is not None
        self.assertTrue(match("'work", "~/git/work"))
        self.assertFalse(match("'wrk", "~/git/work"))
        self.assertTrue(match("^nvim", "  nvim x"))
        self.assertFalse(match("^vim", "nvim"))
        self.assertTrue(match("rana$", "work/rana"))
        self.assertFalse(match("!rana", "work/rana"))
        self.assertTrue(match("!rana", "vault"))
        self.assertTrue(match("vault | rana", "work/rana"))
        self.assertTrue(match("'abc'", "x abc y"))
        self.assertFalse(match("'abc'", "abcabc"))
        self.assertFalse(match("Vue", "vue"))
        self.assertTrue(match("vue", "Vue"))
        self.assertTrue(match("a\\ b", "a b"))
        self.assertFalse(match("a\\ b", "ab"))
