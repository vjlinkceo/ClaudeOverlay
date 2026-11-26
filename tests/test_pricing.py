import unittest

from overlay.claude.client import AnswerStats
from overlay.core.pricing import estimate_cost, estimate_question, estimate_tokens, format_cost, image_tokens


class PricingTests(unittest.TestCase):
    def test_sonnet_answer(self):
        stats = AnswerStats(model="Sonnet 5.5", model_id="claude-sonnet-5-5")
        stats.input_tokens, stats.output_tokens, stats.cache_write, stats.cache_read = 1000, 500, 2000, 10000
        usd = estimate_cost(stats)
        self.assertAlmostEqual(usd, (1000 * 2 + 500 * 10 + 2000 * 2.5 + 10000 * 0.2) / 1e6)
        stats.fast = True
        self.assertAlmostEqual(estimate_cost(stats), usd * 2)

    def test_unknown_model(self):
        self.assertIsNone(estimate_cost(AnswerStats(model="X", model_id="claude-unknown")))

    def test_format(self):
        self.assertEqual(format_cost(0.0001), "<0.1¢")
        self.assertEqual(format_cost(0.0034), "0.3¢")
        self.assertEqual(format_cost(0.42), "42.0¢")
        self.assertEqual(format_cost(1.5), "$1.50")


class EstimateTests(unittest.TestCase):
    def test_tokens_follow_bytes(self):
        self.assertEqual(estimate_tokens(""), 0)
        latin, cyrillic = estimate_tokens("hello world " * 10), estimate_tokens("привет мир " * 10)
        self.assertTrue(20 <= latin <= 35)
        self.assertTrue(40 <= cyrillic <= 60)
        self.assertEqual(image_tokens(1920 * 1080), 1920 * 1080 // 750)

    def test_question_estimate(self):
        usd = estimate_question("claude-sonnet-5-5", new_tokens=1000, cached_tokens=10000, output_tokens=200)
        self.assertAlmostEqual(usd, (1000 * 2.5 + 10000 * 0.2 + 200 * 10) / 1e6)
        self.assertIsNone(estimate_question("x", new_tokens=1, cached_tokens=1, output_tokens=1))


if __name__ == "__main__":
    unittest.main()
