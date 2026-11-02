"""The fzf port: ranking parity with fzf itself, highlighting and the search syntax."""

import os
import random
import shutil
import subprocess
import unittest

from herdscope import fuzzy


def fzf(query, texts):
    """What the fzf binary ranks for `query`, whatever options this machine sets for it."""
    env = {**os.environ, "FZF_DEFAULT_OPTS": "", "FZF_DEFAULT_OPTS_FILE": ""}
    found = subprocess.run(
        ["fzf", "--filter", query, "--tiebreak=index"], input="\n".join(texts), text=True, capture_output=True, env=env
    )
    return found.stdout.splitlines()


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
        # Punctuation that starts a word scores a boundary bonus, as letters do.
        "a-b",
        "x -b",
        "foo_test",
        "foo _test",
        "a.vue",
        "a .vue",
        # A whole word beside an underscore ranks below one beside other punctuation.
        "x_foo_y",
        "x-foo-y",
        "ab__ab",
        "zz ab",
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
            "-",
            "-b",
            "_test",
            ".vue",
            "'foo'",
            "'ab' ab",
            "'ab' | foo",
            "^Rés",
        ]
        for query in queries:
            self.assertEqual(fuzzy.rank(self.TEXTS, query), fzf(query, self.TEXTS), query)

    @unittest.skipUnless(shutil.which("fzf"), "fzf is not installed")
    def test_accents_fold_as_in_the_fzf_binary(self):
        # Every character of the scripts fzf folds, looked for by each plain letter and digit.
        blocks = [(0xC0, 0x2B0), (0x363, 0x370), (0x1D00, 0x1D80), (0x1E00, 0x1F00), (0x2070, 0x20A0), (0x2184, 0x2185)]
        codes = [*(code for start, end in blocks for code in range(start, end)), *range(0xFF01, 0xFF66)]
        self.assertTrue({ord(char) for chars in fuzzy.FOLDS.values() for char in chars} <= set(codes))
        texts = [f"x{chr(code)}" for code in codes]  # After a letter, so a combining one has a base.
        for query in [*"abcdefghijklmnopqrstuvwxyz0123456789", "A", "O", "Ab", "İ", "'İ", "lodz", "!o"]:
            self.assertEqual(fuzzy.rank(texts, query), fzf(query, texts), query)

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

    def test_a_query_scores_as_its_full_match_does(self):
        # Scoring takes shortcuts, the commonest query (one fuzzy term) most of all; matching
        # for highlights doesn't. They must agree on every text and query.
        rng = random.Random(5)
        words = ["rana", "vue", "'rana", "^nvim", "rana$", "!vue", "|", "abc", "B", "café", "e"]
        for _ in range(3000):
            query = " ".join(rng.sample(words, rng.randint(1, 3)))
            for text in self.TEXTS:
                full = fuzzy.Query(query).match(text)
                self.assertEqual(fuzzy.Query(query).score(text), full and full[0], (query, text))

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
